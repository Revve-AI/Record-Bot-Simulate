# 🚀 Hướng dẫn deploy

App này dùng micro của **máy đang mở trình duyệt**? **KHÔNG.** Vì recorder
chạy bằng `sounddevice` ở phía **server**, micro được đọc từ **máy chạy
`python app.py`**. Hãy chọn cách deploy phù hợp:

| Tình huống | Cách triển khai khuyến nghị |
| --- | --- |
| Cộng tác viên ghi âm trên chính máy của họ (1 người) | **A. Chạy local** |
| Nhiều cộng tác viên, mỗi người chạy độc lập | **A. Chạy local** + đóng gói cài đặt 1-cú-bấm |
| Muốn chia sẻ tạm thời cho khách demo | **B. Gradio share link** (vẫn ghi âm bằng micro của *máy chạy app*, hữu ích cho demo) |
| Server tập trung — muốn cộng tác viên ghi âm bằng micro **của họ** | **C. Bản refactor dùng micro client** (xem mục cuối) |

---

## A. Chạy local (khuyến nghị cho cộng tác viên)

### A.1. Cài thủ công

```bash
# macOS
brew install portaudio
git clone <repo> && cd assistant_recording
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

```bash
# Ubuntu / Debian
sudo apt install -y python3-venv libportaudio2 libsndfile1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

```powershell
# Windows (PowerShell)
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Sau đó mở [http://localhost:7860](http://localhost:7860).

### A.2. Cấp quyền micro

- **macOS**: lần đầu chạy, hệ thống sẽ hỏi quyền *Microphone* cho Terminal /
  iTerm / VS Code. Phải đồng ý. Nếu lỡ từ chối, vào
  *System Settings → Privacy & Security → Microphone* để bật lại.
- **Windows**: *Settings → Privacy → Microphone* → bật cho ứng dụng terminal.
- **Linux**: thường không cần. Kiểm tra `arecord -l` thấy thiết bị là OK.

### A.3. Script khởi động 1 cú bấm

`run.sh` cho macOS / Linux:

```bash
#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
python app.py
```

`run.bat` cho Windows:

```bat
@echo off
cd /d %~dp0
if not exist .venv ( py -3.11 -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python app.py
pause
```

Đặt cả thư mục `assistant_recording/` + thư mục `vib_call_…/` cạnh nhau, gửi
cộng tác viên kèm hướng dẫn “bấm `run.sh` / `run.bat`”.

---

## B. Chạy trên server + Gradio share link

Tạm thời (link tồn tại 72h, có https public):

1. Sửa cuối `app.py`:

   ```python
   app.queue().launch(server_name="0.0.0.0", server_port=7860, share=True)
   ```

2. Chạy:

   ```bash
   python app.py
   ```

   Bạn sẽ thấy: `Running on public URL: https://xxxx.gradio.live`

> ⚠️ Micro vẫn là của **máy đang chạy `app.py`** — phù hợp khi demo, không
> phù hợp để cộng tác viên từ xa ghi âm.

---

## C. Server tập trung — micro client (cần refactor)

Nếu muốn cộng tác viên ghi âm bằng micro **của họ** qua trình duyệt, hãy thay
khối `VADRecorder` (đang dùng `sounddevice`) bằng **streaming microphone của
Gradio** + chạy VAD trên từng chunk:

```python
mic = gr.Audio(sources=["microphone"], streaming=True, type="numpy")

def stream_handler(chunk, state):
    # chunk = (sr, np.ndarray)
    # 1) resample về 16 kHz nếu cần
    # 2) feed vào VADIterator để phát hiện end-of-speech
    # 3) khi 'end' xuất hiện → finalise & lưu file
    return ...

mic.stream(stream_handler, [mic, state], [state, ...])
```

Khi đó server **không cần PortAudio**, chỉ cần GPU/CPU để chạy `silero-vad`,
và có thể deploy:

### C.1. Docker

`Dockerfile`:

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

`docker-compose.yml`:

```yaml
services:
  recorder:
    build: .
    ports:
      - "7860:7860"
    volumes:
      - ../vib_call_20260115-224538:/data/input:ro
      - ./output:/data/output
    environment:
      - DEFAULT_INPUT_DIR=/data/input
      - DEFAULT_OUTPUT_DIR=/data/output
```

Lưu ý: gỡ `sounddevice` khỏi `requirements.txt` ở chế độ này và đảm bảo
`app.py` không còn import nó. Reverse-proxy (nginx / Caddy) phải hỗ trợ
WebSocket vì Gradio dùng WS cho streaming audio.

### C.2. Hugging Face Spaces

1. Tạo Space (Gradio SDK), upload thư mục `assistant_recording/`.
2. Vẫn cần refactor sang micro client (Spaces không có micro trên server).
3. Mount dataset cho thư mục `vib_call_…`.

---

## D. Sao lưu kết quả

Mặc định kết quả nằm tại `assistant_recording/output/<tên_hội_thoại>/`. Hãy:

- Định kỳ đẩy lên S3 / GCS / Drive theo nhu cầu.
- Kèm `dialog.json` khi chia sẻ — đây là metadata duy nhất gắn audio với text.

---

## E. Kiểm tra nhanh sau khi cài

```bash
python -c "import gradio, torch, soundfile, sounddevice; \
           from silero_vad import load_silero_vad; \
           print('OK', gradio.__version__)"
```

Nếu in ra `OK <version>` là xong. Nếu lỗi `OSError: PortAudio library not found`,
quay lại mục PortAudio ở [README.md](./README.md).
