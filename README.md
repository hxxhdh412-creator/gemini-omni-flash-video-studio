# 🎬 Gemini Omni 1.1 Flash Video Studio & Antigravity Skill (`gemini-omni-flash-api`)

Bộ mã nguồn tinh gọn và **Agent Skill (`gemini-omni-flash-api`)** chuyên dụng để sản xuất **Video Thương Mại & TVC Điện Ảnh Đa Cảnh (`10s / 30s / 60s+`, `1920x1080 Full HD`)** bằng **Gemini Omni 1.1 Flash (`gemini-omni-1.1-flash`)**, kết hợp:
- 🖼️ **Native Image Ingredients (`StructuredContentNode[11]` = `StructuredImage`)**: Đưa trực tiếp ảnh nhân vật, sản phẩm hoặc giao diện phần mềm vào khe **"Thành phần tạo" (Ingredients)** của Google Vids RPC (`DoWorkspaceOperation` / `376` & `374`) để hiển thị tự nhiên bên trong bối cảnh điện ảnh.
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
├── omni_server.py                                # 🌐 HTTP Cloud Server + REST API + Web UI + 24/7 Headless Cookie Auto-Rotator
├── omni_cli.py                                   # 💻 Cross-Platform Server CLI (status, set-cookie, rotate-cookie, scene, server)
├── Dockerfile                                    # 🐳 Docker Image chuẩn cho Linux Cloud Server (Python 3.12 + FFmpeg)
├── docker-compose.yml                            # 🐳 Chạy 1-Click trên VPS / Cloud Server với Auto-Restart
├── start-server.sh                               # 🚀 Script khởi chạy trực tiếp trên Ubuntu / Debian VPS
├── README.md                                     # Tài liệu kiến trúc & hướng dẫn sử dụng
├── produce.ps1                                   # CLI vận hành nhanh trên Windows
├── install-skill.ps1                             # Cài đặt 1-Click skill vào ~/.gemini/config/skills/
├── requirements.txt                              # Các thư viện Python cần thiết
├── examples/
│   └── make_emotional_1take_vo.py                # Pipeline mẫu 30s/60s: Lồng tiếng 1-Take giàu cảm xúc (gemini-3.8-flash-tts)
└── skills/
    └── gemini-omni-flash-api/                    # Trọn bộ Skill chuẩn Antigravity / Claude Code / Gemini CLI
```

---

## 🌐 Triển Khai Lên Máy Chủ Cloud / VPS (`omni_server.py` & Docker)

### Cách 1: Chạy trực tiếp trên máy chủ Linux / Ubuntu / Claude Code Cloud
```bash
git clone https://github.com/hxxhdh412-creator/gemini-omni-flash-video-studio.git
cd gemini-omni-flash-video-studio

# Cài đặt Cookie & GEMINI_API_KEY ngay bằng CLI:
python3 omni_cli.py set-cookie \
  --cookie "SID=...; HSID=...; SSID=...; SAPISID=...; __Secure-1PSID=...; __Secure-1PSIDTS=..." \
  --doc-id "1x5CzXuOZgiBZzrRlgMjrvnTomyll0BmLws2-1zwysJc" \
  --authuser 0 \
  --api-key "AIzaSy..."

# Kiểm tra trạng thái & Xoay vòng Cookie tự động (không cần cài Chrome):
python3 omni_cli.py status
python3 omni_cli.py rotate-cookie

# Khởi chạy Web Dashboard & REST API Server trên cổng 8080:
./start-server.sh
```

### Cách 2: Chạy bằng Docker Compose (`docker compose up -d`)
```bash
export GEMINI_API_KEY="AIzaSy..."
export OMNI_COOKIE="SID=...; __Secure-1PSIDTS=..."
export OMNI_DOC_ID="1x5CzXuOZgiBZzrRlgMjrvnTomyll0BmLws2-1zwysJc"
docker compose up -d --build
```

### 🔌 Các Cổng REST API Cho AI Agent / Webhooks (`http://<server-ip>:8080`)
- **`GET  /`**: Giao diện Web Studio quản lý Cookie và tạo video trực quan trên trình duyệt.
- **`GET  /api/session/status`**: Kiểm tra tình trạng Cookie và số lần tự động xoay vòng (`RotateCookies`).
- **`POST /api/session`**: Cập nhật nóng chuỗi `cookie`, lệnh `curl`, `doc_id` hoặc `gemini_api_key` từ xa mà không cần khởi động lại server.
- **`POST /api/session/rotate`**: Kích hoạt xoay vòng Cookie tức thì qua HTTPS (`RotateCookies`).
- **`POST /api/generate-scene`**: Tạo 1 cảnh video `10s` (hỗ trợ truyền đường dẫn hoặc Base64 ảnh vào `image` để đưa vào khe **Thành phần tạo / `StructuredImage`**).
- **`POST /api/produce-commercial`**: Sản xuất trọn gói TVC đa cảnh `30s / 60s` (`1080p Full HD`) kèm giọng đọc **1-Take `gemini-3.8-flash-tts`** và nhạc nền **Google Lyria RealTime**.
- **`GET  /api/jobs/<job_id>`**: Theo dõi tiến độ render và lấy link tải file `/outputs/<filename>.mp4`.

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
.\produce.ps1 edit "Continuous cinematic shot in a sleek modern studio. The person in the reference image presents naturally with warm lighting and smooth camera push-in." `
  --backend vids `
  --image "outputs/reference_image.png" `
  --output "outputs/scene1_ingredient.mp4"
```

### 4. Chạy Pipeline Lồng Tiếng 1-Take Đồng Bộ 100%
```powershell
python examples/make_emotional_1take_vo.py
```

---

## 🛡️ Bảo Mật (Security & Cleanliness)
- File `vids_session.json` (chứa Cookie phiên làm việc cá nhân) và `.env` đã được khóa chặt trong `.gitignore`, tuyệt đối không đẩy lên Git.
- Chỉ cần chạy `.\produce.ps1 switch-account` hoặc `.\produce.ps1 refresh` trên máy mới để tự động khởi tạo `vids_session.json` cục bộ qua Chrome CDP (`port 9222`).
