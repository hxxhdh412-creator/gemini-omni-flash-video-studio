#!/usr/bin/env python3
"""
OMNI 1.1 FLASH VIDEO STUDIO — Production Cloud Server & REST API (`omni_server.py`)
===================================================================================
Deployable on any Linux/Windows VPS, Docker container, or Cloud VM (Claude Code, Railway, Render, etc.).

Key Server Features:
1. Zero-Dependency HTTP Server + Modern Dark-Emerald Web Studio UI (`http://0.0.0.0:8080`).
2. Headless 24/7 Cookie Keep-Alive & Auto-Rotator:
   - Automatically refreshes `__Secure-1PSIDTS` via Google's `RotateCookies` endpoint every 8 minutes
     using `SAPISIDHASH` + `__Secure-1PSIDRTS` (works on headless Linux servers without Chrome!).
   - Also auto-syncs with Chrome CDP (`port 9222`) if available on the host.
3. Environment & Hot-Reload Session Management:
   - Bootstraps `vids_session.json` automatically from `OMNI_COOKIE`, `OMNI_DOC_ID`, `OMNI_AUTHUSER` env vars,
     or via `POST /api/session` (accepts raw Cookie string, `vids_session.json` object, or raw browser cURL).
4. Full Async Job Queue & REST API for AI Agents (Claude Code, n8n, Make, Custom Apps):
   - `GET  /api/health`             -> Server & session health check
   - `GET  /api/session/status`     -> Current `doc_id`, `authuser`, cookie status, last rotation time
   - `POST /api/session`            -> Hot-update cookie / cURL / `doc_id` / `GEMINI_API_KEY` without restarting
   - `POST /api/session/rotate`     -> Force immediate headless `__Secure-1PSIDTS` rotation
   - `POST /api/generate-scene`     -> Generate a 10s Omni 1.1 Flash clip (with optional `StructuredImage` ingredient & chaining)
   - `POST /api/produce-commercial` -> End-to-End 10s/30s/60s 1080p Commercial (Multi-Scene Omni + 1-Take Gemini 3.8 TTS + Lyria BGM)
   - `GET  /api/jobs`               -> List all active & completed jobs
   - `GET  /api/jobs/<job_id>`      -> Poll job progress, logs, and download URLs
   - `GET  /outputs/<filename>`     -> Download generated `.mp4` / `.json` files
"""

import argparse
import base64
import hashlib
import http.cookies
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL_VIDEO_DIR = os.path.join(REPO_ROOT, "skills", "gemini-omni-flash-api", "scripts", "video")
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
OUTPUTS_DIR = os.path.join(REPO_ROOT, "outputs")
UPLOADS_DIR = os.path.join(OUTPUTS_DIR, "uploads")
JOBS_DIR = os.path.join(OUTPUTS_DIR, "jobs")

os.makedirs(OUTPUTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(JOBS_DIR, exist_ok=True)

sys.path.insert(0, SKILL_VIDEO_DIR)
sys.path.insert(0, EXAMPLES_DIR)

from edit_video import (
    DEFAULT_API_URL,
    DEFAULT_DOC_ID,
    DEFAULT_ORIGIN,
    DEFAULT_SERVER_TOKEN,
    DEFAULT_SESSION_PATH,
    describe_image_subject_for_omni,
    detect_image_mime_and_dimensions,
    ensure_png_or_jpeg_image_file,
    format_editing_prompt,
    get_ffmpeg_exe,
    load_vids_session,
    log,
    refresh_session_from_cdp,
    run_vids_rpc_edit_or_generate,
    run_vids_rpc_generate_image,
    update_session_from_curl,
)

# Global Job Registry & Rotation State
JOBS_LOCK = threading.Lock()
JOBS: dict[str, dict] = {}
SESSION_META = {
    "last_rotated_at": None,
    "last_rotate_status": "idle",
    "rotate_count": 0,
}


def load_dotenv_if_present():
    env_path = os.path.join(REPO_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v


def bootstrap_session_from_env():
    """
    If vids_session.json does not exist yet and OMNI_COOKIE is set in environment/.env,
    initializes vids_session.json so cloud deployments can be configured via environment variables.
    Never overwrites an existing vids_session.json that was updated via Web UI / API / cURL.
    """
    load_dotenv_if_present()
    if os.path.exists(DEFAULT_SESSION_PATH):
        try:
            with open(DEFAULT_SESSION_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("cookie") and existing.get("doc_id"):
                return
        except Exception:
            pass
    env_cookie = os.environ.get("OMNI_COOKIE", "").strip()
    if not env_cookie:
        return
    cookie_parts = []
    sapisid = None
    for part in env_cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in ("OSID", "__Secure-OSID"):
            continue
        cookie_parts.append(f"{k}={v}")
        if k == "SAPISID":
            sapisid = v
        elif k in ("__Secure-1PAPISID", "__Secure-3PAPISID") and not sapisid:
            sapisid = v

    sess = {
        "api_url": os.environ.get("OMNI_API_URL", DEFAULT_API_URL),
        "origin": DEFAULT_ORIGIN,
        "doc_id": os.environ.get("OMNI_DOC_ID", DEFAULT_DOC_ID),
        "authuser": os.environ.get("OMNI_AUTHUSER", "0"),
        "sapisid": sapisid or "",
        "server_token": os.environ.get("OMNI_SERVER_TOKEN", DEFAULT_SERVER_TOKEN),
        "cookie": "; ".join(cookie_parts),
    }
    os.makedirs(os.path.dirname(DEFAULT_SESSION_PATH), exist_ok=True)
    with open(DEFAULT_SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(sess, f, indent=2)
    log(f"[Server Bootstrap] Initialized vids_session.json from OMNI_COOKIE env (doc_id={sess['doc_id']}, authuser={sess['authuser']})")


def rotate_google_cookies_headless(session_path: str = DEFAULT_SESSION_PATH) -> dict:
    """
    Headless Pure-Python Cookie Rotator for Linux/Cloud Servers:
    1. First checks if local Chrome CDP (port 9222) is running; if so, pulls live rotated cookies.
    2. Otherwise, calls Google's `https://accounts.google.com/RotateCookies` endpoint directly over HTTPS
       using the current cookie jar (`SID`, `HSID`, `SSID`, `SAPISID`, `__Secure-1PSID`, `__Secure-1PSIDRTS`)
       and updates `__Secure-1PSIDTS`, `__Secure-3PSIDTS`, `SIDCC`, `__Secure-1PSIDCC`, `__Secure-3PSIDCC`
       in `vids_session.json` so the session stays alive on headless servers!
    """
    # Try CDP first (non-launching on headless Linux)
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1.0).read()
        cdp_sess = refresh_session_from_cdp(session_path=session_path, auto_launch=False)
        if cdp_sess:
            SESSION_META["last_rotated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            SESSION_META["last_rotate_status"] = "ok_cdp"
            SESSION_META["rotate_count"] += 1
            return {"status": "ok", "method": "cdp", "authuser": cdp_sess.get("authuser"), "doc_id": cdp_sess.get("doc_id")}
    except Exception:
        pass

    if not os.path.exists(session_path):
        return {"status": "error", "message": "vids_session.json not found"}

    with open(session_path, "r", encoding="utf-8") as f:
        sess = json.load(f)

    cookie_str = sess.get("cookie", "")
    if not cookie_str:
        return {"status": "error", "message": "Empty cookie in vids_session.json"}

    cookie_map = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_map[k.strip()] = v.strip()

    sapisid = sess.get("sapisid") or cookie_map.get("SAPISID") or cookie_map.get("__Secure-1PAPISID") or ""
    origin = "https://accounts.google.com"
    now_ts = int(time.time())
    sapisid_hash = hashlib.sha1(f"{now_ts} {sapisid} {origin}".encode("utf-8")).hexdigest()

    # Google RotateCookies payload: [658, "<random_or_timestamp>"]
    payload = json.dumps([658, f"-{now_ts}000"]).encode("utf-8")
    req = urllib.request.Request(
        "https://accounts.google.com/RotateCookies",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie_str,
            "Origin": origin,
            "Referer": "https://accounts.google.com/",
            "Authorization": f"SAPISIDHASH {now_ts}_{sapisid_hash}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
        method="POST",
    )

    ctx = ssl._create_unverified_context()
    updated_keys = []
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            set_cookies = resp.headers.get_all("Set-Cookie") or []
            for sc_header in set_cookies:
                first_pair = sc_header.split(";", 1)[0].strip()
                if "=" in first_pair:
                    k, v = first_pair.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k not in ("OSID", "__Secure-OSID") and v:
                        cookie_map[k] = v
                        updated_keys.append(k)
    except urllib.error.HTTPError as he:
        set_cookies = he.headers.get_all("Set-Cookie") if he.headers else []
        for sc_header in set_cookies or []:
            first_pair = sc_header.split(";", 1)[0].strip()
            if "=" in first_pair:
                k, v = first_pair.split("=", 1)
                k, v = k.strip(), v.strip()
                if k not in ("OSID", "__Secure-OSID") and v:
                    cookie_map[k] = v
                    updated_keys.append(k)

    # Also ping docs.google.com/videos/d/<doc_id>/edit to pick up fresh COMPASS / SIDCC cookies
    doc_id = sess.get("doc_id", DEFAULT_DOC_ID)
    authuser = sess.get("authuser", "0")
    try:
        ping_req = urllib.request.Request(
            f"https://docs.google.com/videos/u/{authuser}/d/{doc_id}/edit?authuser={authuser}",
            headers={
                "Cookie": "; ".join(f"{k}={v}" for k, v in cookie_map.items()),
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            },
            method="GET",
        )
        with urllib.request.urlopen(ping_req, timeout=15, context=ctx) as resp:
            for sc_header in resp.headers.get_all("Set-Cookie") or []:
                first_pair = sc_header.split(";", 1)[0].strip()
                if "=" in first_pair:
                    k, v = first_pair.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k not in ("OSID", "__Secure-OSID") and v:
                        cookie_map[k] = v
                        updated_keys.append(k)
    except Exception:
        pass

    sess["cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_map.items())
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(sess, f, indent=2)

    SESSION_META["last_rotated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SESSION_META["last_rotate_status"] = f"ok_http (updated: {','.join(updated_keys) or 'keepalive'})"
    SESSION_META["rotate_count"] += 1
    return {
        "status": "ok",
        "method": "http_rotate",
        "updated_cookies": updated_keys,
        "last_rotated_at": SESSION_META["last_rotated_at"],
    }


def start_cookie_keepalive_daemon(interval_sec: int = 480):
    def _loop():
        while True:
            try:
                if os.path.exists(DEFAULT_SESSION_PATH):
                    res = rotate_google_cookies_headless(DEFAULT_SESSION_PATH)
                    log(f"[Cookie KeepAlive] {res}")
            except Exception as e:
                log(f"[Cookie KeepAlive Warning] {e}")
            time.sleep(interval_sec)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def update_session_payload(body: dict) -> dict:
    """
    Updates vids_session.json and/or GEMINI_API_KEY from a JSON request body.
    Supports:
      - {"curl": "curl 'https://appsgenaiserver...' ..."}
      - {"cookie": "SID=...; __Secure-1PSIDTS=...", "doc_id": "...", "authuser": "0"}
      - {"vids_session": {...}}
      - {"gemini_api_key": "AIzaSy..."}
    """
    if body.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = body["gemini_api_key"].strip()
        env_file = os.path.join(REPO_ROOT, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(f'GEMINI_API_KEY="{os.environ["GEMINI_API_KEY"]}"\n')

    if body.get("curl"):
        sess = update_session_from_curl(body["curl"], DEFAULT_SESSION_PATH)
        return {"status": "ok", "source": "curl", "doc_id": sess.get("doc_id"), "authuser": sess.get("authuser")}

    if body.get("vids_session") and isinstance(body["vids_session"], dict):
        sess = body["vids_session"]
        os.makedirs(os.path.dirname(DEFAULT_SESSION_PATH), exist_ok=True)
        with open(DEFAULT_SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2)
        return {"status": "ok", "source": "vids_session", "doc_id": sess.get("doc_id"), "authuser": sess.get("authuser")}

    if body.get("cookie"):
        existing = {}
        if os.path.exists(DEFAULT_SESSION_PATH):
            try:
                with open(DEFAULT_SESSION_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        raw_cookie = body["cookie"].strip()
        parts = []
        sapisid = existing.get("sapisid", "")
        for p in raw_cookie.split(";"):
            p = p.strip()
            if "=" in p:
                k, v = p.split("=", 1)
                k, v = k.strip(), v.strip()
                if k in ("OSID", "__Secure-OSID"):
                    continue
                parts.append(f"{k}={v}")
                if k in ("SAPISID", "__Secure-1PAPISID") and v:
                    sapisid = v

        sess = {
            "api_url": body.get("api_url") or existing.get("api_url", DEFAULT_API_URL),
            "origin": DEFAULT_ORIGIN,
            "doc_id": body.get("doc_id") or existing.get("doc_id", DEFAULT_DOC_ID),
            "authuser": str(body.get("authuser", existing.get("authuser", "0"))),
            "sapisid": sapisid,
            "server_token": body.get("server_token") or existing.get("server_token", DEFAULT_SERVER_TOKEN),
            "cookie": "; ".join(parts),
        }
        os.makedirs(os.path.dirname(DEFAULT_SESSION_PATH), exist_ok=True)
        with open(DEFAULT_SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2)
        return {"status": "ok", "source": "cookie", "doc_id": sess.get("doc_id"), "authuser": sess.get("authuser")}

    return {"status": "ok", "message": "Updated API key only"}


def get_session_status() -> dict:
    has_file = os.path.exists(DEFAULT_SESSION_PATH)
    sess = {}
    if has_file:
        try:
            with open(DEFAULT_SESSION_PATH, "r", encoding="utf-8") as f:
                sess = json.load(f)
        except Exception:
            pass
    cookie_str = sess.get("cookie", "")
    has_1psidts = "__Secure-1PSIDTS=" in cookie_str
    has_sapisid = bool(sess.get("sapisid")) or ("SAPISID=" in cookie_str)
    session_ready = bool(has_file and has_1psidts and has_sapisid)
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    return {
        "session_ready": session_ready,
        "doc_id": sess.get("doc_id"),
        "authuser": sess.get("authuser", "0"),
        "has_1psidts": has_1psidts,
        "has_sapisid": has_sapisid,
        "cookie_length": len(cookie_str),
        "gemini_api_key_configured": bool(session_ready or gemini_key),
        "tts_backend": "vids_cookie_uc254" if session_ready else ("gemini_api" if gemini_key else "neural_fallback"),
        "last_rotated_at": SESSION_META["last_rotated_at"],
        "last_rotate_status": SESSION_META["last_rotate_status"],
        "rotate_count": SESSION_META["rotate_count"],
    }


def save_base64_image_if_needed(img_spec: str, job_dir: str, idx: int) -> str:
    """
    Accepts a local file path, file:/// URI, relative repo path, HTTP/HTTPS image URL,
    or base64 data URL / raw base64 string.
    Inspects binary magic bytes to save with matching .jpg or .png extension (and converts WebP to PNG)
    so Google Vids RPC StructuredImage (Node[11]) and Reference-to-Video (UseCase 376) receive exact MIME and dimensions.
    """
    if not img_spec:
        return ""
    clean_spec = str(img_spec).strip()
    if clean_spec.lower().startswith("file:///"):
        clean_spec = urllib.parse.unquote(clean_spec[8:])
        if os.name == "nt" and clean_spec.startswith("/") and len(clean_spec) > 2 and clean_spec[2] == ":":
            clean_spec = clean_spec[1:]
    elif clean_spec.lower().startswith("file://"):
        clean_spec = urllib.parse.unquote(clean_spec[7:])
    if clean_spec.startswith("/outputs/"):
        clean_spec = clean_spec.lstrip("/")
    if os.path.exists(clean_spec):
        norm_p, _, _, _, _ = ensure_png_or_jpeg_image_file(os.path.abspath(clean_spec))
        return norm_p
    rel_candidate = os.path.join(REPO_ROOT, clean_spec)
    if os.path.exists(rel_candidate):
        norm_p, _, _, _, _ = ensure_png_or_jpeg_image_file(os.path.abspath(rel_candidate))
        return norm_p
    if clean_spec.lower().startswith(("http://", "https://")):
        req = urllib.request.Request(clean_spec, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        mime, _, _ = detect_image_mime_and_dimensions(raw)
        ext = ".jpg" if mime == "image/jpeg" else ".webp" if mime == "image/webp" else ".png"
        out_p = os.path.join(job_dir, f"ingredient_{idx}{ext}")
        with open(out_p, "wb") as f:
            f.write(raw)
        norm_p, _, _, _, _ = ensure_png_or_jpeg_image_file(out_p)
        return norm_p
    if clean_spec.startswith("data:image/"):
        _, b64_data = clean_spec.split(",", 1)
    else:
        b64_data = clean_spec
    if b64_data.endswith("..."):
        raise ValueError(
            "Reference image base64 string was truncated ('...'). Please re-upload the image file in Web Studio."
        )
    raw = base64.b64decode(b64_data)
    mime, _, _ = detect_image_mime_and_dimensions(raw)
    ext = ".jpg" if mime == "image/jpeg" else ".webp" if mime == "image/webp" else ".png"
    out_p = os.path.join(job_dir, f"ingredient_{idx}{ext}")
    with open(out_p, "wb") as f:
        f.write(raw)
    norm_p, _, _, _, _ = ensure_png_or_jpeg_image_file(out_p)
    return norm_p


def run_job_generate_scene(job_id: str, payload: dict):
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    ffmpeg_exe = get_ffmpeg_exe()
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["logs"].append("Starting Omni 1.1 Flash scene generation...")

        prompt = payload.get("prompt", "")
        duration = max(3, min(15, int(payload.get("duration", 15))))
        aspect_ratio = str(payload.get("aspect_ratio") or "16:9").strip()
        upscale_1080p = bool(payload.get("upscale_1080p", False))
        extend_mode = str(payload.get("extend_mode") or "none").strip()
        out_name = payload.get("output_filename") or f"omni_scene_{job_id}.mp4"
        out_path = os.path.join(OUTPUTS_DIR, os.path.basename(out_name))
        node_path = os.path.join(OUTPUTS_DIR, os.path.splitext(os.path.basename(out_name))[0] + "_node.json")

        prior_nodes = []
        prior_video_mp4 = None
        raw_priors = list(payload.get("prior_node_files") or [])
        if payload.get("prior_video"):
            raw_priors.insert(0, payload["prior_video"])

        for pn in raw_priors:
            clean_pn = str(pn or "").strip().lstrip("/\\")
            if clean_pn.startswith("outputs/") or clean_pn.startswith("outputs\\"):
                clean_pn = clean_pn.split("/", 1)[-1].split("\\", 1)[-1]
            cand = pn if os.path.exists(pn) else os.path.join(OUTPUTS_DIR, clean_pn)
            if not os.path.exists(cand):
                continue
            if cand.lower().endswith(".mp4"):
                if not prior_video_mp4:
                    prior_video_mp4 = cand
                stem = os.path.splitext(cand)[0]
                if stem.endswith("_raw720p"):
                    stem = stem[: -len("_raw720p")]
                cand_node = stem + "_node.json"
                if os.path.exists(cand_node) and cand_node not in prior_nodes:
                    prior_nodes.append(cand_node)
            elif cand.lower().endswith(".json"):
                if cand not in prior_nodes:
                    prior_nodes.append(cand)
                stem = cand[: -len("_node.json")] if cand.lower().endswith("_node.json") else os.path.splitext(cand)[0]
                for mp4_cand in (stem + ".mp4", stem + "_raw720p.mp4"):
                    if os.path.exists(mp4_cand) and not prior_video_mp4:
                        prior_video_mp4 = mp4_cand
                        break

        is_extending = bool(prior_nodes or prior_video_mp4) and extend_mode != "none"
        if (prior_nodes or prior_video_mp4) and extend_mode == "none":
            extend_mode = "extend_concat" if prior_video_mp4 else "chain_only"
            is_extending = True

        image_files = []
        input_mode = str(payload.get("input_mode") or "auto").strip().lower()
        raw_img = "" if input_mode == "text" else str(payload.get("image") or "").strip()
        # If extending a prior video and the user didn't explicitly upload a new custom image (or left default asset_01),
        # extract the exact final frame from prior_video_mp4 to guarantee seamless visual continuation
        use_last_frame_from_prior = False
        if is_extending and prior_video_mp4 and (not raw_img or "asset_01_kenh_ket_noi_1080p.png" in raw_img):
            use_last_frame_from_prior = True

        if use_last_frame_from_prior and ffmpeg_exe:
            last_frame_png = os.path.join(job_dir, "prior_last_frame.png")
            try:
                subprocess.run(
                    [ffmpeg_exe, "-y", "-sseof", "-0.15", "-i", prior_video_mp4, "-frames:v", "1", last_frame_png],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=15,
                )
                if os.path.exists(last_frame_png) and os.path.getsize(last_frame_png) > 500:
                    image_files.append(last_frame_png)
                    with JOBS_LOCK:
                        JOBS[job_id]["logs"].append(
                            f"🔗 [Video Extend] Extracted final frame from {os.path.basename(prior_video_mp4)} for seamless continuation."
                        )
            except Exception:
                pass

        if input_mode != "text":
            if not image_files and raw_img:
                image_files.append(save_base64_image_if_needed(raw_img, job_dir, 1))
            for i, img in enumerate(payload.get("images") or []):
                image_files.append(save_base64_image_if_needed(img, job_dir, i + 1))
            image_files = [p for p in image_files if p]

        if image_files:
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(
                    f"🖼️ [Reference-to-Video UC=376] Using reference image: {', '.join(os.path.basename(p) for p in image_files)}"
                )
        else:
            prompt = re.sub(r"\s+from the reference image\b", "", prompt, flags=re.IGNORECASE)
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append("📝 [Text-to-Video UC=374] Generating scene directly from text prompt (no reference image)...")

        if is_extending:
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(
                    f"➕ [Google Vids Extend Mode: {extend_mode}] Continuing from prior scene "
                    f"({os.path.basename(prior_video_mp4 or (prior_nodes[0] if prior_nodes else ''))})..."
                )
            if "direct seamless continuation" not in prompt.lower() and "extend this video" not in prompt.lower():
                prompt = (
                    "Direct seamless continuation from the final frame of the previous scene, maintaining 100% identical "
                    "character, facial features, outfit, environment, lighting, and camera continuity: " + prompt
                )

        def _on_raw_ready(raw_p: str, node_p: str):
            raw_fn = os.path.basename(raw_p)
            node_fn = os.path.basename(node_p)
            with JOBS_LOCK:
                JOBS[job_id]["preview_url"] = f"/outputs/{raw_fn}"
                JOBS[job_id]["node_json_url"] = f"/outputs/{node_fn}"
                JOBS[job_id]["logs"].append(
                    f"⚡ Native 720p stream ready from Google Vids: /outputs/{raw_fn}"
                    + (" (Mastering 1080p Full HD in background...)" if upscale_1080p else "")
                )

        # If stitching with prior_video_mp4 (extend_concat), generate segment into a temporary/segment path first
        should_concat = extend_mode == "extend_concat" and prior_video_mp4 and os.path.exists(prior_video_mp4)
        seg_out_path = (
            os.path.join(OUTPUTS_DIR, f"{os.path.splitext(os.path.basename(out_name))[0]}_ext_seg.mp4")
            if should_concat
            else out_path
        )

        res = run_vids_rpc_edit_or_generate(
            prompt_text=prompt,
            output_path=seg_out_path,
            prior_node_files=prior_nodes,
            image_files=image_files,
            duration_sec=duration,
            save_response_json=node_path,
            upscale_1080p=upscale_1080p,
            aspect_ratio=aspect_ratio,
            on_raw_ready=_on_raw_ready,
        )

        if should_concat and ffmpeg_exe:
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(
                    f"🎬 [Video Extend] Stitching {os.path.basename(prior_video_mp4)} + new {duration}s segment -> {os.path.basename(out_path)}..."
                )
            target_res = "1080:1920" if aspect_ratio == "9:16" else "1920:1080"
            vf_concat = (
                f"[0:v]fps=24,scale={target_res}:flags=lanczos,setsar=1[v0];"
                f"[1:v]fps=24,scale={target_res}:flags=lanczos,setsar=1[v1];"
                "[v0][v1]concat=n=2:v=1:a=0[vout]"
            )
            # Check if both videos have audio streams; if not, stitch video cleanly
            concat_cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                prior_video_mp4,
                "-i",
                res["output_path"],
                "-filter_complex",
                vf_concat,
                "-map",
                "[vout]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "17",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                out_path,
            ]
            c_proc = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if c_proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                final_file = os.path.basename(out_path)
            else:
                final_file = os.path.basename(res["output_path"])
        else:
            final_file = os.path.basename(res["output_path"])

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["output_file"] = final_file
            JOBS[job_id]["download_url"] = f"/outputs/{final_file}"
            JOBS[job_id]["node_json_url"] = f"/outputs/{os.path.basename(node_path)}"
            JOBS[job_id]["logs"].append(f"✅ Completed: /outputs/{final_file}")
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(e)
            JOBS[job_id]["logs"].append(traceback.format_exc())


def run_job_produce_commercial(job_id: str, payload: dict):
    """
    High-Speed Parallel Multi-Scene Commercial Pipeline (Matches native vids.new speed!):
    - Thread A (Audio): Synthesizes 1-Take Emotional Vietnamese Voiceover (`gemini-3.8-flash-tts`) concurrently
    - Thread B (Music): Composes 48kHz Stereo soundtrack (`Google Lyria RealTime`) concurrently
    - Thread C (Video): Generates Scene 1 Anchor first, then optionally runs Scene 2..N in parallel threads
      when `parallel_scenes=True` (default), cutting 4-scene 60s TVC generation time by ~55%!
    """
    from concurrent.futures import ThreadPoolExecutor
    from make_emotional_1take_vo import EMOTIONAL_DIRECTOR_STYLE, build_1take_master, call_gemini_38_flash_tts_1take
    from produce_commercial import generate_bgm_track

    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    ffmpeg_exe = get_ffmpeg_exe()
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
            JOBS[job_id]["scene_previews"] = {}
            JOBS[job_id]["logs"].append("🚀 Starting parallel vids.new-speed commercial pipeline...")

        scenes_in = payload.get("scenes") or []
        if not scenes_in:
            raise ValueError("payload.scenes array is required")

        n_scenes = len(scenes_in)
        scene_dur = max(3, min(15, int(payload.get("scene_duration", 15))))
        aspect_ratio = str(payload.get("aspect_ratio") or "16:9").strip()
        voice_name = payload.get("voice", "Puck")
        chain_anchor = bool(payload.get("chain_anchor", True))
        parallel_scenes = bool(payload.get("parallel_scenes", True))
        burn_subtitles = bool(payload.get("burn_subtitles", True))
        total_dur = n_scenes * scene_dur
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

        formatted_scenes = []
        for i, sc in enumerate(scenes_in):
            idx = i + 1
            sc_dur_i = max(3, min(15, int(sc.get("duration", scene_dur))))
            vo_text = sc.get("vo", "")
            formatted_scenes.append(
                {
                    "idx": idx,
                    "badge": sc.get("badge", f"0{idx} // OMNI 1.1 FLASH ({sc_dur_i}S)"),
                    "vo": vo_text,
                    "sub_1": sc.get("sub_1") or vo_text[: len(vo_text) // 2],
                    "sub_2": sc.get("sub_2") or vo_text[len(vo_text) // 2 :],
                }
            )

        # Launch 1-Take TTS and Lyria BGM in background threads IMMEDIATELY so they cost 0 extra seconds!
        bgm_wav = os.path.join(job_dir, "bgm.wav")
        full_1take_text = "\n\n".join(sc["vo"] for sc in formatted_scenes)

        def _bg_bgm():
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(f"🎵 [Parallel Audio] Composing {total_dur}s Lyria RealTime 48kHz soundtrack...")
            inst_raw = payload.get("instrumental")
            inst_val = bool(inst_raw) if isinstance(inst_raw, bool) else None
            generate_bgm_track(
                output_wav=bgm_wav,
                duration_sec=total_dur,
                bgm_prompt=payload.get("bgm_prompt", "Cinematic modern corporate electronic commercial music"),
                bpm=int(payload.get("bpm", 116)),
                scale=payload.get("scale", "MINOR"),
                instrumental=inst_val,
            )
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(f"✅ [Parallel Audio] Lyria RealTime 48kHz BGM ready.")

        def _bg_tts():
            vo_style = payload.get("vo_style") or payload.get("style") or EMOTIONAL_DIRECTOR_STYLE
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(f"🎙️ [Parallel Audio] Recording Single-Pass 1-Take voiceover ({voice_name}, vids.new UC=254 / gemini-3.8-flash-tts)...")
            res_tts = call_gemini_38_flash_tts_1take(
                full_text=full_1take_text,
                style=vo_style,
                api_key=api_key,
                voice=voice_name,
            )
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(f"✅ [Parallel Audio] 1-Take Voiceover ready.")
            return res_tts

        audio_pool = ThreadPoolExecutor(max_workers=2)
        fut_bgm = audio_pool.submit(_bg_bgm)
        fut_tts = audio_pool.submit(_bg_tts)

        scene_mp4s = [None] * n_scenes
        node_jsons = [None] * n_scenes

        # Pre-resolve Scene 1 subject identity if a custom image was uploaded so Scene 2..N prompts
        # don't accidentally revert to the default "32-year-old Vietnamese male founder" template.
        input_mode = str(payload.get("input_mode") or "auto").strip().lower()
        scene1_custom_subject = ""
        scene1_custom_img_path = ""
        if input_mode != "text" and scenes_in and scenes_in[0].get("image"):
            try:
                s1_img_p = save_base64_image_if_needed(scenes_in[0]["image"], job_dir, 1)
                if s1_img_p and os.path.exists(s1_img_p):
                    s1_info = describe_image_subject_for_omni(s1_img_p)
                    if not s1_info.get("is_hebum_ui"):
                        scene1_custom_img_path = s1_img_p
                        if s1_info.get("subject_description"):
                            scene1_custom_subject = s1_info["subject_description"]
            except Exception:
                pass

        def _is_default_hebum_asset(img_str: str) -> bool:
            b = os.path.basename(str(img_str or "").strip()).lower()
            return any(b.startswith(f"asset_0{k}_") for k in range(1, 7))

        def _gen_single_scene(zero_idx: int, prior_files: list, use_rpc_lock: bool):
            sc = scenes_in[zero_idx]
            idx = zero_idx + 1
            sc_dur_i = max(3, min(15, int(sc.get("duration", scene_dur))))
            with JOBS_LOCK:
                JOBS[job_id]["logs"].append(f"🎬 [Scene {idx}/{n_scenes}] Generating {sc_dur_i}s Omni 1.1 Flash clip ({aspect_ratio})...")
            raw_mp4 = os.path.join(OUTPUTS_DIR, f"job_{job_id}_s{idx}_720p.mp4")
            node_json = os.path.join(job_dir, f"s{idx}_node.json")
            img_files = []
            sc_img_raw = "" if input_mode == "text" else str(sc.get("image") or "").strip()
            if input_mode != "text":
                if zero_idx > 0 and chain_anchor and scene1_custom_img_path:
                    # If Scene 1 has a custom uploaded portrait/product and Scene 2..N still had a default asset_01..06,
                    # use Scene 1's custom portrait instead of the default HEBUM UI screenshot!
                    if not sc_img_raw or _is_default_hebum_asset(sc_img_raw):
                        img_files.append(scene1_custom_img_path)
                    else:
                        cur_img_p = save_base64_image_if_needed(sc_img_raw, job_dir, idx)
                        if cur_img_p:
                            img_files.append(cur_img_p)
                elif sc_img_raw:
                    cur_img_p = save_base64_image_if_needed(sc_img_raw, job_dir, idx)
                    if cur_img_p and cur_img_p not in img_files:
                        img_files.append(cur_img_p)
            sc_prompt = sc.get("prompt", "")
            if input_mode == "text" or not img_files:
                sc_prompt = re.sub(r"\s+from the reference image\b", "", sc_prompt, flags=re.IGNORECASE)
            elif scene1_custom_img_path and (
                "32-year-old vietnamese male" in sc_prompt.lower()
                or "charcoal-black blazer" in sc_prompt.lower()
                or "hebum" in sc_prompt.lower()
            ):
                sc_prompt = (
                    "The person in the reference image smiles warmly and presents naturally with confident gestures "
                    "in a modern executive studio office with soft cinematic lighting and smooth camera movement."
                )
            res = run_vids_rpc_edit_or_generate(
                prompt_text=sc_prompt,
                output_path=raw_mp4,
                prior_node_files=prior_files,
                image_files=[p for p in img_files if p],
                duration_sec=sc_dur_i,
                save_response_json=node_json,
                upscale_1080p=False,
                aspect_ratio=aspect_ratio,
                use_lock=use_rpc_lock,
            )
            rel_preview = f"/outputs/{os.path.basename(raw_mp4)}"
            with JOBS_LOCK:
                JOBS[job_id]["scene_previews"][str(zero_idx)] = rel_preview
                if zero_idx == 0:
                    JOBS[job_id]["preview_url"] = rel_preview
                JOBS[job_id]["logs"].append(f"⚡ [Scene {idx}/{n_scenes}] Ready -> {rel_preview}")
            scene_mp4s[zero_idx] = res["output_path"]
            node_jsons[zero_idx] = res["node_json_path"]

        if chain_anchor:
            # Generate Scene 1 first to obtain s1_node.json anchor
            _gen_single_scene(0, [], use_rpc_lock=False)
            anchor_list = [node_jsons[0]] if node_jsons[0] else []
            if n_scenes > 1:
                if parallel_scenes:
                    with ThreadPoolExecutor(max_workers=min(3, n_scenes - 1)) as vpool:
                        futs = [vpool.submit(_gen_single_scene, i, anchor_list, False) for i in range(1, n_scenes)]
                        for f in futs:
                            f.result()
                else:
                    for i in range(1, n_scenes):
                        _gen_single_scene(i, anchor_list, use_rpc_lock=True)
        else:
            # No anchor dependency -> generate all scenes concurrently!
            if parallel_scenes and n_scenes > 1:
                with ThreadPoolExecutor(max_workers=min(3, n_scenes)) as vpool:
                    futs = [vpool.submit(_gen_single_scene, i, [], False) for i in range(n_scenes)]
                    for f in futs:
                        f.result()
            else:
                for i in range(n_scenes):
                    _gen_single_scene(i, [], use_rpc_lock=True)

        fut_bgm.result()
        precomputed_pcm = fut_tts.result()
        audio_pool.shutdown(wait=False)

        out_name = payload.get("output_filename") or f"commercial_{job_id}_1080p.mp4"
        out_mp4 = os.path.join(OUTPUTS_DIR, os.path.basename(out_name))

        with JOBS_LOCK:
            JOBS[job_id]["logs"].append(f"🎚️ Mastering {total_dur}s 1080p Full HD (Clean Audio Bus + Spectral Carve + Sidechain 6:1)...")
        build_1take_master(
            scenes=formatted_scenes,
            scene_mp4s=scene_mp4s,
            bgm_wav=bgm_wav,
            work_dir=job_dir,
            output_mp4=out_mp4,
            api_key=api_key,
            ffmpeg_exe=ffmpeg_exe,
            tag_label=f"JOB-{job_id[:6]}",
            scene_duration=scene_dur,
            voice=voice_name,
            aspect_ratio=aspect_ratio,
            burn_subtitles=burn_subtitles,
            precomputed_pcm_tuple=precomputed_pcm,
        )

        final_file = os.path.basename(out_mp4)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["output_file"] = final_file
            JOBS[job_id]["download_url"] = f"/outputs/{final_file}"
            JOBS[job_id]["logs"].append(f"✅ Master 1080p saved: /outputs/{final_file}")
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(e)
            JOBS[job_id]["logs"].append(traceback.format_exc())


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <title>Omni 1.1 Flash Video Studio — Cloud Server</title>
  <style>
    :root { --bg: #07130e; --card: #0d2118; --accent: #10b981; --lime: #dfff62; --text: #ecfdf5; --muted: #6ee7b7; }
    * { box-sizing: border-box; font-family: 'Segoe UI', system-ui, sans-serif; }
    body { margin: 0; padding: 28px; background: var(--bg); color: var(--text); }
    .container { max-width: 1120px; margin: 0 auto; }
    h1 { margin: 0 0 6px; font-size: 26px; color: var(--lime); }
    .sub { color: var(--muted); margin-bottom: 22px; font-size: 14px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    .card { background: var(--card); border: 1px solid #1a3a2a; border-radius: 12px; padding: 20px; }
    h2 { margin-top: 0; font-size: 18px; color: var(--lime); }
    label { display: block; font-size: 13px; color: var(--muted); margin: 10px 0 4px; }
    input, textarea { width: 100%; background: #06100b; border: 1px solid #1f4432; color: #fff; padding: 10px; border-radius: 8px; font-size: 13px; }
    textarea { min-height: 84px; resize: vertical; }
    button { background: var(--accent); color: #04130c; font-weight: 700; border: none; padding: 10px 18px; border-radius: 8px; cursor: pointer; margin-top: 12px; }
    button.secondary { background: #1e3a2f; color: var(--text); margin-left: 8px; }
    pre { background: #040b08; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 12px; color: #a7f3d0; max-height: 280px; }
    .badge { display: inline-block; padding: 4px 10px; border-radius: 99px; font-size: 12px; font-weight: 700; background: #064e3b; color: var(--lime); }
  </style>
</head>
<body>
  <div class="container">
    <h1>🎬 OMNI 1.1 FLASH VIDEO STUDIO — CLOUD SERVER</h1>
    <div class="sub">Server-Ready REST API & Web Dashboard • Native StructuredImage (Node[11]) • 24/7 Headless Cookie Auto-Rotator • 1-Take Gemini 3.8 TTS</div>
    <div class="grid">
      <div class="card">
        <h2>🔐 1. Trạng Thái Phiên & Cập Nhật Cookie (`vids.new`)</h2>
        <div id="statusBox"><span class="badge">Đang kiểm tra...</span></div>
        <label>Chuỗi Cookie (`SID=...; __Secure-1PSIDTS=...`) hoặc lệnh cURL:</label>
        <textarea id="cookieInput" placeholder="Dán chuỗi Cookie hoặc cURL từ vids.new vào đây..."></textarea>
        <div style="display:flex; gap:10px;">
          <div style="flex:2"><label>Google Vids `doc_id`:</label><input id="docIdInput" placeholder="1x5CzXuOZgiBZzrRlgMjrvnTomyll0BmLws2-1zwysJc" /></div>
          <div style="flex:1"><label>`authuser`:</label><input id="authuserInput" value="0" /></div>
        </div>
        <label>`GEMINI_API_KEY` (cho Gemini 3.8 Flash TTS & Lyria RealTime):</label>
        <input id="apiKeyInput" placeholder="AIzaSy..." />
        <button onclick="saveSession()">💾 Lưu Cookie & Khóa Bảo Mật</button>
        <button class="secondary" onclick="rotateNow()">🔄 Xoay vòng Cookie (`RotateCookies`)</button>
      </div>

      <div class="card">
        <h2>🎥 2. Tạo Nhanh Cảnh Video Omni 1.1 Flash (`10s`)</h2>
        <label>Prompt Mô Tả Cảnh (Tiếng Anh):</label>
        <textarea id="promptInput">Continuous unbroken cinematic shot in a sleek dark-emerald executive glass office. A charismatic 32-year-old Vietnamese founder gestures toward a frameless ultrawide glass monitor displaying the exact CRM interface from the reference image. No spoken dialogue.</textarea>
        <label>Đường dẫn ảnh Thành phần (`StructuredImage` — tùy chọn):</label>
        <input id="imageInput" value="assets_1080p/asset_01_kenh_ket_noi_1080p.png" />
        <label>Tên file xuất (`outputs/`):</label>
        <input id="outNameInput" value="server_scene_1080p.mp4" />
        <button onclick="generateScene()">🚀 Bắt Đầu Tạo Video (Async Job)</button>
        <pre id="jobLog">Chưa có tiến trình nào.</pre>
      </div>
    </div>
  </div>
  <script>
    async function loadStatus() {
      const r = await fetch('/api/session/status');
      const d = await r.json();
      document.getElementById('statusBox').innerHTML =
        `<span class="badge">${d.session_ready ? '✅ SESSION READY' : '⚠️ THIẾU COOKIE'}</span> ` +
        `doc_id: <code>${d.doc_id || 'N/A'}</code> | authuser: <code>${d.authuser}</code> | ` +
        `1PSIDTS: <b>${d.has_1psidts}</b> | Rotate: <b>${d.rotate_count}</b> (${d.last_rotated_at || 'chưa xoay'})`;
      if (d.doc_id) document.getElementById('docIdInput').value = d.doc_id;
      document.getElementById('authuserInput').value = d.authuser || '0';
    }
    async function saveSession() {
      const val = document.getElementById('cookieInput').value.trim();
      const payload = {
        doc_id: document.getElementById('docIdInput').value.trim(),
        authuser: document.getElementById('authuserInput').value.trim(),
        gemini_api_key: document.getElementById('apiKeyInput').value.trim()
      };
      if (val.startsWith('curl ')) payload.curl = val;
      else if (val) payload.cookie = val;
      const r = await fetch('/api/session', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      alert(JSON.stringify(await r.json()));
      loadStatus();
    }
    async function rotateNow() {
      const r = await fetch('/api/session/rotate', { method: 'POST' });
      alert(JSON.stringify(await r.json()));
      loadStatus();
    }
    async function generateScene() {
      const payload = {
        prompt: document.getElementById('promptInput').value.trim(),
        image: document.getElementById('imageInput').value.trim(),
        output_filename: document.getElementById('outNameInput').value.trim(),
        upscale_1080p: true
      };
      const r = await fetch('/api/generate-scene', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      const d = await r.json();
      pollJob(d.job_id);
    }
    async function pollJob(jobId) {
      const box = document.getElementById('jobLog');
      const timer = setInterval(async () => {
        const r = await fetch('/api/jobs/' + jobId);
        const d = await r.json();
        box.textContent = JSON.stringify(d, null, 2);
        if (d.status === 'completed' || d.status === 'failed') clearInterval(timer);
      }, 2500);
    }
    loadStatus();
  </script>
</body>
</html>
"""


class OmniStudioHandler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status: int = 200):
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept, Mcp-Session-Id, Last-Event-ID")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
            pass

    def do_OPTIONS(self):
        self._send_json({"ok": True})

    def do_DELETE(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            web_index = os.path.join(REPO_ROOT, "web", "index.html")
            if os.path.exists(web_index):
                with open(web_index, "rb") as f:
                    raw = f.read()
            else:
                raw = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path.startswith("/assets_1080p/"):
            rel_name = os.path.basename(urllib.parse.unquote(path.split("/assets_1080p/", 1)[1]))
            file_p = os.path.join(REPO_ROOT, "assets_1080p", rel_name)
            if not os.path.exists(file_p) or not os.path.isfile(file_p):
                self._send_json({"error": "Asset not found"}, 404)
                return
            mime, _ = mimetypes.guess_type(file_p)
            mime = mime or "image/png"
            size = os.path.getsize(file_p)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(file_p, "rb") as f:
                try:
                    shutil.copyfileobj(f, self.wfile)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    pass
            return

        if path == "/api/outputs":
            items = []
            if os.path.exists(OUTPUTS_DIR):
                for fn in sorted(os.listdir(OUTPUTS_DIR), reverse=True):
                    fp = os.path.join(OUTPUTS_DIR, fn)
                    if os.path.isfile(fp) and fn.endswith((".mp4", ".wav", ".json", ".png", ".jpg", ".jpeg")):
                        items.append(
                            {
                                "name": fn,
                                "size_bytes": os.path.getsize(fp),
                                "size_mb": round(os.path.getsize(fp) / (1024 * 1024), 2),
                                "url": f"/outputs/{urllib.parse.quote(fn)}",
                            }
                        )
            self._send_json({"files": items})
            return

        if path == "/api/health":
            self._send_json({"status": "ok", "service": "omni-1.1-flash-video-studio", "session": get_session_status()})
            return

        if path == "/api/skill/SKILL.md":
            sk_path = os.path.join(REPO_ROOT, "skills", "gemini-omni-flash-api", "SKILL.md")
            txt = ""
            if os.path.exists(sk_path):
                with open(sk_path, "r", encoding="utf-8") as f:
                    txt = f.read()
            raw = txt.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)
            return

        if path in ("/mcp", "/api/mcp/tools") or path.startswith("/mcp/"):
            import importlib
            import omni_mcp_server
            importlib.reload(omni_mcp_server)
            from omni_mcp_server import MCP_INSTRUCTIONS, MCP_SERVER_INFO, MCP_TOOLS_SCHEMA

            host_hdr = self.headers.get("Host") or "localhost:8765"
            scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
            mcp_url = f"{scheme}://{host_hdr}{path if path.startswith('/mcp') else '/mcp'}"

            # Support SSE handshake if client requests text/event-stream
            accept_hdr = self.headers.get("Accept", "")
            if "text/event-stream" in accept_hdr and path.startswith("/mcp"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                evt = f"event: endpoint\ndata: {path}\n\n"
                self.wfile.write(evt.encode("utf-8"))
                self.wfile.flush()
                return

            self._send_json(
                {
                    "server": MCP_SERVER_INFO,
                    "mcp_url": mcp_url,
                    "instructions": MCP_INSTRUCTIONS,
                    "antigravity_config": {
                        "mcpServers": {
                            "omni-flash-video-studio": {
                                "serverUrl": mcp_url,
                            }
                        }
                    },
                    "transports": {
                        "serverUrl": mcp_url,
                        "streamable_http": "/mcp",
                    },
                    "tools_count": len(MCP_TOOLS_SCHEMA),
                    "tools": MCP_TOOLS_SCHEMA,
                }
            )
            return

        if path == "/api/session/status":
            self._send_json(get_session_status())
            return

        if path == "/api/jobs":
            with JOBS_LOCK:
                self._send_json({"jobs": list(JOBS.values())})
            return

        if path.startswith("/api/jobs/"):
            job_id = path.split("/api/jobs/", 1)[1].strip("/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send_json({"error": "Job not found"}, 404)
            else:
                self._send_json(job)
            return

        if path.startswith("/outputs/"):
            rel_name = os.path.basename(urllib.parse.unquote(path.split("/outputs/", 1)[1]))
            file_p = os.path.join(OUTPUTS_DIR, rel_name)
            if not os.path.exists(file_p) or not os.path.isfile(file_p):
                self._send_json({"error": "File not found"}, 404)
                return
            mime, _ = mimetypes.guess_type(file_p)
            mime = mime or "application/octet-stream"
            size = os.path.getsize(file_p)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(file_p, "rb") as f:
                try:
                    shutil.copyfileobj(f, self.wfile)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    pass
            return

        self._send_json({"error": f"Unknown endpoint: {path}"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body = json.loads(raw_body) if raw_body.strip() else {}
        except Exception as e:
            self._send_json({"error": f"Invalid JSON: {e}"}, 400)
            return

        if path == "/mcp" or path.startswith("/mcp/"):
            import importlib
            import omni_mcp_server
            importlib.reload(omni_mcp_server)
            from omni_mcp_server import handle_mcp_jsonrpc

            if isinstance(body, list):
                res_list = [r for r in (handle_mcp_jsonrpc(item) for item in body) if r is not None]
                self._send_json(res_list)
            else:
                resp = handle_mcp_jsonrpc(body)
                if resp is None:
                    try:
                        self.send_response(202)
                        self.end_headers()
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
                        pass
                else:
                    self._send_json(resp)
            return

        if path == "/api/session":
            res = update_session_payload(body)
            self._send_json(res)
            return

        if path == "/api/session/rotate":
            res = rotate_google_cookies_headless(DEFAULT_SESSION_PATH)
            self._send_json(res)
            return

        if path == "/api/tts-preview":
            try:
                import wave
                from make_emotional_1take_vo import EMOTIONAL_DIRECTOR_STYLE, call_gemini_38_flash_tts_1take

                api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
                text = body.get("text", "").strip()
                if not text:
                    self._send_json({"error": "Missing voiceover text"}, 400)
                    return
                voice = body.get("voice", "Puck")
                style = body.get("style") or EMOTIONAL_DIRECTOR_STYLE
                out_name = os.path.basename(body.get("output_filename") or f"vo_1take_{uuid.uuid4().hex[:6]}.wav")
                out_wav = os.path.join(OUTPUTS_DIR, out_name)
                pcm_bytes, sr = call_gemini_38_flash_tts_1take(full_text=text, style=style, api_key=api_key, voice=voice)
                with wave.open(out_wav, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm_bytes)
                dur = round((len(pcm_bytes) / 2.0) / sr, 2)
                self._send_json(
                    {
                        "status": "ok",
                        "download_url": f"/outputs/{out_name}",
                        "duration_sec": dur,
                        "voice": voice,
                        "style": style,
                    }
                )
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        if path == "/api/bgm-preview":
            try:
                from produce_commercial import generate_bgm_track, parse_bgm_prompt_profile

                prompt = body.get("prompt", "Cinematic modern corporate electronic commercial music")
                dur = int(body.get("duration", 30))
                bpm = int(body.get("bpm", 116))
                scale = body.get("scale", "MINOR")
                inst_raw = body.get("instrumental")
                instrumental = bool(inst_raw) if isinstance(inst_raw, bool) else None
                out_name = os.path.basename(body.get("output_filename") or f"lyria_bgm_{uuid.uuid4().hex[:6]}.wav")
                out_wav = os.path.join(OUTPUTS_DIR, out_name)
                profile = parse_bgm_prompt_profile(prompt, requested_bpm=bpm, scale=scale)
                generate_bgm_track(
                    output_wav=out_wav,
                    duration_sec=dur,
                    bgm_prompt=prompt,
                    bpm=bpm,
                    scale=scale,
                    force_regenerate=True,
                    instrumental=instrumental,
                )
                self._send_json(
                    {
                        "status": "ok",
                        "download_url": f"/outputs/{out_name}",
                        "duration_sec": dur,
                        "profile": profile,
                    }
                )
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        if path == "/api/upload-image":
            try:
                data_str = (body.get("data") or "").strip()
                if not data_str:
                    self._send_json({"error": "Missing image base64 data"}, 400)
                    return
                if data_str.startswith("data:image/"):
                    _, b64_data = data_str.split(",", 1)
                else:
                    b64_data = data_str
                raw = base64.b64decode(b64_data)
                mime, real_w, real_h = detect_image_mime_and_dimensions(raw)
                orig_name = os.path.splitext(os.path.basename(body.get("filename") or "ref"))[0]
                safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", orig_name).strip("_")[:36] or "ref"
                ext = ".jpg" if mime == "image/jpeg" else ".webp" if mime == "image/webp" else ".png"
                fn = f"upload_{int(time.time())}_{uuid.uuid4().hex[:4]}_{safe_stem}{ext}"
                saved_path = os.path.join(OUTPUTS_DIR, fn)
                with open(saved_path, "wb") as f:
                    f.write(raw)
                norm_path, _, norm_mime, norm_w, norm_h = ensure_png_or_jpeg_image_file(saved_path)
                final_fn = os.path.basename(norm_path)
                subj_info = {"is_hebum_ui": False, "is_character_or_person": False, "subject_description": "", "suggested_prompt": ""}
                if body.get("analyze_subject", True):
                    subj_info = describe_image_subject_for_omni(norm_path)
                self._send_json(
                    {
                        "status": "ok",
                        "filename": final_fn,
                        "relative_path": f"outputs/{final_fn}",
                        "download_url": f"/outputs/{final_fn}",
                        "mime": norm_mime,
                        "width": norm_w,
                        "height": norm_h,
                        "is_character_or_person": subj_info.get("is_character_or_person", False),
                        "subject_description": subj_info.get("subject_description", ""),
                        "suggested_prompt": subj_info.get("suggested_prompt", ""),
                    }
                )
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        if path == "/api/generate-image":
            try:
                prompt = (body.get("prompt") or "").strip()
                if not prompt:
                    self._send_json({"error": "Missing image prompt"}, 400)
                    return
                aspect_ratio = body.get("aspect_ratio", "16:9")
                style = body.get("style", "PHOTOGRAPHY")
                num_images = max(1, min(4, int(body.get("num_images", 1))))
                out_name = os.path.basename(body.get("output_filename") or f"cookie_img_{uuid.uuid4().hex[:6]}.png")
                if not out_name.lower().endswith((".png", ".jpg", ".jpeg")):
                    out_name += ".png"
                out_path = os.path.join(OUTPUTS_DIR, out_name)
                ref_imgs = []
                if body.get("image"):
                    ref_imgs.append(save_base64_image_if_needed(body["image"], OUTPUTS_DIR, 1))
                for i, im in enumerate(body.get("images") or []):
                    ref_imgs.append(save_base64_image_if_needed(im, OUTPUTS_DIR, i + 1))
                ref_imgs = [p for p in ref_imgs if p]
                res = run_vids_rpc_generate_image(
                    prompt_text=prompt,
                    output_path=out_path,
                    aspect_ratio=aspect_ratio,
                    style=style,
                    num_images=num_images,
                    image_files=ref_imgs,
                    save_all=(num_images > 1),
                )
                imgs = []
                for p in res.get("images", [res["output_path"]]):
                    fn = os.path.basename(p)
                    imgs.append(
                        {
                            "filename": fn,
                            "relative_path": f"outputs/{fn}",
                            "download_url": f"/outputs/{fn}",
                        }
                    )
                self._send_json(
                    {
                        "status": "ok",
                        "backend": "vids_cookie_image",
                        "download_url": imgs[0]["download_url"],
                        "relative_path": imgs[0]["relative_path"],
                        "images": imgs,
                        "aspect_ratio": res.get("aspect_ratio", aspect_ratio),
                        "style": res.get("style", style),
                    }
                )
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
            return

        if path == "/api/generate-scene":
            job_id = uuid.uuid4().hex[:10]
            job_obj = {
                "job_id": job_id,
                "type": "generate-scene",
                "status": "queued",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [],
            }
            with JOBS_LOCK:
                JOBS[job_id] = job_obj
            t = threading.Thread(target=run_job_generate_scene, args=(job_id, body), daemon=True)
            t.start()
            self._send_json({"status": "queued", "job_id": job_id, "poll_url": f"/api/jobs/{job_id}"}, 202)
            return

        if path == "/api/produce-commercial":
            job_id = uuid.uuid4().hex[:10]
            job_obj = {
                "job_id": job_id,
                "type": "produce-commercial",
                "status": "queued",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [],
            }
            with JOBS_LOCK:
                JOBS[job_id] = job_obj
            t = threading.Thread(target=run_job_produce_commercial, args=(job_id, body), daemon=True)
            t.start()
            self._send_json({"status": "queued", "job_id": job_id, "poll_url": f"/api/jobs/{job_id}"}, 202)
            return

        self._send_json({"error": f"Unknown POST endpoint: {path}"}, 404)


class ResilientThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        import sys
        exc_type, _, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError):
            return
        super().handle_error(request, client_address)


def main():
    parser = argparse.ArgumentParser(description="Omni 1.1 Flash Video Studio — Cloud Server & REST API")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")), help="Bind port (default: 8765)")
    parser.add_argument("--rotate-interval", type=int, default=480, help="Cookie keep-alive rotation interval in seconds (default: 480)")
    args = parser.parse_args()

    bootstrap_session_from_env()
    start_cookie_keepalive_daemon(interval_sec=args.rotate_interval)

    server = ResilientThreadingHTTPServer((args.host, args.port), OmniStudioHandler)
    log(f"==================================================================")
    log(f"OMNI 1.1 FLASH VIDEO STUDIO SERVER RUNNING ON http://{args.host}:{args.port}")
    log(f"  • Web Studio UI    : http://localhost:{args.port}/")
    log(f"  • Session Status   : http://localhost:{args.port}/api/session/status")
    log(f"  • Update Cookie    : POST http://localhost:{args.port}/api/session")
    log(f"  • Generate 10s Clip: POST http://localhost:{args.port}/api/generate-scene")
    log(f"  • Produce 30s/60s  : POST http://localhost:{args.port}/api/produce-commercial")
    log(f"==================================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down server...")


if __name__ == "__main__":
    main()
