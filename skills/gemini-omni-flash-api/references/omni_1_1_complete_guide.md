# Gemini Omni 1.1 Flash (`gemini-omni-1.1-flash`) — Complete Video Editing & Generation Reference

> **Source**: Official Google Gemini API Documentation (`gemini-api-guides/omni.md`, `gemini-api-guides/models/gemini-omni-flash.md`, GA Release August 27, 2026) + Antigravity Unlimited Google Vids RPC Bridge (`DoWorkspaceOperation` / `374`).

---

## 1. Model Specifications

| Property | Value |
| :--- | :--- |
| **Model ID (GA)** | `gemini-omni-1.1-flash` |
| **Preview Model ID** | `gemini-omni-flash-preview` (deprecated Sept 30, 2026) |
| **API Endpoint** | Interactions API (`/v1beta/interactions` via `client.interactions.create`) |
| **Context Window** | `1,048,576` tokens |
| **Supported Inputs** | Text, Image (`PNG`, `JPEG`, `WEBP`), Video (`MP4` up to `10s` for upload edit/extend) |
| **Output Video** | `3s`–`10s` per turn (`24 FPS`), up to `40s` total via multi-turn extension |
| **Output Resolutions** | `360p` (`640x360`), `720p` (`1280x720`, default), `1080p` (`1920x1080`), `4k` (`3840x2160`) |
| **Aspect Ratios** | `16:9` (Landscape), `9:16` (Portrait) |
| **Provenance** | Invisible SynthID watermark embedded in all generated frames |

---

## 2. Core Video Editing Capabilities

Gemini Omni 1.1 Flash is a native multimodal conversational video editor. Unlike traditional diffusion video models that only support text-to-video, Omni 1.1 Flash understands physics, lighting, object permanence, and temporal continuity across turns.

### 2.1 Stateful Turn-by-Turn Video Editing (`previous_interaction_id` / `StructuredContentNode`)
Generate or edit a video in Turn 1, then refine specific aspects in Turn 2, Turn 3, etc., without re-describing the entire scene:
- **Object removal / invisibility**: `"Make the violin invisible. Keep everything else the same."`
- **Outfit / accessory editing**: `"Change her dress to a sleeveless champagne-gold silk camisole with thin spaghetti straps. Keep everything else the same."`
- **Product / prop replacement**: `"Replace the bottle in her hand with the crystal perfume bottle in <IMAGE_REF_0>. Keep everything else the same."`
- **Lighting & atmosphere edits**: `"Change the lighting to dramatic golden-hour sunset sunbeams. Keep everything else the same."`
- **Material & physics transformations**: `"When the person touches the mirror, make the mirror ripple beautifully like liquid, and the person's arm turns into reflective mirror material."`
- **In-scene text modification**: `"Change the text on the storefront sign to say 'AURA D'OR PARIS'. Keep everything else the same."`
- **Style transfer**: `"Transform the style to Japanese anime. Keep everything else the same."`

#### Python SDK Example (Stateful Multi-Turn Edit)
```python
import base64
from google import genai

client = genai.Client()

# Turn 1: Initial video generation
res1 = client.interactions.create(
    model="gemini-omni-1.1-flash",
    input="A woman playing violin outdoors in a sunlit Parisian courtyard.",
    response_format={"type": "video", "resolution": "1080p", "delivery": "uri"},
)

# Turn 2: Conversational edit preserving scene, motion, and subject
res2 = client.interactions.create(
    model="gemini-omni-1.1-flash",
    previous_interaction_id=res1.id,
    input="Make the violin invisible. Keep everything else the same.",
    response_format={"type": "video", "resolution": "1080p", "delivery": "uri"},
)
```

---

### 2.2 Editing Uploaded Local Videos (`Files API` + Source/Reference Role Tags)
Upload any local video (`<= 10s`, `MP4`) via the Gemini Files API (`client.files.upload`), wait until `state == "ACTIVE"`, and pass its URI in `input`:

```python
import time
from google import genai

client = genai.Client()

video_file = client.files.upload(file="input_clip.mp4")
while video_file.state.name == "PROCESSING":
    time.sleep(5)
    video_file = client.files.get(name=video_file.name)

interaction = client.interactions.create(
    model="gemini-omni-1.1-flash",
    input=[
        {"type": "video", "uri": video_file.uri, "mime_type": "video/mp4"},
        {"type": "text", "text": "[# Sources <VIDEO_0>@Video1] Add a small black cat that jumps onto the sofa. Keep everything else the same."},
    ],
    response_format={"type": "video", "resolution": "1080p", "delivery": "uri"},
)
```

#### Editing a Video with Reference Images / Reference Videos
When editing a source video while injecting a specific product, outfit, or character likeness from reference media, declare explicit `[# Sources ...]` and `[# References ...]` tags at the start of the prompt and **omit** `task="edit"` so multimodal references are not restricted:

```text
[# Sources <VIDEO_0>@Video1] [# References <IMAGE_REF_0>@Image1] Replace the perfume bottle on the table with the crystal perfume bottle in <IMAGE_REF_0>. Keep everything else the same. Use Image1 as an object reference.
```

---

### 2.3 Audio Control During Video Editing
- **Preserve / Adapt Original Audio (Default)**: If the input video contains an audio stream, Gemini Omni 1.1 Flash preserves and adapts the existing dialogue/music/SFX to match the visual edit.
- **Regenerate All Audio From Scratch (`--strip-audio`)**: If you want a completely new soundtrack or ambient soundscape matching the new visual style, **strip the audio track (`ffmpeg -an`) before uploading**, and describe the new audio in your prompt (e.g., `"Soft dreamy luxury piano and strings background music. No spoken dialogue."`).

---

### 2.4 Seamless Video Extension (`3s–10s` per turn, up to `40s` total)
Extend an existing video by appending `3s–10s` to the end. Omni uses the last `10s` of the source video as context and slightly blends the final frames for a zero-cut transition:
- **Prompt-based extension (Recommended)**: Omit `task="extend"` and begin the prompt with `"Extend this video:"` or `"Continue the scene:"`. This allows simultaneously passing `<IMAGE_REF_0>` and `<VIDEO_REF_0>` to introduce new characters or products during the extension!
- **Timestamp reset rule**: In extension prompts, `[0-3s]` refers to the start of the **newly extended segment** (e.g., `10s–13s` of a `10s` base video).

```python
interaction = client.interactions.create(
    model="gemini-omni-1.1-flash",
    input=[
        {"type": "video", "uri": base_video.uri, "mime_type": "video/mp4"},
        {"type": "image", "uri": product_img.uri, "mime_type": "image/png"},
        {
            "type": "text",
            "text": "[# Sources <VIDEO_0>@Video1] [# References <IMAGE_REF_0>@Image1] Extend this video: the model lifts the perfume bottle shown in <IMAGE_REF_0> and sprays a fine golden mist.",
        },
    ],
    response_format={"type": "video", "resolution": "1080p", "delivery": "uri"},
)
```

---

### 2.5 First & Last Frame Interpolation & Seamless Loops
- **First Frame to Video**: `[# Sources <FIRST_FRAME>@Image1] ... Use Image1 as the starting frame.`
- **First + Last Frame Transition**: `[# Sources <FIRST_FRAME>@Image1 <LAST_FRAME>@Image2] ... Use Image1 as the starting frame and Image2 as the final frame.`
- **Seamless Looping Video**: Pass the same image twice with `[# Sources <FIRST_FRAME>@Image1 <LAST_FRAME>@Image1] ... Use this image as the first frame and the last frame.`

---

## 3. Golden Prompting Rules for Video Editing & Consistency

1. **Keep Editing Prompts Simple & Direct**:
   - **Avoid** overly verbose re-descriptions of the existing video when editing a single element.
   - **Always append**: `"Keep everything else the same."` when modifying a specific object, outfit, lighting, or background.
2. **Timecode Choreography (`[0-5s]`, `[5-10s]`)**:
   - Control exact action timing inside a 10s generation or edit:
     ```text
     [0-5s] Frontal medium close-up: the model rests her hand on the crystal bottle.
     [5-10s] Smooth camera push-in as she lifts the bottle and removes the 24k gold cap.
     ```
3. **Single Continuous Shot vs. Multi-Shot**:
   - By default, Omni may introduce cinematic cuts. To lock a single unbroken shot, explicitly include: `"Continuous, unbroken shot. No scene cuts."`
4. **Negative Constraints in Natural Language**:
   - Because `negative_prompt` is not a separate API field, embed concise negatives directly in the prompt: `"No spoken dialogue. No text overlays. No extra sleeves."`

---

## 4. Unlimited Google Vids RPC Backend (`DoWorkspaceOperation` / `374`)

When `GEMINI_API_KEY` is on the Free Tier (where `gemini-omni-1.1-flash` is restricted to Paid Tier), Antigravity can execute **unlimited Gemini Omni 1.1 Flash video generation, editing, and multi-scene chaining** via the authenticated Google Vids session (`scripts/video/vids_session.json`).

### How Multi-Node Editing & Chaining Works in Google Vids RPC
Every Omni 1.1 Flash response from `https://appsgenaiserver-pa.clients6.google.com/v1/genai/generate` returns a `StructuredContentNode` at `parsed[0][0][0][0]` containing:
- The generated video's internal Drive/Scotty asset descriptor
- The semantic scene description & audio metadata
- The signed download URL (`https://contribution-rt.usercontent.google.com/download?...`)

By passing one or more prior `StructuredContentNode` objects inside the input `children` array before the text prompt node:
```python
children = list(prior_video_nodes) + [[None, None, edit_or_continuation_prompt]]
```
Omni 1.1 Flash receives the full visual and temporal context of those prior video nodes, enabling:
1. **Direct Scene Editing / Re-shooting (Outfit, Angle, Hairpin, Prop Fix)**: Pass the reference scene node(s) (`[s2_node, s3_node]`) + a `VISUAL CONSISTENCY LOCK` prompt to regenerate or edit a scene so the subject's face, dress, accessories, and product match 100% across all clips.
2. **Multi-Scene Chaining (30s–60s+ Commercials)**: Pass `[s1_node]` into Scene 2, and `[s1_node, s2_node]` into Scene 3, locking character, product, and environment across unlimited 10-second segments.
3. **Native Image Ingredients (`StructuredContentNode[11]` = `StructuredImage`)**:
   - In Google Vids RPC (`DoWorkspaceOperation` / `374`), slot `[11]` of a `StructuredContentNode` is `StructuredImage` (`[b64_str, None, None, None, None, None, None, [None, mime_type, [width, height]]]`).
   - Passing `--image path/to/ui_or_product_1080p.png` (or `image_files=[...]` in `run_vids_rpc_edit_or_generate`) injects the full-resolution image directly into Omni 1.1 Flash's native **Thành phần (Ingredients)** slot so 3D monitors, dashboards, or products inside the scene render the exact reference image with native perspective, depth-of-field, and glass reflections.
   - **Important Rule when using `StructuredImage` in Vids RPC**: Never include literal `<IMAGE_REF_0>` or `[# References <IMAGE_REF_0>@Image1]` strings in the Vids RPC text node (otherwise Omni prints literal `0.png` on screens). `edit_video.py` automatically strips `[# References ...]` and replaces `<IMAGE_REF_N>` with `"the reference image"` whenever `StructuredImage` nodes are attached.
   - **Multi-Account Download Auth (`&authuser=<N>`)**: Always append `&authuser={session['authuser']}` and header `x-goog-authuser: {session['authuser']}` when downloading from `https://contribution-rt.usercontent.google.com/download` so non-default Google accounts (`authuser=1, 2...`) never hit `HTTP 403`.

---

## 5. 5-Layer Product & Subject Synchronization Protocol (`100% Product Invariance`)

When creating multi-scene commercial TVCs (30s–60s+), products often drift between medium human shots and macro product shots if only a generic description (e.g. `"matte-black bottle with silver X-MEN text"`) is used. Always enforce all 5 layers below (automated via `edit_video.py --product-lock ... --subject-lock ... --verify-anchor ...`):

1. **Layer 1 — 5-Point Product Blueprint Lock (`--product-lock`)**:
   Explicitly lock all 5 geometric degrees of freedom of the product in every scene prompt:
   - **Silhouette & Cap**: Exact container geometry and cap shape (e.g., `"angular octagonal matte-black bottle with a flat matte-black flip-top cap"`).
   - **Brand Typography Orientation & Coordinates**: Never leave text orientation ambiguous — specify whether the brand name is **VERTICAL** (`"vertical silver-chrome uppercase text 'X-MEN' reading bottom-to-top along the LEFT half of the front label"`) or **HORIZONTAL** (`"horizontal centered uppercase text"`).
   - **Emblem / Logo Coordinates**: Exact graphic and placement relative to the brand text (e.g., `"a crimson-red shield crest with a gold rampant lion and gold crown positioned on the middle-RIGHT half of the front face, directly beside the vertical 'X-MEN' lettering"`).
   - **Sub-label Micro-text & Spelling Lock**: Exact spelling and position of secondary text (e.g., `"two crisp horizontal white lines reading 'SHAMPOO' and '500 ml' at the bottom-right corner"`).
   - **Material & Specular Response**: Surface finish under lighting (e.g., `"anodized matte-black body with brushed silver-chrome embossed letters"`).

2. **Layer 2 — Explicit Multimodal Role Binding (`[# References <VIDEO_REF_0>@Video1 ...]`)**:
   Whenever passing prior `StructuredContentNode` objects (`--prior-node-json`), always prepend explicit `<VIDEO_REF_N>` role declarations so Omni 1.1 Flash binds `<VIDEO_REF_0>` as a strict **3D Object Reference** and **Character Identity Reference**:
   ```text
   [# References <VIDEO_REF_0>@Video1 <VIDEO_REF_1>@Video2]
   STRICT MULTIMODAL OBJECT & CHARACTER BINDING: Use <VIDEO_REF_0> as the mandatory 3D object reference for the product and identity reference for the subject. Do NOT alter, rotate, or redesign the product's label orientation, brand typography placement, emblem position, bottle silhouette, or cap geometry shown in <VIDEO_REF_0>.
   ```

3. **Layer 3 — Dual-Anchor Chaining Topology (`[Scene_1_Anchor_Node, Previous_Scene_Node]`)**:
   Always keep Scene 1 (`all_nodes[0]`) as `<VIDEO_REF_0>` in every subsequent scene (`[all_nodes[0], all_nodes[i-1]]`) so macro shots in Scene 3 or finale shots in Scene 6 never drift away from Scene 1's canonical product and character design.

4. **Layer 4 — Automated Gemini Vision Consistency Audit & Surgical Repair (`--verify-anchor`)**:
   Pass `--verify-anchor scene1_preview.png` to `edit_video.py`. After generating a scene, `edit_video.py` extracts a frame at `3.5s` and runs `gemini-2.5-flash` Vision QA against Scene 1's frame. If the product's text orientation (e.g., horizontal vs. vertical), crest position, or subject identity drifts (`score < 85`), `edit_video.py` automatically regenerates the scene with a targeted QA correction prompt.

5. **Layer 5 — Live Chrome CDP Session Auto-Refresh & Zero-Touch `vids.new` Account Switching (`refresh_session_from_cdp`)**:
   - `edit_video.py` automatically launches Chrome CDP (`port 9222`, profile `C:\Users\ASUS\.gemini\antigravity-browser-profile`) if not already running, and pulls live auto-rotated cookies (`__Secure-1PSIDTS`, `COMPASS`, `SAPISID`) while stripping Play Store `OSID`/`__Secure-OSID`, ensuring zero `401`/`403` interruptions across long multi-scene pipelines.
   - **One-Command Account Switching (`--switch-account`)**: Running `python scripts/video/edit_video.py --switch-account` opens `https://vids.new` in the CDP browser, automatically inspects `http://127.0.0.1:9222/json/list` to detect the active `docs.google.com/videos/u/<authuser>/d/<doc_id>` tab, and binds the new `doc_id`, `authuser` (`0`, `1`, `2`...), and cookies into `scripts/video/vids_session.json` with zero manual copy-pasting.

---

## 6. AI Music Generation (`Google Lyria RealTime` + `Studio Synthesizer`) & Expressive Voiceover (`Gemini 3.8 Flash TTS`)

### 6.1 Google Lyria RealTime AI Music (`models/lyria-realtime-exp`)
Generate custom 48kHz Stereo WAV background music at zero extra cost using `GEMINI_API_KEY` (Free Tier supported) via `C:\Users\ASUS\.gemini\config\skills\media-use\audio\scripts\lyria-recipe.py`:
```bash
python "C:\Users\ASUS\.gemini\config\skills\media-use\audio\scripts\lyria-recipe.py" \
  --output bgm_10s.wav \
  --duration 10 \
  --bpm 118 \
  --brightness 0.85 \
  --density 0.72 \
  --scale MINOR \
  --prompt "Cinematic futuristic synthwave electronic commercial music, deep driving bassline"
```
If offline, fall back to the deterministic WebAudio synthesizer (`compose-music.mjs --preset luxury --bpm 110 --duration 30 --markers 0,10,20`).

### 6.2 Expressive Vietnamese Voiceover (`gemini-3.8-flash-tts` / `gemini-3.8-flash-lite-tts`) & 100% Synchronization Protocol
Synthesize natural Vietnamese narration via `/v1beta/interactions` using `speech_metadata` style annotations and enforce the **3 Golden Rules of Voiceover Synchronization**:
1. **Single-Model & Single-Style Lock (`100% Uniform Timbre`)**: Lock all scenes and clauses of a video to **one single TTS model** (e.g. `gemini-3.8-flash-lite-tts` or `gemini-3.8-flash-tts`), **one single voice** (`Puck`), and **one identical `MASTER_STYLE_PROMPT`**. Never mix `3.8-flash-tts`, `3.8-flash-lite-tts`, and `2.5-flash-preview-tts` across scenes in the same video.
2. **Sample-Accurate Clause Synthesis & Pure-DSP RMS Normalization (`-15.5 dBFS`)**: Synthesize each scene's two clauses (`clause_1` and `clause_2`), trim leading/trailing silence via 10ms windowed energy scanning, normalize active speech RMS to `-15.5 dBFS` with soft-knee limiting, and lock uniform tempo (`1.16x`) so ASS subtitles (`sub_1`, `sub_2`) match the exact spoken millisecond window.
3. **Strip Competing Raw Video Audio (`[i:a]`) on Speaking Scenes**: When Omni 1.1 Flash depicts a human character speaking or presenting, `[i:a]` often contains subtle English mumbling or competing music. Strip `[i:a]` during final mastering so only the crystal-clear 48kHz Vietnamese VO and sidechain-ducked Google Lyria RealTime BGM are heard.

### 6.3 Spectral Voiceover Carve & Sidechain Auto-Ducking (`produce_commercial.py`)
Combine Omni's native ambient/SFX track (`0.30`), Lyria RealTime BGM (`0.58`), and Gemini 3.8 TTS Voiceover (`1.65`) using:
- **Per-Scene Audio Micro-Declick (`afade=t=in:st=0:d=0.35,afade=t=out:st=9.65:d=0.35`)**: Eliminates any hard audio click or ambient jump at 10-second scene boundaries during concatenation.
- **Spectral Carve**: Peaking EQ cuts on the music bed at `1000Hz (-3.5dB)` and `1650Hz (-5.5dB)` so the music never fights vocal formants.
- **Dynamic Sidechain Ducking**: `sidechaincompress=threshold=0.015:ratio=6:attack=20:release=580` triggered by the vocal bus, followed by a `0.95` True-Peak Limiter ceiling.

```bash
python scripts/video/produce_commercial.py \
  --prompt "Continuous cinematic tracking shot..." \
  --vo-text "Giữa màn đêm rực sáng bên Cầu Rồng Đà Nẵng," \
  --vo-text "đánh thức bản lĩnh dẫn đầu trong từng chuyển động." \
  --bgm-prompt "Cinematic futuristic synthwave electronic commercial music" \
  --bpm 118 --scale MINOR \
  --output master_1080p.mp4
```

---

## 7. 7-Rule Zero-Defect Commercial Production Mindset (Mandatory QA Protocol)

Whenever producing multi-scene commercials (30s / 60s) with Gemini Omni 1.1 Flash, always enforce these 7 production rules (automatically guarded by `sanitize_visual_prompt_for_omni` and `sanitize_ass_text`):

1. **Zero Raw CSS Hex Codes in Visual Prompts**: Never write `#151a18` or `#dfff62` inside the video generation prompt—diffusion models treat `#RRGGBB` as literal text to print onto computer monitors or walls. Always use descriptive color names (`deep dark-emerald`, `glowing neon-lime`).
2. **Pure Graphical UI on Screens (No Small AI Text)**: For software/SaaS/workshop commercials showing monitors, dashboards, or laptops, instruct Omni 1.1 Flash to render **purely graphical charts, glowing wave lines, and geometric nodes with zero text or words on the screen**. Render all crisp Vietnamese/English KPI callouts and subtitles via the 1080p Vector ASS HUD layer.
3. **Strict `libass` Subtitle Override Syntax (`{\c&HBBGGRR&}`)**: Always use valid `libass` brace tags (`{\c&H62FFDF&}keyword{\c&HFFFFFF&}`, BGR byte order) or pass text through `sanitize_ass_text()`, which automatically converts `<c&H...&>`, `<color=#RRGGBB>`, or `**markdown**` into valid `libass` overrides so raw code never leaks onto the screen.
4. **Conditional Product Lock vs. Service/SaaS Lock**: Only pass `--product-lock` when a physical bottle/package exists. When `product_lock=None`, `build_product_and_subject_lock_block` locks human subject identity and keeps office desks clean of random hallucinated bottles.
5. **Dual-Anchor Chaining (`[Scene_1_Anchor, Immediate_Prior_Scene]`)**: Scene 1 is always `<VIDEO_REF_0>` across all subsequent scenes (`i >= 2` uses `[node_0, node_{i-1}]`) so facial identity, blazer/wardrobe, and studio lighting never drift over 60 seconds.
6. **Seamless Inter-Scene Visual & Audio Polish**: Apply micro-fades (`fade=d=0.18` video, `afade=d=0.35` audio) on every 10s segment before concatenation, plus a live animated 1080p Vector Progress Bar HUD (`ProgTrack` / `ProgFill`) and slide-in KPI Feature Callout Pills (`KpiPill`).
7. **Dual-Frame Vision QA Gate (`2.5s` & `7.5s`)**: Inspect both early (`2.5s`) and late (`7.5s`) frames of every scene before final delivery to confirm zero garbled screen text, zero hallucinated props, and 100% character continuity.

