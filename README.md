# 🎙️ Studio ghi âm trợ lý ảo

Ứng dụng web (Gradio) hỗ trợ cộng tác viên **thu âm các turn "Trợ lý"** trong các
cuộc hội thoại đã có sẵn. Hệ thống tự động:

- Đọc các file `.dialog` trong thư mục đầu vào (đường dẫn do người dùng chọn).
- **Tự động** phát audio khách hàng (turn `user`, cắt thẳng từ wav gốc theo
  timestamp trong dialog) → khi audio kết thúc thì nghỉ 1s rồi sang turn tiếp.
- Khi đến turn `assistant` thì **dừng chờ cộng tác viên** bấm ghi âm; CTV
  bấm nút stop để kết thúc.
- Chuẩn hoá viết hoa và dấu câu cho mọi văn bản hiển thị.
- Lưu kết quả thành các file `.wav` + `dialog.json` + `dialog_normalized.dialog`.
- **Đánh dấu hội thoại đã hoàn tất** (✅) và lần mở app sau chỉ hiện hội thoại
  chưa thu âm.

UI tiếng Việt, nút lớn, hướng dẫn rõ ràng cho người không chuyên kỹ thuật.

---

## 1. Yêu cầu hệ thống

- **Python**: 3.10 hoặc mới hơn.
- **Hệ điều hành**: macOS / Linux / Windows.
- **Micro hoạt động** (cắm tai nghe có mic được khuyến nghị) — trình duyệt
  sẽ xin quyền truy cập mic lần đầu chạy.

---

## 2. Cài đặt nhanh

```bash
cd assistant_recording

# 1) Tạo môi trường ảo
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2) Nâng cấp pip & cài thư viện
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. Chạy ứng dụng

```bash
python app.py
```

Mở trình duyệt tại địa chỉ in ra trong terminal (mặc định:
[http://localhost:7860](http://localhost:7860)).

> Nếu chạy trên server từ xa và muốn truy cập qua link tạm thời:
> sửa `share=False` thành `share=True` ở cuối `app.py` và chạy lại.

---

## 4. Hướng dẫn sử dụng (cho cộng tác viên)

App đã ghi rõ **3 bước ngay trên màn hình** — bạn không cần đọc gì thêm. Phần
dưới đây chỉ là nhắc lại cho rõ:

### 🔹 Bước 1 — Chọn 1 hội thoại rồi bấm "▶️ Bắt đầu"

Phần "**🗂️ Chọn 1 cuộc hội thoại**" liệt kê danh sách dạng:

```
⬜ Chưa thu  ·  Hội thoại #1  ·  06/01/2026 15:36
⬜ Chưa thu  ·  Hội thoại #2  ·  09/01/2026 03:32
✅ Đã xong  ·  Hội thoại #3  ·  11/01/2026 03:04   ← chỉ hiện khi bỏ tick "chỉ hiện chưa thu âm"
```

Chọn 1 hội thoại → bấm **▶️ Bắt đầu**.

### 🔹 Bước 2 — Theo từng câu, làm 1 trong 2 việc

| Câu của ai | Việc của bạn |
| --- | --- |
| 🧑 **Khách hàng** (xanh dương) | Lắng nghe audio tự phát, không cần làm gì — app sẽ tự sang câu tiếp sau ~1.5s |
| 🤖 **Trợ lý** (xanh lá) | Đến lượt bạn → **ghi âm bằng micro** (xem Bước 3) |

### 🔹 Bước 3 — Ghi âm bằng micro của trình duyệt

App dùng **micro của chính máy bạn** (qua trình duyệt). Lần đầu chạy, trình
duyệt sẽ hỏi xin quyền truy cập micro — chọn **Allow / Cho phép**.

1. Khi đến câu trợ lý, bạn thấy 1 component audio **🎤 Bấm nút mic để ghi âm**.
2. Bấm nút **🎤 record** trong component đó → trình duyệt bắt đầu thu.
3. Đọc to & rõ câu hiển thị trên màn hình. Có thể đọc lại đoạn nào nếu vấp.
4. Đọc xong → bấm nút lớn màu đỏ **🛑 KẾT THÚC GHI ÂM** ngay dưới component
   (hoặc bấm nút stop nhỏ bên trong component cũng được).
5. Bản ghi xuất hiện bên dưới để **nghe lại**:
   - 👍 Ưng → bấm **💾 Lưu & Sang câu tiếp**
   - 👎 Chưa ưng → bấm **🔄 Ghi lại**

### 🔹 Bước 4 — Khi xong tất cả câu

Khu vực giữa hiện banner **🎉 Tuyệt vời! Bạn đã ghi xong tất cả các câu**.
Bấm **📦 Hoàn tất & Xuất kết quả** — kết quả lưu vào thư mục con của `./output/`,
gồm:

- `turn_XX_user.wav` — audio khách hàng
- `turn_XX_assistant.wav` — audio do bạn ghi
- `dialog_normalized.dialog` — văn bản đã chuẩn hoá
- `dialog.json` — metadata đầy đủ

Có thể chọn ngay hội thoại tiếp theo ở phía trên để tiếp tục.

### 💡 Mẹo cho cộng tác viên

- Đeo **tai nghe có mic** sẽ cho chất lượng tốt hơn nhiều so với mic laptop.
- Đọc với tốc độ và ngữ điệu **tự nhiên** như đang nói chuyện thật, không cần đọc rập khuôn.
- Nếu lỡ đọc sai 1 chữ ở giữa câu, cứ đọc tiếp đến hết — bấm **🔄 Ghi lại** sau cũng được.
- Phòng càng yên tĩnh càng tốt; tiếng quạt / tiếng đường xa thì máy bỏ qua được.

---

## 5. Cấu trúc kết quả

```
output/
└── 2026-01-09T03-32-44-871Z-room_d0e8a538-…/
    ├── turn_00_user.wav
    ├── turn_01_assistant.wav     ← do bạn ghi
    ├── turn_02_user.wav
    ├── turn_03_assistant.wav     ← do bạn ghi
    ├── dialog_normalized.dialog
    └── dialog.json
```

Ví dụ `dialog.json`:

```json
{
  "source_dialog": "2026-01-09T….dialog",
  "created_at": "2026-05-10T10:24:11",
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

---

## 6. Mẹo & xử lý sự cố

| Vấn đề | Cách xử lý |
| --- | --- |
| Không thấy file hội thoại trong dropdown | Kiểm tra đường dẫn **Thư mục hội thoại** rồi bấm **🔄 Quét thư mục** |
| Audio khách hàng không phát | App cắt audio theo `start_sample` / `end_sample` trong file `.dialog`; turn nào thiếu timestamp sẽ không có audio. Bấm **Bỏ qua câu này** để sang turn kế. |
| Trình duyệt không nhận giọng | Kiểm tra trình duyệt đã được cấp quyền **Microphone** (icon mic trên thanh URL). Trên macOS phải cấp quyền Microphone cho trình duyệt ở *System Settings → Privacy & Security*. |

---

## 7. Tuỳ biến nhanh

Sửa các hằng số ở đầu `app.py`:

```python
SAMPLE_RATE         = 16000   # tần số lấy mẫu micro
USER_PAUSE_SEC      = 0.6     # giây nghỉ giữa các turn user
MAX_RECORDING_SEC   = 90      # giới hạn an toàn 1 lần ghi
DEFAULT_INPUT_DIR   = "./input"
DEFAULT_OUTPUT_DIR  = "./output"
```

---

## 8. Triển khai

Xem [`DEPLOY.md`](./DEPLOY.md) để biết cách deploy lên server / chia sẻ qua mạng nội bộ.
