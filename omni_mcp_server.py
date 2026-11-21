#!/usr/bin/env python3
"""
OMNI 1.2 FLASH VIDEO STUDIO — Model Context Protocol (MCP) Server (`omni_mcp_server.py`)
========================================================================================
Supports BOTH:
  1. Standard MCP `stdio` transport (for Claude Code CLI, Antigravity, Claude Desktop, Cursor)
  2. Streamable HTTP JSON-RPC 2.0 transport (`POST /mcp` on `omni_server.py`, port 8765) for Cloud/Local Agents

Exposes the Complete 11-Tool AI Video & Media Production Pipeline to AI Agents:
  1.  `omni_session_status`      — Check Omni Studio API service health and active media engine status
  2.  `omni_session_update`      — Update studio workspace configuration or API credentials
  3.  `omni_upload_image`        — Upload a reference image (file path, `file:///`, `https://` URL, or base64) to `/outputs` with auto subject analysis
  4.  `omni_generate_scene`      — Generate or extend a 3s–15s video clip via `gemini-omni-1.2-flash` (Reference-to-Video, Text-to-Video, or +15s Video Extension)
  5.  `omni_generate_image`      — Generate 16:9 / 1:1 / 9:16 high-resolution AI images via Omni Studio Image Engine (with Reference Image support)
  6.  `omni_synthesize_1take_vo` — Synthesize expressive Vietnamese/English voiceover via `gemini-3.8-flash-tts` (12 studio voices + acoustic style mastering)
  7.  `omni_generate_lyria_bgm`  — Compose 48kHz Stereo Instrumental BGM or Full Vocal Songs via `Lyria 3.5 Studio Audio API`
  8.  `omni_produce_commercial`  — Parallel 3-Thread End-to-End 30s/60s 1080p Commercial Pipeline (Multi-Scene Video + 1-Take VO + Lyria BGM + Spectral Carve + ASS HUD)
  9.  `omni_job_status`          — Poll async pipeline job status, step-by-step logs, 720p `preview_url`, and 1080p `download_url`
  10. `omni_list_outputs`        — List all generated `.mp4` videos, `.png`/`.jpg` images, `.wav` audio tracks, and `_node.json` anchors in `/outputs`
  11. `omni_skill_packs`         — One-click MCP onboarding & production workflow guide for connected AI Agents
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
    "version": "2.5.0",
}

MCP_INSTRUCTIONS = (
    "Omni Studio Pro (v2.5) — Official AI Video, Commercial TVC (30s/60s), Reference Image, 1-Take Voiceover (12 voices) & Lyria 3.5 Music Production MCP Server. "
    "Standard workflow for AI Agents: "
    "(1) Call omni_session_status to verify studio service readiness (session_ready=true). "
    "(2) If the user provides a reference image (local path, URL, or base64), pass it directly to the `image` parameter of omni_generate_scene / omni_generate_image / omni_produce_commercial, or call omni_upload_image first to normalize and analyze the subject. "
    "(3) To generate high-resolution AI images, banners, posters, or product/character variations, call omni_generate_image (default `engine='omni'`, or set `engine='chatgpt'` / `'auto'`) or call omni_generate_image_chatgpt directly for the ChatGPT Image Engine: "
    "omit `image` for Text-to-Image, or pass `image`/`images` for Reference-to-Image consistency; choose `aspect_ratio` (`16:9` landscape/web/YouTube, `1:1` square Facebook/Instagram Ads, `9:16` vertical TikTok/Reels/Story) and one of 14 `style` presets (default `PHOTOGRAPHY`). "
    "For Vietnamese posters/banners with on-image text, wrap exact Vietnamese text in quotes (`\"...\"`) and specify `'exact Vietnamese text with diacritics'` so it is preserved verbatim; never use `#RRGGBB` hex codes. "
    "(4) To generate a 3s–15s video clip (default 15s, 16:9 or 9:16), call omni_generate_scene: "
    "set `input_mode='reference'` and pass `image` for Reference-to-Video (preserves 100% of the person's appearance, product, or UI screenshot); "
    "set `input_mode='text'` (or omit `image`) for pure Text-to-Video generation; "
    "pass `prior_video` + `extend_mode='extend_concat'` to seamlessly extend an existing video by +15s. "
    "(5) To synthesize expressive Vietnamese/English voiceover (12 studio voices: Puck, Aoede, Achernar, Charon, Kore, Fenrir, Orus, Zephyr, vi-vn-concierge-2, vi-vn-csagent-1, vi-vn-training-1, vi-vn-tutor-1) with style mastering (fast, slow, warm, authoritative, energetic, cinematic reverb), call omni_synthesize_1take_vo. "
    "(6) To compose 48kHz Stereo music via Lyria 3.5 Studio API, call omni_generate_lyria_bgm: set `instrumental=true` for background music or `instrumental=false` for a Full Vocal Song with AI singing and lyrics. "
    "(7) To produce a complete multi-scene 30s (2x15s) or 60s (4x15s / 6x10s) 1080p commercial, call omni_produce_commercial (runs Video + 1-Take VO + Lyria BGM concurrently in 3 parallel threads with Clean Audio Bus, Spectral Carve, and Sidechain Auto-Ducking 6:1). "
    "(8) When running asynchronous jobs (wait=false), poll omni_job_status to retrieve `preview_url` (720p stream) and `download_url` (/outputs/<file>.mp4)."
)

ALL_12_STUDIO_VOICES_DESC = (
    "Studio Voice ID (12 official voices): "
    "`Puck` (Dynamic male founder), `Aoede` (Warm expressive female), `Achernar` (Gentle refined female), "
    "`Charon` (Deep authoritative male), `Kore` (Modern youthful female), `Fenrir` (Dramatic powerful male), "
    "`Orus` (Calm expert male), `Zephyr` (Elegant natural female), "
    "`vi-vn-concierge-2` (Vietnamese premium commercial female), `vi-vn-csagent-1` (Vietnamese friendly customer care female), "
    "`vi-vn-training-1` (Vietnamese presenter/trainer male), `vi-vn-tutor-1` (Vietnamese warm instructor male). Default: `Puck`."
)

MCP_TOOLS_SCHEMA = [
    {
        "name": "omni_session_status",
        "description": (
            "Check the live operational health and readiness of the Omni Studio Pro media generation engines "
            "(Video Engine `gemini-omni-1.2-flash`, Voiceover Engine `gemini-3.8-flash-tts`, and Music Engine `lyria-3.5-studio`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "If true, refreshes the studio workspace connection status before returning.",
                }
            },
        },
    },
    {
        "name": "omni_session_update",
        "description": (
            "Update the Omni Studio workspace project configuration (`project_id`, `account_index`, or `gemini_api_key`) on the studio server."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Workspace project document ID.",
                },
                "authuser": {
                    "type": "string",
                    "description": "Workspace account index (`0`, `1`, `2`...).",
                },
                "gemini_api_key": {
                    "type": "string",
                    "description": "Optional Gemini API Key for cloud synthesis.",
                },
            },
        },
    },
    {
        "name": "omni_upload_image",
        "description": (
            "Upload and normalize a reference image (from a local file path, `file:///` URI, `http://`/`https://` URL, or base64 data URI) "
            "into `/outputs` for use in Reference-to-Video (`omni_generate_scene`), AI Image Generation (`omni_generate_image`), "
            "or Multi-Scene Commercial (`omni_produce_commercial`). Automatically analyzes the subject and returns `relative_path`, "
            "`download_url`, `subject_description`, and `suggested_prompt`."
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
            "Stage 1 Pipeline Tool: Generate or extend a 3s–15s cinematic video scene using the `gemini-omni-1.2-flash` Studio Video API. "
            "• Reference-to-Video mode (`input_mode='reference'` or when `image`/`images` is provided): normalizes the reference image to a 16:9 or 9:16 canvas "
            "and generates a video that preserves 100% of the exact person, character, product, or software UI from the reference image. "
            "• Text-to-Video mode (`input_mode='text'` or when `image` is empty): generates a cinematic video scene directly from the text prompt without any reference image. "
            "• Video Extension mode (`prior_video` + `extend_mode='extend_concat'`): extracts the final frame + anchor node of the prior video, "
            "generates a seamless +15s continuation, and stitches them into a continuous longer MP4 (`15s -> 30s -> 45s -> 60s`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_mode": {
                    "type": "string",
                    "enum": ["reference", "text", "auto"],
                    "description": "`reference`: Reference-to-Video using `image`/`images`. `text`: Pure Text-to-Video ignoring any reference image. `auto` (default): uses Reference-to-Video when `image` is provided, or Text-to-Video when `image` is empty.",
                },
                "prompt": {
                    "type": "string",
                    "description": "English or Vietnamese cinematic visual prompt describing the scene action, camera movement, and lighting. Do not include raw hex codes (#RRGGBB).",
                },
                "duration": {
                    "type": "integer",
                    "description": "Clip duration in seconds (`3` to `15`, default `15`s).",
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Aspect ratio: `16:9` (Landscape) or `9:16` (Portrait). Default: `16:9`.",
                },
                "image": {
                    "type": "string",
                    "description": "Optional reference image (local path, `outputs/...`, `file:///`, `https://` URL, or base64 data URI) for Reference-to-Video generation.",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of up to 3 reference images (paths/URLs/base64) for multi-ingredient Reference-to-Video.",
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
                    "description": "Optional list of prior `_node.json` filenames/paths (e.g. `['s1_node.json']`) to lock character and environment continuity across scenes.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output MP4 filename inside `/outputs` (e.g. `scene1_1080p.mp4`).",
                },
                "upscale_1080p": {
                    "type": "boolean",
                    "description": "If false (default), returns the native 720p clip immediately (~25s). If true, masters to 1920x1080 Full HD.",
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
            "Generate high-resolution AI images, ad banners, posters, product visuals, or reference-guided variations via the Omni Studio Image Generation API. "
            "HOW TO USE: "
            "(1) Mode: Omit `image`/`images` for pure Text-to-Image, or pass 1–3 reference images (`image`/`images`) for Reference-to-Image to preserve exact product, logo, or character appearance. "
            "(2) Prompting: Describe subject, setting, lighting, camera angle, and composition clearly; describe colors in natural words (never use `#RRGGBB` hex codes). "
            "(3) Vietnamese Typography on Banners/Posters: Wrap any exact Vietnamese headline/CTA in quotes (e.g., `\"GIẢM 30% HÔM NAY\"`) and include `'exact Vietnamese text with diacritics'` in the prompt so Vietnamese text is rendered accurately without auto-translation. "
            "(4) Aspect Ratio & Style: Pick `aspect_ratio` (`16:9`, `1:1`, or `9:16`) matching the target channel and choose from 14 visual `style` presets. "
            "(5) Async Retrieval: If the response returns `status='running'` with a `job_id`, poll `omni_job_status(job_id=...)` until `status='completed'` to get `public_download_url` and `images[]`. "
            "Generated PNGs in `/outputs` can be passed directly into `omni_generate_scene`'s `image` parameter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Detailed English or Vietnamese visual prompt (subject, composition, lighting, mood, camera angle). "
                        "Rules: Do NOT include `#RRGGBB` hex color codes (use natural color names like 'deep emerald green', 'warm ivory cream'). "
                        "To render exact Vietnamese text on banners/posters, wrap the exact text in quotes (`\"...\"`) and add `'exact Vietnamese text with diacritics'`."
                    ),
                },
                "image": {
                    "type": "string",
                    "description": "Optional single reference image (local path, `outputs/...`, `file:///`, `https://` URL, or base64) for Reference-to-Image mode to preserve character, product, or brand consistency. Omit for pure Text-to-Image.",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of multiple reference images (up to 3) to combine character, product, and logo/background references.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["16:9", "1:1", "9:16", "4:3", "3:4"],
                    "default": "16:9",
                    "description": (
                        "Output aspect ratio (5 native full-res PNG formats): "
                        "`16:9` (1376x768 Landscape — YouTube thumbnails, website hero banners, slides, horizontal video frames); "
                        "`1:1` (1024x1024 Square — Facebook/Instagram Feed posts, e-commerce product cards, square ad banners); "
                        "`3:4` (896x1200 Vertical Feed — Facebook/Instagram vertical ads & posters, closest native ratio to 4:5); "
                        "`4:3` (1200x896 Classic Landscape — presentations, editorial graphics); "
                        "`9:16` (768x1376 Vertical Portrait — TikTok, Instagram Reels, YouTube Shorts, Stories, vertical video frames). "
                        "Default: `16:9`."
                    ),
                },
                "style": {
                    "type": "string",
                    "enum": [
                        "PHOTOGRAPHY",
                        "ILLUSTRATION",
                        "BACKGROUND",
                        "LANDSCAPE",
                        "FLATLAY",
                        "CLIPART",
                        "WATERCOLOR",
                        "VECTOR_ART",
                        "HANDICRAFT",
                        "CYBERPUNK",
                        "PIXEL_ART",
                        "SURPRISE_ME",
                        "VECTOR_ICON",
                        "UNSPECIFIED",
                    ],
                    "default": "PHOTOGRAPHY",
                    "description": (
                        "Visual style preset (14 supported styles): "
                        "`PHOTOGRAPHY` (Photorealistic studio/commercial/portrait — best default for ads & real products), "
                        "`FLATLAY` (Top-down overhead product/food/cosmetics layout), "
                        "`BACKGROUND` (Clean uncluttered backdrop/texture for banners & text overlay), "
                        "`LANDSCAPE` (Scenic wide-angle outdoor/architecture vistas), "
                        "`ILLUSTRATION` (Editorial/storytelling digital art), "
                        "`VECTOR_ART` (Clean scalable flat/modern graphic illustration), "
                        "`VECTOR_ICON` (Minimalist badge, emblem, or UI icon graphic), "
                        "`WATERCOLOR` (Soft painterly watercolor aesthetic), "
                        "`CLIPART` (Simple isolated graphic sticker/cutout), "
                        "`HANDICRAFT` (Paper-cut, clay, felt, or handmade tactile craft look), "
                        "`CYBERPUNK` (Neon futuristic sci-fi lighting), "
                        "`PIXEL_ART` (Retro 8-bit/16-bit pixel game art), "
                        "`SURPRISE_ME` (Creative automatic style selection), "
                        "`UNSPECIFIED` (Neutral style driven purely by prompt). Default: `PHOTOGRAPHY`."
                    ),
                },
                "num_images": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "default": 1,
                    "description": "Number of image variations to generate and save (`1` to `4`). Use `2`–`4` when exploring creative concepts. Default: `1`.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Output PNG filename inside `/outputs` (e.g. `promo_banner_1x1.png`). Auto-generated if omitted.",
                },
                "engine": {
                    "type": "string",
                    "enum": ["omni", "chatgpt", "auto"],
                    "default": "omni",
                    "description": (
                        "Image generation backend engine: "
                        "`omni` (default — Omni Studio native Imagen engine, fastest with 14 style presets and up to 4 variations), "
                        "`chatgpt` (ChatGPT Image Engine — excels at complex typography, photorealistic editorial layouts, and multi-turn visual editing), "
                        "`auto` (uses `omni` first and automatically falls back to `chatgpt` if unavailable)."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_generate_image_chatgpt",
        "description": (
            "Generate or edit high-resolution AI images via the ChatGPT Image Engine. "
            "HOW TO USE: "
            "(1) Mode: Omit `image`/`images` for pure Text-to-Image, or pass 1–3 reference images (`image`/`images`) for Reference-to-Image / image editing. "
            "(2) Prompting: Describe subject, composition, lighting, typography, and color palette in natural language. "
            "Wrap exact Vietnamese or English on-image headlines/CTAs in quotes (`\"...\"`). "
            "(3) Aspect Ratio: Choose `16:9` (landscape banner/thumbnail), `1:1` (square social post), `9:16` (vertical Reels/TikTok/Story), `4:3`, `3:4`, `3:2`, or `2:3`. "
            "(4) Iterative Editing: Pass the `chatgpt_conversation_id` returned from a previous job to refine or edit the previous image in the same conversation. "
            "(5) Async Retrieval: Runs asynchronously and returns within <=5s; if `status='running'`, immediately poll `omni_job_status(job_id=...)` until `status='completed'` to get `public_download_url` and `images[]`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Detailed English or Vietnamese prompt describing the image to generate or the edit to apply to the reference image(s). "
                        "Wrap exact on-image text/headlines in quotes (`\"...\"`)."
                    ),
                },
                "image": {
                    "type": "string",
                    "description": "Optional single reference image (local path, `outputs/...`, `file:///`, `https://` URL, or base64 data URI) for Reference-to-Image or image editing.",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of up to 3 reference images (paths/URLs/base64) to combine or edit together.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["16:9", "1:1", "9:16", "4:3", "3:4", "3:2", "2:3"],
                    "default": "16:9",
                    "description": "Desired output aspect ratio (`16:9`, `1:1`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`). Default: `16:9`.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Optional ChatGPT conversation ID returned by a prior `omni_generate_image_chatgpt` job to continue editing in the same thread.",
                },
                "output_filename": {
                    "type": "string",
                    "description": "Optional output image filename inside `/outputs` (e.g. `chatgpt_poster_1x1.png`). Auto-generated if omitted.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "omni_synthesize_1take_vo",
        "description": (
            "Stage 2 Pipeline Tool: Synthesize expressive Vietnamese or English voiceover via the Omni Studio TTS API (`gemini-3.8-flash-tts`) "
            "in **Single-Pass 1-Take** mode. Supports all **12 studio voices**, strips trailing metadata headers, normalizes to `-15.0 dBFS` unifiedGain, "
            "and applies acoustic style mastering (`style`) for tempo, pitch warmth/brightness, EQ, and room reverb."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Vietnamese or English voiceover script. Separate multiple scenes with a blank line (`\\n\\n`) so the voice actor performs the entire commercial in one continuous emotional take.",
                },
                "voice": {
                    "type": "string",
                    "description": ALL_12_STUDIO_VOICES_DESC,
                },
                "style": {
                    "type": "string",
                    "description": (
                        "Optional Vietnamese or English style/direction prompt (e.g., `'Giọng nam trầm ấm, quyền lực, đọc nhanh 1.15x'`, "
                        "`'Giọng nữ nhẹ nhàng, sâu lắng, truyền cảm'`, `'Sôi động, hào hứng, vang điện ảnh'`). Automatically masters tempo, pitch, and EQ."
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
            "Stage 3 Pipeline Tool: Compose a custom 48kHz Stereo WAV soundtrack or Full Vocal Song via the Omni Studio Lyria 3.5 Music API. "
            "Supports both **Instrumental BGM** (`instrumental=true`) and **Full Vocal Songs with AI singer & lyrics** (`instrumental=false`), "
            "with automatic Vietnamese-to-English musical genre and instrumentation translation."
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
                        "If `false`, composes a Full Vocal Song with AI singing and lyrics via Lyria 3.5. "
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
            "Stage 4 Full Pipeline Orchestrator: Produce a complete 15s / 30s / 60s 1080p Full HD commercial end-to-end in 3 concurrent parallel threads: "
            "(1) Multi-scene `gemini-omni-1.2-flash` video generation (supports Reference-to-Video with custom portrait/product images or pure Text-to-Video), "
            "(2) Single-Pass 1-Take emotional Vietnamese voiceover via `gemini-3.8-flash-tts` (12 studio voices, `-15.0 dBFS` unifiedGain), "
            "(3) Custom 48kHz Stereo soundtrack via `Lyria 3.5 Studio Music API`, and "
            "(4) Broadcast mastering (Clean Audio Bus, Spectral Carve at 1000Hz/1650Hz, Sidechain Auto-Ducking 6:1, and breath-synced `.ass` HUD subtitles)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_mode": {
                    "type": "string",
                    "enum": ["reference", "text", "auto"],
                    "description": "`reference`: Reference-to-Video using scene `image`. `text`: Pure Text-to-Video ignoring all reference images. `auto` (default): uses Reference-to-Video when `image` is provided.",
                },
                "scenes": {
                    "type": "array",
                    "description": "Ordered array of scenes (e.g. 2x15s scenes for 30s TVC, 4x15s or 6x10s scenes for 60s TVC).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "badge": {"type": "string", "description": "Top-left HUD chapter badge (e.g. '01 // KẾT NỐI ĐA KÊNH')."},
                            "image": {"type": "string", "description": "Optional path, URL, or base64 of reference image (character portrait, product, or 1080p UI screenshot). Leave empty for pure Text-to-Video."},
                            "prompt": {"type": "string", "description": "English or Vietnamese visual prompt for `gemini-omni-1.2-flash`."},
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
                    "description": ALL_12_STUDIO_VOICES_DESC,
                },
                "vo_style": {
                    "type": "string",
                    "description": "Optional Vietnamese/English voiceover style mastering prompt (e.g. `'Giọng nam ấm áp, hào hứng, truyền cảm hứng'`).",
                },
                "bgm_prompt": {
                    "type": "string",
                    "description": "Vietnamese or English Lyria 3.5 BGM style prompt.",
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
        "description": "Check the real-time status (`queued`, `running`, `completed`, `failed`), logs, instant 720p `preview_url`, and `/outputs/<filename>` `download_url` (`public_download_url`) of any async pipeline job (`omni_generate_image`, `omni_generate_scene`, `omni_synthesize_1take_vo`, `omni_generate_lyria_bgm`, or `omni_produce_commercial`).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID returned by `omni_generate_image`, `omni_generate_scene`, `omni_synthesize_1take_vo`, `omni_generate_lyria_bgm`, or `omni_produce_commercial`. Omit to list all recent jobs.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "Optional long-poll duration in seconds (`0` to `5`, default `4`) to wait for the job to complete before returning.",
                },
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
            "Retrieve the Omni Studio Pro MCP connection configuration (`serverUrl`) or read the production workflow documentation (`gemini-omni-flash-api`)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_packs", "get_skill", "install_pack"],
                    "default": "install_pack",
                    "description": "install_pack (default): get 1-Link MCP connection config; list_packs: list available workflow packs; get_skill: read workflow instructions.",
                },
                "skill_slug": {
                    "type": "string",
                    "description": "Workflow guide name (`gemini-omni-flash-api` or `omni-1-1-complete-guide`).",
                },
            },
        },
    },
]


def execute_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """Executes one of the 11 Omni Flash Video & Media Pipeline MCP tools and returns a structured dict result."""
    import omni_server as srv
    from edit_video import (
        describe_image_subject_for_omni,
        ensure_png_or_jpeg_image_file,
        run_vids_rpc_generate_image,
    )
    from make_emotional_1take_vo import EMOTIONAL_DIRECTOR_STYLE, call_gemini_38_flash_tts_1take
    from produce_commercial import generate_bgm_track, parse_bgm_prompt_profile

    args = dict(arguments or {})

    if tool_name == "omni_session_status":
        if args.get("refresh") or args.get("rotate_now"):
            threading.Thread(
                target=srv.rotate_google_cookies_headless,
                args=(srv.DEFAULT_SESSION_PATH,),
                daemon=True,
            ).start()
        raw_st = srv.get_session_status()
        chatgpt_ready = False
        try:
            from chatgpt_image_backend import is_chatgpt_image_available
            chatgpt_ready = is_chatgpt_image_available(force=bool(args.get("refresh")))
        except Exception:
            chatgpt_ready = False
        return {
            "status": "ok",
            "session_ready": raw_st.get("session_ready", True),
            "studio_status": "authenticated" if raw_st.get("session_ready") else "configuration_required",
            "workspace_project_id": raw_st.get("doc_id"),
            "account_index": raw_st.get("authuser", "0"),
            "video_engine": "gemini-omni-1.2-flash",
            "tts_engine": "gemini-3.8-flash-tts",
            "music_engine": "lyria-3.5-studio",
            "image_engine": "omni-studio-imagen",
            "chatgpt_image_engine": "chatgpt-web-image",
            "chatgpt_image_ready": chatgpt_ready,
            "last_refreshed_at": raw_st.get("last_rotated_at"),
            "refresh_count": raw_st.get("rotate_count", 0),
        }

    if tool_name == "omni_session_update":
        upd = srv.update_session_payload(args)
        threading.Thread(
            target=srv.rotate_google_cookies_headless,
            args=(srv.DEFAULT_SESSION_PATH,),
            daemon=True,
        ).start()
        raw_st = srv.get_session_status()
        return {
            "update": upd,
            "status": {
                "session_ready": raw_st.get("session_ready", True),
                "workspace_project_id": raw_st.get("doc_id"),
                "account_index": raw_st.get("authuser", "0"),
            },
        }

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
        subj_info = {"is_character_or_person": False, "subject_description": "", "suggested_prompt": ""}
        if args.get("analyze_subject", True):
            res_holder = []

            def _analyze():
                try:
                    res_holder.append(describe_image_subject_for_omni(norm_path))
                except Exception:
                    pass

            ta = threading.Thread(target=_analyze, daemon=True)
            ta.start()
            ta.join(timeout=3.0)
            if res_holder:
                subj_info = res_holder[0]
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        return {
            "status": "completed",
            "filename": final_fn,
            "path": norm_path,
            "relative_path": f"outputs/{final_fn}",
            "download_url": f"/outputs/{final_fn}",
            "public_download_url": f"{host_url}/outputs/{final_fn}",
            "mime": norm_mime,
            "width": norm_w,
            "height": norm_h,
            "is_character_or_person": subj_info.get("is_character_or_person", False),
            "subject_description": subj_info.get("subject_description", ""),
            "suggested_prompt": subj_info.get("suggested_prompt", ""),
        }

    def _format_job_with_public_urls(j: dict) -> dict:
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        out = dict(j)
        if out.get("download_url"):
            out["public_download_url"] = f"{host_url}{out['download_url']}"
        if out.get("preview_url"):
            out["public_preview_url"] = f"{host_url}{out['preview_url']}"
        if out.get("status") in ("queued", "running"):
            jid = out.get("job_id")
            out["poll_tool"] = "omni_job_status"
            out["poll_url"] = f"/api/jobs/{jid}"
            out["next_action"] = (
                f"Job '{jid}' is actively running on the Studio GPU server. "
                f"IMMEDIATELY call `omni_job_status(job_id='{jid}', wait_seconds=5)` in a loop until `status` is `'completed'`, "
                f"then return `public_download_url` to the user. Do NOT stop or ask the user to wait before polling!"
            )
        return out

    def _wait_for_job_bounded(job_id: str, max_sec: float = 5.0) -> dict:
        deadline = time.time() + min(max_sec, 5.0)
        while time.time() < deadline:
            with srv.JOBS_LOCK:
                j = srv.JOBS.get(job_id)
                if j and j.get("status") in ("completed", "failed"):
                    return _format_job_with_public_urls(j)
            time.sleep(0.5)
        with srv.JOBS_LOCK:
            j = srv.JOBS.get(job_id) or {"job_id": job_id, "status": "running"}
            return _format_job_with_public_urls(j)

    if tool_name == "omni_generate_scene":
        args.setdefault("upscale_1080p", False)
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
        t = threading.Thread(target=srv.run_job_generate_scene, args=(job_id, args), daemon=True)
        t.start()
        return _wait_for_job_bounded(job_id, max_sec=5.0)

    def _run_chatgpt_image_job(
        prompt: str,
        out_path: str,
        aspect_ratio: str,
        ref_imgs: list[str],
        conversation_id: str = "",
    ) -> dict:
        from chatgpt_image_backend import generate_chatgpt_image

        job_id = uuid.uuid4().hex[:10]
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        with srv.JOBS_LOCK:
            srv.JOBS[job_id] = {
                "job_id": job_id,
                "type": "generate-image-chatgpt",
                "status": "running",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [f"🖼️ Generating {aspect_ratio} image via ChatGPT Image Engine..."],
            }

        def _on_status(_stage: str, msg: str):
            with srv.JOBS_LOCK:
                if job_id in srv.JOBS:
                    srv.JOBS[job_id]["logs"].append(f"⏳ {msg}")

        def _bg_chatgpt_img():
            try:
                res = generate_chatgpt_image(
                    prompt=prompt,
                    output_path=out_path,
                    aspect_ratio=aspect_ratio,
                    image_files=ref_imgs,
                    conversation_id=conversation_id,
                    on_status=_on_status,
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
                            "public_download_url": f"{host_url}/outputs/{fn}",
                        }
                    )
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id].update(
                        {
                            "status": "completed",
                            "engine": "chatgpt-web-image",
                            "output_file": imgs[0]["filename"],
                            "download_url": imgs[0]["download_url"],
                            "public_download_url": imgs[0]["public_download_url"],
                            "relative_path": imgs[0]["relative_path"],
                            "images": imgs,
                            "mime": res.get("mime", "image/png"),
                            "reference_images_used": len(ref_imgs),
                            "aspect_ratio": res.get("aspect_ratio", aspect_ratio),
                            "chatgpt_conversation_id": res.get("chatgpt_conversation_id", ""),
                            "assistant_text": res.get("assistant_text", ""),
                        }
                    )
                    srv.JOBS[job_id]["logs"].append(f"✅ ChatGPT image ready: {imgs[0]['download_url']}")
            except Exception as e:
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id]["status"] = "failed"
                    srv.JOBS[job_id]["error"] = str(e)
                    srv.JOBS[job_id]["logs"].append(f"❌ Error: {e}")

        t = threading.Thread(target=_bg_chatgpt_img, daemon=True)
        t.start()
        return _wait_for_job_bounded(job_id, max_sec=5.0)

    if tool_name == "omni_generate_image_chatgpt":
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Missing required 'prompt' for omni_generate_image_chatgpt")
        aspect_ratio = args.get("aspect_ratio", "16:9")
        conversation_id = (args.get("conversation_id") or "").strip()
        out_name = os.path.basename(args.get("output_filename") or f"chatgpt_img_{uuid.uuid4().hex[:6]}.png")
        if not out_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            out_name += ".png"
        out_path = os.path.join(OUTPUTS_DIR, out_name)
        ref_imgs = []
        if args.get("image"):
            ref_imgs.append(srv.save_base64_image_if_needed(args["image"], OUTPUTS_DIR, 1))
        for i, im in enumerate(args.get("images") or []):
            ref_imgs.append(srv.save_base64_image_if_needed(im, OUTPUTS_DIR, i + 1))
        ref_imgs = [p for p in ref_imgs if p]
        return _run_chatgpt_image_job(
            prompt=prompt,
            out_path=out_path,
            aspect_ratio=aspect_ratio,
            ref_imgs=ref_imgs,
            conversation_id=conversation_id,
        )

    if tool_name == "omni_generate_image":
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Missing required 'prompt' for omni_generate_image")
        engine = str(args.get("engine") or "omni").strip().lower()
        aspect_ratio = args.get("aspect_ratio", "16:9")
        style = args.get("style", "PHOTOGRAPHY")
        num_images = max(1, min(4, int(args.get("num_images", 1))))
        out_name = os.path.basename(args.get("output_filename") or f"studio_img_{uuid.uuid4().hex[:6]}.png")
        if not out_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            out_name += ".png"
        out_path = os.path.join(OUTPUTS_DIR, out_name)
        ref_imgs = []
        if args.get("image"):
            ref_imgs.append(srv.save_base64_image_if_needed(args["image"], OUTPUTS_DIR, 1))
        for i, im in enumerate(args.get("images") or []):
            ref_imgs.append(srv.save_base64_image_if_needed(im, OUTPUTS_DIR, i + 1))
        ref_imgs = [p for p in ref_imgs if p]

        if engine == "chatgpt":
            return _run_chatgpt_image_job(
                prompt=prompt,
                out_path=out_path,
                aspect_ratio=aspect_ratio,
                ref_imgs=ref_imgs,
            )

        job_id = uuid.uuid4().hex[:10]
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        with srv.JOBS_LOCK:
            srv.JOBS[job_id] = {
                "job_id": job_id,
                "type": "generate-image",
                "status": "running",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [f"🖼️ Generating {aspect_ratio} image ({style})..."],
            }

        def _bg_img():
            try:
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
                            "public_download_url": f"{host_url}/outputs/{fn}",
                        }
                    )
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id].update(
                        {
                            "status": "completed",
                            "engine": "omni-studio-imagen",
                            "output_file": imgs[0]["filename"],
                            "download_url": imgs[0]["download_url"],
                            "public_download_url": imgs[0]["public_download_url"],
                            "relative_path": imgs[0]["relative_path"],
                            "images": imgs,
                            "reference_images_used": len(ref_imgs),
                            "aspect_ratio": res.get("aspect_ratio", aspect_ratio),
                            "style": res.get("style", style),
                        }
                    )
                    srv.JOBS[job_id]["logs"].append(f"✅ Image ready: {imgs[0]['download_url']}")
            except Exception as e:
                if engine == "auto":
                    try:
                        from chatgpt_image_backend import generate_chatgpt_image, is_chatgpt_image_available

                        if is_chatgpt_image_available(force=True):
                            with srv.JOBS_LOCK:
                                srv.JOBS[job_id]["logs"].append(f"⚠️ Primary image engine error ({e}); falling back to ChatGPT Image Engine...")
                            res_cg = generate_chatgpt_image(
                                prompt=prompt,
                                output_path=out_path,
                                aspect_ratio=aspect_ratio,
                                image_files=ref_imgs,
                            )
                            imgs = []
                            for p in res_cg.get("images", [res_cg["output_path"]]):
                                fn = os.path.basename(p)
                                imgs.append(
                                    {
                                        "filename": fn,
                                        "path": p,
                                        "relative_path": f"outputs/{fn}",
                                        "download_url": f"/outputs/{fn}",
                                        "public_download_url": f"{host_url}/outputs/{fn}",
                                    }
                                )
                            with srv.JOBS_LOCK:
                                srv.JOBS[job_id].update(
                                    {
                                        "status": "completed",
                                        "engine": "chatgpt-web-image",
                                        "output_file": imgs[0]["filename"],
                                        "download_url": imgs[0]["download_url"],
                                        "public_download_url": imgs[0]["public_download_url"],
                                        "relative_path": imgs[0]["relative_path"],
                                        "images": imgs,
                                        "reference_images_used": len(ref_imgs),
                                        "aspect_ratio": res_cg.get("aspect_ratio", aspect_ratio),
                                        "chatgpt_conversation_id": res_cg.get("chatgpt_conversation_id", ""),
                                    }
                                )
                                srv.JOBS[job_id]["logs"].append(f"✅ Fallback ChatGPT image ready: {imgs[0]['download_url']}")
                            return
                    except Exception as cg_err:
                        e = RuntimeError(f"Primary engine failed ({e}); ChatGPT fallback failed ({cg_err})")
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id]["status"] = "failed"
                    srv.JOBS[job_id]["error"] = str(e)

        t = threading.Thread(target=_bg_img, daemon=True)
        t.start()
        return _wait_for_job_bounded(job_id, max_sec=5.0)

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
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        job_id = uuid.uuid4().hex[:10]
        with srv.JOBS_LOCK:
            srv.JOBS[job_id] = {
                "job_id": job_id,
                "type": "synthesize-1take-vo",
                "status": "running",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [f"🎙️ Synthesizing 1-Take Voiceover ({voice})..."],
            }

        def _bg_vo():
            try:
                pcm_bytes, sr = call_gemini_38_flash_tts_1take(full_text=text, style=style, api_key=api_key, voice=voice)
                with wave.open(out_wav, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(pcm_bytes)
                dur = round((len(pcm_bytes) / 2.0) / sr, 2)
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id].update(
                        {
                            "status": "completed",
                            "engine": "gemini-3.8-flash-tts",
                            "mode": "single-pass-1take-style-mastered",
                            "voice": voice,
                            "style": style,
                            "duration_sec": dur,
                            "output_file": out_name,
                            "output_path": out_wav,
                            "relative_path": f"outputs/{out_name}",
                            "download_url": f"/outputs/{out_name}",
                            "public_download_url": f"{host_url}/outputs/{out_name}",
                        }
                    )
                    srv.JOBS[job_id]["logs"].append(f"✅ Voiceover ready ({dur}s): /outputs/{out_name}")
            except Exception as e:
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id]["status"] = "failed"
                    srv.JOBS[job_id]["error"] = str(e)

        t = threading.Thread(target=_bg_vo, daemon=True)
        t.start()
        return _wait_for_job_bounded(job_id, max_sec=5.0)

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
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        job_id = uuid.uuid4().hex[:10]
        with srv.JOBS_LOCK:
            srv.JOBS[job_id] = {
                "job_id": job_id,
                "type": "generate-lyria-bgm",
                "status": "running",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [f"🎵 Generating Lyria BGM ({dur}s, {bpm} BPM, {scale})..."],
            }

        def _bg_bgm():
            try:
                generate_bgm_track(
                    output_wav=out_wav,
                    duration_sec=dur,
                    bgm_prompt=prompt,
                    bpm=bpm,
                    scale=scale,
                    force_regenerate=True,
                    instrumental=instrumental,
                )
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id].update(
                        {
                            "status": "completed",
                            "engine": "lyria-3.5-studio",
                            "duration_sec": dur,
                            "bpm": profile.get("bpm", bpm),
                            "scale": scale,
                            "instrumental": instrumental,
                            "profile": profile,
                            "output_file": out_name,
                            "output_path": out_wav,
                            "relative_path": f"outputs/{out_name}",
                            "download_url": f"/outputs/{out_name}",
                            "public_download_url": f"{host_url}/outputs/{out_name}",
                        }
                    )
                    srv.JOBS[job_id]["logs"].append(f"✅ Lyria BGM ready ({dur}s): /outputs/{out_name}")
            except Exception as e:
                with srv.JOBS_LOCK:
                    srv.JOBS[job_id]["status"] = "failed"
                    srv.JOBS[job_id]["error"] = str(e)

        t = threading.Thread(target=_bg_bgm, daemon=True)
        t.start()
        return _wait_for_job_bounded(job_id, max_sec=5.0)

    if tool_name == "omni_produce_commercial":
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
        t = threading.Thread(target=srv.run_job_produce_commercial, args=(job_id, args), daemon=True)
        t.start()
        return _wait_for_job_bounded(job_id, max_sec=5.0)

    if tool_name == "omni_job_status":
        job_id = args.get("job_id")
        wait_sec = min(5, max(0, int(args.get("wait_seconds", 4))))
        if job_id:
            with srv.JOBS_LOCK:
                j = srv.JOBS.get(job_id)
            if not j:
                return {"error": f"Job '{job_id}' not found"}
            if wait_sec > 0 and j.get("status") in ("queued", "running"):
                return _wait_for_job_bounded(job_id, max_sec=float(wait_sec))
            with srv.JOBS_LOCK:
                return _format_job_with_public_urls(srv.JOBS[job_id])
        with srv.JOBS_LOCK:
            return {"jobs": [_format_job_with_public_urls(x) for x in srv.JOBS.values()]}

    if tool_name == "omni_list_outputs":
        items = []
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        if os.path.exists(OUTPUTS_DIR):
            for fn in sorted(os.listdir(OUTPUTS_DIR), reverse=True):
                fp = os.path.join(OUTPUTS_DIR, fn)
                if os.path.isfile(fp) and fn.endswith((".mp4", ".wav", ".json", ".png", ".jpg", ".jpeg", ".webp")):
                    items.append(
                        {
                            "name": fn,
                            "path": fp,
                            "relative_path": f"outputs/{fn}",
                            "size_mb": round(os.path.getsize(fp) / (1024 * 1024), 2),
                            "download_url": f"/outputs/{fn}",
                            "public_download_url": f"{host_url}/outputs/{fn}",
                        }
                    )
        return {"count": len(items), "files": items}

    if tool_name == "omni_skill_packs":
        action = args.get("action", "install_pack")
        slug = args.get("skill_slug", "gemini-omni-flash-api")
        host_url = os.environ.get("OMNI_PUBLIC_URL", "https://omni.mcpads.win").rstrip("/")
        mcp_link = f"{host_url}/mcp"

        if action == "list_packs":
            return {
                "mcp_server_url": mcp_link,
                "packs": [
                    {
                        "pack_id": "omni-video-studio",
                        "skills": ["gemini-omni-flash-api"],
                        "description": (
                            "Official 11-Tool AI Video & Commercial Production Suite (Reference-to-Video, Text-to-Video, "
                            "+15s Video Extension, 30s/60s Commercial Pipeline, 12-Voice 1-Take TTS, and Lyria 3.5 Music)."
                        ),
                    }
                ],
            }
        if action == "get_skill":
            return {
                "skill_slug": slug,
                "mcp_server_url": mcp_link,
                "content": MCP_INSTRUCTIONS,
            }

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
                        "name": "Omni Studio Pro — AI Video & Commercial Production Guide",
                        "mimeType": "text/markdown",
                        "description": "Production guide covering Reference-to-Video, Text-to-Video, Anchor Chaining, 1-Take 12-Voice TTS, and Lyria 3.5 Music.",
                    }
                ]
            },
        }

    if method == "resources/read":
        uri = params.get("uri", "")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/markdown",
                        "text": MCP_INSTRUCTIONS,
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
                        "description": "Prompt template for orchestrating a 30s/60s commercial with Reference-to-Video ingredients, 1-Take 12-Voice TTS, and Lyria 3.5.",
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
