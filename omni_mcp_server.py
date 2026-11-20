#!/usr/bin/env python3
"""
OMNI 1.1 FLASH VIDEO STUDIO — Model Context Protocol (MCP) Server (`omni_mcp_server.py`)
========================================================================================
Supports BOTH:
  1. Standard MCP `stdio` transport (for Claude Code CLI, Antigravity, Claude Desktop, Cursor)
  2. Streamable HTTP JSON-RPC 2.0 transport (`POST /mcp` on `omni_server.py`, port 8765) for Cloud Agents

Exposes the Complete 8-Stage Video Production Pipeline to AI Agents:
  1. `omni_session_status`      — Check `vids.new` cookie health, `doc_id`, `authuser`, `GEMINI_API_KEY`, & auto-rotate stats
  2. `omni_session_update`      — Hot-update `vids.new` Cookie / cURL / `doc_id` / `GEMINI_API_KEY` & trigger `RotateCookies`
  3. `omni_generate_scene`      — Generate/edit a 10s clip via `gemini-omni-1.1-flash` with `StructuredImage` (Node[11]) & Anchor Chaining
  4. `omni_synthesize_1take_vo` — Synthesize expressive Vietnamese voiceover via flagship `gemini-3.8-flash-tts` (Single-Pass 1-Take, C2PA stripped, 1.00x)
  5. `omni_generate_lyria_bgm`  — Compose custom 48kHz Stereo WAV soundtrack via `Google Lyria RealTime` (`models/lyria-realtime-exp`)
  6. `omni_produce_commercial`  — Full End-to-End 10s/30s/60s 1080p Commercial Pipeline (Multi-Scene Omni + 1-Take VO + Lyria BGM + Spectral Carve + ASS HUD)
  7. `omni_job_status`          — Poll async pipeline job status, step-by-step logs, and download URLs
  8. `omni_list_outputs`        — List all mastered `.mp4` videos, `.wav` audio tracks, and `_node.json` anchors in `/outputs`
"""

import json
import os
import sys
import threading
import time
import traceback
import uuid
import wave

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL_VIDEO_DIR = os.path.join(REPO_ROOT, "skills", "gemini-omni-flash-api", "scripts", "video")
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
OUTPUTS_DIR = os.path.join(REPO_ROOT, "outputs")

sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, SKILL_VIDEO_DIR)
sys.path.insert(0, EXAMPLES_DIR)

MCP_SERVER_INFO = {
    "name": "omni-flash-video-studio-mcp",
    "version": "2.2.0",
}

MCP_INSTRUCTIONS = (
    "Omni Studio Pro — Hệ thống sản xuất Video AI, TVC 30s/60s, Giọng đọc 1-Take & Tạo ảnh qua Cookie bằng Gemini Omni 1.1 Flash (vids.new). "
    "Quy trình chuẩn cho AI Agent: "
    "(1) Gọi omni_session_status trước để kiểm tra kết nối Cookie vids.new (session_ready=true) và GEMINI_API_KEY. "
    "(2) Nếu cần tạo ảnh tham chiếu/thành phần (giao diện phần mềm, sản phẩm, bối cảnh) mà chưa có ảnh sẵn, gọi omni_generate_image (tạo ảnh trực tiếp qua Cookie vids.new UseCase 141, hỗ trợ 16:9, 1:1, 9:16 và 9 phong cách, không tốn API key). "
    "(3) Để tạo nhanh 1 cảnh video (3s–15s, mặc định 15s, hỗ trợ 16:9 hoặc 9:16) ở tốc độ gốc vids.new (~22s), gọi omni_generate_scene (truyền ảnh vào tham số image để nạp vào khe StructuredImage Node[11]; truyền prior_node_files=['..._node.json'] nếu muốn khóa nhân vật và bối cảnh nối tiếp từ Cảnh 1). "
    "(4) Để sản xuất trọn gói TVC đa cảnh 30s (2x15s) hoặc 60s (4x15s / 6x10s) tốc độ cao, gọi omni_produce_commercial: hệ thống tự động chạy SONG SONG 3 luồng (Video đa cảnh + Giọng đọc Single-Pass 1-Take gemini-3.8-flash-tts đã lọc bỏ 6KB C2PA ở tốc độ tự nhiên 1.00x + Nhạc nền Google Lyria RealTime 48kHz Stereo + Mastering Clean Audio Bus, Spectral Carve 1000Hz/1650Hz, Sidechain Auto-Ducking 6:1 và phụ đề HUD đồng bộ nhịp thở). "
    "(5) Khi chạy tác vụ bất đồng bộ (wait=false), dùng omni_job_status để lấy preview_url (xem ngay luồng 720p gốc) và download_url (/outputs/<file>.mp4). "
    "(6) Kho Skill Video AI (omni_skill_packs): khi người dùng yêu cầu cài bộ skill tạo video lên máy/nền tảng của họ hoặc muốn đọc toàn văn SKILL.md, hãy gọi omni_skill_packs (action='install_pack' hoặc action='get_skill')."
)

MCP_TOOLS_SCHEMA = [
    {
        "name": "omni_session_status",
        "description": (
            "Check the live health of the Gemini Omni 1.1 Flash (`vids.new`) session cookie (`__Secure-1PSIDTS`, `SAPISID`), "
            "active `doc_id`, `authuser`, `GEMINI_API_KEY` status, and 24/7 headless `RotateCookies` keep-alive counter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "rotate_now": {
                    "type": "boolean",
                    "description": "If true, immediately triggers a headless HTTPS `RotateCookies` refresh before returning status.",
                }
            },
        },
    },
    {
        "name": "omni_session_update",
        "description": (
            "Hot-update the Google Vids (`vids.new`) session credentials (`cookie` string, browser `curl` command, `doc_id`, `authuser`) "
            "and/or `gemini_api_key` on the server without restarting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cookie": {
                    "type": "string",
                    "description": "Full Cookie header string (`SID=...; HSID=...; SAPISID=...; __Secure-1PSIDTS=...`).",
                },
                "curl": {
                    "type": "string",
                    "description": "Optional raw browser cURL command copied from DevTools Network tab.",
                },
                "doc_id": {
                    "type": "string",
                    "description": "Google Vids document ID (`1x5CzXuOZgiBZzrRlgMjrvnTomyll0BmLws2-1zwysJc`).",
                },
                "authuser": {
                    "type": "string",
                    "description": "Google account index (`0`, `1`, `2`...).",
                },
                "gemini_api_key": {
                    "type": "string",
                    "description": "Optional `GEMINI_API_KEY` for `gemini-3.8-flash-tts` and `Google Lyria RealTime`.",
                },
            },
        },
    },
    {
        "name": "omni_generate_scene",
        "description": (
            "Stage 1 Pipeline Tool: Generate or edit a 3s–15s cinematic video scene (supports 10s or 15s native clips!) using `gemini-omni-1.1-flash` (`vids.new` RPC `374`). "
            "Supports injecting a 1080p UI screenshot or product photo directly into Omni's native `StructuredImage` (`StructuredContentNode[11]` / Thành phần tạo) slot, "
            "chaining from a prior scene's `_node.json` (`prior_node_files`) for 100% character/office continuity, and upscaling to 1080p Full HD (`Lanczos + CAS 0.45`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "English cinematic visual prompt. Include 'No spoken dialogue.' when using external Vietnamese TTS voiceover. Do not include raw hex codes (#RRGGBB).",
                },
                "duration": {
                    "type": "integer",
                    "description": "Clip duration in seconds (`3` to `15`, supports `15`s native Omni 1.1 Flash generation or `10`s). Default: `15`.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Aspect ratio: `16:9` (Landscape) or `9:16` (Portrait). Default: `16:9`.",
                },
                "image": {
                    "type": "string",
                    "description": "Optional path (e.g. `assets_1080p/asset_01_kenh_ket_noi_1080p.png`) or base64 data URI of the reference image to inject into `StructuredContentNode[11]` (`StructuredImage`).",
                },
                "prior_node_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of prior `_node.json` filenames/paths (e.g. `['s1_node.json']`) to lock character face, outfit, and studio environment across scenes.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output MP4 filename inside `/outputs` (e.g. `scene1_1080p.mp4`).",
                },
                "upscale_1080p": {
                    "type": "boolean",
                    "description": "If false, returns the native 720p clip immediately at `vids.new` speed (~22s). If true, masters to 1920x1080 Full HD (`veryfast`). Default: false for instant speed.",
                },
                "wait": {
                    "type": "boolean",
                    "description": "If true (default), waits ~40s and returns the completed video path & `_node.json`. If false, queues an async job and returns `job_id` immediately.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_generate_image",
        "description": (
            "Generate high-resolution AI reference/ingredient images directly via the Google Vids (`vids.new`) session Cookie "
            "(`appsgenaiserver-pa.clients6.google.com/v1/genai/generate`, UseCase `141`). "
            "No API key quota required! Generated images are saved in `/outputs` and can be passed directly into `omni_generate_scene`'s `image` (`StructuredImage` / Node[11]) parameter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "English visual prompt describing the image to generate (do not include hex color codes).",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Aspect ratio: `16:9` (Landscape), `1:1` (Square), or `9:16` (Portrait). Default: `16:9`.",
                },
                "style": {
                    "type": "string",
                    "description": "Image style preset: `PHOTOGRAPHY`, `VECTOR_ART`, `ILLUSTRATION`, `BACKGROUND`, `WATERCOLOR`, `CYBERPUNK`, `FLATLAY`, `LANDSCAPE`, `UNSPECIFIED`. Default: `PHOTOGRAPHY`.",
                },
                "num_images": {
                    "type": "integer",
                    "description": "Number of image variations to save (`1` to `4`). Default: `1`.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output PNG filename inside `/outputs` (e.g. `cookie_ref_image.png`).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_synthesize_1take_vo",
        "description": (
            "Stage 2 Pipeline Tool: Synthesize expressive, deeply emotional Vietnamese voiceover using flagship `gemini-3.8-flash-tts` in **Single-Pass 1-Take** mode. "
            "Automatically strips the 6,072-byte C2PA/SynthID trailer at the end of the WAV buffer, applies a single `unifiedGain` (`-15.0 dBFS`), "
            "preserves natural human breathing at `1.00x` speed, and optionally splits multi-scene narration (separated by `\\n\\n`) at natural silence valleys."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Vietnamese voiceover script. Separate multiple scenes with a blank line (`\\n\\n`) so the voice actor performs the entire commercial in one continuous emotional take.",
                },
                "voice": {
                    "type": "string",
                    "description": "Gemini 3.8 TTS voice name (`Puck` for charismatic male founder, `Aoede` for warm female, `Charon`, `Kore`, `Fenrir`). Default: `Puck`.",
                },
                "style": {
                    "type": "string",
                    "description": "Optional emotional director style annotation for `speech_metadata`.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output WAV filename inside `/outputs` (e.g. `vo_1take_master.wav`).",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "omni_generate_lyria_bgm",
        "description": (
            "Stage 3 Pipeline Tool: Compose a custom 48kHz Stereo WAV background soundtrack using `Google Lyria RealTime` (`models/lyria-realtime-exp`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Music generation prompt (e.g. 'Cinematic modern corporate electronic commercial music, driving synth pulse and warm deep bass').",
                },
                "duration": {
                    "type": "integer",
                    "description": "Duration in seconds (e.g. 10, 30, 60). Default: 30.",
                },
                "bpm": {
                    "type": "integer",
                    "description": "Tempo in beats per minute (default: 116).",
                },
                "scale": {
                    "type": "string",
                    "description": "Musical scale (`MINOR`, `MAJOR`, `PENTATONIC`). Default: `MINOR`.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output WAV filename inside `/outputs` (e.g. `lyria_bgm_60s.wav`).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_produce_commercial",
        "description": (
            "Stage 4 Full Pipeline Orchestrator: Produce a complete 10s / 30s / 60s 1080p Full HD commercial end-to-end. "
            "Combines: (1) Multi-scene `gemini-omni-1.1-flash` video generation with Scene 1 Anchor Chaining + native `StructuredImage` (Node[11]) ingredients, "
            "(2) Single-Pass 1-Take emotional Vietnamese voiceover via flagship `gemini-3.8-flash-tts` (`1.00x` speed, C2PA stripped, `-15.0 dBFS` unifiedGain), "
            "(3) Custom 48kHz Stereo soundtrack via `Google Lyria RealTime`, and "
            "(4) Broadcast mastering (Clean Audio Bus stripping raw video mumbling `[i:a]`, Spectral Carve at 1000Hz/1650Hz, Sidechain Auto-Ducking 6:1, and breath-synced `.ass` HUD subtitles)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "description": "Ordered array of 10-second scenes (e.g. 3 scenes for 30s, 6 scenes for 60s).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "badge": {"type": "string", "description": "Top-left HUD chapter badge (e.g. '01 // KẾT NỐI ĐA KÊNH')."},
                            "image": {"type": "string", "description": "Optional path or base64 of 1080p asset for `StructuredImage` (`Node[11]`)."},
                            "prompt": {"type": "string", "description": "English visual prompt for `gemini-omni-1.1-flash` (end with 'No spoken dialogue.')."},
                            "vo": {"type": "string", "description": "Expressive Vietnamese voiceover line for this 10s scene."},
                            "sub_1": {"type": "string", "description": "First half subtitle line (displayed before mid-sentence breath pause)."},
                            "sub_2": {"type": "string", "description": "Second half subtitle line (displayed after mid-sentence breath pause)."},
                        },
                        "required": ["prompt", "vo"],
                    },
                },
                "scene_duration": {
                    "type": "integer",
                    "description": "Duration per scene in seconds (`15` for 15s native clips: 2 scenes = 30s, 4 scenes = 60s; or `10` for 10s clips: 3 scenes = 30s, 6 scenes = 60s). Default: `15`.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Aspect ratio: `16:9` or `9:16`. Default: `16:9`.",
                },
                "parallel_scenes": {
                    "type": "boolean",
                    "description": "If true (default), generates Scene 2..N in parallel threads alongside 1-Take TTS and Lyria BGM, cutting total render time by ~55%.",
                },
                "chain_anchor": {
                    "type": "boolean",
                    "description": "If true (default), chains Scene 2..N from Scene 1's Anchor Node (`s1_node.json`). If false, generates all scenes concurrently.",
                },
                "burn_subtitles": {
                    "type": "boolean",
                    "description": "If true (default), burns breath-synced ASS HUD subtitles. If false, outputs clean video without text overlay.",
                },
                "voice": {
                    "type": "string",
                    "description": "Gemini 3.8 Flash TTS voice (`Puck`, `Aoede`, `Charon`, `Kore`, `Fenrir`). Default: `Puck`.",
                },
                "bgm_prompt": {
                    "type": "string",
                    "description": "Google Lyria RealTime BGM style prompt.",
                },
                "bpm": {
                    "type": "integer",
                    "description": "BGM BPM (default: 116).",
                },
                "scale": {
                    "type": "string",
                    "description": "BGM Scale (`MINOR`, `MAJOR`, `PENTATONIC`). Default: `MINOR`.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Final mastered 1080p MP4 filename in `/outputs` (e.g. `commercial_master_1080p.mp4`).",
                },
                "wait": {
                    "type": "boolean",
                    "description": "If false (default for multi-scene), runs in background job queue and returns `job_id` for polling via `omni_job_status`. If true, blocks until full render finishes.",
                },
            },
            "required": ["scenes"],
        },
    },
    {
        "name": "omni_job_status",
        "description": "Check the real-time status (`queued`, `running`, `completed`, `failed`), logs, and `/outputs/<filename>.mp4` download URL of a pipeline job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID returned by `omni_generate_scene` or `omni_produce_commercial`. Omit to list all recent jobs.",
                }
            },
        },
    },
    {
        "name": "omni_list_outputs",
        "description": "List all generated `.mp4` videos, `.png` images, `.wav` voiceovers/music tracks, and `_node.json` anchor files available in `/outputs`.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "omni_skill_packs",
        "description": (
            "Dùng khi người dùng hoặc AI Agent yêu cầu: (1) xem danh sách bộ skill sản xuất Video AI & TVC Gemini Omni 1.1 Flash, "
            "(2) lấy lệnh cài đặt tự động 1 dòng (PowerShell/Bash) hoặc cấu hình 1-Link MCP (`serverUrl`) cho Antigravity, Claude Code, Cursor, Windsurf, "
            "hoặc (3) đọc trực tiếp toàn văn quy trình `SKILL.md` chuẩn (`gemini-omni-flash-api`) để áp dụng ngay."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_packs", "get_skill", "install_pack"],
                    "default": "install_pack",
                    "description": "install_pack (mặc định): lấy link kết nối MCP 1 dòng và lệnh cài skill; list_packs: liệt kê các gói skill; get_skill: đọc toàn văn SKILL.md & hướng dẫn kỹ thuật.",
                },
                "skill_slug": {
                    "type": "string",
                    "description": "Tên skill cần đọc (`gemini-omni-flash-api` hoặc `omni-1-1-complete-guide`).",
                },
            },
        },
    },
]


def execute_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """Executes one of the 8 Omni 1.1 Flash Video Pipeline MCP tools and returns a structured dict result."""
    import omni_server as srv
    from make_emotional_1take_vo import EMOTIONAL_DIRECTOR_STYLE, call_gemini_38_flash_tts_1take
    from produce_commercial import generate_bgm_track

    args = arguments or {}

    if tool_name == "omni_session_status":
        rotate_res = None
        if args.get("rotate_now"):
            rotate_res = srv.rotate_google_cookies_headless(srv.DEFAULT_SESSION_PATH)
        st = srv.get_session_status()
        if rotate_res:
            st["rotate_result"] = rotate_res
        return st

    if tool_name == "omni_session_update":
        upd = srv.update_session_payload(args)
        rot = srv.rotate_google_cookies_headless(srv.DEFAULT_SESSION_PATH)
        return {"update": upd, "rotation": rot, "status": srv.get_session_status()}

    if tool_name == "omni_generate_scene":
        wait = args.get("wait", True)
        job_id = uuid.uuid4().hex[:10]
        job_obj = {
            "job_id": job_id,
            "type": "generate-scene",
            "status": "queued",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "logs": [],
        }
        with srv.JOBS_LOCK:
            srv.JOBS[job_id] = job_obj
        if wait:
            srv.run_job_generate_scene(job_id, args)
            with srv.JOBS_LOCK:
                return srv.JOBS[job_id]
        else:
            t = threading.Thread(target=srv.run_job_generate_scene, args=(job_id, args), daemon=True)
            t.start()
            return {"status": "queued", "job_id": job_id, "poll_tool": "omni_job_status"}

    if tool_name == "omni_generate_image":
        from edit_video import run_vids_rpc_generate_image

        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Missing required 'prompt' for omni_generate_image")
        aspect_ratio = args.get("aspect_ratio", "16:9")
        style = args.get("style", "PHOTOGRAPHY")
        num_images = max(1, min(4, int(args.get("num_images", 1))))
        out_name = os.path.basename(args.get("output_filename") or f"cookie_img_{uuid.uuid4().hex[:6]}.png")
        if not out_name.lower().endswith((".png", ".jpg", ".jpeg")):
            out_name += ".png"
        out_path = os.path.join(OUTPUTS_DIR, out_name)
        res = run_vids_rpc_generate_image(
            prompt_text=prompt,
            output_path=out_path,
            aspect_ratio=aspect_ratio,
            style=style,
            num_images=num_images,
            save_all=(num_images > 1),
        )
        imgs = []
        for p in res.get("images", [res["output_path"]]):
            fn = os.path.basename(p)
            imgs.append(
                {
                    "filename": fn,
                    "path": p,
                    "relative_path": f"outputs/{fn}",
                    "download_url": f"/outputs/{fn}",
                }
            )
        return {
            "status": "completed",
            "backend": "vids_cookie_image",
            "download_url": imgs[0]["download_url"],
            "relative_path": imgs[0]["relative_path"],
            "images": imgs,
            "aspect_ratio": res.get("aspect_ratio", aspect_ratio),
            "style": res.get("style", style),
        }

    if tool_name == "omni_synthesize_1take_vo":
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured on the server.")
        text = args.get("text", "").strip()
        voice = args.get("voice", "Puck")
        style = args.get("style") or EMOTIONAL_DIRECTOR_STYLE
        out_name = os.path.basename(args.get("output_filename") or f"vo_1take_{uuid.uuid4().hex[:6]}.wav")
        out_wav = os.path.join(OUTPUTS_DIR, out_name)
        pcm_bytes, sr = call_gemini_38_flash_tts_1take(full_text=text, style=style, api_key=api_key, voice=voice)
        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm_bytes)
        dur = round((len(pcm_bytes) / 2.0) / sr, 2)
        return {
            "status": "completed",
            "model": "gemini-3.8-flash-tts",
            "mode": "single-pass-1take-c2pa-filtered",
            "voice": voice,
            "duration_sec": dur,
            "output_path": out_wav,
            "download_url": f"/outputs/{out_name}",
        }

    if tool_name == "omni_generate_lyria_bgm":
        prompt = args.get("prompt", "Cinematic modern corporate electronic commercial music")
        dur = int(args.get("duration", 30))
        bpm = int(args.get("bpm", 116))
        scale = args.get("scale", "MINOR")
        out_name = os.path.basename(args.get("output_filename") or f"lyria_bgm_{dur}s_{uuid.uuid4().hex[:6]}.wav")
        out_wav = os.path.join(OUTPUTS_DIR, out_name)
        generate_bgm_track(output_wav=out_wav, duration_sec=dur, bgm_prompt=prompt, bpm=bpm, scale=scale)
        return {
            "status": "completed",
            "model": "models/lyria-realtime-exp",
            "duration_sec": dur,
            "bpm": bpm,
            "scale": scale,
            "output_path": out_wav,
            "download_url": f"/outputs/{out_name}",
        }

    if tool_name == "omni_produce_commercial":
        wait = bool(args.get("wait", False))
        job_id = uuid.uuid4().hex[:10]
        job_obj = {
            "job_id": job_id,
            "type": "produce-commercial",
            "status": "queued",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "logs": [],
        }
        with srv.JOBS_LOCK:
            srv.JOBS[job_id] = job_obj
        if wait:
            srv.run_job_produce_commercial(job_id, args)
            with srv.JOBS_LOCK:
                return srv.JOBS[job_id]
        else:
            t = threading.Thread(target=srv.run_job_produce_commercial, args=(job_id, args), daemon=True)
            t.start()
            return {
                "status": "queued",
                "job_id": job_id,
                "scene_count": len(args.get("scenes") or []),
                "poll_tool": "omni_job_status",
                "poll_url": f"/api/jobs/{job_id}",
            }

    if tool_name == "omni_job_status":
        job_id = args.get("job_id")
        with srv.JOBS_LOCK:
            if job_id:
                return srv.JOBS.get(job_id) or {"error": f"Job '{job_id}' not found"}
            return {"jobs": list(srv.JOBS.values())}

    if tool_name == "omni_list_outputs":
        items = []
        if os.path.exists(OUTPUTS_DIR):
            for fn in sorted(os.listdir(OUTPUTS_DIR), reverse=True):
                fp = os.path.join(OUTPUTS_DIR, fn)
                if os.path.isfile(fp) and fn.endswith((".mp4", ".wav", ".json", ".png", ".jpg", ".jpeg")):
                    items.append(
                        {
                            "name": fn,
                            "path": fp,
                            "size_mb": round(os.path.getsize(fp) / (1024 * 1024), 2),
                            "download_url": f"/outputs/{fn}",
                        }
                    )
        return {"count": len(items), "files": items}

    if tool_name == "omni_skill_packs":
        action = args.get("action", "install_pack")
        slug = args.get("skill_slug", "gemini-omni-flash-api")
        skill_md_path = os.path.join(REPO_ROOT, "skills", "gemini-omni-flash-api", "SKILL.md")
        guide_md_path = os.path.join(REPO_ROOT, "skills", "gemini-omni-flash-api", "references", "omni_1_1_complete_guide.md")
        host_url = os.environ.get("OMNI_PUBLIC_URL", "http://localhost:8765").rstrip("/")
        mcp_link = f"{host_url}/mcp"

        if action == "list_packs":
            return {
                "mcp_server_url": mcp_link,
                "packs": [
                    {
                        "pack_id": "omni-video-studio",
                        "skills": ["gemini-omni-flash-api", "omni-1-1-complete-guide"],
                        "description": "Trọn bộ Skill sản xuất Video 15s, TVC đa cảnh 30s/60s, Tạo ảnh qua Cookie & Giọng đọc 1-Take Gemini 3.8 TTS.",
                    }
                ],
            }
        if action == "get_skill":
            target_p = guide_md_path if "guide" in slug else skill_md_path
            content = ""
            if os.path.exists(target_p):
                with open(target_p, "r", encoding="utf-8") as f:
                    content = f.read()
            return {"skill_slug": slug, "mcp_server_url": mcp_link, "content": content}

        return {
            "status": "ok",
            "mcp_server_url": mcp_link,
            "antigravity_mcp_config": {
                "mcpServers": {
                    "omni-flash-video-studio": {
                        "serverUrl": mcp_link,
                    }
                }
            },
            "claude_code_one_liner": f"claude mcp add --transport http omni-flash-video-studio {mcp_link}",
            "skill_download_url": f"{host_url}/api/skill/SKILL.md",
            "instructions": MCP_INSTRUCTIONS,
        }

    raise ValueError(f"Unknown MCP tool: {tool_name}")


def handle_mcp_jsonrpc(req: dict) -> dict | None:
    """
    Processes a single JSON-RPC 2.0 MCP request object (`initialize`, `tools/list`, `tools/call`, `resources/list`, `prompts/list`, `ping`).
    Returns None for notifications (requests without `id`).
    """
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        client_proto = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_proto,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": MCP_SERVER_INFO,
                "instructions": MCP_INSTRUCTIONS,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS_SCHEMA},
        }

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "resources": [
                    {
                        "uri": "omni://guide/complete",
                        "name": "Gemini Omni 1.1 Flash Complete Video Production Guide",
                        "mimeType": "text/markdown",
                        "description": "Architecture guide covering StructuredImage (Node[11]), Anchor Chaining, 1-Take Gemini 3.8 TTS, and Lyria RealTime.",
                    }
                ]
            },
        }

    if method == "resources/read":
        uri = params.get("uri", "")
        guide_path = os.path.join(REPO_ROOT, "skills", "gemini-omni-flash-api", "references", "omni_1_1_complete_guide.md")
        text = ""
        if os.path.exists(guide_path):
            with open(guide_path, "r", encoding="utf-8") as f:
                text = f.read()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/markdown",
                        "text": text,
                    }
                ]
            },
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "prompts": [
                    {
                        "name": "omni_commercial_director",
                        "description": "Prompt template for orchestrating a 30s/60s commercial with StructuredImage ingredients and 1-Take emotional voiceover.",
                    }
                ]
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            res_data = execute_mcp_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res_data, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": False,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": str(e), "traceback": traceback.format_exc()},
                                ensure_ascii=False,
                                indent=2,
                            ),
                        }
                    ],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_stdio_loop():
    """Standard MCP stdio loop reading newline-delimited JSON-RPC messages from stdin."""
    import omni_server as srv

    srv.bootstrap_session_from_env()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_mcp_jsonrpc(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_loop()
