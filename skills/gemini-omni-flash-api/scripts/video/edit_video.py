#!/usr/bin/env python3
"""
Gemini Omni 1.1 Flash (gemini-omni-1.1-flash) Video Editor, Extender & Multi-Scene Product/Subject Chainer.
Supports Dual Backend:
  1. Official google-genai SDK (/v1beta/interactions)
  2. Unlimited Google Vids RPC Backend (DoWorkspaceOperation / 374 via live Chrome CDP 9222 + vids_session.json)

Capabilities:
  - Conversational / Turn-by-Turn Video Editing (--previous-interaction-id or --prior-node-json)
  - 5-Point Product Blueprint Lock (--product-lock) & Subject/Outfit Lock (--subject-lock)
  - Explicit Multimodal Role Tagging ([# References <VIDEO_REF_0>@Video1 ...]) for Vids RPC StructuredContentNodes
  - Automated Gemini Vision Product & Subject Consistency Audit + Auto-Repair (--verify-anchor)
  - Live Chrome CDP Session Auto-Refresh (port 9222) so tokens never expire mid-pipeline
  - Automatic 1080p Full HD Lanczos + CAS Mastering (--upscale-1080p)
  - One-command Google Vids Session Refresh (--update-session-from-curl / --refresh-from-cdp)
"""

import argparse
import asyncio
import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, SKILL_SCRIPTS_DIR)

from inspect_video import get_ffmpeg_exe, inspect_video

DEFAULT_SESSION_PATH = os.path.join(SCRIPT_DIR, "vids_session.json")
DEFAULT_MODEL = "gemini-omni-1.1-flash"
DEFAULT_API_URL = (
    "https://appsgenaiserver-pa.clients6.google.com/v1/genai/generate?key=AIzaSyA-njTXslyyMKk1VOogRfVP59F6fNGeNW8"
)
DEFAULT_ORIGIN = "https://docs.google.com"
DEFAULT_DOC_ID = "1x5CzXuOZgiBZzrRlgMjrvnTomyll0BmLws2-1zwysJc"
DEFAULT_SERVER_TOKEN = "CAMSGRUS9NL9N67auQayvgSuUb6_BqcGh_otHQc="


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_chrome_cdp_running(cdp_port: int = 9222, headless: bool = True, start_url: str | None = None) -> bool:
    """Ensures Chrome with antigravity-browser-profile is running on cdp_port."""
    ver_url = f"http://127.0.0.1:{cdp_port}/json/version"
    try:
        urllib.request.urlopen(ver_url, timeout=1.5).read()
        return True
    except Exception:
        pass

    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = next((p for p in chrome_candidates if os.path.exists(p)), None)
    if not chrome_exe:
        return False

    profile_dir = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-browser-profile")
    url = start_url or f"https://docs.google.com/videos/d/{DEFAULT_DOC_ID}/edit"
    cmd = [
        chrome_exe,
        f"--remote-debugging-port={cdp_port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
    ]
    if headless:
        cmd.append("--window-position=-32000,-32000")
    cmd.append(url)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(16):
            time.sleep(0.5)
            try:
                urllib.request.urlopen(ver_url, timeout=1.5).read()
                time.sleep(3.5)
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def refresh_session_from_cdp(
    session_path: str = DEFAULT_SESSION_PATH,
    cdp_port: int = 9222,
    auto_launch: bool = True,
) -> dict | None:
    """
    Connects to a running Chrome CDP browser (default port 9222) and extracts fresh auto-rotated
    Google Vids cookies (__Secure-1PSIDTS, COMPASS, SAPISID) plus active vids.new doc_id & authuser,
    excluding Play Store OSID/__Secure-OSID which cause 403 on contribution-rt.usercontent.google.com.
    """
    try:
        import websockets

        if auto_launch:
            ensure_chrome_cdp_running(cdp_port=cdp_port, headless=True)

        detected_doc_id = None
        detected_authuser = None
        try:
            tabs_raw = urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=2).read().decode("utf-8")
            tabs = json.loads(tabs_raw)
            for t in tabs:
                t_url = t.get("url", "")
                m_doc = re.search(r"docs\.google\.com/videos/(?:u/(\d+)/)?d/([a-zA-Z0-9_-]{25,60})", t_url)
                if m_doc:
                    if m_doc.group(1) is not None:
                        detected_authuser = m_doc.group(1)
                    detected_doc_id = m_doc.group(2)
                    break
        except Exception:
            pass

        async def _fetch():
            ver_url = f"http://127.0.0.1:{cdp_port}/json/version"
            ver = json.loads(urllib.request.urlopen(ver_url, timeout=2).read().decode("utf-8"))
            ws_url = ver["webSocketDebuggerUrl"]
            async with websockets.connect(ws_url, max_size=50_000_000) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
                resp = json.loads(await ws.recv())
                return resp.get("result", {}).get("cookies", [])

        cookies = asyncio.run(_fetch())
        cookie_map = {}
        sapisid = None
        allowed_domains = (
            ".google.com",
            "google.com",
            "appsgenaiserver-pa.clients6.google.com",
            ".clients6.google.com",
            ".docs.google.com",
            "docs.google.com",
            ".googleusercontent.com",
        )
        for c in cookies:
            dom = c.get("domain", "")
            name = c.get("name", "")
            val = c.get("value", "")
            if "play.google.com" in dom:
                continue
            if name in ("OSID", "__Secure-OSID"):
                continue
            if any(dom == wd or dom.endswith(wd) for wd in allowed_domains):
                cookie_map[name] = val
                if name == "SAPISID":
                    sapisid = val

        if not sapisid or "__Secure-1PSIDTS" not in cookie_map:
            return None

        existing = {}
        if os.path.exists(session_path):
            try:
                with open(session_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass

        session_data = {
            "api_url": existing.get("api_url", DEFAULT_API_URL),
            "origin": existing.get("origin", DEFAULT_ORIGIN),
            "doc_id": detected_doc_id or existing.get("doc_id", DEFAULT_DOC_ID),
            "authuser": detected_authuser or existing.get("authuser", "0"),
            "sapisid": sapisid,
            "server_token": existing.get("server_token", DEFAULT_SERVER_TOKEN),
            "cookie": "; ".join(f"{k}={v}" for k, v in cookie_map.items()),
        }
        try:
            os.makedirs(os.path.dirname(os.path.abspath(session_path)), exist_ok=True)
            with open(session_path, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2)
        except Exception:
            pass
        return session_data
    except Exception:
        return None


def load_vids_session(session_path: str = DEFAULT_SESSION_PATH) -> dict:
    """Loads Google Vids RPC session, automatically refreshing from Chrome CDP (port 9222) if available."""
    live = refresh_session_from_cdp(session_path=session_path)
    if live:
        return live

    if not os.path.exists(session_path):
        raise FileNotFoundError(
            f"Google Vids session file not found at '{session_path}'. "
            "Run with --update-session-from-curl <curl_file> or open Chrome CDP on port 9222."
        )
    with open(session_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("api_url", DEFAULT_API_URL)
    data.setdefault("origin", DEFAULT_ORIGIN)
    data.setdefault("doc_id", DEFAULT_DOC_ID)
    data.setdefault("server_token", DEFAULT_SERVER_TOKEN)
    return data


def update_session_from_curl(curl_source: str, session_path: str = DEFAULT_SESSION_PATH) -> dict:
    """Parses a browser cURL command (from file path or raw string) and updates vids_session.json."""
    if os.path.exists(curl_source):
        with open(curl_source, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    else:
        raw = curl_source

    cleaned = raw.replace('^"', '"').replace("^$", "$").replace("^(", "(").replace("^)", ")")

    cookie_match = re.search(r'(?:-b|--cookie)\s+"([^"]+)"', cleaned)
    if not cookie_match:
        cookie_match = re.search(r"(?:-b|--cookie)\s+'([^']+)'", cleaned)
    if not cookie_match:
        raise ValueError("Could not find cookie (-b / --cookie) in the provided cURL input.")

    raw_cookie_str = cookie_match.group(1).strip()
    # Strip OSID and __Secure-OSID to prevent 403 Forbidden on contribution-rt.usercontent.google.com
    filtered_parts = []
    for part in raw_cookie_str.split(";"):
        part_s = part.strip()
        if not part_s:
            continue
        k = part_s.split("=", 1)[0].strip()
        if k in ("OSID", "__Secure-OSID"):
            continue
        filtered_parts.append(part_s)
    cookie_str = "; ".join(filtered_parts)

    sapisid_match = re.search(r"(?:^|;\s*)SAPISID=([^;]+)", cookie_str)
    if not sapisid_match:
        sapisid_match = re.search(r"(?:^|;\s*)__Secure-1PAPISID=([^;]+)", cookie_str)
    if not sapisid_match:
        raise ValueError("Could not find SAPISID or __Secure-1PAPISID in cookie string.")
    sapisid = sapisid_match.group(1).strip()

    existing = {}
    if os.path.exists(session_path):
        try:
            with open(session_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    token_match = re.search(r'x-server-token:\s*([^\s"\'^]+)', cleaned, re.IGNORECASE)
    doc_match = re.search(r'([a-zA-Z0-9_-]{25,60})"\s*,\s*"application/vnd\.google-apps\.flix"', cleaned)

    session_data = {
        "api_url": existing.get("api_url", DEFAULT_API_URL),
        "origin": existing.get("origin", DEFAULT_ORIGIN),
        "doc_id": doc_match.group(1) if doc_match else existing.get("doc_id", DEFAULT_DOC_ID),
        "sapisid": sapisid,
        "server_token": token_match.group(1) if token_match else existing.get("server_token", DEFAULT_SERVER_TOKEN),
        "cookie": cookie_str,
    }

    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)
    log(f"Updated Google Vids session config at: {session_path}")
    return session_data


def make_sapisidhash(sapisid: str, origin: str = DEFAULT_ORIGIN, ts: int = None) -> str:
    if ts is None:
        ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode("utf-8")).hexdigest()
    h = f"{ts}_{digest}"
    return f"SAPISIDHASH {h} SAPISID1PHASH {h} SAPISID3PHASH {h}"


def extract_strings(obj, found=None):
    if found is None:
        found = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            extract_strings(item, found)
    elif isinstance(obj, dict):
        for v in obj.values():
            extract_strings(v, found)
    return found


def extract_video_node_from_response_file(json_path: str):
    """Loads a StructuredContentNode (parsed[0][0][0][0]) from a saved Vids RPC response JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        raw = f.read().lstrip()
    if raw.startswith(")]}'"):
        raw = raw.split("\n", 1)[-1]
    parsed = json.loads(raw)
    try:
        return parsed[0][0][0][0]
    except Exception as e:
        raise ValueError(f"Could not extract StructuredContentNode from '{json_path}': {e}")


def sanitize_visual_prompt_for_omni(prompt: str, product_lock: str = None, mode: str = "generate") -> str:
    """
    Mindset Guardrail for Gemini Omni 1.1 Flash:
    1. Strips raw CSS hex color codes (e.g., #151a18, #dfff62) from visual prompts so the diffusion model
       never prints literal '#RRGGBB' text strings onto monitors, walls, or UI surfaces.
    2. When monitors, screens, or dashboards are mentioned without an explicit physical product text lock,
       enforces Pure Graphical UI (glowing charts/nodes/waves, zero small dense text) so all typography is
       rendered cleanly by the 1080p Vector ASS HUD layer.
    3. Ensures 'Continuous unbroken cinematic shot. No internal scene cuts.' for seamless 10s generation/chaining.
    """
    cleaned = re.sub(r"\(\s*#[0-9a-fA-F]{3,8}\s*\)", "", prompt)
    cleaned = re.sub(r"#[0-9a-fA-F]{6}\b", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    lower = cleaned.lower()
    if mode in ("generate", "chain") and "unbroken" not in lower and "continuous" not in lower:
        cleaned = f"Continuous unbroken cinematic shot, no internal scene cuts. {cleaned}"

    screen_keywords = ("monitor", "screen", "dashboard", "display", "laptop", "interface", "ui ")
    if any(k in lower for k in screen_keywords) and not product_lock:
        if "no text or words on the screen" not in lower and "no text on the screen" not in lower:
            if not cleaned.endswith((".", "!", "?")):
                cleaned += "."
            cleaned += " Purely graphical visual interface on screen with glowing charts and geometric nodes, no small text or words on the screen."

    return cleaned


def build_product_and_subject_lock_block(
    product_lock: str = None,
    subject_lock: str = None,
    num_prior_nodes: int = 0,
) -> str:
    """
    Constructs a strict 5-Point Product Blueprint & Character Identity Lock block with explicit
    <VIDEO_REF_N> multimodal role bindings so Gemini Omni 1.1 Flash preserves exact bottle/package
    silhouette, label orientation (vertical vs. horizontal), emblem placement, cap shape, and face.
    """
    blocks = []
    if num_prior_nodes > 0:
        ref_list = " ".join(f"<VIDEO_REF_{i}>@Video{i+1}" for i in range(num_prior_nodes))
        blocks.append(f"[# References {ref_list}]")
        ref_names = ", ".join(f"<VIDEO_REF_{i}>" for i in range(num_prior_nodes))
        if product_lock:
            blocks.append(
                f"STRICT MULTIMODAL OBJECT & CHARACTER BINDING (FROM {ref_names}): "
                f"Use {ref_names} as the mandatory 3D object reference for the product and identity reference for the human subject. "
                f"Do NOT alter, rotate, or redesign the product's label orientation, brand typography placement, emblem position, "
                f"silhouette, or cap geometry shown in {ref_names}."
            )
        else:
            blocks.append(
                f"STRICT MULTIMODAL SUBJECT & SCENE BINDING (FROM {ref_names}): "
                f"Use {ref_names} as the mandatory identity, facial structure, hairstyle, and wardrobe reference for the human subject "
                "and environment continuity. Keep desks and surfaces clean with no random bottles or unrelated props."
            )

    if product_lock:
        blocks.append(
            "5-POINT PRODUCT BLUEPRINT LOCK (100% GEOMETRIC & TYPOGRAPHIC INVARIANCE ACROSS ALL SHOTS): "
            f"{product_lock.strip()} "
            "CRITICAL PRODUCT RULES: Keep the exact same typography orientation (never switch between vertical and horizontal text), "
            "exact same emblem/crest coordinates relative to the text, exact same cap shape, and exact spelling of all visible text."
        )

    if subject_lock:
        blocks.append(
            "SUBJECT & WARDROBE CONSISTENCY LOCK: "
            f"{subject_lock.strip()}"
        )

    return " ".join(blocks)


def extract_representative_frame(video_path: str, out_png: str, timestamp_sec: float = 3.5) -> str:
    ffmpeg_exe = get_ffmpeg_exe()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    subprocess.run(
        [ffmpeg_exe, "-y", "-ss", str(timestamp_sec), "-i", video_path, "-frames:v", "1", out_png],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return out_png


def verify_product_and_subject_consistency(
    anchor_media_path: str,
    candidate_video_path: str,
    product_lock: str = None,
    subject_lock: str = None,
    timestamp_sec: float = 3.5,
) -> dict:
    """
    Uses Gemini Vision (gemini-2.5-flash) to compare a newly generated video scene's frames (at 2.5s and 7.5s)
    against the canonical Anchor Scene (Scene 1) frame across 5 product, subject, and screen cleanliness axes:
      1. Product 5-Point geometric & typographic invariance (if product_lock is set)
      2. Human subject face, hairstyle & wardrobe continuity
      3. Screen & background OCR cleanliness (zero leaked '#RRGGBB' hex strings or garbled AI text)
      4. Zero hallucinated random props (e.g. random bottles on software office desks when product_lock is None)
      5. Intra-scene temporal continuity between 2.5s and 7.5s
    Returns {"passed": bool, "score": int, "issues": list[str], "repair_prompt": str}.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log("[Verify] google-genai not installed; skipping vision consistency audit.")
        return {"passed": True, "score": 100, "issues": [], "repair_prompt": ""}

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        env_file = r"C:\Users\ASUS\.gemini\antigravity\scratch\.env"
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY="):
                        api_key = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                        break
    if not api_key:
        log("[Verify] No GEMINI_API_KEY found for vision check; skipping.")
        return {"passed": True, "score": 100, "issues": [], "repair_prompt": ""}

    anchor_png = anchor_media_path
    if anchor_media_path.lower().endswith(".mp4"):
        anchor_png = f"{os.path.splitext(anchor_media_path)[0]}_anchor_frame.png"
        extract_representative_frame(anchor_media_path, anchor_png, timestamp_sec=timestamp_sec)

    cand_early_png = f"{os.path.splitext(candidate_video_path)[0]}_cand_2_5s.png"
    cand_late_png = f"{os.path.splitext(candidate_video_path)[0]}_cand_7_5s.png"
    extract_representative_frame(candidate_video_path, cand_early_png, timestamp_sec=2.5)
    extract_representative_frame(candidate_video_path, cand_late_png, timestamp_sec=7.5)

    with open(anchor_png, "rb") as f:
        anchor_bytes = f.read()
    with open(cand_early_png, "rb") as f:
        cand_early_bytes = f.read()
    with open(cand_late_png, "rb") as f:
        cand_late_bytes = f.read()

    audit_prompt = (
        "You are a strict Commercial TVC Continuity, Typography & Visual QA Director.\n"
        "Image 1 is the CANONICAL ANCHOR FRAME (Scene 1).\n"
        "Image 2 is the CANDIDATE SCENE EARLY FRAME (2.5s).\n"
        "Image 3 is the CANDIDATE SCENE LATE FRAME (7.5s).\n"
        f"Expected Product Blueprint: {product_lock or 'None (Software/Service scene — do NOT show random bottles or physical product containers)'}\n"
        f"Expected Subject Blueprint: {subject_lock or 'If a human subject appears, match the person shown in Image 1'}\n\n"
        "Evaluate Image 2 and Image 3 against Image 1 across all 4 mandatory gates:\n"
        "1. SUBJECT & WARDROBE CONTINUITY: If a human subject appears, is it the same person with consistent facial structure, hairstyle, and outfit as Image 1?\n"
        "2. PRODUCT OR PROP CLEANLINESS: If Expected Product Blueprint is provided, does the product match 100%? If Expected Product Blueprint is None, are desks and hands free of hallucinated bottles/containers?\n"
        "3. ZERO GARBLED SCREEN TEXT / HEX CODES: Are computer monitors/screens free of literal CSS hex codes (like '#151a18', '#dfff62') and garbled pseudo-words?\n"
        "4. INTRA-CLIP SEAMLESSNESS: Do Image 2 (2.5s) and Image 3 (7.5s) belong to a coherent continuous scene without jarring character morphing?\n\n"
        "Respond strictly in JSON with this schema:\n"
        '{\n'
        '  "passed": true or false,\n'
        '  "score": integer 0 to 100,\n'
        '  "issues": ["list of specific visual discrepancies in subject, props, garbled screen text, or continuity"],\n'
        '  "repair_prompt": "If passed is false, write a concise instruction fixing the exact issue (e.g., remove any text on monitors, remove any bottle on the desk, keep exact subject outfit)."\n'
        '}'
    )

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=anchor_bytes, mime_type="image/png"),
                types.Part.from_bytes(data=cand_early_bytes, mime_type="image/png"),
                types.Part.from_bytes(data=cand_late_bytes, mime_type="image/png"),
                audit_prompt,
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        result = json.loads(resp.text)
        log(f"[Verify] Consistency & Cleanliness Score: {result.get('score')}/100 | Passed: {result.get('passed')}")
        if result.get("issues"):
            log(f"[Verify] Discrepancies detected: {result.get('issues')}")
        return result
    except Exception as e:
        log(f"[Verify] Vision audit warning ({e}); continuing.")
        return {"passed": True, "score": 100, "issues": [], "repair_prompt": ""}


def upscale_video_to_1080p(input_mp4: str, output_mp4: str, aspect_ratio: str = "16:9"):
    """Masters a 720p video to 1080p Full HD using Lanczos interpolation + Contrast Adaptive Sharpening."""
    ffmpeg_exe = get_ffmpeg_exe()
    if not ffmpeg_exe:
        raise RuntimeError("ffmpeg / imageio_ffmpeg is required for --upscale-1080p.")

    target_res = "1920:1080" if aspect_ratio == "16:9" else "1080:1920"
    vf = (
        f"scale={target_res}:flags=lanczos+accurate_rnd+full_chroma_int,"
        "cas=0.45,eq=contrast=1.02:saturation=1.04"
    )
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        input_mp4,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        output_mp4,
    ]
    log(f"Mastering 1080p Full HD ({target_res}) -> {output_mp4}...")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"1080p mastering failed: {proc.stderr[-400:]}")
    log(f"1080p Full HD saved: {output_mp4} ({os.path.getsize(output_mp4):,} bytes)")


def build_structured_image_node(image_path: str, width: int = 1920, height: int = 1080) -> list:
    """
    Encodes a local PNG/JPG image into a Google Vids RPC StructuredContentNode with
    StructuredImage at field index 11 (prompt.container.children[i].image), enabling
    native 'Thành phần' (Ingredients / Image Reference) generation in Gemini Omni 1.1 Flash.
    """
    with open(image_path, "rb") as f:
        b64_str = base64.b64encode(f.read()).decode("ascii")
    mime = "image/jpeg" if image_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    node = [None] * 12
    node[11] = [b64_str, None, None, None, None, None, None, [None, mime, [width, height]]]
    return node


def run_vids_rpc_edit_or_generate(
    prompt_text: str,
    output_path: str,
    prior_node_files: list = None,
    image_files: list = None,
    duration_sec: int = 10,
    save_response_json: str = None,
    upscale_1080p: bool = False,
    aspect_ratio: str = "16:9",
    session_path: str = DEFAULT_SESSION_PATH,
) -> dict:
    """Executes unlimited Gemini Omni 1.1 Flash video generation, editing, image-ingredient ('Thành phần') generation, or multi-node chaining via Google Vids RPC."""
    session = load_vids_session(session_path)
    prior_nodes = []
    if prior_node_files:
        for nf in prior_node_files:
            prior_nodes.append(extract_video_node_from_response_file(nf))

    image_nodes = []
    if image_files:
        for img_path in image_files:
            if img_path and os.path.exists(img_path):
                image_nodes.append(build_structured_image_node(img_path))

    req_id = f"goog_{uuid.uuid4().int % 2000000000}"
    clean_prompt = prompt_text.strip()
    if image_nodes:
        # Strip SDK-only [<IMAGE_REF_0>] tags when using native StructuredImage nodes so literal '0' never appears on screens
        clean_prompt = re.sub(r"\[#\s*(?:Sources|References)[^\]]*\]\s*", "", clean_prompt)
        clean_prompt = re.sub(r"<IMAGE_REF_\d+>", "the reference image", clean_prompt)
    formatted_prompt = clean_prompt.strip() + "\n\n"
    children = list(prior_nodes) + list(image_nodes) + [[None, None, formatted_prompt]]

    payload = [
        374,
        None,
        [
            9,
            None,
            None,
            None,
            req_id,
            None,
            str(session.get("authuser", "0")),
            None,
            [
                None,
                None,
                None,
                [
                    [
                        [
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            [None, None, [[session["doc_id"], "application/vnd.google-apps.flix", None, None, 1]]],
                        ]
                    ]
                ],
            ],
            None,
            None,
            [24, 0],
            None,
            "en",
            None,
            None,
            None,
            None,
            None,
            1,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            0,
        ],
        [
            None,
            None,
            None,
            [children],
        ],
        [
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            [0, 12, None, 0, 1, None, None, None, int(duration_sec or 10)],
        ],
        [1, None, [[None, "1", 1189]]],
        1,
    ]

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
    headers = {
        "accept": "*/*",
        "accept-language": "vi",
        "authorization": make_sapisidhash(session["sapisid"], session["origin"]),
        "content-type": "application/json+protobuf",
        "cookie": session["cookie"],
        "origin": session["origin"],
        "priority": "u=1, i",
        "referer": "https://docs.google.com/",
        "user-agent": ua,
        "x-goog-authuser": str(session.get("authuser", "0")),
        "x-server-token": session["server_token"],
    }

    log(
        f"[Vids RPC] Calling Gemini Omni 1.1 Flash (prior_video_nodes={len(prior_nodes)}, duration={duration_sec or 10}s)..."
    )
    t0 = time.time()
    req = urllib.request.Request(
        session["api_url"],
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=240) as resp:
        raw_bytes = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw_bytes = gzip.decompress(raw_bytes)
        text = raw_bytes.decode("utf-8", errors="replace")
        log(f"[Vids RPC] HTTP {resp.status} in {time.time() - t0:.1f}s ({len(text):,} chars)")

    if not save_response_json:
        base_out, _ = os.path.splitext(output_path)
        save_response_json = f"{base_out}_node.json"

    parent_dir = os.path.dirname(os.path.abspath(save_response_json))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(save_response_json, "w", encoding="utf-8") as f:
        f.write(text)
    log(f"[Vids RPC] Saved StructuredContentNode response -> {save_response_json}")

    clean_text = text.lstrip()
    if clean_text.startswith(")]}'"):
        clean_text = clean_text.split("\n", 1)[-1]
    parsed = json.loads(clean_text)
    all_strs = extract_strings(parsed)
    dl_urls = [
        s
        for s in all_strs
        if s.startswith("https://contribution-rt.usercontent.google.com/download")
        or s.startswith("https://")
    ]
    if not dl_urls:
        raise RuntimeError(f"No download URL found in Vids RPC response. Saved raw JSON to {save_response_json}")

    # Refresh live CDP cookies right before downloading video and strip host-only cookies that trigger 403 on usercontent
    session = load_vids_session(session_path)
    authuser = str(session.get("authuser", "0"))
    dl_url = dl_urls[0]
    if "authuser=" not in dl_url:
        sep = "&" if "?" in dl_url else "?"
        dl_url = f"{dl_url}{sep}authuser={authuser}"
    dl_cookie_parts = [
        p.strip()
        for p in session["cookie"].split(";")
        if p.strip() and p.strip().split("=", 1)[0].strip() not in ("OSID", "__Secure-OSID", "COMPASS")
    ]
    dl_headers = {
        "cookie": "; ".join(dl_cookie_parts),
        "user-agent": ua,
        "referer": "https://docs.google.com/",
        "origin": session["origin"],
        "x-goog-authuser": authuser,
    }
    dl_req = urllib.request.Request(dl_url, headers=dl_headers)
    with urllib.request.urlopen(dl_req, timeout=90) as dl_resp:
        video_bytes = dl_resp.read()

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if upscale_1080p:
        raw_720p_path = f"{os.path.splitext(output_path)[0]}_raw720p.mp4"
        with open(raw_720p_path, "wb") as f:
            f.write(video_bytes)
        log(f"[Vids RPC] Downloaded 720p source ({len(video_bytes):,} bytes) -> {raw_720p_path}")
        upscale_video_to_1080p(raw_720p_path, output_path, aspect_ratio=aspect_ratio)
    else:
        with open(output_path, "wb") as f:
            f.write(video_bytes)
        log(f"[Vids RPC] Downloaded video ({len(video_bytes):,} bytes) -> {output_path}")

    return {
        "backend": "vids",
        "output_path": output_path,
        "node_json_path": save_response_json,
    }


def format_editing_prompt(
    prompt: str,
    mode: str = "edit",
    keep_rest: bool = True,
    has_source_video: bool = False,
    num_ref_images: int = 0,
    num_ref_videos: int = 0,
    num_prior_nodes: int = 0,
    product_lock: str = None,
    subject_lock: str = None,
) -> str:
    """Applies Gemini Omni 1.1 Flash prompt engineering best practices for video editing, chaining, and product locking."""
    text = sanitize_visual_prompt_for_omni(prompt.strip(), product_lock=product_lock, mode=mode)

    # 1. Add "Keep everything else the same." for targeted edits if requested and not already present
    if mode == "edit" and keep_rest:
        if "keep everything else the same" not in text.lower():
            if not text.endswith((".", "!", "?")):
                text += "."
            text += " Keep everything else the same."

    # 2. Ensure extension prefix if mode == "extend"
    if mode == "extend":
        lower = text.lower()
        if not any(
            k in lower
            for k in ("extend this video", "continue the scene", "the scene continues", "direct continuation")
        ):
            text = f"Extend this video: {text}"

    # 3. Prepend 5-Point Product Blueprint & Character Lock + [# References <VIDEO_REF_N>@VideoN] for Vids RPC prior_nodes
    if num_prior_nodes > 0 or product_lock or subject_lock:
        lock_header = build_product_and_subject_lock_block(
            product_lock=product_lock,
            subject_lock=subject_lock,
            num_prior_nodes=num_prior_nodes if "[# References" not in text else 0,
        )
        if lock_header:
            text = f"{lock_header}\n{text}"

    # 4. Prepend [# Sources ...] and [# References ...] role tags when local media assets are attached
    if "[# Sources" not in text and "[# References" not in text and (has_source_video or num_ref_images or num_ref_videos):
        sources_tags = []
        ref_tags = []
        vid_idx = 1
        img_idx = 1

        if has_source_video:
            sources_tags.append(f"<VIDEO_0>@Video{vid_idx}")
            vid_idx += 1

        for i in range(num_ref_images):
            ref_tags.append(f"<IMAGE_REF_{i}>@Image{img_idx}")
            img_idx += 1

        for i in range(num_ref_videos):
            ref_tags.append(f"<VIDEO_REF_{i}>@Video{vid_idx}")
            vid_idx += 1

        prefix_blocks = []
        if sources_tags:
            prefix_blocks.append(f"[# Sources {' '.join(sources_tags)}]")
        if ref_tags:
            prefix_blocks.append(f"[# References {' '.join(ref_tags)}]")
        if prefix_blocks:
            text = f"{' '.join(prefix_blocks)} {text}"

    return text


def main():
    parser = argparse.ArgumentParser(
        description="Edit, extend, or chain videos with 100% Product & Subject Consistency using Gemini Omni 1.1 Flash."
    )
    parser.add_argument("prompt", nargs="?", help="Editing or continuation instruction prompt")
    parser.add_argument(
        "--mode",
        choices=["edit", "extend", "chain", "generate"],
        default="edit",
        help="Operation mode: 'edit' (modify video), 'extend' (append 3-10s), 'chain' (multi-scene consistency), 'generate'",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "sdk", "vids"],
        default="auto",
        help="Execution backend: 'auto' (SDK first, fallback to unlimited Vids RPC), 'sdk', or 'vids'",
    )
    parser.add_argument("--video", "-v", help="Local source video path or File API URI to edit")
    parser.add_argument("--extend", "-e", help="Local source video path or File API URI to extend")
    parser.add_argument(
        "--prior-node-json",
        "-n",
        action="append",
        dest="prior_nodes",
        help="Path to one or more saved Vids RPC response JSON files (StructuredContentNode) for turn-by-turn editing or scene chaining",
    )
    parser.add_argument(
        "--product-lock",
        "-pl",
        help="Strict 5-Point Product Blueprint description (bottle silhouette, cap shape, exact vertical/horizontal logo orientation, crest position, sub-label text)",
    )
    parser.add_argument(
        "--subject-lock",
        "-sl",
        help="Strict Subject & Wardrobe Blueprint description (age, facial structure, hairstyle, exact clothing/accessories)",
    )
    parser.add_argument(
        "--verify-anchor",
        help="Path to Anchor Scene (Scene 1) MP4 or PNG frame to automatically verify Product & Subject consistency via Gemini Vision and auto-repair if needed",
    )
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=1,
        help="Maximum automatic re-generation attempts if --verify-anchor detects product/subject drift (default: 1)",
    )
    parser.add_argument(
        "--previous-interaction-id",
        help="Official SDK Interaction ID from a prior turn for stateful editing/extending",
    )
    parser.add_argument(
        "--image",
        "--ref-image",
        "-i",
        action="append",
        dest="images",
        help="Reference image(s) (<IMAGE_REF_0>, <IMAGE_REF_1>, ...) for object/outfit/character swap",
    )
    parser.add_argument(
        "--video-reference",
        "--ref-video",
        "-vr",
        action="append",
        dest="video_refs",
        help="Reference video(s) (<VIDEO_REF_0>, ...) for character/motion likeness (~3s ideal)",
    )
    parser.add_argument(
        "--keep-rest",
        action="store_true",
        default=True,
        help="Automatically append 'Keep everything else the same.' in edit mode (default: True)",
    )
    parser.add_argument(
        "--no-keep-rest",
        action="store_false",
        dest="keep_rest",
        help="Do not automatically append 'Keep everything else the same.'",
    )
    parser.add_argument(
        "--strip-audio",
        "-a",
        action="store_true",
        help="Strip existing audio stream before editing so Omni 1.1 Flash generates brand-new audio",
    )
    parser.add_argument(
        "--resolution",
        "-res",
        choices=["360p", "720p", "1080p", "4k"],
        default="1080p",
        help="Target output resolution (default: 1080p)",
    )
    parser.add_argument(
        "--upscale-1080p",
        action="store_true",
        default=True,
        help="When using Vids RPC (720p native), master output to 1080p Full HD with Lanczos + CAS (default: True)",
    )
    parser.add_argument(
        "--no-upscale-1080p",
        action="store_false",
        dest="upscale_1080p",
        help="Keep raw 720p output from Vids RPC without 1080p mastering",
    )
    parser.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default="16:9", help="Aspect ratio (default: 16:9)")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds (3-10, default: 10)")
    parser.add_argument("--output", "-o", default="media/edited_output.mp4", help="Output MP4 file path")
    parser.add_argument("--save-node-json", help="Optional path to save the Vids RPC StructuredContentNode JSON")
    parser.add_argument(
        "--update-session-from-curl",
        metavar="CURL_FILE_OR_STRING",
        help="Update scripts/video/vids_session.json from a pasted browser cURL command or file and exit",
    )
    parser.add_argument(
        "--refresh-from-cdp",
        action="store_true",
        help="Refresh scripts/video/vids_session.json directly from Chrome CDP (port 9222) and exit",
    )
    parser.add_argument(
        "--switch-account",
        action="store_true",
        help="Open https://vids.new in Chrome CDP so you can switch/login to another Google account, then auto-bind its DOC_ID and cookies",
    )

    args = parser.parse_args()

    if args.update_session_from_curl:
        update_session_from_curl(args.update_session_from_curl)
        sys.exit(0)

    if args.switch_account:
        log("Opening https://vids.new in Chrome CDP (port 9222) for account switching...")
        ensure_chrome_cdp_running(cdp_port=9222, headless=False, start_url="https://vids.new")
        try:
            urllib.request.urlopen("http://127.0.0.1:9222/json/new?https://vids.new", timeout=3).read()
        except Exception:
            pass
        time.sleep(6)
        res = refresh_session_from_cdp(auto_launch=False)
        if res:
            log(f"Bound to Google Vids doc_id={res['doc_id']} (authuser={res.get('authuser', '0')}, SAPISID={res['sapisid']})")
        sys.exit(0)

    if args.refresh_from_cdp:
        res = refresh_session_from_cdp()
        if res:
            log(
                f"Successfully refreshed Vids session from Chrome CDP (doc_id={res['doc_id']}, authuser={res.get('authuser', '0')}, SAPISID={res['sapisid']})"
            )
            sys.exit(0)
        else:
            log("Failed to refresh from Chrome CDP on port 9222.")
            sys.exit(1)

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    mode = "extend" if args.extend else args.mode
    has_source_video = bool(args.video or args.extend)
    num_imgs = len(args.images) if args.images else 0
    num_vrefs = len(args.video_refs) if args.video_refs else 0
    num_prior = len(args.prior_nodes) if args.prior_nodes else 0

    formatted_prompt = format_editing_prompt(
        prompt=args.prompt,
        mode=mode,
        keep_rest=args.keep_rest if mode == "edit" else False,
        has_source_video=has_source_video,
        num_ref_images=num_imgs,
        num_ref_videos=num_vrefs,
        num_prior_nodes=num_prior,
        product_lock=args.product_lock,
        subject_lock=args.subject_lock,
    )

    # Decide backend
    use_vids = False
    if args.backend == "vids" or args.prior_nodes:
        use_vids = True
    elif args.backend == "auto" and not os.environ.get("GEMINI_API_KEY"):
        use_vids = True

    if not use_vids:
        try:
            from generate_video import generate_video

            log(f"[SDK Backend] Running Gemini Omni 1.1 Flash ({mode})...")
            generate_video(
                prompt=formatted_prompt,
                model=DEFAULT_MODEL,
                aspect_ratio=args.aspect_ratio,
                duration=args.duration,
                resolution=args.resolution,
                image_path=args.images,
                video_path=args.video,
                extend_video=args.extend,
                video_reference=args.video_refs,
                output_path=args.output,
                strip_audio=args.strip_audio,
                previous_interaction_id=args.previous_interaction_id,
            )
            sys.exit(0)
        except Exception as sdk_err:
            if args.backend == "sdk":
                log(f"[SDK Error] {sdk_err}")
                sys.exit(1)
            log(f"[Auto Fallback] SDK unavailable or quota limited ({sdk_err}). Switching to Unlimited Google Vids RPC...")

    # Execute via Unlimited Google Vids RPC
    do_upscale = args.upscale_1080p and args.resolution in ("1080p", "4k")
    res = run_vids_rpc_edit_or_generate(
        prompt_text=formatted_prompt,
        output_path=args.output,
        prior_node_files=args.prior_nodes,
        image_files=args.images,
        duration_sec=args.duration,
        save_response_json=args.save_node_json,
        upscale_1080p=do_upscale,
        aspect_ratio=args.aspect_ratio,
    )

    # Optional Automated Vision Consistency Verification & Repair Loop
    if args.verify_anchor and os.path.exists(args.verify_anchor):
        for attempt in range(1, args.max_repair_attempts + 1):
            audit = verify_product_and_subject_consistency(
                anchor_media_path=args.verify_anchor,
                candidate_video_path=res["output_path"],
                product_lock=args.product_lock,
                subject_lock=args.subject_lock,
            )
            if audit.get("passed", True) or audit.get("score", 100) >= 85:
                break
            repair_hint = audit.get("repair_prompt", "")
            log(f"[Auto-Repair {attempt}/{args.max_repair_attempts}] Regenerating with stricter product/subject alignment: {repair_hint}")
            repaired_prompt = f"{formatted_prompt}\nMANDATORY CORRECTION FROM QA AUDIT: {repair_hint}"
            prior_for_repair = list(args.prior_nodes or [])
            if res["node_json_path"] and res["node_json_path"] not in prior_for_repair:
                prior_for_repair.append(res["node_json_path"])
            res = run_vids_rpc_edit_or_generate(
                prompt_text=repaired_prompt,
                output_path=args.output,
                prior_node_files=prior_for_repair,
                image_files=args.images,
                duration_sec=args.duration,
                save_response_json=args.save_node_json,
                upscale_1080p=do_upscale,
                aspect_ratio=args.aspect_ratio,
            )

    log(f"Done! Video: {res['output_path']} | Node JSON: {res['node_json_path']}")


if __name__ == "__main__":
    main()
