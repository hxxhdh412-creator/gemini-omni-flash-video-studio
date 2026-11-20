# QUY TẮC HỆ THỐNG DỰ ÁN: GEMINI OMNI 1.1 FLASH VIDEO STUDIO (`AGENTS.md`)

Tệp quy tắc này tự động áp dụng cho toàn bộ dự án trong workspace. **Từ thời điểm này, toàn bộ dự án CHỈ tập trung 100% vào việc sản xuất Video AI & TVC bằng Gemini Omni 1.1 Flash (`gemini-omni-1.1-flash`)** (loại bỏ hoàn toàn nhánh làm video bằng code giao diện React/Remotion).

---

## 🎬 1. QUY TẮC TUYỆT ĐỐI: 100% GEMINI OMNI 1.1 FLASH (`gemini-omni-1.1-flash`) (BẮT BUỘC)

1. **Mọi yêu cầu tạo video, TVC, clip quảng cáo, giới thiệu sản phẩm hoặc phần mềm đều BẮT BUỘC sử dụng `gemini-omni-1.1-flash`**:
   - Thực thi qua bộ công cụ & skill `gemini-omni-flash-api` (`omni_server.py`, `omni_cli.py`, `skills/gemini-omni-flash-api/scripts/video/edit_video.py`, `produce_commercial.py`).
   - **TUYỆT ĐỐI KHÔNG** sử dụng hay đề xuất tạo video bằng Remotion/React trong dự án này.

2. **Quy chuẩn Multi-Scene Chaining & Thành Phần Tạo (`StructuredImage`) trên Google Vids RPC (`DoWorkspaceOperation / 374`)**:
   - **Thành phần tạo (`StructuredContentNode[11]` = `StructuredImage`)**: Khi có ảnh tài nguyên (giao diện phần mềm CRM/SaaS, ảnh sản phẩm, ảnh nhân vật), luôn nạp trực tiếp ảnh `1080p` vào khe `StructuredImage` (`Node[11]`, tham số `--image` / `image_files=[...]`) để hiển thị tự nhiên bên trong màn hình 3D hoặc bối cảnh điện ảnh.
   - **Anchor Node Chaining (`prior_node_files=[s1_node]`)**: Cảnh 1 (`s1_node.json`) luôn đóng vai trò **Anchor Node (`StructuredContentNode`)** được truyền vào các cảnh tiếp theo để giữ đồng nhất 100% gương mặt nhân vật, trang phục và bối cảnh studio xuyên suốt 30s–60s+.
   - **Quy tắc Prompt & Tải Video**:
     - Không truyền thẻ SDK-only (`[# References <VIDEO_REF_0>@Video1]` hoặc `<IMAGE_REF_0>`) vào prompt text của Vids RPC (`374`).
     - Không dùng mã màu hex (`#RRGGBB`) trong prompt hình ảnh.
     - Luôn thực thi tuần tự qua `_VIDS_RPC_LOCK` và tự động gắn `&authuser=<N>` + header `x-goog-authuser` khi tải video từ `contribution-rt.usercontent.google.com/download`.

---

## 🎙️ 2. QUY CHUẨN LỒNG TIẾNG GEMINI 3.8 TTS (SINGLE-PASS 1-TAKE & C2PA FILTER — BẮT BUỘC)

Khi tạo hoặc cập nhật giọng đọc AI (Voice-Over) cho bất kỳ video `gemini-omni-1.1-flash` nào:

1. **Khóa cứng Model `gemini-3.8-flash-tts` & Cơ chế "Single-Pass 1-Take" (`examples/make_emotional_1take_vo.py`)**:
   - Luôn sử dụng model cao cấp `gemini-3.8-flash-tts` (qua endpoint `https://generativelanguage.googleapis.com/v1beta/interactions`).
   - **Không trộn lẫn** các model TTS khác nhau giữa các cảnh trong cùng 1 video.
   - Gộp toàn bộ lời thoại các cảnh vào **1 lần gọi API duy nhất** (ngăn cách mỗi cảnh bằng `\n\n`) kèm chỉ dẫn diễn xuất giàu cảm xúc (`EMOTIONAL_DIRECTOR_STYLE`), sau đó tự động tách tại các khoảng nghỉ chuyển đoạn (`Silence Valleys`) và dùng **1 hệ số âm lượng `unifiedGain` duy nhất (`-15.0 dBFS`)** cho toàn bài ở tốc độ tự nhiên (`1.00x`).

2. **Bắt buộc lọc bỏ 6KB C2PA / SynthID Metadata ở đuôi file WAV**:
   - Khi đọc PCM từ buffer WAV của `gemini-3.8-flash-tts`, **BẮT BUỘC** chỉ cắt đúng `data_start : data_start + chunk_size`, tuyệt đối không đọc tới hết file để loại bỏ hoàn toàn ~6.072 bytes C2PA/SynthID ở cuối file.

3. **Clean Audio Bus + Spectral Carve + Sidechain Auto-Ducking**:
   - Ngắt kênh âm thanh nháp (`[i:a]`) của video gốc khi nhân vật đang thuyết trình để không bị lẫn tiếng lầm bầm nền.
   - Kết hợp bộ lọc **Spectral Carve (`1000Hz -3.5dB`, `1650Hz -5.5dB`) + Sidechain Auto-Ducking (`ratio 6:1`)** trên nền nhạc **Google Lyria RealTime (`48kHz Stereo`)**.

---

## 🌐 3. QUY CHUẨN CÔNG CỤ MÁY CHỦ (`omni_server.py` & `omni_cli.py`)

- Mọi tính năng mới của hệ thống Omni 1.1 Flash đều được đồng bộ vào `omni_server.py` (REST API + Web Studio Dashboard 5 Tab + Headless 24/7 Cookie Auto-Rotator qua `RotateCookies`) và `omni_cli.py` để triển khai trực tiếp lên máy chủ GitLab (`https://gitlab.hebum.com.vn/Huu_AI/tool-omni-flash.git`) và GitHub (`https://github.com/hxxhdh412-creator/gemini-omni-flash-video-studio.git`).
