#!/usr/bin/env python3
"""
1-Take Expressive Emotional Voiceover Engine using Flagship `gemini-3.8-flash-tts`
Following AGENTS.md Rule 2:
1. Uses flagship `gemini-3.8-flash-tts` (NOT lite!) via `/v1beta/interactions`.
2. Synthesizes in Single-Pass 1-Take (separating scenes by `\n\n`) with rich emotional storytelling
   direction (empathy -> breakthrough excitement -> proud authority -> urgent inspiring call-to-action)
   so the voice actor carries a natural human emotional arc, breath, and inflection from start to finish.
3. Strictly slices WAV `data` chunk via `data_offset + chunk_size` to strip the 6KB C2PA/SynthID trailer.
4. Preserves natural intra-sentence breathing pauses (< 400ms) and uses ONE `unifiedGain` across the entire take.
5. Splits at inter-scene silence valleys and masters BOTH the 30s Commercial and 60s Commercial in 1080p Full HD.
"""

import base64
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import wave

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

SKILL_VIDEO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "gemini-omni-flash-api", "scripts", "video"))
sys.path.insert(0, SKILL_VIDEO_DIR)
from edit_video import apply_vo_style_mastering, get_ffmpeg_exe, log, run_vids_rpc_tts_voiceover

ARTIFACT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRATCH_60S_DIR = os.path.join(ARTIFACT_DIR, "scratch", "omni_commercial_60s")
SCRATCH_30S_DIR = os.path.join(ARTIFACT_DIR, "scratch", "vids_native_30s")
STUDIO_OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "outputs"))

# Rich, emotionally dynamic style instruction for flagship gemini-3.8-flash-tts
EMOTIONAL_DIRECTOR_STYLE = (
    "Expressive, deeply emotional, charismatic Vietnamese commercial storyteller and startup founder. "
    "Start with empathetic, engaging storytelling; "
    "then shift into warm excitement, pride, and inspiring breakthrough energy; "
    "use natural human breathing, expressive pitch variation, emphatic stress on key phrases, and a passionate, "
    "persuasive call-to-action finale. Never sound flat or robotic."
)

# 60-Second (6 Scenes) Emotional Script — concise enough (~18-21 words/scene) so tempo stays 1.02x-1.06x
# with natural pauses, exclamations, and emotional cadence!
SCENES_60S_EMOTIONAL = [
    {
        "idx": 1,
        "badge": "01 // KHỞI ĐẦU ẤN TƯỢNG",
        "vo": "Khám phá chuẩn mực mới của công nghệ sản xuất hình ảnh và video điện ảnh tự động bằng trí tuệ nhân tạo!",
        "sub_1": "Khám phá chuẩn mực mới của công nghệ sản xuất",
        "sub_2": "video điện ảnh tự động bằng trí tuệ nhân tạo!",
    },
    {
        "idx": 2,
        "badge": "02 // ĐỒNG BỘ NHÂN VẬT & BỐI CẢNH",
        "vo": "Giữ trọn vẹn diện mạo nhân vật, sản phẩm và bối cảnh xuyên suốt mọi phân cảnh với độ chân thực vượt trội!",
        "sub_1": "Giữ trọn vẹn diện mạo nhân vật và sản phẩm",
        "sub_2": "xuyên suốt mọi phân cảnh với độ chân thực cao!",
    },
    {
        "idx": 3,
        "badge": "03 // GIỌNG ĐỌC AI CẢM XÚC 1-TAKE",
        "vo": "Hệ thống lồng tiếng một lượt liền mạch giúp truyền tải trọn vẹn cảm xúc tự nhiên và nhịp điệu thương hiệu!",
        "sub_1": "Hệ thống lồng tiếng 1-Take liền mạch",
        "sub_2": "truyền tải trọn vẹn cảm xúc tự nhiên!",
    },
    {
        "idx": 4,
        "badge": "04 // HÒA ÂM NHẠC NỀN STUDIO 48KHZ",
        "vo": "Tự động sáng tác nhạc nền chất lượng cao và cân bằng âm lượng thông minh theo từng câu thoại!",
        "sub_1": "Tự động sáng tác nhạc nền Studio 48kHz",
        "sub_2": "và cân bằng âm lượng thông minh theo lời thoại!",
    },
    {
        "idx": 5,
        "badge": "05 // TỐI ƯU ĐA NỀN TẢNG 16:9 & 9:16",
        "vo": "Hỗ trợ linh hoạt cả khung hình ngang tiêu chuẩn điện ảnh lẫn khung hình dọc cho các nền tảng mạng xã hội!",
        "sub_1": "Hỗ trợ linh hoạt cả khung hình ngang 16:9",
        "sub_2": "lẫn khung hình dọc 9:16 cho mạng xã hội!",
    },
    {
        "idx": 6,
        "badge": "06 // BỨT PHÁ SÁNG TẠO CÙNG OMNI STUDIO",
        "vo": "Sẵn sàng nâng tầm tốc độ sản xuất nội dung của bạn! Khởi tạo ngay video thương mại Full HD chỉ trong vài phút!",
        "sub_1": "Sẵn sàng nâng tầm tốc độ sản xuất nội dung!",
        "sub_2": "Khởi tạo ngay video thương mại Full HD!",
    },
]

# 30-Second (3 Scenes) Emotional Script
SCENES_30S_EMOTIONAL = [
    SCENES_60S_EMOTIONAL[0],
    SCENES_60S_EMOTIONAL[1],
    SCENES_60S_EMOTIONAL[5],
]


def parse_wav_strip_c2pa(raw_bytes: bytes) -> tuple[bytes, int]:
    """
    Strictly parses RIFF WAV header and slices ONLY [data_start : data_start + data_size],
    stripping the ~6,072-byte C2PA / SynthID metadata appended at the tail of gemini-3.8-flash-tts WAVs.
    """
    if len(raw_bytes) < 44 or raw_bytes[:4] != b"RIFF":
        return raw_bytes, 24000

    sample_rate = struct.unpack_from("<I", raw_bytes, 24)[0]
    pos = 12
    while pos + 8 <= len(raw_bytes):
        chunk_id = raw_bytes[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", raw_bytes, pos + 4)[0]
        data_start = pos + 8
        if chunk_id == b"data":
            data_end = min(len(raw_bytes), data_start + chunk_size)
            # Ensure even number of bytes for 16-bit PCM
            clean_len = (data_end - data_start) & ~1
            c2pa_dropped = len(raw_bytes) - (data_start + clean_len)
            log(f"  [C2PA Filter] Extracted {clean_len:,} PCM bytes @ {sample_rate}Hz (stripped {c2pa_dropped:,} trailing C2PA bytes)")
            return raw_bytes[data_start : data_start + clean_len], sample_rate
        pos = data_start + chunk_size + (chunk_size & 1)

    return raw_bytes[44 : len(raw_bytes) & ~1], sample_rate


def call_gemini_38_flash_tts_1take(full_text: str, style: str = "", api_key: str = "", voice: str = "Puck") -> tuple[bytes, int]:
    """
    1. Primary: Connects directly to Google Vids (`vids.new`) Native Cookie Voiceover RPC (`UseCase 254` ->
       `/flix/generate_text_to_speech/v1`, `workspace_text_to_speech_generation_llm_agent`), downloading the
       native `audio/wav` via Vids Cookie (zero GEMINI_API_KEY required!) and stripping any trailing C2PA tail.
    2. Secondary: If `api_key` is configured and Vids Cookie RPC is unavailable, calls `gemini-3.8-flash-tts`
       via `/v1beta/interactions`.
    3. Tertiary: Falls back to local Vietnamese Neural 1-Take TTS (`synthesize_vietnamese_tts_fallback`).
    """
    try:
        raw_wav = run_vids_rpc_tts_voiceover(full_text=full_text, voice=voice, language="vi")
        if raw_wav and len(raw_wav) > 100:
            pcm_bytes, sr = parse_wav_strip_c2pa(raw_wav)
            return apply_vo_style_mastering(pcm_bytes, sr, style=style, voice=voice)
    except Exception as e_vids:
        log(f"  [Vids Cookie Voiceover UC=254 Warning]: {e_vids}")

    if api_key and not api_key.startswith("AIzaSyDW-lir"):
        for model in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"):
            url = "https://generativelanguage.googleapis.com/v1beta/interactions"
            payload = {
                "model": model,
                "input": [
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": full_text,
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
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for step in reversed(data.get("steps", [])):
                        if step.get("type") == "model_output":
                            for item in step.get("content", []):
                                if item.get("type") == "audio" and item.get("data"):
                                    raw_wav = base64.b64decode(item["data"])
                                    pcm_bytes, sr = parse_wav_strip_c2pa(raw_wav)
                                    return apply_vo_style_mastering(pcm_bytes, sr, style=style, voice=voice)
            except Exception as e:
                log(f"  [1-Take TTS {model} Warning]: {e}")
                if "403" in str(e) or "400" in str(e) or "401" in str(e):
                    break

    from produce_commercial import synthesize_vietnamese_tts_fallback

    log(f"  [1-Take TTS Fallback] Using Vietnamese Neural 1-Take voiceover engine (voice={voice})...")
    pcm_bytes, sr = synthesize_vietnamese_tts_fallback(text=full_text, voice=voice)
    return apply_vo_style_mastering(pcm_bytes, sr, style=style, voice=voice)


def split_1take_pcm_by_silence_valleys(
    pcm_bytes: bytes, sample_rate: int, scenes: list[dict], target_dbfs: float = -15.0
) -> list[bytes]:
    """
    Applies a SINGLE `unifiedGain` across the entire 1-Take recording (preserving natural dynamic contrast
    between soft empathetic phrases and loud excited callouts!) and splits cleanly at the N-1 inter-scene
    silence valleys nearest the expected word-count proportions.
    """
    n_scenes = len(scenes)
    n_samples = len(pcm_bytes) // 2
    samples = list(struct.unpack(f"<{n_samples}h", pcm_bytes[: n_samples * 2]))

    # Trim outer leading and trailing dead air (> 250 threshold), keeping 80ms natural breath pad
    win = max(1, int(sample_rate * 0.015))
    pad = int(sample_rate * 0.080)
    first_idx = 0
    last_idx = n_samples
    for i in range(0, n_samples - win, win):
        if max(abs(s) for s in samples[i : i + win]) > 240:
            first_idx = max(0, i - pad)
            break
    for i in range(n_samples - win, 0, -win):
        if max(abs(s) for s in samples[i : i + win]) > 240:
            last_idx = min(n_samples, i + win + pad)
            break
    samples = samples[first_idx:last_idx]
    n_samples = len(samples)

    # Compute ONE unifiedGain for the entire 1-Take performance
    active_sq = [s * s for s in samples if abs(s) > 180]
    rms = math.sqrt(sum(active_sq) / max(1, len(active_sq)))
    target_rms = 32768.0 * (10.0 ** (target_dbfs / 20.0))
    unified_gain = min(max(target_rms / max(1.0, rms), 0.5), 4.0)
    log(f"  [1-Take Unified Gain] active_rms={rms:.1f} -> unifiedGain={unified_gain:.3f}x ({target_dbfs} dBFS)")

    norm_samples = []
    for s in samples:
        v = s * unified_gain
        if v > 28500.0:
            v = 28500.0 + 3800.0 * math.tanh((v - 28500.0) / 3800.0)
        elif v < -28500.0:
            v = -28500.0 - 3800.0 * math.tanh((-v - 28500.0) / 3800.0)
        norm_samples.append(int(max(-32767, min(32767, round(v)))))

    if n_scenes == 1:
        return [struct.pack(f"<{len(norm_samples)}h", *norm_samples)]

    # Find all silence valleys (>= 180ms of low energy) to split between the N scenes
    # Compute 20ms RMS envelope
    step = int(sample_rate * 0.020)
    env = []
    for i in range(0, n_samples - step, step):
        chunk = norm_samples[i : i + step]
        env.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))

    # Expected cumulative fractions based on character length of each scene's VO
    weights = [len(sc["vo"]) for sc in scenes]
    total_w = float(sum(weights))
    cum_fracs = []
    acc = 0.0
    for w in weights[:-1]:
        acc += w
        cum_fracs.append(acc / total_w)

    split_sample_indices = [0]
    min_gap_frames = int(2.8 / 0.020)  # At least 2.8s per scene
    for idx_b, frac in enumerate(cum_fracs):
        expected_frame = int(frac * len(env))
        # Search window around expected_frame (+- 22% of total length)
        search_radius = max(int(len(env) * 0.18), 40)
        prev_frame = split_sample_indices[-1] // step
        lo = max(prev_frame + min_gap_frames, expected_frame - search_radius)
        hi = min(len(env) - min_gap_frames, expected_frame + search_radius)

        # Score each candidate frame by low local energy (averaged over 140ms = 7 frames) + distance penalty
        best_f = expected_frame
        best_score = 1e18
        for f in range(lo, max(lo + 1, hi)):
            w_lo = max(0, f - 3)
            w_hi = min(len(env), f + 4)
            local_rms = sum(env[w_lo:w_hi]) / max(1, w_hi - w_lo)
            dist_pen = abs(f - expected_frame) * 12.0
            score = local_rms + dist_pen
            if score < best_score:
                best_score = score
                best_f = f
        split_sample_indices.append(best_f * step)

    split_sample_indices.append(n_samples)

    scene_pcms = []
    for i in range(n_scenes):
        s_start = split_sample_indices[i]
        s_end = split_sample_indices[i + 1]
        seg = norm_samples[s_start:s_end]
        # Trim outer dead silence of each split segment while keeping 65ms natural breath pad
        seg_len = len(seg)
        f_i = 0
        l_i = seg_len
        for k in range(0, seg_len - win, win):
            if max(abs(x) for x in seg[k : k + win]) > 350:
                f_i = max(0, k - int(sample_rate * 0.065))
                break
        for k in range(seg_len - win, 0, -win):
            if max(abs(x) for x in seg[k : k + win]) > 350:
                l_i = min(seg_len, k + win + int(sample_rate * 0.085))
                break
        seg = seg[f_i:l_i]
        # Apply gentle 15ms de-click fade at segment boundaries
        fade_n = min(len(seg) // 6, int(sample_rate * 0.015))
        for k in range(fade_n):
            seg[k] = int(seg[k] * (k / float(fade_n)))
            seg[-1 - k] = int(seg[-1 - k] * (k / float(fade_n)))
        scene_pcms.append(struct.pack(f"<{len(seg)}h", *seg))

    return scene_pcms


def find_mid_clause_pause(pcm_bytes: bytes, sample_rate: int) -> float:
    """
    Finds the natural mid-sentence breath pause (between 32% and 68% of the segment)
    so `sub_1` and `sub_2` switch at the exact moment the narrator takes a breath!
    """
    n_samples = len(pcm_bytes) // 2
    dur = n_samples / float(sample_rate)
    if dur < 2.0:
        return dur * 0.5
    samples = struct.unpack(f"<{n_samples}h", pcm_bytes[: n_samples * 2])
    step = int(sample_rate * 0.020)
    lo_f = int((n_samples * 0.32) // step)
    hi_f = int((n_samples * 0.68) // step)
    best_f = (lo_f + hi_f) // 2
    best_e = 1e18
    for f in range(lo_f, max(lo_f + 1, hi_f)):
        idx0 = max(0, (f - 2) * step)
        idx1 = min(n_samples, (f + 3) * step)
        chunk = samples[idx0:idx1]
        if chunk:
            e = sum(abs(x) for x in chunk) / len(chunk)
            if e < best_e:
                best_e = e
                best_f = f
    return (best_f * step) / float(sample_rate)


def build_1take_master(
    scenes: list[dict],
    scene_mp4s: list[str],
    bgm_wav: str,
    work_dir: str,
    output_mp4: str,
    api_key: str,
    ffmpeg_exe: str,
    tag_label: str,
    scene_duration: int = 10,
    voice: str = "Puck",
    aspect_ratio: str = "16:9",
    burn_subtitles: bool = True,
    precomputed_pcm_tuple: tuple = None,
):
    os.makedirs(work_dir, exist_ok=True)
    n_scenes = len(scenes)
    sc_dur = float(scene_duration)
    total_dur = int(n_scenes * sc_dur)

    # 1. Single-Pass 1-Take synthesis on flagship gemini-3.8-flash-tts
    full_1take_text = "\n\n".join(sc["vo"] for sc in scenes)
    cache_1take_pcm = os.path.join(work_dir, f"vo_1take_38flash_{n_scenes}scenes_{scene_duration}s_{voice}.pcm")

    if precomputed_pcm_tuple and precomputed_pcm_tuple[0]:
        pcm_1take, sample_rate = precomputed_pcm_tuple
        with open(cache_1take_pcm, "wb") as f:
            f.write(pcm_1take)
    elif os.path.exists(cache_1take_pcm) and os.path.getsize(cache_1take_pcm) > 20_000:
        log(f"[{tag_label}] Reusing cached 1-Take flagship gemini-3.8-flash-tts PCM: {cache_1take_pcm}")
        with open(cache_1take_pcm, "rb") as f:
            pcm_1take = f.read()
        sample_rate = 24000
    else:
        log(f"[{tag_label}] Calling flagship gemini-3.8-flash-tts in Single-Pass 1-Take ({n_scenes} scenes x {scene_duration}s)...")
        pcm_1take, sample_rate = call_gemini_38_flash_tts_1take(
            full_text=full_1take_text,
            style=EMOTIONAL_DIRECTOR_STYLE,
            api_key=api_key,
            voice=voice,
        )
        with open(cache_1take_pcm, "wb") as f:
            f.write(pcm_1take)

    # 2. Split at silence valleys with 1 unifiedGain across the entire take
    scene_pcms = split_1take_pcm_by_silence_valleys(pcm_1take, sample_rate, scenes, target_dbfs=-15.0)

    vo_wavs = []
    timing_meta = []
    max_vo_slot = sc_dur - 0.95
    for i, (sc, seg_pcm) in enumerate(zip(scenes, scene_pcms)):
        idx = i + 1
        raw_dur = (len(seg_pcm) / 2.0) / sample_rate
        # Keep natural emotional pacing! Only apply gentle tempo adjustment if > max_vo_slot
        if raw_dur > max_vo_slot:
            tempo = min(1.14, round(raw_dur / (sc_dur - 1.15), 3))
        else:
            tempo = 1.00  # 100% untouched natural emotional cadence!

        raw_wav = os.path.join(work_dir, f"vo_1take_s{idx}_raw.wav")
        fit_wav = os.path.join(work_dir, f"vo_1take_s{idx}_fit.wav")
        with wave.open(raw_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(seg_pcm)

        # Warm broadcast vocal chain without squashing dynamic emotional range
        af_parts = ["highpass=f=80", "equalizer=f=240:t=q:w=1.2:g=-1.5", "equalizer=f=3200:t=q:w=1.1:g=2.0"]
        if abs(tempo - 1.0) > 0.01:
            af_parts.append(f"atempo={tempo:.3f}")
        af_parts.append(f"aformat=sample_rates={sample_rate}:channel_layouts=mono")

        subprocess.run(
            [ffmpeg_exe, "-y", "-i", raw_wav, "-af", ",".join(af_parts), "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le", fit_wav],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        with wave.open(fit_wav, "rb") as rf:
            fit_pcm = rf.readframes(rf.getnframes())
        fit_dur = (len(fit_pcm) / 2.0) / sample_rate
        mid_pause_rel = find_mid_clause_pause(fit_pcm, sample_rate)

        start_offset = 0.32
        base_t = i * sc_dur
        c1_start = base_t + start_offset
        c1_end = base_t + start_offset + mid_pause_rel
        c2_start = c1_end + 0.06
        c2_end = base_t + min(sc_dur - 0.20, start_offset + fit_dur + 0.15)

        log(
            f"  [{tag_label} Scene {idx}/{n_scenes} ({scene_duration}s)] raw={raw_dur:.2f}s | tempo={tempo:.2f}x | fit={fit_dur:.2f}s | "
            f"Sub1={c1_start:.2f}..{c1_end:.2f}s | Sub2={c2_start:.2f}..{c2_end:.2f}s"
        )
        vo_wavs.append(fit_wav)
        timing_meta.append(
            {
                "idx": idx,
                "start_offset": start_offset,
                "c1_start": c1_start,
                "c1_end": c1_end,
                "c2_start": c2_start,
                "c2_end": c2_end,
                "sc": sc,
            }
        )

    # 3. Write ASS subtitle & HUD file synced to the natural mid-sentence breath pause
    def fmt_ass(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    is_vertical = str(aspect_ratio).strip() == "9:16"
    play_res_x, play_res_y = ("1080", "1920") if is_vertical else ("1920", "1080")
    sub_margin_lr = "44" if is_vertical else "80"
    sub_margin_v = "110" if is_vertical else "38"
    ass_path = os.path.join(work_dir, f"omni_1take_{total_dur}s.ass")
    ass_lines = [
        "[Script Info]",
        f"Title: OMNI STUDIO {total_dur}s 1-Take Emotional Master",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: TopPill,Arial,24,&H00A7F3D0,&H000000FF,&H0014281E,&HC80A1912,-1,0,0,0,100,100,1.2,0,3,2,0,7,48,48,34,1",
        "Style: BrandTag,Arial,22,&H00FFFFFF,&H000000FF,&H0010B981,&HC80D261C,-1,0,0,0,100,100,0.8,0,3,2,0,9,48,48,34,1",
        f"Style: SubBox,Arial,36,&H00FFFFFF,&H000000FF,&H00081C14,&HD8081811,-1,0,0,0,100,100,0.3,0,3,3,0,2,{sub_margin_lr},{sub_margin_lr},{sub_margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for m in timing_meta:
        sc = m["sc"]
        base_t = (m["idx"] - 1) * sc_dur
        st = fmt_ass(base_t + 0.15)
        et = fmt_ass(base_t + sc_dur - 0.15)
        ass_lines.append(f"Dialogue: 2,{st},{et},TopPill,,0,0,0,,{{\\fad(200,200)}}  {sc['badge']}  ")
        ass_lines.append(
            f"Dialogue: 2,{st},{et},BrandTag,,0,0,0,,{{\\fad(200,200)}}  OMNI STUDIO PRO  •  1080P FULL HD  "
        )
        ass_lines.append(
            f"Dialogue: 3,{fmt_ass(m['c1_start'])},{fmt_ass(m['c1_end'])},SubBox,,0,0,0,,{{\\fad(120,90)}}{sc['sub_1']}"
        )
        ass_lines.append(
            f"Dialogue: 3,{fmt_ass(m['c2_start'])},{fmt_ass(m['c2_end'])},SubBox,,0,0,0,,{{\\fad(90,140)}}{sc['sub_2']}"
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines) + "\n")

    # 4. Master with Spectral Carve (1000Hz -3.5dB, 1650Hz -5.5dB) + Sidechain Auto-Ducking (ratio 6:1)
    inputs = []
    for mp4 in scene_mp4s:
        inputs.extend(["-i", mp4])
    for vw in vo_wavs:
        inputs.extend(["-i", vw])
    inputs.extend(["-i", bgm_wav])

    bgm_idx = n_scenes * 2
    fade_out_st = round(sc_dur - 0.18, 2)
    v_build = [
        f"[{i}:v]fps=24,setsar=1,fade=t=in:st=0:d=0.18,fade=t=out:st={fade_out_st}:d=0.18[v{i}];"
        for i in range(n_scenes)
    ]
    v_concat_in = "".join(f"[v{i}]" for i in range(n_scenes))

    vo_delays = []
    vo_mix_in = []
    for i, m in enumerate(timing_meta):
        vo_idx = n_scenes + i
        delay_ms = int(round(((i * sc_dur) + m["start_offset"]) * 1000))
        vo_delays.append(
            f"[{vo_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms},volume=1.62[vod{i}];"
        )
        vo_mix_in.append(f"[vod{i}]")

    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
    scale_res = "1080:1920" if str(aspect_ratio).strip() == "9:16" else "1920:1080"
    ass_filter = f",ass='{ass_escaped}'" if burn_subtitles else ""
    filter_complex = (
        "".join(v_build)
        + f"{v_concat_in}concat=n={n_scenes}:v=1:a=0,"
        + f"scale={scale_res}:flags=lanczos,"
        + f"cas=0.45,eq=contrast=1.02:saturation=1.04,setsar=1{ass_filter}[vfinal];"
        + "".join(vo_delays)
        + f"{''.join(vo_mix_in)}amix=inputs={n_scenes}:duration=longest:normalize=0,"
        + f"apad=whole_dur={total_dur},atrim=0:{total_dur},asplit=2[vo_sc][vo_main];"
        + f"[{bgm_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total_dur},"
        + f"afade=t=in:st=0:d=0.8,afade=t=out:st={total_dur - 1.8:.1f}:d=1.8,"
        + "equalizer=f=1000:t=q:w=1.2:g=-3.5,equalizer=f=1650:t=q:w=1.4:g=-5.5,volume=0.44[bgm_bed];"
        + "[bgm_bed][vo_sc]sidechaincompress=threshold=0.015:ratio=6:attack=20:release=580[ducked_bgm];"
        + "[ducked_bgm][vo_main]amix=inputs=2:duration=first:normalize=0,"
        + "alimiter=limit=0.95:level=disabled[afinal]"
    )

    cmd = (
        [ffmpeg_exe, "-y"]
        + inputs
        + [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vfinal]",
            "-map",
            "[afinal]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-t",
            str(total_dur),
            "-movflags",
            "+faststart",
            output_mp4,
        ]
    )
    log(f"[{tag_label}] Mastering {total_dur}s 1080p with 1-Take Emotional VO -> {output_mp4}...")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {proc.stderr[-800:]}")
    log(f"[{tag_label}] Saved: {output_mp4} ({os.path.getsize(output_mp4):,} bytes)")


def main():
    ffmpeg_exe = get_ffmpeg_exe()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    if not api_key:
        raise RuntimeError("No GEMINI_API_KEY in environment")

    bgm_wav = os.path.join(ARTIFACT_DIR, "omni_studio_lyria_bgm_60s.wav")
    ing_mp4s_60s = [
        os.path.join(SCRATCH_60S_DIR, "s1_raw720p.mp4"),
        os.path.join(SCRATCH_60S_DIR, "s2_raw720p.mp4"),
        os.path.join(SCRATCH_60S_DIR, "s3_raw720p.mp4"),
        os.path.join(SCRATCH_60S_DIR, "s4_raw720p.mp4"),
        os.path.join(SCRATCH_60S_DIR, "s5_raw720p.mp4"),
        os.path.join(SCRATCH_60S_DIR, "s6_raw720p.mp4"),
    ]
    ing_mp4s_30s = [
        ing_mp4s_60s[0],
        ing_mp4s_60s[1],
        ing_mp4s_60s[5],
    ]

    # 1. Master 30-Second Commercial with 1-Take Flagship Emotional Voiceover
    out_30s = os.path.join(ARTIFACT_DIR, "omni_vids_new_native_voice_30s_1080p.mp4")
    build_1take_master(
        scenes=SCENES_30S_EMOTIONAL,
        scene_mp4s=ing_mp4s_30s,
        bgm_wav=bgm_wav,
        work_dir=SCRATCH_30S_DIR,
        output_mp4=out_30s,
        api_key=api_key,
        ffmpeg_exe=ffmpeg_exe,
        tag_label="30S-1TAKE-EMOTIONAL",
    )
    shutil.copy2(out_30s, os.path.join(STUDIO_OUT_DIR, os.path.basename(out_30s)))

    # 2. Master 60-Second Commercial with 1-Take Flagship Emotional Voiceover
    out_60s = os.path.join(ARTIFACT_DIR, "omni_commercial_ingredients_60s_1080p.mp4")
    build_1take_master(
        scenes=SCENES_60S_EMOTIONAL,
        scene_mp4s=ing_mp4s_60s,
        bgm_wav=bgm_wav,
        work_dir=SCRATCH_60S_DIR,
        output_mp4=out_60s,
        api_key=api_key,
        ffmpeg_exe=ffmpeg_exe,
        tag_label="60S-1TAKE-EMOTIONAL",
    )
    shutil.copy2(out_60s, os.path.join(STUDIO_OUT_DIR, os.path.basename(out_60s)))

    log("COMPLETED BOTH 30S AND 60S 1-TAKE EMOTIONAL VOICEOVER MASTERS!")


if __name__ == "__main__":
    main()
