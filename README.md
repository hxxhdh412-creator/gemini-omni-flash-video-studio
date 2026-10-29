# 🎬 Gemini Omni 1.1 Flash Video Studio & Antigravity Skill (`gemini-omni-flash-api`)

Bộ mã nguồn tinh gọn và **Agent Skill (`gemini-omni-flash-api`)** chuyên dụng để sản xuất **Video Thương Mại & TVC Điện Ảnh Đa Cảnh (`10s / 30s / 60s+`, `1920x1080 Full HD`)** bằng **Gemini Omni 1.1 Flash (`gemini-omni-1.1-flash`)**, kết hợp:
- 🖼️ **Native Image Ingredients (`StructuredContentNode[11]` = `StructuredImage`)**: Đưa trực tiếp ảnh giao diện phần mềm (CRM, SaaS, App) hoặc ảnh sản phẩm thật vào khe **"Thành phần tạo" (Ingredients)** của Google Vids RPC (`DoWorkspaceOperation` / `374`) để hiển thị tự nhiên bên trong màn hình 3D / bối cảnh điện ảnh.
- 🔗 **Dual-Anchor Multi-Scene Chaining (`[Scene_1_Anchor, Prev_Scene]`)**: Giữ nguyên 100% khuôn mặt nhân vật, trang phục, bối cảnh văn phòng và thiết kế sản phẩm xuyên suốt chuỗi 6 phân cảnh (`60s`).
- 🎙️ **100% Synchronized Vietnamese AI Voiceover (`Gemini 3.8 Flash TTS`)**:
  - **Single-Model & Single-Style Lock**: Khóa cố định 1 model TTS duy nhất (`gemini-3.8-flash-lite-tts` hoặc `gemini-3.8-flash-tts`), 1 giọng đọc (`Puck`) và 1 phong cách phát thanh xuyên suốt toàn bộ video (không trộn lẫn model giữa các cảnh).
  - **Clause-Level Sync & Pure-DSP `-15.5 dBFS` RMS Normalization**: Tách từng vế câu (`clause_1`, `clause_2`), cắt khoảng lặng đầu/cuối chính xác từng mẫu âm thanh (sample-accurate), chuẩn hóa âm lượng `-15.5 dBFS` và đồng bộ phụ đề `.ass` khớp đến từng phần trăm giây.
  - **Clean Audio Bus**: Tự động ngắt kênh âm thanh nháp (`[i:a]`) của video gốc khi nhân vật đang thuyết trình để loại bỏ hoàn toàn tiếng lầm bầm nền.
- 🎵 **Google Lyria RealTime AI Music (`models/lyria-realtime-exp`)**: Tự động sáng tác nhạc nền `48kHz Stereo WAV` theo BPM và Scale (`MAJOR` / `MINOR` / `PENTATONIC`), kết hợp **Spectral Carve (`1kHz` & `1.65kHz`)** và **Sidechain Auto-Ducking**.

---

## 📂 Cấu Trúc Kho Mã Nguồn (Repository Structure)

```text
gemini-omni-flash-video-studio/
├── README.md                                     # Tài liệu kiến trúc & hướng dẫn sử dụng
├── produce.ps1                                   # CLI vận hành nhanh (switch-account, refresh, commercial, edit)
├── install-skill.ps1                             # Cài đặt 1-Click skill vào ~/.gemini/config/skills/
├── requirements.txt                              # Các thư viện Python cần thiết
├── assets_1080p/                                 # 6 ảnh giao diện CRM 1920x1080 mẫu (đã làm sạch & nâng cấp Full HD)
├── examples/
│   ├── produce_omni_ingredients_master.py        # Pipeline mẫu 60s: Nạp 6 ảnh 1080p vào Thành phần tạo (StructuredImage)
│   └── sync_vo_and_remaster_ingredients.py       # Pipeline mẫu 60s: Đồng bộ giọng đọc Single-Model + Clause Sync + Remaster
└── skills/
    └── gemini-omni-flash-api/                    # Trọn bộ Skill chuẩn Antigravity / Claude Code / Gemini CLI
        ├── SKILL.md                              # Định nghĩa Skill & hướng dẫn điều phối cho AI Agent
        ├── references/
        │   └── omni_1_1_complete_guide.md        # Cẩm nang kỹ thuật đầy đủ (SDK + Vids RPC 374 + StructuredImage + VO Sync)
        └── scripts/
            ├── upload_file.py                    # Công cụ tải file lên Gemini Files API
            ├── audio/
            │   └── lyria-recipe.py               # Engine tạo nhạc nền Google Lyria RealTime (48kHz Stereo WAV)
            └── video/
                ├── edit_video.py                 # Core Engine: SDK + Vids RPC (374) + StructuredImage (Node[11]) + Auto-CDP
                ├── generate_video.py             # Trình tạo video qua Official Google GenAI SDK
                ├── inspect_video.py              # Kiểm tra thông số kỹ thuật video qua ffprobe
                ├── prep_video.py                 # Chuẩn hóa & cắt video đầu vào (10s, 720p/1080p, 24fps)
                ├── produce_commercial.py         # Orchestrator sản xuất TVC trọn gói 1-Click
                └── vids_session.example.json     # Mẫu cấu hình phiên Google Vids (không chứa cookie thật)
```

---

## ⚡ Cài Đặt Nhanh (Quick Start)

### 1. Cài đặt thư viện Python & Skill
```powershell
pip install -r requirements.txt
.\install-skill.ps1
```

### 2. Thiết lập `GEMINI_API_KEY` & Phiên `vids.new` (Không giới hạn)
```powershell
$env:GEMINI_API_KEY = "AIzaSy..."
# Mở Chrome CDP cổng 9222 và tự động lấy DOC_ID + Cookie từ https://vids.new:
.\produce.ps1 switch-account
```

### 3. Tạo Video với Ảnh Thành Phần (`StructuredImage` / Ingredients)
```powershell
.\produce.ps1 edit "Continuous cinematic shot in a dark-emerald glass office. A Vietnamese founder gestures toward a sleek ultrawide monitor displaying the exact CRM interface from the reference image. No spoken dialogue." `
  --backend vids `
  --image "assets_1080p/asset_01_kenh_ket_noi_1080p.png" `
  --output "outputs/scene1_ingredient.mp4"
```

### 4. Chạy Pipeline 60 Giây Đầy Đủ (6 Cảnh `StructuredImage` + Giọng Đọc Đồng Bộ 100%)
```powershell
python examples/produce_omni_ingredients_master.py
python examples/sync_vo_and_remaster_ingredients.py
```

---

## 🛡️ Bảo Mật (Security & Cleanliness)
- File `vids_session.json` (chứa Cookie phiên làm việc cá nhân) và `.env` đã được khóa chặt trong `.gitignore`, tuyệt đối không đẩy lên Git.
- Chỉ cần chạy `.\produce.ps1 switch-account` hoặc `.\produce.ps1 refresh` trên máy mới để tự động khởi tạo `vids_session.json` cục bộ qua Chrome CDP (`port 9222`).
