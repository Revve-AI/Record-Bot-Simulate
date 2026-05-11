# 🚀 Hướng dẫn deploy

Phiên bản hiện tại **ghi âm bằng micro của trình duyệt client** (qua
`gr.Audio(sources=["microphone"])`), nên việc deploy đơn giản hơn nhiều
phiên bản cũ — server **không cần** truy cập micro vật lý.

| Mục đích | Hình thức |
| --- | --- |
| Demo nhanh trên máy mình | **A. Chạy local** |
| Cho cộng tác viên ở xa dùng ngay | **B. Chạy local + gradio.live share link** |
| Triển khai chính thức trên server / nhiều CTV cùng dùng | **C. Docker / VM** |

---

## A. Chạy local

### A.1. Yêu cầu hệ thống

| Thành phần | Phiên bản |
| --- | --- |
| **Python** | 3.10 trở lên (Dockerfile dùng 3.11) |
| **HĐH** | macOS · Linux · Windows |
| **Trình duyệt** (cho CTV) | Chrome / Edge / Firefox / Safari mới |
| **Thư viện hệ thống** | `libsndfile` (`soundfile` cần), `ffmpeg` (Gradio dùng để decode browser audio) |

Cài thư viện hệ thống:

```bash
# macOS
brew install libsndfile ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install -y libsndfile1 ffmpeg

# Windows
# Cài ffmpeg từ https://www.gyan.dev/ffmpeg/builds/ rồi thêm vào PATH.
# libsndfile thường đã đi kèm wheel của soundfile.
```

> 💡 **Không cần PortAudio / sounddevice** ở phiên bản này — server không thu
> âm bằng device vật lý nữa, nên việc deploy headless rất dễ.

### A.2. Cài Python packages

```bash
cd assistant_recording

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

Nội dung `requirements.txt`:

```
gradio>=5.0.0
silero-vad>=5.1.2
torch>=2.1.0
numpy>=1.24.0
soundfile>=0.12.1
```

> ⏱️ Lần đầu chạy `silero-vad` sẽ tải mô hình ONNX (~2 MB) về cache nên cần
> internet ở lượt khởi động đầu.

### A.3. Chuẩn bị dữ liệu đầu vào

Đặt các file dialog & wav vào thư mục `input/`:

```
assistant_recording/
├── app.py
├── input/                                            ← thư mục đầu vào
│   ├── 2026-01-06T15-37-47-….dialog
│   ├── 2026-01-06T15-37-47-….wav
│   └── ...
└── output/                                           ← sẽ tự tạo
```

Format mỗi dòng trong `.dialog`:

```
user: <text>          <START_sample>    <END_sample>
assistant: <text>     <START_sample>    <END_sample>
```

`START_sample` / `END_sample` là vị trí mẫu (sample) tại sample rate của file
`.wav` (thường 16 kHz). Đó là khoảng cắt cho phần audio của khách hàng (user)
phát lại trong app.

**Chuẩn hoá dialog 1 lần** (viết hoa, dấu câu, tách câu, gắn tên riêng,
phát hiện đảo speaker, xoá turn rác):

```bash
python format_dialogs.py
```

### A.4. Chạy app

```bash
python app.py
```

Mặc định:

- **PORT** = 7860 → mở [http://localhost:7860](http://localhost:7860)
- **SHARE** = 1 → Gradio tự tạo public link `https://xxxxx.gradio.live` (sống
  72 giờ) để gửi cho CTV ở xa.

Tuỳ biến qua biến môi trường:

```bash
PORT=8080 SHARE=0 python app.py     # chỉ chạy local, đổi port
```

---

## B. Chạy local + gradio.live cho CTV ở xa

Đây là cách **đơn giản nhất** để cộng tác viên ở bất cứ đâu đều thao tác được:

1. Trên máy bạn (có file `input/` đầy đủ): `python app.py`
2. Trong terminal sẽ thấy 2 link:
   ```
   Running on local URL:  http://localhost:7860
   Running on public URL: https://abc123xyz.gradio.live
   ```
3. Gửi link `gradio.live` cho CTV qua chat / email.
4. CTV mở link, gõ tên, làm việc bình thường:
   - Mic dùng là **mic của máy CTV** (qua trình duyệt).
   - Lần đầu trình duyệt hỏi quyền micro → **Allow**.
5. Mọi bản ghi vẫn lưu về `output/<tên CTV>/<dialog>/` trên **máy bạn**.

> ⚠️ Lưu ý:
> - Máy bạn phải bật khi CTV làm việc.
> - Link `.gradio.live` sống **72h**, mỗi lần `python app.py` lại lấy link mới.
> - Để link cố định hơn → dùng Cloudflare Tunnel, ngrok, hoặc deploy theo
>   mục C.

---

## C. Deploy chính thức bằng Docker

### C.1. `Dockerfile` (đã có sẵn)

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 7860
ENV PORT=7860
CMD ["python", "app.py"]
```

### C.2. Build + chạy

```bash
# Build (lần đầu ~3-5 phút do tải torch CPU)
docker build -t assistant-recording .

# Chạy — mount thư mục input/ và output/ để dữ liệu nằm ngoài container
docker run --rm -p 7860:7860 \
    -v "$PWD/input:/app/input:ro" \
    -v "$PWD/output:/app/output" \
    -e SHARE=0 \
    assistant-recording
```

> 💡 Để Docker container tạo public link gradio.live, set `-e SHARE=1` (mặc
> định) và đảm bảo container có internet ra ngoài.

### C.3. `docker-compose.yml` (tuỳ chọn)

```yaml
services:
  recorder:
    build: .
    container_name: assistant-recording
    restart: unless-stopped
    ports:
      - "7860:7860"
    volumes:
      - ./input:/app/input:ro
      - ./output:/app/output
    environment:
      - SHARE=0          # 1 nếu muốn gradio.live
      - PORT=7860
```

Chạy:

```bash
docker compose up -d --build
docker compose logs -f
```

### C.4. Deploy sau reverse proxy (nginx / Caddy)

Gradio dùng WebSocket; reverse proxy phải forward đúng. Ví dụ nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:7860;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 86400;
    client_max_body_size 50M;     # cho phép upload bản ghi mic lớn
}
```

Trình duyệt yêu cầu **HTTPS** để dùng `getUserMedia` (micro). Bắt buộc bật
TLS (Let's Encrypt qua Caddy / Certbot). Localhost được trừ ngoại lệ.

### C.5. Tài nguyên đề xuất

| Thông số | Đề xuất |
| --- | --- |
| CPU | 2 vCPU (xử lý silero-vad cho từng dialog mới load) |
| RAM | 1.5 GB (torch CPU + Gradio) |
| Đĩa | ~500 MB cho image + chỗ chứa output `.wav` |

---

## D. Cấu hình & biến môi trường

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `PORT` | `7860` | Cổng Gradio server lắng nghe |
| `SHARE` | `1` | `1` → tạo public link `*.gradio.live`; `0` → chỉ chạy local |

Trong UI cũng có ô **"Cấu hình nâng cao"** (sidebar phải, accordion đóng
mặc định) cho phép đổi nhanh:

- 📂 Thư mục hội thoại đầu vào (mặc định `./input`)
- 💾 Thư mục output (mặc định `./output`)

---

## E. Cấu trúc kết quả

```
output/
└── <tên CTV>/
    └── 2026-01-09T03-32-44-871Z-room_…/
        ├── turn_00_user.wav            ← cắt từ wav gốc theo timestamp
        ├── turn_01_assistant.wav       ← do CTV ghi qua micro browser
        ├── turn_02_user.wav
        ├── ...
        ├── dialog_normalized.dialog    ← text đã chuẩn hoá
        └── dialog.json                 ← metadata đầy đủ
```

File `dialog.json` ví dụ:

```json
{
  "source_dialog": "2026-01-09T…dialog",
  "created_at": "2026-05-11T10:24:11",
  "sample_rate": 16000,
  "turns": [
    {
      "index": 0,
      "role": "user",
      "text_raw": "ừ chào em",
      "text_normalized": "Ừ chào em.",
      "audio_file": "turn_00_user.wav"
    },
    {
      "index": 1,
      "role": "assistant",
      "text_raw": "chào anh em có thể giúp gì cho anh",
      "text_normalized": "Chào anh em có thể giúp gì cho anh?",
      "audio_file": "turn_01_assistant.wav"
    }
  ]
}
```

Mỗi CTV có thư mục riêng → done-list không lẫn giữa các CTV.

---

## F. Kiểm tra nhanh sau khi cài

```bash
python -c "
import gradio, torch, soundfile
from silero_vad import load_silero_vad
print('Gradio', gradio.__version__)
print('Torch', torch.__version__)
print('OK')
"
```

Nếu in được phiên bản và `OK` là cài thành công.

---

## G. Sự cố thường gặp

| Vấn đề | Cách xử lý |
| --- | --- |
| Mở app nhưng trình duyệt không xin quyền micro | Phải truy cập qua `localhost` hoặc `https://`. Trên HTTP public, `getUserMedia` bị chặn. |
| Ghi âm xong nhưng audio rỗng | Trình duyệt chặn permission. Kiểm tra biểu tượng micro 🔒 trên thanh URL → Allow. |
| Dropdown không cuộn được | Đã fix trong CSS (`max-height: 320px; overflow-y: auto`). Refresh hard (Ctrl+Shift+R). |
| `silero-vad` lần đầu chạy không tải được model | Đảm bảo có internet ở lượt đầu, hoặc copy thủ công file `.onnx` vào `~/.cache/torch/hub/snakers4_silero-vad_master/`. |
| Gradio cảnh báo `share=True` không tải được nhị phân | Mạng bị block CDN `gradio-builds.s3...`. Dùng `SHARE=0` rồi tự lo tunneling. |
| File `.dialog` đầu vào sai/lệch role | Chạy lại `python format_dialogs.py` — script tự detect speaker đảo + chuẩn hoá text. |

---

## H. Sao lưu / chuyển dữ liệu output

Output đơn giản là thư mục `output/`. Cách dùng:

```bash
# Đẩy lên S3
aws s3 sync output/ s3://my-bucket/recordings/

# Hoặc tar gửi qua email / chat
tar czf recordings-$(date +%F).tar.gz output/
```

`dialog.json` của mỗi phiên ghi đã đủ thông tin để map text ↔ audio, không
cần thêm chú thích.
