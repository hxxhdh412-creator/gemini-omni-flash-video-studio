#!/usr/bin/env python3
"""
OMNI 1.1 FLASH VIDEO STUDIO — Model Context Protocol (MCP) Server (`omni_mcp_server.py`)
========================================================================================
Supports BOTH:
  1. Standard MCP `stdio` transport (for Claude Code CLI, Antigravity, Claude Desktop, Cursor)
  2. Streamable HTTP JSON-RPC 2.0 transport (`POST /mcp` on `omni_server.py`, port 8765) for 1-Link Cloud/Local Agents

Exposes the Complete 11-Tool Video & Media Production Pipeline to AI Agents (100% Native `vids.new` Cookie Engine):
  1.  `omni_session_status`      — Check `vids.new` cookie health, `doc_id`, `authuser`, & 24/7 `RotateCookies` stats
  2.  `omni_session_update`      — Hot-update `vids.new` Cookie / cURL / `doc_id` / `GEMINI_API_KEY` & trigger `RotateCookies`
  3.  `omni_upload_image`        — Upload a reference image (file path, `file:///`, `https://` URL, or base64) to `/outputs` with auto subject analysis
  4.  `omni_generate_scene`      — Generate/extend a 3s–15s video via `gemini-omni-1.1-flash` (`UC 376` R2V with `@Image 1` tempblob + `UC 374` T2V + `+15s` Extend)
  5.  `omni_generate_image`      — Generate 16:9 / 1:1 / 9:16 AI images via `vids.new` Cookie (`UC 141` + Reference Image support)
  6.  `omni_synthesize_1take_vo` — Synthesize expressive Vietnamese voiceover via `vids.new` Cookie TTS (`UC 254`, 12 voices) + Style Mastering & C2PA filter
  7.  `omni_generate_lyria_bgm`  — Compose 48kHz Stereo Instrumental BGM or Full Vocal Songs with AI singing via `vids.new` Cookie Lyria v3.5 (`UC 242`)
  8.  `omni_produce_commercial`  — Parallel 3-Thread End-to-End 30s/60s 1080p Commercial Pipeline (Multi-Scene Omni + 1-Take VO + Lyria BGM + Spectral Carve + ASS HUD)
  9.  `omni_job_status`          — Poll async pipeline job status, step-by-step logs, 720p `preview_url`, and 1080p `download_url`
  10. `omni_list_outputs`        — List all generated `.mp4` videos, `.png`/`.jpg` images, `.wav` audio tracks, and `_node.json` anchors in `/outputs`
  11. `omni_skill_packs`         — One-click 1-Link MCP onboarding & full `SKILL.md` guide reader for connected AI Agents
"""

import json
import os
import re
import shutil
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
    "version": "2.4.0",
}

MCP_INSTRUCTIONS = (
    "Omni Studio Pro (v2.4) — Hệ thống sản xuất Video AI, TVC 30s/60s, Tạo ảnh, Giọng đọc 1-Take (12 giọng) & Nhạc Lyria v3.5 "
    "chạy 100% trực tiếp trên Cookie Google Vids (vids.new) tốc độ cao (không phụ thuộc GEMINI_API_KEY). "
    "Quy trình chuẩn cho AI Agent: "
    "(1) Gọi omni_session_status trước để kiểm tra kết nối Cookie vids.new (session_ready=true). "
    "(2) Nếu có ảnh tham chiếu từ máy/URL/base64, có thể truyền trực tiếp đường dẫn/URL/base64 vào tham số `image` của omni_generate_scene / omni_generate_image / omni_produce_commercial, hoặc gọi omni_upload_image trước để chuẩn hóa và phân tích chủ thể. "
    "(3) Để tạo ảnh AI mới (hoặc biến đổi từ ảnh tham chiếu), gọi omni_generate_image (Cookie vids.new UseCase 141, hỗ trợ 16:9, 1:1, 9:16 và 9 phong cách). "
    "(4) Để tạo nhanh 1 cảnh video (3s–15s, mặc định 15s, 16:9 hoặc 9:16) ở tốc độ gốc vids.new (~22s–28s), gọi omni_generate_scene: "
    "khi truyền `image`, hệ thống tự động chuẩn hóa khung 16:9/9:16 và tải lên Google Vids TemporaryBlob (Resumable 2 bước) để kích hoạt UseCase 376 (Reference-to-Video `@Image 1`) giữ nguyên 100% gương mặt/sản phẩm/UI; "
    "khi truyền `prior_video` + `extend_mode='extend_concat'`, hệ thống tự động nối dài video cũ thêm +15s mượt mà. "
    "(5) Để tạo giọng đọc AI tiếng Việt/Anh (12 giọng vids.new UseCase 254: Puck, Aoede, Achernar, Charon, Kore, Fenrir, Orus, Zephyr, vi-vn-concierge-2, vi-vn-csagent-1, vi-vn-training-1, vi-vn-tutor-1) kèm chỉ dẫn phong cách (nhanh, chậm, trầm ấm, quyền lực, sôi động, vang điện ảnh), gọi omni_synthesize_1take_vo. "
    "(6) Để sáng tác nhạc 48kHz Stereo bằng Lyria v3.5 trên Cookie vids.new (UseCase 242), gọi omni_generate_lyria_bgm: đặt `instrumental=true` cho nhạc nền không lời hoặc `instrumental=false` cho bài hát hoàn chỉnh có ca sĩ AI hát lời (Full Vocal Song). "
    "(7) Để sản xuất trọn gói TVC đa cảnh 30s (2x15s) hoặc 60s (4x15s / 6x10s) tốc độ cao, gọi omni_produce_commercial (chạy song song 3 luồng Video + 1-Take VO + Lyria BGM + Mastering Clean Audio Bus, Spectral Carve, Sidechain 6:1). "
    "(8) Khi chạy tác vụ bất đồng bộ (wait=false), dùng omni_job_status để lấy preview_url (720p tức thì) và download_url (/outputs/<file>.mp4)."
)

ALL_12_VIDS_VOICES_DESC = (
    "Voice ID (12 native `vids.new` Cookie voices via UseCase 254): "
    "`Puck` (Nam trẻ năng động/Founder), `Aoede` (Nữ ấm áp truyền cảm), `Achernar` (Nữ nhẹ nhàng tinh tế), "
    "`Charon` (Nam trầm quyền lực), `Kore` (Nữ trẻ trung hiện đại), `Fenrir` (Nam mạnh mẽ kịch tính), "
    "`Orus` (Nam điềm tĩnh chuyên gia), `Zephyr` (Nữ thanh lịch phóng khoáng), "
    "`vi-vn-concierge-2` (Nữ TVC cao cấp), `vi-vn-csagent-1` (Nữ CSKH thân thiện), "
    "`vi-vn-training-1` (Nam hướng dẫn/đào tạo), `vi-vn-tutor-1` (Nam giảng viên trầm ấm). Default: `Puck`."
)

MCP_TOOLS_SCHEMA = [
    {
        "name": "omni_session_status",
        "description": (
            "Check the live health of the Gemini Omni 1.1 Flash (`vids.new`) session cookie (`__Secure-1PSIDTS`, `SAPISID`), "
            "active `doc_id`, `authuser`, `tts_backend` (`vids_cookie_uc254`), and 24/7 headless `RotateCookies` keep-alive counter."
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
                    "description": "Optional `GEMINI_API_KEY` (not required when `vids.new` Cookie is active).",
                },
            },
        },
    },
    {
        "name": "omni_upload_image",
        "description": (
            "Upload and normalize a reference image (from a local file path, `file:///` URI, `http://`/`https://` URL, or base64 data URI) "
            "into `/outputs` for use in Reference-to-Video (`omni_generate_scene` UseCase 376), Cookie Image Generation (`omni_generate_image` UseCase 141), "
            "or Multi-Scene Commercial (`omni_produce_commercial`). Automatically analyzes whether the image is a human portrait/character or software UI "
            "and returns `relative_path`, `download_url`, `subject_description`, and `suggested_prompt`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Local file path (e.g. `C:/path/to/photo.jpg`), `file:///` URI, `https://` image URL, or `data:image/...;base64,...` string.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional target filename stem (e.g. `founder_portrait`).",
                },
                "analyze_subject": {
                    "type": "boolean",
                    "description": "If true (default), inspects the image subject and returns `subject_description` & `suggested_prompt`.",
                },
            },
            "required": ["image"],
        },
    },
    {
        "name": "omni_generate_scene",
        "description": (
            "Stage 1 Pipeline Tool: Generate or extend a 3s–15s cinematic video scene using `gemini-omni-1.1-flash` on Google Vids (`vids.new`). "
            "• When `image` (or `images`) is provided: automatically pads/normalizes the image to a 16:9 or 9:16 safe canvas, uploads it via 2-step Resumable TemporaryBlob "
            "to `docs.google.com/upload/temporaryblob/videos`, and invokes **UseCase 376 (`Reference-to-Video` / `@Image 1`)** + `StructuredImage` (`Node[11]`) "
            "so the generated video preserves 100% of the exact person's face, product, or software UI screenshot! "
            "• When `prior_video` (or `prior_node_files`) is provided with `extend_mode='extend_concat'`: extracts the final frame + anchor node of the prior video, "
            "generates a seamless +15s continuation, and stitches them into a continuous longer MP4 (`15s -> 30s -> 45s -> 60s`)."
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
                    "description": "Clip duration in seconds (`3` to `15`, default `15`s native Omni 1.1 Flash generation).",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Aspect ratio: `16:9` (Landscape) or `9:16` (Portrait). Default: `16:9`.",
                },
                "image": {
                    "type": "string",
                    "description": "Optional reference image (local path, `outputs/...`, `file:///`, `https://` URL, or base64 data URI) for UseCase 376 Reference-to-Video (`@Image 1` + `Node[11]`).",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of up to 3 reference images (paths/URLs/base64) for multi-ingredient Reference-to-Video (`@Image 1`, `@Image 2`, `@Image 3`).",
                },
                "prior_video": {
                    "type": "string",
                    "description": "Optional existing `.mp4` filename or path in `/outputs` (e.g. `outputs/scene1.mp4`) to seamlessly extend by +15s.",
                },
                "extend_mode": {
                    "type": "string",
                    "enum": ["none", "extend_concat", "chain_only"],
                    "description": "`extend_concat`: generates a +15s continuation from `prior_video` and stitches old + new into one longer MP4. `chain_only`: outputs only the new chained 15s segment. `none`: standalone generation.",
                },
                "prior_node_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of prior `_node.json` filenames/paths (e.g. `['s1_node.json']`) to lock character face, outfit, and studio environment across scenes.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output MP4 filename inside `/outputs` (e.g. `scene1_720p.mp4`).",
                },
                "upscale_1080p": {
                    "type": "boolean",
                    "description": "If false (default), returns the native 720p clip immediately at `vids.new` speed (~22s–28s). If true, masters to 1920x1080 Full HD (`Lanczos + CAS 0.45`).",
                },
                "wait": {
                    "type": "boolean",
                    "description": "If true (default), waits ~25s and returns the completed video path & `download_url`. If false, queues an async job and returns `job_id` in <10ms.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_generate_image",
        "description": (
            "Generate high-resolution AI images (or transform a reference image) directly via the Google Vids (`vids.new`) session Cookie "
            "(`appsgenaiserver-pa.clients6.google.com/v1/genai/generate`, UseCase `141` + 1080p Still Fallback). "
            "No API key quota required! Generated images are saved in `/outputs` and can be passed directly into `omni_generate_scene`'s `image` parameter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "English or Vietnamese visual prompt describing the image to generate (do not include hex color codes).",
                },
                "image": {
                    "type": "string",
                    "description": "Optional reference image (local path, `outputs/...`, `file:///`, `https://` URL, or base64) to guide character/product/style consistency.",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of reference images.",
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
            "Stage 2 Pipeline Tool: Synthesize expressive Vietnamese or English voiceover directly via Google Vids (`vids.new`) Cookie Voiceover RPC "
            "(`UseCase 254` -> `/flix/generate_text_to_speech/v1`, zero API key needed!) or flagship `gemini-3.8-flash-tts` in **Single-Pass 1-Take** mode. "
            "Supports all **12 distinct `vids.new` voices**, strips the 6,072-byte C2PA/SynthID trailer, normalizes to `-15.0 dBFS` unifiedGain, "
            "and applies acoustic style mastering (`style`) for tempo, pitch warmth/brightness, EQ, and room reverb."
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
                    "description": ALL_12_VIDS_VOICES_DESC,
                },
                "style": {
                    "type": "string",
                    "description": (
                        "Optional Vietnamese or English style/direction prompt (e.g., `'Giọng nam trầm ấm, quyền lực, đọc nhanh 1.15x'`, "
                        "`'Giọng nữ nhẹ nhàng, sâu lắng, truyền cảm'`, `'Sôi động, hào hứng, vang điện ảnh'`). Automatically masters tempo, pitch, and EQ!"
                    ),
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
            "Stage 3 Pipeline Tool: Compose a custom 48kHz Stereo WAV soundtrack or Full Vocal Song directly via Google Vids (`vids.new`) Cookie Lyria v3.5 RPC "
            "(`UseCase 242` -> `/flix/generate_music_full_song/v0`, zero API key needed!). "
            "Supports both **Instrumental BGM** (`instrumental=true`) and **Full Vocal Songs with AI singer & lyrics** (`instrumental=false`), "
            "with automatic Vietnamese-to-English musical genre/instrument translation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Vietnamese or English music generation prompt (e.g. `'Nhạc piano nhẹ nhàng sâu lắng cảm xúc'` for instrumental, "
                        "or `'Bài hát pop ballad có ca sĩ nữ hát tiếng Việt về khát vọng khởi nghiệp'` for full vocal song)."
                    ),
                },
                "instrumental": {
                    "type": "boolean",
                    "description": (
                        "If `true`, forces 100% Instrumental BGM (no singing, ideal for commercial background under voiceover). "
                        "If `false`, composes a Full Vocal Song with AI singing and lyrics via Lyria v3.5 (`UseCase 242`). "
                        "If omitted, auto-detects from prompt keywords (`ca sĩ hát`, `bài hát có lời`, `vocal`, `lyrics`)."
                    ),
                },
                "duration": {
                    "type": "integer",
                    "description": "Duration in seconds (e.g. 15, 30, 60). Default: 30.",
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
                    "description": "Output WAV filename inside `/outputs` (e.g. `lyria_bgm_30s.wav`).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_produce_commercial",
        "description": (
            "Stage 4 Full Pipeline Orchestrator: Produce a complete 15s / 30s / 60s 1080p Full HD commercial end-to-end at `vids.new` parallel speed. "
            "Combines in 3 concurrent threads: "
            "(1) Multi-scene `gemini-omni-1.1-flash` video generation with Scene 1 Anchor Chaining + `UseCase 376` Reference-to-Video (`@Image 1` + `Node[11]`), "
            "(2) Single-Pass 1-Take emotional Vietnamese voiceover via `vids.new` Cookie TTS (`UseCase 254`, 12 voices, C2PA stripped, `-15.0 dBFS` unifiedGain), "
            "(3) Custom 48kHz Stereo soundtrack via `vids.new` Cookie Lyria v3.5 (`UseCase 242`), and "
            "(4) Broadcast mastering (Clean Audio Bus stripping raw video mumbling `[i:a]`, Spectral Carve at 1000Hz/1650Hz, Sidechain Auto-Ducking 6:1, and breath-synced `.ass` HUD subtitles)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "description": "Ordered array of scenes (e.g. 2x15s scenes for 30s TVC, 4x15s or 6x10s scenes for 60s TVC).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "badge": {"type": "string", "description": "Top-left HUD chapter badge (e.g. '01 // KẾT NỐI ĐA KÊNH')."},
                            "image": {"type": "string", "description": "Optional path, URL, or base64 of reference image (character portrait, product, or 1080p UI screenshot) for UseCase 376 Reference-to-Video (`@Image 1` + `Node[11]`)."},
                            "prompt": {"type": "string", "description": "English visual prompt for `gemini-omni-1.1-flash` (end with 'No spoken dialogue.')."},
                            "vo": {"type": "string", "description": "Expressive Vietnamese voiceover line for this scene."},
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
                    "description": "Aspect ratio: `16:9` (Landscape) or `9:16` (Portrait). Default: `16:9`.",
                },
                "parallel_scenes": {
                    "type": "boolean",
                    "description": "If true (default), generates Scene 2..N in parallel threads alongside 1-Take TTS and Lyria BGM, cutting total render time by ~55%.",
                },
                "chain_anchor": {
                    "type": "boolean",
                    "description": "If true (default), chains Scene 2..N from Scene 1's Anchor Node (`s1_node.json`) and custom reference portrait. If false, generates all scenes concurrently.",
                },
                "burn_subtitles": {
                    "type": "boolean",
                    "description": "If true (default), burns breath-synced ASS HUD subtitles. If false, outputs clean video without text overlay.",
                },
                "voice": {
                    "type": "string",
                    "description": ALL_12_VIDS_VOICES_DESC,
                },
                "vo_style": {
                    "type": "string",
                    "description": "Optional Vietnamese/English voiceover style mastering prompt (e.g. `'Giọng nam ấm áp, hào hứng, truyền cảm hứng'`).",
                },
                "bgm_prompt": {
                    "type": "string",
                    "description": "Vietnamese or English Google Lyria v3.5 BGM style prompt.",
                },
                "instrumental": {
                    "type": "boolean",
                    "description": "If true (default for commercials with voiceover), generates Instrumental BGM. If false, generates Full Vocal Song.",
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
                    "description": "If false (default for multi-scene), runs in background job queue and returns `job_id` in <10ms for polling via `omni_job_status`. If true, blocks until full render finishes.",
                },
            },
            "required": ["scenes"],
        },
    },
    {
        "name": "omni_job_status",
        "description": "Check the real-time status (`queued`, `running`, `completed`, `failed`), logs, instant 720p `preview_url`, and `/outputs/<filename>.mp4` `download_url` of a pipeline job.",
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
        "description": "List all generated `.mp4` videos, `.png`/`.jpg` images, `.wav` voiceovers/music tracks, and `_node.json` anchor files available in `/outputs`.",
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
    """Executes one of the 11 Omni 1.1 Flash Video & Media Pipeline MCP tools and returns a structured dict result."""
    import omni_server as srv
    from edit_video import (
        describe_image_subject_for_omni,
        detect_image_mime_and_dimensions,
        ensure_png_or_jpeg_image_file,
        run_vids_rpc_generate_image,
    )
    from make_emotional_1take_vo import EMOTIONAL_DIRECTOR_STYLE, call_gemini_38_flash_tts_1take
    from produce_commercial import generate_bgm_track, parse_bgm_prompt_profile

    args = dict(arguments or {})

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

    if tool_name == "omni_upload_image":
        img_spec = (args.get("image") or args.get("data") or "").strip()
        if not img_spec:
            raise ValueError("Missing required 'image' (file path, URL, or base64) for omni_upload_image")
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        resolved_path = srv.save_base64_image_if_needed(img_spec, OUTPUTS_DIR, 1)
        if not resolved_path or not os.path.exists(resolved_path):
            raise ValueError(f"Could not resolve or decode image: {img_spec[:120]}")
        orig_name = os.path.splitext(os.path.basename(args.get("filename") or resolved_path))[0]
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", orig_name).strip("_")[:36] or "ref"
        norm_path, raw_bytes, norm_mime, norm_w, norm_h = ensure_png_or_jpeg_image_file(resolved_path)
        ext = os.path.splitext(norm_path)[1] or ".png"
        if os.path.dirname(os.path.abspath(norm_path)) != os.path.abspath(OUTPUTS_DIR) or safe_stem not in os.path.basename(norm_path):
            fn = f"upload_{int(time.time())}_{uuid.uuid4().hex[:4]}_{safe_stem}{ext}"
            dest_p = os.path.join(OUTPUTS_DIR, fn)
            shutil.copy2(norm_path, dest_p)
            norm_path = dest_p
        final_fn = os.path.basename(norm_path)
        subj_info = {"is_hebum_ui": False, "is_character_or_person": False, "subject_description": "", "suggested_prompt": ""}
        if args.get("analyze_subject", True):
            subj_info = describe_image_subject_for_omni(norm_path)
        return {
            "status": "completed",
            "filename": final_fn,
            "path": norm_path,
            "relative_path": f"outputs/{final_fn}",
            "download_url": f"/outputs/{final_fn}",
            "mime": norm_mime,
            "width": norm_w,
            "height": norm_h,
            "is_character_or_person": subj_info.get("is_character_or_person", False),
            "subject_description": subj_info.get("subject_description", ""),
            "suggested_prompt": subj_info.get("suggested_prompt", ""),
        }

    if tool_name == "omni_generate_scene":
        # Default upscale_1080p to False for instant native vids.new 720p generation speed (~25s)
        args.setdefault("upscale_1080p", False)
        wait = bool(args.get("wait", True))
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
            return {
                "status": "queued",
                "job_id": job_id,
                "poll_tool": "omni_job_status",
                "poll_url": f"/api/jobs/{job_id}",
            }

    if tool_name == "omni_generate_image":
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
        ref_imgs = []
        if args.get("image"):
            ref_imgs.append(srv.save_base64_image_if_needed(args["image"], OUTPUTS_DIR, 1))
        for i, im in enumerate(args.get("images") or []):
            ref_imgs.append(srv.save_base64_image_if_needed(im, OUTPUTS_DIR, i + 1))
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
            "reference_images_used": len(ref_imgs),
            "aspect_ratio": res.get("aspect_ratio", aspect_ratio),
            "style": res.get("style", style),
        }

    if tool_name == "omni_synthesize_1take_vo":
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        text = args.get("text", "").strip()
        if not text:
            raise ValueError("Missing 'text' argument for omni_synthesize_1take_vo")
        voice = args.get("voice", "Puck")
        style = args.get("style") or EMOTIONAL_DIRECTOR_STYLE
        out_name = os.path.basename(args.get("output_filename") or f"vo_1take_{uuid.uuid4().hex[:6]}.wav")
        if not out_name.lower().endswith(".wav"):
            out_name += ".wav"
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
            "backend": "vids_cookie_uc254",
            "model": "vids-tts-uc254 / gemini-3.8-flash-tts",
            "mode": "single-pass-1take-c2pa-filtered-style-mastered",
            "voice": voice,
            "style": style,
            "duration_sec": dur,
            "output_path": out_wav,
            "relative_path": f"outputs/{out_name}",
            "download_url": f"/outputs/{out_name}",
        }

    if tool_name == "omni_generate_lyria_bgm":
        prompt = args.get("prompt", "Cinematic modern corporate electronic commercial music")
        dur = int(args.get("duration", 30))
        bpm = int(args.get("bpm", 116))
        scale = args.get("scale", "MINOR")
        inst_raw = args.get("instrumental")
        instrumental = bool(inst_raw) if isinstance(inst_raw, bool) else None
        out_name = os.path.basename(args.get("output_filename") or f"lyria_bgm_{dur}s_{uuid.uuid4().hex[:6]}.wav")
        if not out_name.lower().endswith(".wav"):
            out_name += ".wav"
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
        return {
            "status": "completed",
            "backend": "vids_cookie_lyria_uc242",
            "model": "vids-lyria-3.5-uc242 / models/lyria-realtime-exp",
            "duration_sec": dur,
            "bpm": profile.get("bpm", bpm),
            "scale": scale,
            "instrumental": instrumental,
            "profile": profile,
            "output_path": out_wav,
            "relative_path": f"outputs/{out_name}",
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
                            "relative_path": f"outputs/{fn}",
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
                        "description": (
                            "Trọn bộ 11 MCP Tools & Skill sản xuất Video 15s (UseCase 376 Reference-to-Video & UseCase 374 Text-to-Video), "
                            "Nối dài Video +15s, TVC đa cảnh 30s/60s, Tạo ảnh qua Cookie (UseCase 141), "
                            "Giọng đọc 1-Take 12 giọng (UseCase 254) & Nhạc Lyria v3.5 có lời/không lời (UseCase 242)."
                        ),
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
                        "description": "Architecture guide covering UseCase 376 Reference-to-Video, StructuredImage (Node[11]), Anchor Chaining, 1-Take UC 254 TTS, and Lyria v3.5 UC 242.",
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
                        "description": "Prompt template for orchestrating a 30s/60s commercial with UseCase 376 Reference-to-Video ingredients, 1-Take 12-Voice TTS, and Lyria v3.5.",
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
