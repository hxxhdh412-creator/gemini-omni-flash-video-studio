#!/usr/bin/env python3
"""
International-Standard End-to-End AI Video Production Pipeline (10s / 30s / 60s+ Seamless Multi-Scene):
  1. Gemini Omni 1.1 Flash Video Generation & Dual-Anchor Multi-Scene Chaining (SDK or Unlimited vids.new RPC)
  2. Auto-CDP Account & Session Management (port 9222 + vids.new auto-discovery + thread-safe Vids RPC lock)
  3. Mindset & Prompt Sanitizer (strips raw hex codes, enforces Pure Graphical UI on screens, unbroken 10s shots)
  4. Google Lyria RealTime AI Music Generation (models/lyria-realtime-exp, 48kHz Stereo WAV) + Studio Synth Fallback
  5. Single-Pass 1-Take Gemini 3.8 Flash TTS Engine (1 API call for all scenes, 6KB C2PA tail stripped, unifiedGain, HyperFrames 'voice-clean' DSP)
  6. Seamless Per-Scene Audio Micro-Declick + Spectral Voiceover Carve (1kHz/1.65kHz) + Sidechain Auto-Ducking + True-Peak Limiter
  7. Pre-Upscaled 1080p Full HD Lanczos + CAS Cache + Fast 9:16 Blur + GPU NVENC Auto-Detection + Vector ASS HUD + Storyboard Grid
"""

import argparse
import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from edit_video import (
    DEFAULT_SESSION_PATH,
    format_editing_prompt,
    get_ffmpeg_exe,
    log,
    refresh_session_from_cdp,
    run_vids_rpc_edit_or_generate,
    verify_product_and_subject_consistency,
)

_BUNDLED_LYRIA = os.path.join(os.path.dirname(SCRIPT_DIR), "audio", "lyria-recipe.py")
_GLOBAL_LYRIA = os.path.join(
    os.path.expanduser("~"), ".gemini", "config", "skills", "media-use", "audio", "scripts", "lyria-recipe.py"
)
LYRIA_SCRIPT = _BUNDLED_LYRIA if os.path.exists(_BUNDLED_LYRIA) else _GLOBAL_LYRIA
COMPOSE_MUSIC_SCRIPT = os.path.join(
    os.path.expanduser("~"), ".gemini", "antigravity", "scratch", "video-bds-quahd", "scripts", "compose-music.mjs"
)

_CACHED_H264_ARGS = None


def get_fast_h264_args(ffmpeg_exe: str) -> list[str]:
    """Detects whether NVIDIA h264_nvenc is available; falls back to libx264 -preset veryfast -crf 16."""
    global _CACHED_H264_ARGS
    if _CACHED_H264_ARGS is not None:
        return list(_CACHED_H264_ARGS)
    try:
        res = subprocess.run(
            [
                ffmpeg_exe,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=256x256:d=0.1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=6,
        )
        if res.returncode == 0:
            log("[Video Encoder] Hardware GPU acceleration enabled (h264_nvenc).")
            _CACHED_H264_ARGS = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18", "-pix_fmt", "yuv420p"]
            return list(_CACHED_H264_ARGS)
    except Exception:
        pass
    _CACHED_H264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p"]
    return list(_CACHED_H264_ARGS)


def sanitize_ass_text(text: str, default_reset_bgr: str = "&HFFFFFF&") -> str:
    """
    Mindset & Subtitle Guardrail:
    Automatically converts accidental HTML-like color tags (<c&H0062FFDF&>...</c>, <color=#DFFF62>...</color>)
    or Markdown bold (**keyword**) into valid libass override tags ({\\c&H62FFDF&}...{\\c&HFFFFFF&})
    and strips any remaining raw angle-bracket tags so raw code never leaks onto the video frame.
    """
    if not text:
        return ""
    s = text
    s = re.sub(r"<c&H0*([0-9A-Fa-f]{6})&?>", r"{\\c&H\1&}", s)
    s = re.sub(r"</c>", f"{{\\c{default_reset_bgr}}}", s, flags=re.IGNORECASE)

    def _rgb_to_ass_bgr(m):
        rr, gg, bb = m.group(1), m.group(2), m.group(3)
        return f"{{\\c&H{bb.upper()}{gg.upper()}{rr.upper()}&}}"

    s = re.sub(r"<color=#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})>", _rgb_to_ass_bgr, s, flags=re.IGNORECASE)
    s = re.sub(r"</color>", f"{{\\c{default_reset_bgr}}}", s, flags=re.IGNORECASE)
    s = re.sub(r"\*\*([^*]+)\*\*", rf"{{\\c&H62FFDF&}}\1{{\\c{default_reset_bgr}}}", s)
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)
    return s


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    data_size = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_bytes


def extract_clean_pcm(raw_bytes: bytes) -> tuple[bytes, int]:
    """
    Extracts strictly the PCM audio payload from a RIFF/WAVE buffer (`data_start` to `data_start + chunk_size`),
    stripping the ~6,072-byte C2PA / SynthID provenance metadata appended at the tail of Gemini 3.8 TTS WAV files.
    """
    if len(raw_bytes) > 44 and raw_bytes[:4] == b"RIFF":
        sample_rate = struct.unpack_from("<I", raw_bytes, 24)[0] or 24000
        pos = 12
        while pos + 8 <= len(raw_bytes):
            chunk_id = raw_bytes[pos : pos + 4]
            chunk_size = struct.unpack_from("<I", raw_bytes, pos + 4)[0]
            if chunk_id == b"data":
                data_start = pos + 8
                data_end = min(len(raw_bytes), data_start + chunk_size)
                return raw_bytes[data_start:data_end], sample_rate
            pos += 8 + chunk_size
    return raw_bytes, 24000


def ensure_wav_bytes(raw_bytes: bytes, sample_rate: int = 24000) -> bytes:
    pcm, sr = extract_clean_pcm(raw_bytes)
    return pcm_to_wav(pcm, sample_rate=sr or sample_rate)


def synthesize_gemini_38_tts(
    api_key: str,
    text: str,
    style: str = "warm, confident, professional commercial narrator speaking natural Vietnamese",
    voice: str = "Puck",
    preferred_model: str = "gemini-3.8-flash-tts",
) -> tuple[bytes, str]:
    """
    Synthesizes speech via Gemini 3.8 Flash TTS (/v1beta/interactions) with C2PA tail stripping.
    If the preferred model hits its daily RPD quota (HTTP 429 per day), automatically falls back
    for the entire call to gemini-3.8-flash-lite-tts so multi-scene 1-Take synthesis never fails.
    """
    model_chain = []
    for m in (preferred_model, "gemini-3.8-flash-lite-tts", "gemini-2.5-flash-preview-tts"):
        if m not in model_chain:
            model_chain.append(m)

    for model in model_chain:
        is_gen_content = "2.5-" in model
        if is_gen_content:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            payload = {
                "contents": [{"parts": [{"text": f"Read in a {style}:\n\n{text}"}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
                },
            }
        else:
            url = "https://generativelanguage.googleapis.com/v1beta/interactions"
            payload = {
                "model": model,
                "input": [
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                                "annotations": [{"type": "speech_metadata", "style": style}],
                            }
                        ],
                    }
                ],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voice}]},
            }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if is_gen_content:
                    inline = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("inlineData", {})
                    )
                    if inline.get("data"):
                        return ensure_wav_bytes(base64.b64decode(inline["data"])), model
                else:
                    for step in reversed(data.get("steps", [])):
                        if step.get("type") == "model_output":
                            for item in step.get("content", []):
                                if item.get("type") == "audio" and item.get("data"):
                                    return ensure_wav_bytes(base64.b64decode(item["data"])), model
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            log(f"  [TTS {model} HTTP {e.code}]: {err_body[:180]}")
            if e.code == 429 and any(k in err_body.lower() for k in ("per day", "rpd", "daily", "quota")):
                log(f"  [TTS 1-Take Fallback] {model} hit daily RPD quota; switching whole 1-Take call to next model...")
                continue
            time.sleep(4)
            continue
    raise RuntimeError("Failed to synthesize Gemini 3.8 TTS audio.")


def get_audio_duration(ffmpeg_exe: str, path: str) -> float:
    p = subprocess.run([ffmpeg_exe, "-i", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr)
    if not m:
        return 4.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def synthesize_single_pass_1take_vo(
    ffmpeg_exe: str,
    api_key: str,
    vo_texts: list[str],
    scene_durations: list[float],
    work_dir: str,
    style: str = "deep, authoritative, warm cinematic Vietnamese commercial narrator with smooth rhythmic pacing",
    voice: str = "Charon",
    preferred_model: str = "gemini-3.8-flash-tts",
) -> tuple[list[str], list[float]]:
    """
    Single-Pass 1-Take Voiceover Engine:
    1. Synthesizes all N scene scripts in ONE single API request (joined by '\\n\\n') to preserve 100% uniform pitch,
       timbre, and breathing across the entire commercial while using only 1 RPD quota request.
    2. Strips the 6KB C2PA/SynthID metadata tail from the WAV response.
    3. Scans 10ms RMS windows to locate the N-1 inter-scene silence valleys and applies a single unifiedGain
       across all scenes with soft-knee tanh limiting.
    4. Fits each segment cleanly into its scene slot (atempo ~1.04x-1.18x) + HyperFrames 'voice-clean' EQ.
    """
    os.makedirs(work_dir, exist_ok=True)
    n_scenes = len(vo_texts)
    clean_texts = [re.sub(r"\{\\[^}]+\}|\*", "", t).strip() for t in vo_texts]
    full_script = "\n\n".join(clean_texts)
    full_raw_path = os.path.join(work_dir, "single_pass_1take_full.wav")

    if os.path.exists(full_raw_path) and os.path.getsize(full_raw_path) > 40_000:
        log(f"[Voice 1-Take] Reusing cached Single-Pass 1-Take recording: {full_raw_path}")
        with open(full_raw_path, "rb") as f:
            pcm_bytes, sample_rate = extract_clean_pcm(f.read())
    else:
        log(f"[Voice 1-Take] Calling {preferred_model} (voice={voice}) in 1 single pass for {n_scenes} scenes...")
        wav_bytes, used_model = synthesize_gemini_38_tts(
            api_key=api_key,
            text=full_script,
            style=style,
            voice=voice,
            preferred_model=preferred_model,
        )
        pcm_bytes, sample_rate = extract_clean_pcm(wav_bytes)
        with open(full_raw_path, "wb") as f:
            f.write(pcm_to_wav(pcm_bytes, sample_rate=sample_rate))
        log(
            f"[Voice 1-Take] Synthesized {len(pcm_bytes) / 2 / sample_rate:.2f}s @ {sample_rate}Hz via {used_model} (100% C2PA stripped)"
        )

    total_samples = len(pcm_bytes) // 2
    samples = list(struct.unpack(f"<{total_samples}h", pcm_bytes[: total_samples * 2]))

    win_size = max(1, sample_rate // 100)  # 10ms window
    num_wins = total_samples // win_size
    win_rms = [0.0] * num_wins
    max_rms = 1.0
    for w in range(num_wins):
        base = w * win_size
        chunk = samples[base : base + win_size]
        rms = math.sqrt(sum(v * v for v in chunk) / len(chunk))
        win_rms[w] = rms
        if rms > max_rms:
            max_rms = rms

    speech_thresh = max(220.0, max_rms * 0.03)
    start_w = 0
    while start_w < num_wins and win_rms[start_w] < speech_thresh:
        start_w += 1
    end_w = num_wins - 1
    while end_w > start_w and win_rms[end_w] < speech_thresh:
        end_w -= 1
    start_w = max(0, start_w - 3)
    end_w = min(num_wins - 1, end_w + 5)
    active_total_wins = max(1, end_w - start_w)

    silence_pauses = []
    run = 0
    p_start = start_w
    for w in range(start_w, end_w + 1):
        if win_rms[w] < 180.0:
            if run == 0:
                p_start = w
            run += 1
        else:
            if run >= 25 and p_start > start_w + 100 and w < end_w - 100:
                silence_pauses.append({"midW": (p_start + w) // 2, "lenW": run})
            run = 0

    char_lens = [max(1, len(t)) for t in clean_texts]
    total_chars = sum(char_lens)
    cum_chars = 0
    split_wins = [start_w]
    for idx in range(n_scenes - 1):
        cum_chars += char_lens[idx]
        ratio = cum_chars / total_chars
        expected_w = start_w + round(active_total_wins * ratio)
        radius_w = round((2.4 * sample_rate) / win_size)
        min_prev_w = split_wins[-1] + round((2.2 * sample_rate) / win_size)
        cands = [p for p in silence_pauses if p["midW"] >= min_prev_w and abs(p["midW"] - expected_w) <= radius_w]
        if cands:
            cands.sort(key=lambda p: p["lenW"] - abs(p["midW"] - expected_w) * 0.15, reverse=True)
            split_wins.append(cands[0]["midW"])
        else:
            split_wins.append(max(min_prev_w, expected_w))
    split_wins.append(end_w)

    active_sq = [win_rms[w] ** 2 for w in range(start_w, end_w + 1) if win_rms[w] > speech_thresh * 1.2]
    global_rms = math.sqrt(sum(active_sq) / len(active_sq)) if active_sq else 4500.0
    unified_gain = min(2.2, max(0.6, 5600.0 / global_rms))

    vo_fit_wavs = []
    vo_fit_durations = []
    tanh_norm = math.tanh(1.12)

    for i in range(n_scenes):
        w0 = split_wins[i]
        w1 = split_wins[i + 1]
        while w0 < w1 and win_rms[w0] < speech_thresh:
            w0 += 1
        while w1 > w0 and win_rms[w1] < speech_thresh:
            w1 -= 1
        w0 = max(split_wins[i], w0 - 3)
        w1 = min(split_wins[i + 1], w1 + 5)

        seg_start = w0 * win_size
        seg_end = min(total_samples, (w1 + 1) * win_size)
        seg_len = max(1, seg_end - seg_start)
        seg_dur = seg_len / sample_rate

        fade_in = min(int(seg_len * 0.15), int(sample_rate * 0.03))
        fade_out = min(int(seg_len * 0.15), int(sample_rate * 0.05))
        out_samples = [0] * seg_len

        for k in range(seg_len):
            val = samples[seg_start + k] * unified_gain
            if fade_in > 0 and k < fade_in:
                val *= 0.5 * (1.0 - math.cos((math.pi * k) / fade_in))
            elif fade_out > 0 and k > seg_len - fade_out:
                val *= 0.5 * (1.0 - math.cos((math.pi * (seg_len - k)) / fade_out))
            lim = math.tanh((val / 32768.0) * 1.12) / tanh_norm
            out_samples[k] = max(-32767, min(32767, int(round(lim * 31500.0))))

        seg_raw_wav = os.path.join(work_dir, f"vo_1take_seg_{i+1}.wav")
        seg_fit_wav = os.path.join(work_dir, f"vo_1take_fit_{i+1}.wav")
        with open(seg_raw_wav, "wb") as f:
            f.write(pcm_to_wav(struct.pack(f"<{seg_len}h", *out_samples), sample_rate=sample_rate))

        s_dur = scene_durations[i] if i < len(scene_durations) else 10.0
        max_dur = max(2.0, s_dur - 0.46)
        min_tempo = seg_dur / max_dur
        tempo = min(1.22, max(1.04, min_tempo * 1.015))

        af_chain = (
            f"atempo={tempo:.3f},"
            "highpass=f=80,"
            "equalizer=f=240:t=q:w=1.2:g=-1.5,"
            "equalizer=f=3100:t=q:w=1.1:g=2.4,"
            "acompressor=threshold=-18dB:ratio=2.8:attack=12:release=160:makeup=2.2dB,"
            "aresample=48000"
        )
        subprocess.run(
            [ffmpeg_exe, "-y", "-i", seg_raw_wav, "-af", af_chain, "-t", f"{max_dur:.3f}", "-ac", "2", seg_fit_wav],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        final_d = min(max_dur, get_audio_duration(ffmpeg_exe, seg_fit_wav))
        vo_fit_wavs.append(seg_fit_wav)
        vo_fit_durations.append(final_d)
        log(f"  [VO Scene {i+1}/{n_scenes}] raw={seg_dur:.2f}s -> fit={final_d:.2f}s (tempo={tempo:.2f}x)")

    return vo_fit_wavs, vo_fit_durations


def generate_bgm_track(
    output_wav: str,
    duration_sec: int,
    bgm_prompt: str,
    bpm: int = 115,
    scale: str = "MINOR",
    brightness: float = 0.82,
    density: float = 0.68,
) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)), exist_ok=True)
    if os.path.exists(output_wav) and os.path.getsize(output_wav) > 100_000:
        log(f"[Music Engine] Reusing cached BGM track -> {output_wav}")
        return output_wav

    if os.path.exists(LYRIA_SCRIPT) and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        try:
            log(f"[Music Engine] Generating {duration_sec}s BGM via Google Lyria RealTime (BPM={bpm}, Scale={scale})...")
            res = subprocess.run(
                [
                    sys.executable,
                    LYRIA_SCRIPT,
                    "--output",
                    output_wav,
                    "--duration",
                    str(duration_sec),
                    "--bpm",
                    str(bpm),
                    "--brightness",
                    str(brightness),
                    "--density",
                    str(density),
                    "--scale",
                    scale,
                    "--prompt",
                    bgm_prompt,
                ],
                timeout=max(40, duration_sec + 25),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if res.returncode == 0 and os.path.exists(output_wav) and os.path.getsize(output_wav) > 20000:
                log(f"[Music Engine] Lyria RealTime BGM saved -> {output_wav} ({os.path.getsize(output_wav):,} bytes)")
                return output_wav
        except Exception as e:
            log(f"[Music Engine] Lyria fallback ({e}) -> switching to Studio Synthesizer")

    if os.path.exists(COMPOSE_MUSIC_SCRIPT):
        log(f"[Music Engine] Composing {duration_sec}s Studio BGM via compose-music.mjs...")
        markers = ",".join(str(t) for t in range(0, duration_sec, 10))
        subprocess.run(
            [
                "node",
                COMPOSE_MUSIC_SCRIPT,
                "--preset",
                "luxury",
                "--bpm",
                str(bpm),
                "--duration",
                str(duration_sec),
                "--markers",
                markers,
                "--out",
                output_wav,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return output_wav

    raise RuntimeError("No music generator available.")


def pre_upscale_scene_1080p(ffmpeg_exe: str, raw_mp4: str, out_1080p_mp4: str, scene_duration: int = 10) -> str:
    """
    Pre-upscales a 720p Omni 1.1 Flash clip once using Lanczos + CAS (0.45) and micro-fades its native audio,
    caching the result so 60s 16:9 and 30s Ads Cut masters concatenate in seconds without re-running CAS.
    """
    if os.path.exists(out_1080p_mp4) and os.path.getsize(out_1080p_mp4) > 400_000:
        return out_1080p_mp4

    fade_out_st = max(0.5, scene_duration - 0.35)
    vf = (
        "fps=24,scale=1920:1080:flags=lanczos+accurate_rnd+full_chroma_int,"
        "cas=0.45,eq=contrast=1.03:saturation=1.05,format=yuv420p,"
        f"fade=t=in:st=0:d=0.18,fade=t=out:st={scene_duration - 0.18:.2f}:d=0.18"
    )
    af = f"aresample=48000,afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_st:.2f}:d=0.35"
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        raw_mp4,
        "-vf",
        vf,
        "-af",
        af,
        *get_fast_h264_args(ffmpeg_exe),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        str(scene_duration),
        out_1080p_mp4,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_1080p_mp4


def write_ass_overlay(
    ass_path: str,
    total_duration: int,
    brand_left: str,
    brand_right: str,
    subtitles: list[dict],
    chapter_badges: list[dict] = None,
    kpi_callouts: list[dict] = None,
    aspect_ratio: str = "16:9",
):
    def fmt_ts(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    is_9x16 = aspect_ratio == "9:16"
    play_w = 1080 if is_9x16 else 1920
    play_h = 1920 if is_9x16 else 1080
    bar_h = 8 if is_9x16 else 6

    if is_9x16:
        styles = [
            "Style: TopBadge,Segoe UI,25,&H0062FFDF,&H000000FF,&H50181A15,&HC8181A15,-1,0,0,0,100,100,2.0,0,3,14,0,8,48,48,78,1",
            "Style: TopRight,Segoe UI,21,&H00E5F3EE,&H000000FF,&H50181A15,&HC8181A15,-1,0,0,0,100,100,1.5,0,3,12,0,8,48,48,138,1",
            "Style: KpiPill,Segoe UI,22,&H0062FFDF,&H000000FF,&H40181A15,&HD8141A17,-1,0,0,0,100,100,1.0,0,3,12,0,2,48,48,310,1",
            "Style: SubBox,Segoe UI,34,&H00FFFFFF,&H000000FF,&H00181A15,&HD8101613,-1,0,0,0,100,100,0.2,0,3,18,0,2,54,54,150,1",
        ]
    else:
        styles = [
            "Style: TopBadge,Segoe UI,23,&H0062FFDF,&H000000FF,&H50181A15,&HC0181A15,-1,0,0,0,100,100,2.5,0,3,13,0,7,56,56,36,1",
            "Style: TopRight,Segoe UI,20,&H00E5F3EE,&H000000FF,&H50181A15,&HC0181A15,-1,0,0,0,100,100,2.0,0,3,12,0,9,56,56,38,1",
            "Style: KpiPill,Segoe UI,22,&H0062FFDF,&H000000FF,&H40181A15,&HD0141A17,-1,0,0,0,100,100,1.5,0,3,12,0,1,56,56,142,1",
            "Style: SubBox,Segoe UI,36,&H00FFFFFF,&H000000FF,&H00181A15,&HD4101613,-1,0,0,0,100,100,0.3,0,3,18,0,2,90,90,48,1",
        ]

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_w}",
        f"PlayResY: {play_h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "Style: ProgTrack,Segoe UI,20,&H90222A26,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "Style: ProgFill,Segoe UI,20,&H0062FFDF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 1,0:00:00.00,{fmt_ts(total_duration)},ProgTrack,,0,0,0,,{{\\pos(0,0)\\p1}}m 0 0 l {play_w} 0 l {play_w} {bar_h} l 0 {bar_h}{{\\p0}}",
    ]

    for sec_i in range(total_duration):
        w = int(round(play_w * ((sec_i + 1) / total_duration)))
        lines.append(
            f"Dialogue: 2,{fmt_ts(sec_i)},{fmt_ts(sec_i + 1)},ProgFill,,0,0,0,,{{\\pos(0,0)\\p1}}m 0 0 l {w} 0 l {w} {bar_h} l 0 {bar_h}{{\\p0}}"
        )

    if brand_left:
        lines.append(
            f"Dialogue: 2,0:00:00.00,{fmt_ts(total_duration)},TopBadge,,0,0,0,,{{\\fad(300,300)}}● {sanitize_ass_text(brand_left)}"
        )
    if chapter_badges:
        for cb in chapter_badges:
            lines.append(
                f"Dialogue: 2,{fmt_ts(cb['start'])},{fmt_ts(cb['end'])},TopRight,,0,0,0,,{{\\fad(250,250)}}{sanitize_ass_text(cb['text'])}"
            )
    elif brand_right:
        lines.append(
            f"Dialogue: 2,0:00:00.00,{fmt_ts(total_duration)},TopRight,,0,0,0,,{{\\fad(300,300)}}{sanitize_ass_text(brand_right)}"
        )

    if kpi_callouts:
        for kp in kpi_callouts:
            lines.append(
                f"Dialogue: 3,{fmt_ts(kp['start'])},{fmt_ts(kp['end'])},KpiPill,,0,0,0,,{{\\fad(220,220)}}{sanitize_ass_text(kp['text'])}"
            )

    for sub in subtitles:
        clean_sub = sanitize_ass_text(sub["text"])
        lines.append(
            f"Dialogue: 4,{fmt_ts(sub['start'])},{fmt_ts(sub['end'])},SubBox,,0,0,0,,{{\\fad(150,150)}}{clean_sub}"
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def master_seamless_commercial(
    ffmpeg_exe: str,
    scene_mp4s: list[str],
    vo_wavs: list[str],
    vo_starts: list[float],
    bgm_wav: str,
    ass_path: str,
    output_mp4: str,
    scene_duration: int = 10,
    aspect_ratio: str = "16:9",
    already_upscaled_16x9: bool = False,
):
    """
    Masters 1 to N 10s scenes into a seamless 1080p Full HD commercial (16:9 or 9:16):
    - When `already_upscaled_16x9=True`, reuses pre-upscaled 1080p clips for 4x faster rendering.
    - When `aspect_ratio='9:16'`, uses fast low-res boxblur (270x480 -> 1080x1920) for 8x faster vertical rendering.
    - Applies Spectral Voiceover Carve (1kHz & 1.65kHz) + Sidechain Auto-Ducking + 0.95 True-Peak Limiter.
    """
    n = len(scene_mp4s)
    total_dur = n * scene_duration
    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")

    inputs = []
    for mp4 in scene_mp4s:
        inputs.extend(["-i", mp4])
    inputs.extend(["-i", bgm_wav])
    for vw in vo_wavs:
        inputs.extend(["-i", vw])

    per_scene_filters = []
    concat_pairs = []
    for i in range(n):
        fade_out_st = max(0.5, scene_duration - 0.35)
        if aspect_ratio == "16:9":
            if already_upscaled_16x9:
                per_scene_filters.append(f"[{i}:v]null[sv{i}];[{i}:a]anull[sa{i}];")
            else:
                per_scene_filters.append(
                    f"[{i}:v]fps=24,scale=1920:1080:flags=lanczos+accurate_rnd+full_chroma_int,"
                    f"cas=0.45,eq=contrast=1.03:saturation=1.05,format=yuv420p,"
                    f"fade=t=in:st=0:d=0.18,fade=t=out:st={scene_duration - 0.18:.2f}:d=0.18[sv{i}];"
                )
                per_scene_filters.append(
                    f"[{i}:a]aresample=48000,afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_st:.2f}:d=0.35[sa{i}];"
                )
        else:
            # 8x faster 9:16 blur: downscale background to 270x480 before boxblur, then scale back to 1080x1920
            per_scene_filters.append(
                f"[{i}:v]fps=24,split=2[vbg{i}][vfg{i}];"
                f"[vbg{i}]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
                f"boxblur=10:2,scale=1080:1920:flags=bilinear,eq=brightness=-0.22:contrast=1.08:saturation=1.15[bg{i}];"
                f"[vfg{i}]scale=-2:1280:flags=lanczos+accurate_rnd+full_chroma_int,"
                f"crop=1080:1280:x='(in_w-1080)*0.32':y=0,"
                f"drawbox=x=0:y=0:w=1080:h=4:color=#DFFF62@0.85:t=fill,"
                f"drawbox=x=0:y=1276:w=1080:h=4:color=#DFFF62@0.85:t=fill[fg{i}];"
                f"[bg{i}][fg{i}]overlay=0:260,format=yuv420p,"
                f"fade=t=in:st=0:d=0.18,fade=t=out:st={scene_duration - 0.18:.2f}:d=0.18[sv{i}];"
            )
            per_scene_filters.append(f"[{i}:a]anull[sa{i}];" if already_upscaled_16x9 else f"[{i}:a]aresample=48000,afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_st:.2f}:d=0.35[sa{i}];")
        concat_pairs.append(f"[sv{i}][sa{i}]")

    v_and_bed = (
        "".join(per_scene_filters)
        + f"{''.join(concat_pairs)}concat=n={n}:v=1:a=1[vcat][omni_a];"
        f"[vcat]ass='{ass_escaped}'[vout];"
        f"[omni_a]volume=0.26[oa];"
        f"[{n}:a]volume=0.56,aresample=48000[bgm_a];"
        f"[oa][bgm_a]amix=inputs=2:duration=first:normalize=0,"
        f"equalizer=f=1000:t=q:w=1.5:g=-3.5,"
        f"equalizer=f=1650:t=q:w=1.6:g=-5.5,"
        f"afade=t=in:st=0:d=0.6,afade=t=out:st={max(0.5, total_dur - 1.5):.1f}:d=1.5[music_bed];"
    )

    if vo_wavs:
        vo_delays = []
        vo_labels = []
        for idx, st in enumerate(vo_starts):
            ms = int(round(st * 1000))
            lbl = f"vo{idx}"
            vo_delays.append(f"[{n + 1 + idx}:a]adelay={ms}|{ms}[{lbl}];")
            vo_labels.append(f"[{lbl}]")
        vo_mix_str = (
            "".join(vo_delays)
            + f"{''.join(vo_labels)}amix=inputs={len(vo_wavs)}:normalize=0,volume=1.68,apad=whole_dur={total_dur}[vo_full];"
            "[vo_full]asplit=2[vo_sc][vo_mix];"
            "[music_bed][vo_sc]sidechaincompress=threshold=0.015:ratio=6:attack=20:release=580:makeup=1.0[bed_ducked];"
            "[bed_ducked][vo_mix]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.95:attack=5:release=50[aout]"
        )
        filter_complex = v_and_bed + vo_mix_str
    else:
        filter_complex = v_and_bed + "[music_bed]alimiter=limit=0.95:attack=5:release=50[aout]"

    os.makedirs(os.path.dirname(os.path.abspath(output_mp4)), exist_ok=True)
    cmd = [
        ffmpeg_exe,
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        *get_fast_h264_args(ffmpeg_exe),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        str(total_dur),
        "-movflags",
        "+faststart",
        output_mp4,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def build_storyboard_contact_sheet(
    ffmpeg_exe: str,
    master_16x9_mp4: str,
    out_png: str,
    num_scenes: int = 6,
    scene_duration: int = 10,
    title_text: str = "GEMINI OMNI 1.1 FLASH  —  CINEMATIC STORYBOARD (1080P FULL HD)",
):
    frame_pngs = []
    work_dir = os.path.dirname(os.path.abspath(out_png))
    for i in range(num_scenes):
        t_sec = i * scene_duration + (scene_duration * 0.52)
        fp = os.path.join(work_dir, f"_sb_frame_{i+1}.png")
        subprocess.run(
            [ffmpeg_exe, "-y", "-ss", f"{t_sec:.2f}", "-i", master_16x9_mp4, "-frames:v", "1", fp],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        frame_pngs.append(fp)

    inputs = []
    for fp in frame_pngs:
        inputs.extend(["-i", fp])

    if num_scenes >= 6:
        fc = (
            "[0:v]scale=620:348[f0];[1:v]scale=620:348[f1];[2:v]scale=620:348[f2];"
            "[3:v]scale=620:348[f3];[4:v]scale=620:348[f4];[5:v]scale=620:348[f5];"
            "[f0][f1][f2][f3][f4][f5]xstack=inputs=6:layout=0_0|630_0|1260_0|0_360|630_360|1260_360:fill=#0B1410,"
            "pad=1920:820:20:88:color=#0B1410,"
            f"drawtext=fontfile='C\\:/Windows/Fonts/segoeuib.ttf':text='{title_text}':x=40:y=28:fontsize=26:fontcolor=#DFFF62[out]"
        )
    else:
        fc = (
            "[0:v]scale=620:348[f0];[1:v]scale=620:348[f1];[2:v]scale=620:348[f2];"
            "[f0][f1][f2]xstack=inputs=3:layout=0_0|630_0|1260_0:fill=#0B1410,"
            "pad=1920:460:20:88:color=#0B1410,"
            f"drawtext=fontfile='C\\:/Windows/Fonts/segoeuib.ttf':text='{title_text}':x=40:y=28:fontsize=26:fontcolor=#DFFF62[out]"
        )
    subprocess.run(
        [ffmpeg_exe, "-y", *inputs, "-filter_complex", fc, "-map", "[out]", "-frames:v", "1", out_png],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for fp in frame_pngs:
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception:
                pass
    log(f"[Storyboard] Saved contact sheet -> {out_png}")


def build_overlays_from_scenes(scene_list: list[dict], vo_durations: list[float], scene_duration: int = 10):
    subtitles = []
    chapter_badges = []
    kpi_callouts = []
    vo_starts = []
    for i, sc in enumerate(scene_list):
        base_t = i * float(scene_duration)
        vdur = vo_durations[i]
        vo_st = base_t + 0.30
        vo_starts.append(vo_st)
        mid_t = vo_st + (vdur * 0.49)
        end_t = min(base_t + scene_duration - 0.25, vo_st + vdur + 0.35)

        if sc.get("hud_right"):
            chapter_badges.append({"start": base_t, "end": base_t + scene_duration, "text": sc["hud_right"]})
        if sc.get("kpi_badge"):
            kpi_callouts.append({"start": base_t + 0.35, "end": base_t + scene_duration - 0.45, "text": sc["kpi_badge"]})
        if sc.get("sub_1") and sc.get("sub_2"):
            subtitles.append({"start": vo_st, "end": mid_t, "text": sc["sub_1"]})
            subtitles.append({"start": mid_t, "end": end_t, "text": sc["sub_2"]})
        elif sc.get("vo_text"):
            subtitles.append({"start": vo_st, "end": end_t, "text": f"“{sc['vo_text']}”"})
    return vo_starts, subtitles, chapter_badges, kpi_callouts


def main():
    parser = argparse.ArgumentParser(
        description="End-to-End Gemini Omni 1.1 Flash + Lyria RealTime Music + Single-Pass 1-Take Gemini 3.8 TTS Commercial Producer (10s/30s/60s)"
    )
    parser.add_argument("--prompt", help="Visual scene prompt for single-scene mode")
    parser.add_argument("--storyboard-json", help="Path to multi-scene storyboard JSON for 30s/60s TVC production")
    parser.add_argument("--vo-text", action="append", dest="vo_texts", help="Voiceover line(s) in Vietnamese/English")
    parser.add_argument(
        "--vo-style",
        default="deep, authoritative, warm cinematic Vietnamese commercial narrator with smooth rhythmic pacing",
        help="Speech style instruction for Gemini 3.8 Flash TTS",
    )
    parser.add_argument("--vo-voice", default="Charon", help="Gemini TTS voice (e.g. Charon, Puck, Aoede, Fenrir, Kore)")
    parser.add_argument(
        "--bgm-prompt",
        default="Cinematic modern electronic luxury commercial background music, driving bass and warm pads",
        help="Music generation prompt for Google Lyria RealTime",
    )
    parser.add_argument("--bpm", type=int, default=116, help="BGM Tempo in BPM (default: 116)")
    parser.add_argument("--scale", default="MINOR", help="Musical scale for Lyria (MAJOR, MINOR, PENTATONIC)")
    parser.add_argument("--brand-left", default="OMNI 1.1 FLASH  ×  VID.NEW STUDIO", help="Top-left HUD badge text")
    parser.add_argument("--brand-right", default="1080P FULL HD MASTER", help="Top-right HUD badge text")
    parser.add_argument("--duration", type=int, default=10, help="Per-scene duration in seconds (default: 10)")
    parser.add_argument("--product-lock", help="Optional 5-Point Product Blueprint Lock")
    parser.add_argument("--subject-lock", help="Optional Subject Identity Lock")
    parser.add_argument("--image", "-i", action="append", dest="images", help="Reference/Ingredient image(s) for StructuredImage (Node[11])")
    parser.add_argument("--prior-node-json", action="append", dest="prior_nodes", help="Prior StructuredContentNode JSONs")
    parser.add_argument("--output", "-o", required=True, help="Final mastered 1080p 16:9 MP4 path")
    parser.add_argument("--ads-output", help="Optional 30s Ads Cut 16:9 MP4 output path (when running 60s storyboard)")
    parser.add_argument("--vertical-output", help="Optional 9:16 Vertical Reels/TikTok 1080x1920 MP4 output path")
    parser.add_argument("--storyboard-png", help="Optional Storyboard Contact Sheet PNG output path")
    args = parser.parse_args()

    out_abs = os.path.abspath(args.output)
    work_dir = os.path.join(os.path.dirname(out_abs), "_work_omni")
    os.makedirs(work_dir, exist_ok=True)
    ffmpeg_exe = get_ffmpeg_exe()
    refresh_session_from_cdp()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

    if not args.prompt and not args.storyboard_json:
        parser.error("Provide either --prompt (single scene) or --storyboard-json (multi-scene 30s/60s).")

    # MULTI-SCENE MODE (--storyboard-json)
    if args.storyboard_json:
        with open(args.storyboard_json, "r", encoding="utf-8") as f:
            sb = json.load(f)
        scenes = sb.get("scenes", [])
        subject_lock = sb.get("subject_lock") or args.subject_lock
        product_lock = sb.get("product_lock") or args.product_lock
        brand_left = sb.get("brand_left") or args.brand_left
        vo_voice = sb.get("voice") or args.vo_voice
        vo_style = sb.get("master_style") or args.vo_style
        scene_dur = int(sb.get("scene_duration", args.duration or 10))
        total_dur = len(scenes) * scene_dur

        vo_texts = [sc.get("vo_text", "") for sc in scenes]
        vo_wavs, vo_durations = [], [float(scene_dur - 1.0)] * len(scenes)
        if any(vo_texts) and api_key:
            vo_wavs, vo_durations = synthesize_single_pass_1take_vo(
                ffmpeg_exe=ffmpeg_exe,
                api_key=api_key,
                vo_texts=vo_texts,
                scene_durations=[float(scene_dur)] * len(scenes),
                work_dir=work_dir,
                style=vo_style,
                voice=vo_voice,
            )

        raw_scene_mp4s = []
        upscaled_scene_mp4s = []
        node_jsons = []

        for i, sc in enumerate(scenes):
            slug = sc.get("slug", f"scene_{i+1:02d}")
            raw_mp4 = os.path.join(work_dir, f"{slug}_raw720p.mp4")
            up_mp4 = os.path.join(work_dir, f"{slug}_1080p_cached.mp4")
            node_json = os.path.join(work_dir, f"{slug}_node.json")

            if not (os.path.exists(raw_mp4) and os.path.getsize(raw_mp4) > 400_000 and os.path.exists(node_json)):
                prior_files = [node_jsons[0]] if len(node_jsons) >= 1 else []
                formatted_prompt = format_editing_prompt(
                    prompt=sc["prompt"],
                    mode="chain" if prior_files else "generate",
                    keep_rest=False,
                    has_source_video=False,
                    num_ref_images=1 if sc.get("image") else 0,
                    num_ref_videos=0,
                    num_prior_nodes=len(prior_files),
                    product_lock=product_lock,
                    subject_lock=subject_lock,
                )
                for attempt in range(3):
                    try:
                        res_v = run_vids_rpc_edit_or_generate(
                            prompt_text=formatted_prompt,
                            output_path=raw_mp4,
                            prior_node_files=prior_files,
                            image_files=[sc["image"]] if sc.get("image") else None,
                            duration_sec=scene_dur,
                            save_response_json=node_json,
                            upscale_1080p=False,
                        )
                        raw_mp4 = res_v["output_path"]
                        node_json = res_v["node_json_path"]
                        break
                    except Exception as e:
                        log(f"  [Scene {i+1} attempt {attempt+1} warning]: {e}")
                        time.sleep(5)
                        refresh_session_from_cdp()

            raw_scene_mp4s.append(raw_mp4)
            node_jsons.append(node_json)
            upscaled_scene_mp4s.append(pre_upscale_scene_1080p(ffmpeg_exe, raw_mp4, up_mp4, scene_duration=scene_dur))

        bgm_wav = os.path.join(work_dir, f"bgm_{total_dur}s.wav")
        generate_bgm_track(
            output_wav=bgm_wav,
            duration_sec=total_dur,
            bgm_prompt=sb.get("bgm_prompt") or args.bgm_prompt,
            bpm=int(sb.get("bpm", args.bpm)),
            scale=sb.get("scale") or args.scale,
        )

        vo_starts, subtitles, chapter_badges, kpi_callouts = build_overlays_from_scenes(
            scenes, vo_durations, scene_duration=scene_dur
        )
        ass_16x9 = os.path.join(work_dir, "overlay_16x9.ass")
        write_ass_overlay(
            ass_16x9,
            total_dur,
            brand_left,
            args.brand_right,
            subtitles,
            chapter_badges=chapter_badges,
            kpi_callouts=kpi_callouts,
            aspect_ratio="16:9",
        )
        log(f"[Master 16:9] Rendering {total_dur}s 1080p Master -> {out_abs}")
        master_seamless_commercial(
            ffmpeg_exe=ffmpeg_exe,
            scene_mp4s=upscaled_scene_mp4s,
            vo_wavs=vo_wavs,
            vo_starts=vo_starts,
            bgm_wav=bgm_wav,
            ass_path=ass_16x9,
            output_mp4=out_abs,
            scene_duration=scene_dur,
            aspect_ratio="16:9",
            already_upscaled_16x9=True,
        )

        if args.ads_output and len(scenes) >= 3:
            ads_idx = [0, len(scenes) // 2 - 1, len(scenes) - 1]
            ads_scenes = [scenes[i] for i in ads_idx]
            ads_ups = [upscaled_scene_mp4s[i] for i in ads_idx]
            ads_vos = [vo_wavs[i] for i in ads_idx] if vo_wavs else []
            ads_durs = [vo_durations[i] for i in ads_idx]
            ads_starts, ads_subs, ads_chaps, ads_kpis = build_overlays_from_scenes(
                ads_scenes, ads_durs, scene_duration=scene_dur
            )
            ass_ads = os.path.join(work_dir, "overlay_ads_30s.ass")
            write_ass_overlay(
                ass_ads,
                len(ads_scenes) * scene_dur,
                brand_left,
                args.brand_right,
                ads_subs,
                chapter_badges=ads_chaps,
                kpi_callouts=ads_kpis,
                aspect_ratio="16:9",
            )
            log(f"[Master Ads 30s] Rendering 30s Ads Cut -> {args.ads_output}")
            master_seamless_commercial(
                ffmpeg_exe=ffmpeg_exe,
                scene_mp4s=ads_ups,
                vo_wavs=ads_vos,
                vo_starts=ads_starts,
                bgm_wav=bgm_wav,
                ass_path=ass_ads,
                output_mp4=os.path.abspath(args.ads_output),
                scene_duration=scene_dur,
                aspect_ratio="16:9",
                already_upscaled_16x9=True,
            )

        if args.vertical_output:
            ass_9x16 = os.path.join(work_dir, "overlay_9x16.ass")
            write_ass_overlay(
                ass_9x16,
                total_dur,
                brand_left,
                args.brand_right,
                subtitles,
                chapter_badges=chapter_badges,
                kpi_callouts=kpi_callouts,
                aspect_ratio="9:16",
            )
            log(f"[Master 9:16] Rendering {total_dur}s Vertical 1080x1920 Master -> {args.vertical_output}")
            master_seamless_commercial(
                ffmpeg_exe=ffmpeg_exe,
                scene_mp4s=upscaled_scene_mp4s,
                vo_wavs=vo_wavs,
                vo_starts=vo_starts,
                bgm_wav=bgm_wav,
                ass_path=ass_9x16,
                output_mp4=os.path.abspath(args.vertical_output),
                scene_duration=scene_dur,
                aspect_ratio="9:16",
                already_upscaled_16x9=True,
            )

        if args.storyboard_png:
            build_storyboard_contact_sheet(
                ffmpeg_exe=ffmpeg_exe,
                master_16x9_mp4=out_abs,
                out_png=os.path.abspath(args.storyboard_png),
                num_scenes=len(scenes),
                scene_duration=scene_dur,
            )

        log(f"MULTI-SCENE PRODUCTION COMPLETE! Master saved -> {out_abs}")
        return

    # SINGLE-SCENE EXECUTION
    formatted_prompt = format_editing_prompt(
        prompt=args.prompt,
        mode="chain" if args.prior_nodes else "generate",
        keep_rest=False,
        has_source_video=False,
        num_ref_images=len(args.images or []),
        num_ref_videos=0,
        num_prior_nodes=len(args.prior_nodes or []),
        product_lock=args.product_lock,
        subject_lock=args.subject_lock,
    )
    raw_video_out = os.path.join(work_dir, "scene_base.mp4")
    res_vid = run_vids_rpc_edit_or_generate(
        prompt_text=formatted_prompt,
        output_path=raw_video_out,
        prior_node_files=args.prior_nodes,
        image_files=args.images,
        duration_sec=args.duration,
        upscale_1080p=False,
    )
    source_mp4 = pre_upscale_scene_1080p(
        ffmpeg_exe, res_vid["output_path"], os.path.join(work_dir, "scene_1080p.mp4"), scene_duration=args.duration
    )

    bgm_wav = os.path.join(work_dir, "bgm_master.wav")
    generate_bgm_track(
        output_wav=bgm_wav,
        duration_sec=args.duration,
        bgm_prompt=args.bgm_prompt,
        bpm=args.bpm,
        scale=args.scale,
    )

    vo_lines = args.vo_texts or []
    vo_wavs = []
    vo_starts = []
    subtitles = []
    if vo_lines and api_key:
        full_line = " ".join(re.sub(r"\{\\[^}]+\}|\*", "", l).strip() for l in vo_lines)
        vo_wavs, vo_durs = synthesize_single_pass_1take_vo(
            ffmpeg_exe=ffmpeg_exe,
            api_key=api_key,
            vo_texts=[full_line],
            scene_durations=[float(args.duration)],
            work_dir=work_dir,
            style=args.vo_style,
            voice=args.vo_voice,
        )
        vo_starts = [0.35]
        vdur = vo_durs[0]
        if len(vo_lines) >= 2:
            mid_t = 0.35 + vdur * 0.49
            subtitles.append({"start": 0.35, "end": mid_t, "text": f"“{sanitize_ass_text(vo_lines[0])}”"})
            subtitles.append(
                {
                    "start": mid_t,
                    "end": min(args.duration - 0.15, 0.35 + vdur + 0.25),
                    "text": f"“{sanitize_ass_text(' '.join(vo_lines[1:]))}”",
                }
            )
        else:
            subtitles.append(
                {
                    "start": 0.35,
                    "end": min(args.duration - 0.15, 0.35 + vdur + 0.25),
                    "text": f"“{sanitize_ass_text(vo_lines[0])}”",
                }
            )

    ass_path = os.path.join(work_dir, "overlay.ass")
    write_ass_overlay(ass_path, args.duration, args.brand_left, args.brand_right, subtitles)
    log(f"[Master Engine] Rendering Seamless 1080p Full HD Master -> {out_abs}")
    master_seamless_commercial(
        ffmpeg_exe=ffmpeg_exe,
        scene_mp4s=[source_mp4],
        vo_wavs=vo_wavs,
        vo_starts=vo_starts,
        bgm_wav=bgm_wav,
        ass_path=ass_path,
        output_mp4=out_abs,
        scene_duration=args.duration,
        already_upscaled_16x9=True,
    )
    log(f"PRODUCTION COMPLETE! Master saved -> {out_abs} ({os.path.getsize(out_abs):,} bytes)")


if __name__ == "__main__":
    main()
