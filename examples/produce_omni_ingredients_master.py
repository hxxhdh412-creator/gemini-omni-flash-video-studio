#!/usr/bin/env python3
"""
Generates the 60-Second Full HD (1920x1080, 16:9) HEBUM — VIBE CODING CRM Video
by injecting all 6 cleaned 1080p CRM UI screenshots directly into Gemini Omni 1.1 Flash's
native 'Thành phần' (Ingredients / StructuredContentNode[11] = StructuredImage) slot,
chained with Scene 1's anchor node for 100% character and studio continuity.
"""

import json
import os
import shutil
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

SKILL_VIDEO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "gemini-omni-flash-api", "scripts", "video"))
sys.path.insert(0, SKILL_VIDEO_DIR)

from edit_video import (
    DEFAULT_SESSION_PATH,
    get_ffmpeg_exe,
    load_vids_session,
    log,
    run_vids_rpc_edit_or_generate,
)

ARTIFACT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRATCH_DIR = os.path.join(ARTIFACT_DIR, "scratch", "vibe_coding_crm_omni")
ASSETS_DIR = os.path.join(ARTIFACT_DIR, "assets_1080p")
STUDIO_OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "outputs"))

INGREDIENT_SCENES = [
    {
        "idx": 1,
        "slug": "s1_ing_multichannel",
        "asset": os.path.join(ASSETS_DIR, "asset_01_kenh_ket_noi_1080p.png"),
        "prompt": (
            "Continuous unbroken cinematic shot in a sleek dark-emerald executive glass office. "
            "A charismatic 32-year-old Vietnamese male business founder in a tailored charcoal-black blazer "
            "stands on the left and gestures proudly toward a large frameless ultrawide glass monitor on the right "
            "that displays the exact HEBUM CRM multi-channel connection hub interface from the reference image. "
            "Smooth camera push-in toward the glowing CRM monitor. Moody emerald lighting, modern electronic pulse. No spoken dialogue."
        ),
    },
    {
        "idx": 2,
        "slug": "s2_ing_livechat",
        "asset": os.path.join(ASSETS_DIR, "asset_02_hop_thu_da_kenh_1080p.png"),
        "prompt": (
            "Continuous unbroken cinematic shot in the same sleek dark-emerald executive glass office. "
            "The same 32-year-old Vietnamese male founder in the charcoal-black blazer stands on the left side of the frame "
            "speaking passionately while gesturing toward a large frameless ultrawide monitor on the right "
            "that displays the exact HEBUM CRM multi-channel Live Chat inbox interface from the reference image. "
            "Smooth camera tracking movement toward the glowing Live Chat screen. Uplifting modern synthwave pulse. No spoken dialogue."
        ),
    },
    {
        "idx": 3,
        "slug": "s3_ing_customer360",
        "asset": os.path.join(ASSETS_DIR, "asset_03_ho_so_khach_hang_cong_no_1080p.png"),
        "prompt": (
            "Continuous unbroken cinematic shot in the same sleek dark-emerald executive glass office. "
            "The same 32-year-old Vietnamese male founder in the charcoal-black blazer stands on the left, nodding with satisfaction "
            "as he points toward a large frameless ultrawide glass monitor on the right "
            "that displays the exact HEBUM CRM 360-degree customer profile and credit limit dashboard from the reference image. "
            "Smooth camera push-in showcasing the crisp CRM customer profile interface on the monitor. No spoken dialogue."
        ),
    },
    {
        "idx": 4,
        "slug": "s4_ing_pipeline",
        "asset": os.path.join(ASSETS_DIR, "asset_04_pipeline_kinh_doanh_1080p.png"),
        "prompt": (
            "Continuous unbroken cinematic shot in the same sleek dark-emerald executive glass office. "
            "The same 32-year-old Vietnamese male founder in the charcoal-black blazer leads a focused strategy briefing on the left "
            "while gesturing toward a large frameless ultrawide glass display on the right "
            "that displays the exact HEBUM CRM 4-column Kanban sales pipeline interface from the reference image. "
            "Smooth camera glide toward the Kanban pipeline screen. Driving modern electronic beat. No spoken dialogue."
        ),
    },
    {
        "idx": 5,
        "slug": "s5_ing_automation",
        "asset": os.path.join(ASSETS_DIR, "asset_05_luong_tu_dong_hoa_1080p.png"),
        "prompt": (
            "Continuous unbroken cinematic shot in the same sleek dark-emerald executive glass office. "
            "The same 32-year-old Vietnamese male founder in the charcoal-black blazer stands on the left beside his desk "
            "and gestures toward a large frameless ultrawide glass monitor on the right "
            "that displays the exact HEBUM CRM automated VIP workflow builder interface from the reference image. "
            "Smooth camera pan and push-in highlighting the workflow automation screen. Inspiring futuristic synth arpeggio. No spoken dialogue."
        ),
    },
    {
        "idx": 6,
        "slug": "s6_ing_dashboard_finale",
        "asset": os.path.join(ASSETS_DIR, "asset_06_bao_cao_dashboard_1080p.png"),
        "prompt": (
            "Continuous unbroken cinematic finale shot in the same sleek dark-emerald executive glass office. "
            "The same 32-year-old Vietnamese male founder in the charcoal-black blazer smiles confidently on the left "
            "and gestures warmly toward a large frameless ultrawide glass command-center monitor on the right "
            "that displays the exact HEBUM CRM real-time business revenue and sales performance dashboard from the reference image. "
            "Triumphant slow camera push-in under golden-emerald rim lighting. Resonant orchestral electronic finale chord. No spoken dialogue."
        ),
    },
]


def master_ingredients_video(
    ffmpeg_exe: str,
    scene_mp4s: list[str],
    vo_wavs: list[str],
    bgm_wav: str,
    ass_path: str,
    output_mp4: str,
):
    inputs = []
    for mp4 in scene_mp4s:
        inputs.extend(["-i", mp4])
    for wav in vo_wavs:
        inputs.extend(["-i", wav])
    inputs.extend(["-i", bgm_wav])

    n_scenes = len(scene_mp4s)
    bgm_idx = n_scenes * 2

    v_Build = []
    a_omni_build = []
    for i in range(n_scenes):
        v_Build.append(
            f"[{i}:v]fps=24,scale=1920:1080:flags=lanczos+accurate_rnd+full_chroma_int,"
            f"cas=0.45,eq=contrast=1.02:saturation=1.04,setsar=1,"
            f"fade=t=in:st=0:d=0.18,fade=t=out:st=9.82:d=0.18[v{i}];"
        )
        a_omni_build.append(
            f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"afade=t=in:st=0:d=0.35,afade=t=out:st=9.65:d=0.35,volume=0.26[oa{i}];"
        )

    v_concat_in = "".join(f"[v{i}]" for i in range(n_scenes))
    oa_concat_in = "".join(f"[oa{i}]" for i in range(n_scenes))

    vo_delays = []
    vo_mix_in = []
    for i in range(n_scenes):
        vo_idx = n_scenes + i
        delay_ms = i * 10000 + 280
        vo_delays.append(
            f"[{vo_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms},volume=1.68[vod{i}];"
        )
        vo_mix_in.append(f"[vod{i}]")

    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")

    filter_complex = (
        "".join(v_Build)
        + f"{v_concat_in}concat=n={n_scenes}:v=1:a=0,ass='{ass_escaped}'[vfinal];"
        + "".join(a_omni_build)
        + f"{oa_concat_in}concat=n={n_scenes}:v=0:a=1[omni_bus];"
        + "".join(vo_delays)
        + f"{''.join(vo_mix_in)}amix=inputs={n_scenes}:duration=longest:normalize=0,apad=whole_dur=60,atrim=0:60,asplit=2[vo_sc][vo_main];"
        + f"[{bgm_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:60,"
        + "afade=t=in:st=0:d=0.8,afade=t=out:st=58.2:d=1.8,"
        + "equalizer=f=1000:t=q:w=1.2:g=-3.5,equalizer=f=1650:t=q:w=1.4:g=-5.5,volume=0.56[bgm_pre];"
        + "[bgm_pre][omni_bus]amix=inputs=2:duration=first:normalize=0[music_sfx_bed];"
        + "[music_sfx_bed][vo_sc]sidechaincompress=threshold=0.015:ratio=6:attack=20:release=580[ducked_bed];"
        + "[ducked_bed][vo_main]amix=inputs=2:duration=first:normalize=0,"
        + "alimiter=limit=0.95:level=disabled[afinal]"
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_mp4)), exist_ok=True)
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
            "60",
            "-movflags",
            "+faststart",
            output_mp4,
        ]
    )
    log(f"Mastering 60s 1080p Full HD Omni Ingredients Video -> {output_mp4}...")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg master failed: {proc.stderr[-800:]}")
    log(f"Mastered 1080p Full HD saved: {output_mp4} ({os.path.getsize(output_mp4):,} bytes)")


def main():
    ffmpeg_exe = get_ffmpeg_exe()
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    os.makedirs(STUDIO_OUT_DIR, exist_ok=True)

    # Seed Scene 1 from already-generated test_img_ingredient_s1.mp4 if not yet copied
    s1_seed_mp4 = os.path.join(ARTIFACT_DIR, "scratch", "test_img_ingredient_s1.mp4")
    s1_seed_json = os.path.join(ARTIFACT_DIR, "scratch", "test_img_ingredient_resp.json")
    s1_target_mp4 = os.path.join(SCRATCH_DIR, "s1_ing_multichannel_raw720p.mp4")
    s1_target_json = os.path.join(SCRATCH_DIR, "s1_ing_multichannel_node.json")
    if os.path.exists(s1_seed_mp4) and os.path.exists(s1_seed_json) and not os.path.exists(s1_target_mp4):
        shutil.copy2(s1_seed_mp4, s1_target_mp4)
        shutil.copy2(s1_seed_json, s1_target_json)
        log("Seeded Scene 1 from verified StructuredImage ingredient clip (test_img_ingredient_s1.mp4).")

    scene_mp4s = []
    node_jsons = []

    for i, sc in enumerate(INGREDIENT_SCENES):
        idx = sc["idx"]
        slug = sc["slug"]
        raw_mp4 = os.path.join(SCRATCH_DIR, f"{slug}_raw720p.mp4")
        node_json = os.path.join(SCRATCH_DIR, f"{slug}_node.json")

        if os.path.exists(raw_mp4) and os.path.getsize(raw_mp4) > 500_000 and os.path.exists(node_json):
            log(f"[Scene {idx}/6] Reusing verified Omni Ingredients clip: {raw_mp4}")
        else:
            # Chain with Scene 1 anchor node so the human founder & office match 100%, plus pass Scene i's 1080p CRM asset as StructuredImage!
            prior_files = [node_jsons[0]] if len(node_jsons) >= 1 else []
            log(
                f"[Scene {idx}/6] Generating Omni 1.1 Flash with Native Image Ingredient '{os.path.basename(sc['asset'])}' (priors={len(prior_files)})..."
            )
            gen_ok = False
            for attempt in range(3):
                try:
                    res = run_vids_rpc_edit_or_generate(
                        prompt_text=sc["prompt"],
                        output_path=raw_mp4,
                        prior_node_files=prior_files,
                        image_files=[sc["asset"]],
                        duration_sec=10,
                        save_response_json=node_json,
                        upscale_1080p=False,
                    )
                    raw_mp4 = res["output_path"]
                    node_json = res["node_json_path"]
                    gen_ok = True
                    break
                except Exception as e:
                    log(f"  [Scene {idx}/6 attempt {attempt+1} warning]: {e}")
                    time.sleep(8)
            if not gen_ok:
                raise RuntimeError(f"Failed to generate Scene {idx} with StructuredImage ingredient.")

        scene_mp4s.append(raw_mp4)
        node_jsons.append(node_json)

    vo_wavs = [os.path.join(SCRATCH_DIR, f"vo_s{i}_fit.wav") for i in range(1, 7)]
    bgm_wav = os.path.join(ARTIFACT_DIR, "hebum_vibe_coding_lyria_bgm_60s.wav")
    ass_cinema = os.path.join(SCRATCH_DIR, "hebum_vibe_coding_60s.ass")

    out_ing_mp4 = os.path.join(ARTIFACT_DIR, "hebum_vibe_coding_crm_omni_ingredients_60s_1080p.mp4")
    master_ingredients_video(
        ffmpeg_exe=ffmpeg_exe,
        scene_mp4s=scene_mp4s,
        vo_wavs=vo_wavs,
        bgm_wav=bgm_wav,
        ass_path=ass_cinema,
        output_mp4=out_ing_mp4,
    )

    studio_copy = os.path.join(STUDIO_OUT_DIR, "hebum_vibe_coding_crm_omni_ingredients_60s_1080p.mp4")
    shutil.copy2(out_ing_mp4, studio_copy)
    log(f"Copied to Studio outputs: {studio_copy}")

    # Extract 6 QA frames (at t=5.5s of each scene) to verify the native 3D monitor CRM UI in every scene
    for i in range(6):
        t_sec = i * 10 + 5.5
        qa_png = os.path.join(SCRATCH_DIR, f"qa_ing_s{i+1}.png")
        subprocess.run(
            [ffmpeg_exe, "-y", "-ss", str(t_sec), "-i", out_ing_mp4, "-frames:v", "1", qa_png],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        log(f"Extracted QA frame Scene {i+1} ({t_sec}s) -> {qa_png}")

    log("ALL 6 OMNI FLASH NATIVE IMAGE INGREDIENT SCENES & 1080P MASTER COMPLETED!")


if __name__ == "__main__":
    main()
