"""Studio ghi âm trợ lý ảo — Gradio app.

Đọc các file .dialog trong thư mục đầu vào, phát audio user theo từng turn,
ghi âm phần trợ lý qua micro của trình duyệt (gr.Audio sources=microphone)
— CTV bấm nút mic để bắt đầu thu, bấm nút "🛑 KẾT THÚC GHI ÂM" lớn để dừng.

silero-vad chỉ dùng cho 2 việc:
  1) Tách audio user theo timestamps trong dialog + trim silence dài.
  2) (Không còn) Auto-stop khi im lặng — đã bỏ vì mic giờ client-side.
"""
from __future__ import annotations

import base64 as _base64
import io
import json
import urllib.parse
import os
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import gradio as gr
import numpy as np
import soundfile as sf
# sounddevice không cần nữa: ghi âm chuyển sang micro client browser qua
# gr.Audio(sources=["microphone"]) để hoạt động được với gradio.live.

import torch
from silero_vad import (
    get_speech_timestamps,
    load_silero_vad,
    read_audio,
)

# ---------- Config ----------
SAMPLE_RATE = 16000
VAD_CHUNK = 512                  # samples per VAD chunk @16 kHz
SILENCE_MS = 1500                # tự dừng khi im lặng 1.5s
USER_PAUSE_SEC = 0.6             # nghỉ ngắn sau turn user trước khi sang câu kế
MAX_RECORDING_SEC = 90           # an toàn: dừng cứng sau 90s
DEFAULT_INPUT_DIR = "./input"
DEFAULT_OUTPUT_DIR = "./output"

print("[Init] Đang tải mô hình silero-vad ...")
VAD_MODEL = load_silero_vad()
print("[Init] Đã tải xong.")

# Warm-up VAD — JIT compile graph ngay từ đầu, tránh action_load đầu tiên
# bị chậm 1-3s do silero-vad lần đầu chạy.
print("[Init] Warm-up silero-vad ...")
_t0 = time.time()
try:
    _warm = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1s silence
    _ = get_speech_timestamps(
        torch.from_numpy(_warm), VAD_MODEL, sampling_rate=SAMPLE_RATE,
        threshold=0.4, min_silence_duration_ms=400, min_speech_duration_ms=80,
    )
    print(f"[Init] Warm-up xong ({1000*(time.time()-_t0):.0f}ms)")
except Exception as _exc:
    print(f"[Init] Warm-up lỗi: {_exc}")


# ---------- Text normalization ----------
from text_utils import normalize_vietnamese_text  # noqa: E402



# ---------- Dialog parsing ----------
# Format dialog: "user: text"  hoặc  "assistant: text"
# (Tuỳ chọn) có thể thêm `\tSTART\tEND` ở cuối — sample index ở 16kHz —
# để cắt audio user chính xác. Nếu không có → fallback VAD.
_ROLE_LINE_RE = re.compile(
    r"^(user|assistant)\s*:\s*(.+?)(?:\s*\t(\d+)\s*\t(\d+))?\s*$"
)
# Format CŨ với speaker_X — giữ regex để hỗ trợ dialog chưa convert
_SPEAKER_LINE_RE = re.compile(
    r"^speaker_(\d+)\s*:\s*(.+?)(?:\s*\t(\d+)\s*\t(\d+))?\s*$"
)
SPEAKER_ROLE = {"0": "assistant", "1": "user"}


def parse_dialog_file(path: str) -> list[dict]:
    turns: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            raw_line = line.rstrip("\n\r")
            if not raw_line.strip():
                continue

            # 1) Định dạng chính: "user: text..." hoặc "assistant: text..."
            m = _ROLE_LINE_RE.match(raw_line)
            if m:
                role = m.group(1)
                text = m.group(2).strip()
                start = int(m.group(3)) if m.group(3) else None
                end = int(m.group(4)) if m.group(4) else None
                turns.append({
                    "role": role,
                    "text_raw": text,
                    "text": normalize_vietnamese_text(text),
                    "start_sample": start,
                    "end_sample": end,
                })
                continue

            # 2) Tương thích ngược: dialog cũ dùng "speaker_X:"
            m = _SPEAKER_LINE_RE.match(raw_line)
            if m:
                role = SPEAKER_ROLE.get(m.group(1), "user")
                text = m.group(2).strip()
                start = int(m.group(3)) if m.group(3) else None
                end = int(m.group(4)) if m.group(4) else None
                turns.append({
                    "role": role,
                    "text_raw": text,
                    "text": normalize_vietnamese_text(text),
                    "start_sample": start,
                    "end_sample": end,
                })
    return turns


def list_dialogs(input_dir: str) -> list[str]:
    p = Path(input_dir).expanduser()
    if not p.exists():
        return []
    return sorted(
        f.name
        for f in p.iterdir()
        if f.suffix == ".dialog" and not f.name.endswith(".dialog.mark")
    )


def sanitize_collaborator_name(name: str) -> str:
    """Chuẩn hoá tên CTV để dùng làm tên thư mục (an toàn cross-platform)."""
    name = (name or "").strip()
    if not name:
        return ""
    name = re.sub(r"[/\\:*?\"<>|]", "_", name)
    name = name.replace("..", "_")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def session_output_dir(output_dir: str, collab_name: str, dialog_name: str) -> str:
    """output/<tên CTV>/<dialog stem>/"""
    safe_name = sanitize_collaborator_name(collab_name) or "unnamed"
    return os.path.join(output_dir, safe_name, Path(dialog_name).stem)


def is_dialog_done(dialog_name: str, output_dir: str, collab_name: str = "") -> bool:
    """'Đã thu âm' khi `dialog.json` tồn tại trong thư mục của CTV này."""
    if not dialog_name:
        return False
    marker = (
        Path(session_output_dir(output_dir, collab_name, dialog_name))
        .expanduser() / "dialog.json"
    )
    return marker.exists()


def _friendly_dialog_label(idx: int, name: str, done: bool) -> str:
    """Hội thoại #N — DD/MM/YYYY HH:MM (ưu tiên dễ đọc thay vì UUID)."""
    try:
        dt = datetime.strptime(name[:19], "%Y-%m-%dT%H-%M-%S")
        when = dt.strftime("%d/%m/%Y  %H:%M")
    except Exception:
        when = name[:19]
    badge = "✅ Đã xong" if done else "⬜ Chưa thu"
    return f"{badge}  ·  Hội thoại #{idx + 1}  ·  {when}"


def build_dropdown_choices(
    input_dir: str,
    output_dir: str,
    hide_done: bool = True,
    collab_name: str = "",
) -> list[tuple[str, str]]:
    """Dropdown choices — đánh dấu done theo từng CTV (mỗi người có output riêng)."""
    choices: list[tuple[str, str]] = []
    all_names = list_dialogs(input_dir)
    for idx, name in enumerate(all_names):
        done = is_dialog_done(name, output_dir, collab_name)
        if done and hide_done:
            continue
        choices.append((_friendly_dialog_label(idx, name, done), name))
    return choices


# ---------- Audio segmentation for user turns ----------
def trim_silences(audio: np.ndarray, sr: int,
                  min_silence_ms: int = 400,
                  speech_pad_ms: int = 150,
                  internal_gap_ms: int = 250) -> np.ndarray:
    """Cắt silence dài ở đầu/cuối + ép silence giữa các đoạn nói còn `internal_gap_ms`.

    Dùng silero-vad để phát hiện các speech region trong segment, sau đó nối
    lại với gap ngắn cố định. Nếu không phát hiện được speech, trả về nguyên
    segment (giữ an toàn).
    """
    if audio is None or len(audio) == 0:
        return audio
    # silero-vad chỉ hỗ trợ 16 kHz hoặc 8 kHz. Nếu khác, giữ nguyên.
    if sr not in (8000, 16000):
        return audio
    try:
        audio_tensor = torch.from_numpy(audio.astype(np.float32))
        timestamps = get_speech_timestamps(
            audio_tensor, VAD_MODEL,
            sampling_rate=sr,
            threshold=0.4,
            min_silence_duration_ms=min_silence_ms,
            min_speech_duration_ms=80,
            speech_pad_ms=speech_pad_ms,
        )
        if not timestamps:
            return audio  # an toàn: không thấy speech → giữ nguyên

        gap = np.zeros(int(internal_gap_ms / 1000.0 * sr), dtype=audio.dtype)
        parts = []
        for i, t in enumerate(timestamps):
            if i > 0:
                parts.append(gap)
            s = max(0, int(t["start"]))
            e = min(len(audio), int(t["end"]))
            if e > s:
                parts.append(audio[s:e])
        if not parts:
            return audio
        return np.concatenate(parts)
    except Exception as exc:
        print(f"[trim_silences] {exc} → giữ nguyên segment")
        return audio


def segment_user_turns(wav_path: str, dialog: list[dict]):
    """Tách audio user từ wav theo start/end samples đã có sẵn trong dialog.

    Format mới: mỗi turn đã có `start_sample` / `end_sample` (đơn vị: sample
    ở SR 16kHz, khớp với file wav). Cắt trực tiếp, không cần VAD.

    Nếu turn không có timestamp (format cũ): fallback dùng VAD (giữ để tương thích).
    """
    if not wav_path or not os.path.exists(wav_path):
        return {i: None for i, t in enumerate(dialog) if t["role"] == "user"}

    # Có timestamp sẵn → cắt thẳng
    has_marks = any(
        t.get("start_sample") is not None and t.get("end_sample") is not None
        for t in dialog
    )

    if has_marks:
        try:
            audio_np, sr = sf.read(wav_path, dtype="float32")
            if audio_np.ndim > 1:
                audio_np = audio_np.mean(axis=1)  # ép về mono

            print(f"[segment] {os.path.basename(wav_path)}: "
                  f"dùng timestamps có sẵn (SR wav = {sr}Hz)")

            # Cắt nhanh theo timestamps, KHÔNG trim ở đây.
            # trim_silences() sẽ chạy lazy (1 lần / 1 turn) trong
            # get_trimmed_user_audio() khi audio thực sự cần phát.
            result: dict[int, tuple | None] = {}
            n_samples = len(audio_np)
            for i, turn in enumerate(dialog):
                if turn["role"] != "user":
                    continue
                start = turn.get("start_sample")
                end = turn.get("end_sample")
                if start is None or end is None:
                    result[i] = None
                    continue
                start = max(0, int(start))
                end = min(n_samples, int(end))
                if end <= start:
                    result[i] = None
                    continue
                result[i] = (sr, audio_np[start:end])
            return result
        except Exception as exc:
            print(f"[Warn] Không cắt được theo timestamps: {exc}")
            # Tiếp tục fallback VAD nếu lỗi

    # Fallback: VAD-based segmentation (cho file dialog format cũ)
    try:
        audio = read_audio(wav_path, sampling_rate=SAMPLE_RATE)
        timestamps = get_speech_timestamps(
            audio, VAD_MODEL,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=900,
            min_speech_duration_ms=150,
            threshold=0.4,
            speech_pad_ms=200,
        )
        audio_np = (
            audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio)
        ).astype(np.float32)
        n_segs = len(timestamps)
        print(f"[segment] VAD fallback: {len(dialog)} turns ↔ {n_segs} segments")
        result = {}
        for i, turn in enumerate(dialog):
            if turn["role"] != "user":
                continue
            if i < n_segs:
                t = timestamps[i]
                # Cắt thô, trim lazy về sau
                result[i] = (SAMPLE_RATE, audio_np[t["start"]: t["end"]])
            else:
                result[i] = None
        return result
    except Exception as exc:
        print(f"[Warn] Không tách được audio: {exc}")
        return {i: None for i, t in enumerate(dialog) if t["role"] == "user"}


# ---------- UI rendering helpers ----------


def get_trimmed_user_audio(state: dict, idx: int):
    """Lazy-trim audio user turn `idx`: lần đầu chạy silero-vad, lần sau lấy cache.

    Nếu trim vượt ngưỡng thời gian (>800ms) thì giữ nguyên segment thô
    để tránh treo UI.
    """
    raw = state.get("user_audio_per_turn", {}).get(idx)
    if raw is None:
        return None
    cache = state.setdefault("_trim_cache", {})
    if idx in cache:
        return cache[idx]
    sr, seg = raw
    t0 = time.time()
    trimmed = trim_silences(seg, sr)
    dt = time.time() - t0
    if dt > 0.3:
        print(f"[trim] turn {idx}: silero-vad mất {1000*dt:.0f}ms (seg {len(seg)} samples)")
    if trimmed is None or len(trimmed) == 0:
        trimmed = seg
    cache[idx] = (sr, trimmed)
    return cache[idx]


# Thư mục lưu user audio tạm để serve qua HTTP.
# Đặt cạnh output/ (cùng thư mục app) thay vì /tmp — vì /tmp trên macOS là
# /var/folders/.../T/ có symlink /var → /private/var khiến Gradio `abs_path.exists()`
# có thể lệch giữa allowed_paths và real path.
_HERE = os.path.dirname(os.path.abspath(__file__))
USER_AUDIO_TMPDIR = os.path.realpath(os.path.join(_HERE, "output", ".user_audio_cache"))
os.makedirs(USER_AUDIO_TMPDIR, exist_ok=True)


def audio_to_data_url(audio_or_path) -> str | None:
    """Encode audio thành base64 data URL — luôn hoạt động không phụ thuộc
    Gradio file endpoint (flaky với absolute paths trong vài version).
    Để tránh phình HTML, chỉ encode N turn gần nhất (_MAX_EMBED_PAST).
    """
    if audio_or_path is None:
        return None
    try:
        if isinstance(audio_or_path, str):
            if not os.path.exists(audio_or_path):
                return None
            with open(audio_or_path, "rb") as f:
                data = f.read()
        elif isinstance(audio_or_path, tuple):
            sr, arr = audio_or_path
            arr = np.asarray(arr)
            buf = io.BytesIO()
            arr_int16 = np.clip(arr * 32767.0, -32768, 32767).astype(np.int16)
            sf.write(buf, arr_int16, sr, format="WAV")
            data = buf.getvalue()
        else:
            return None
        return "data:audio/wav;base64," + _base64.b64encode(data).decode()
    except Exception as exc:
        print(f"[audio_to_data_url] {exc}")
        return None


_MAX_EMBED_PAST = 4  # chỉ nhúng audio cho 4 turn quá khứ gần nhất (base64)


def _bubble_html(state: dict, i: int, role_state: str) -> str:
    """Build 1 bubble theo role + state ('past' | 'current' | 'future')."""
    dialog = state["dialog"]
    idx = state.get("current_turn", 0)
    turn = dialog[i]
    is_user = turn["role"] == "user"
    if is_user:
        avatar, role_name, side_cls = "🧑", "Khách hàng", "user-side"
    else:
        avatar, role_name, side_cls = "🎙️", "Bạn — CTV thu âm", "assistant-side"

    audio_html = ""
    if role_state == "past" and i >= idx - _MAX_EMBED_PAST:
        # Chỉ nhúng audio cho các turn quá khứ gần (≤ _MAX_EMBED_PAST trở lại).
        # Turn cũ hơn chỉ hiển thị text → giảm kích thước HTML, tránh lag UI.
        cache: dict = state.setdefault("_audio_url_cache", {})
        cache_key = ("u", i) if is_user else ("a", i)
        if cache_key not in cache:
            # User audio: dùng bản trimmed (lazy + cached)
            # Assistant: lấy filepath đã ghi
            src = (
                get_trimmed_user_audio(state, i)
                if is_user
                else state.get("recordings", {}).get(i)
            )
            cache[cache_key] = audio_to_data_url(src)
        url = cache[cache_key]
        if url:
            # preload="none" → browser chỉ tải/giải mã khi CTV bấm Play
            audio_html = (
                f'<audio controls preload="none" '
                f'src="{url}" class="bubble-audio"></audio>'
            )

    return f"""
<div class="msg-row {side_cls}">
  <div class="avatar">{avatar}</div>
  <div class="bubble {role_state}" id="bubble-{i}">
    <div class="role-label">{role_name} <span class="turn-num">· Câu {i + 1}/{len(dialog)}</span></div>
    <div class="bubble-text">{turn['text']}</div>
    {audio_html}
  </div>
</div>
"""


def build_chat_html(state: dict) -> str:
    """Chat history — CHỈ các turn ĐÃ XONG (past). Câu hiện tại được render
    riêng ở `build_current_card_html` cùng các control bên dưới."""
    dialog = state.get("dialog", [])
    idx = state.get("current_turn", 0)

    if not dialog:
        return (
            "<div class='chat-empty'>"
            "👆 Chọn 1 cuộc hội thoại ở trên rồi bấm <b>▶️ Bắt đầu</b>"
            "</div>"
        )

    bubbles = [_bubble_html(state, i, "past") for i in range(min(idx, len(dialog)))]
    if not bubbles:
        return (
            "<div class='chat-empty-soft'>"
            "Chưa có câu nào hoàn thành — câu đầu tiên đang ở dưới đây 👇"
            "</div>"
        )
    return "<div class='chat-history'>" + "".join(bubbles) + "</div>"


def build_current_card_html(state: dict) -> str:
    """Thẻ câu hiện tại — bubble cùng style với chat, ngay phía trên các nút."""
    dialog = state.get("dialog", [])
    idx = state.get("current_turn", 0)
    if not dialog or idx >= len(dialog):
        return ""
    turn = dialog[idx]
    is_user = turn["role"] == "user"
    if is_user:
        avatar, role_name, side_cls = "🧑", "Khách hàng đang nói", "user-side"
        hint = "🔊 Audio đang phát — nghe xong app sẽ tự sang câu kế"
    else:
        avatar, role_name, side_cls = "🎙️", "Đến lượt bạn — đọc câu này", "assistant-side"
        hint = "👇 Bấm nút ngay dưới đây để bắt đầu ghi âm"

    return f"""
<div class="msg-row {side_cls} current-row">
  <div class="avatar">{avatar}</div>
  <div class="bubble current bubble-large" id="bubble-current">
    <div class="role-label">{role_name} <span class="turn-num">· Câu {idx + 1}/{len(dialog)}</span></div>
    <div class="bubble-text bubble-text-lg">{turn['text']}</div>
    <div class="bubble-marker bubble-marker-action">{hint}</div>
  </div>
</div>
"""


def build_future_indicator_html(state: dict) -> str:
    dialog = state.get("dialog", [])
    idx = state.get("current_turn", 0)
    if not dialog:
        return ""
    remaining = len(dialog) - idx - 1
    if remaining <= 0:
        return ""
    return f"<div class='future-counter'>⏳ Còn <b>{remaining}</b> câu nữa sau câu này</div>"


def progress_html(current: int, total: int) -> str:
    pct = int(current / total * 100) if total else 0
    return f"""
<div style="margin:6px 0 14px;">
  <div style="display:flex;justify-content:space-between;font-size:13px;color:#555;">
    <span>Tiến độ</span><span>{current}/{total} turn ({pct}%)</span>
  </div>
  <div style="height:10px;background:#E5E7EB;border-radius:5px;overflow:hidden;margin-top:4px;">
    <div style="height:100%;width:{pct}%;
                background:linear-gradient(90deg,#3B82F6,#10B981);
                transition:width .3s;"></div>
  </div>
</div>
"""


# ---------- State machine ----------
# Outputs ordering:
#   0  state
#   1  progress_box      HTML
#   2  chat_box          HTML  (past turns only)
#   3  current_card      HTML  (current turn bubble)
#   4  user_panel        visibility
#   5  user_audio        value
#   6  mic_audio         gr.Audio (sources=microphone)  visibility + value
#   7  recording_panel   visibility
#   8  rec_status        HTML value
#   9  rec_audio         value
#  10  future_indicator  HTML value
#  11  done_panel        visibility
#  12  finish_msg        markdown value
def _render(state: dict):
    dialog = state.get("dialog", [])
    idx = state.get("current_turn", 0)
    total = len(dialog)

    def base():
        return [
            state,
            gr.update(value=progress_html(0, 1)),
            gr.update(value=build_chat_html(state)),
            gr.update(value=build_current_card_html(state)),
            gr.update(visible=False),                # user_panel
            gr.update(value=None),                   # user_audio
            gr.update(visible=False, value=None),    # mic_audio
            gr.update(visible=False),                # recording_panel
            gr.update(value=""),                     # rec_status
            gr.update(value=None),                   # rec_audio
            gr.update(value=build_future_indicator_html(state)),
            gr.update(visible=False),                # done_panel
            gr.update(value=""),                     # finish_msg
            gr.update(visible=False),                # stop_record_btn
        ]

    if not dialog:
        return tuple(base())

    if idx >= total:
        out_dir = state["output_dir"]
        out = base()
        out[1] = gr.update(value=progress_html(total, total))
        celebration = (
            "<div style='padding:24px;text-align:center;"
            "background:linear-gradient(135deg,#ECFDF5 0%,#D1FAE5 100%);"
            "border-radius:16px;border:2px solid #10B981;margin:6px 0 14px;'>"
            "<div style='font-size:42px;'>🎉</div>"
            "<div style='font-size:22px;color:#065F46;font-weight:800;'>"
            "Tuyệt vời! Bạn đã ghi xong tất cả các câu</div>"
            "</div>"
        )
        out[2] = gr.update(value=celebration + build_chat_html(state))
        out[3] = gr.update(value="")           # không có current card khi đã xong
        out[10] = gr.update(value="")          # không "còn câu nữa"
        out[11] = gr.update(visible=True)      # done_panel
        out[12] = gr.update(
            value=(
                f"### 📁 Sắp lưu vào:\n`{out_dir}`\n\n"
                f"Bấm **Hoàn tất & Xuất kết quả** để chốt."
            )
        )
        return tuple(out)

    turn = dialog[idx]
    out = base()
    out[1] = gr.update(value=progress_html(idx, total))

    if turn["role"] == "user":
        # Lazy-trim audio cho turn hiện tại (cache trong state)
        audio = get_trimmed_user_audio(state, idx)
        out[4] = gr.update(visible=True)  # user_panel
        if audio is not None:
            out[5] = gr.update(value=audio, autoplay=True)
        else:
            out[5] = gr.update(
                value=None,
                label="🔊 (Không tách được audio cho câu này — bấm Tiếp theo)",
            )
    else:
        # assistant turn — hiện mic component cho CTV ghi âm
        out[6] = gr.update(visible=True, value=None)  # mic_audio

    return tuple(out)


# ---------- Actions ----------
def action_load(input_dir: str, dialog_name: str, output_dir: str, collab_name: str):
    print(f"[load] CALLED  dialog={dialog_name!r}  input_dir={input_dir!r}  "
          f"output_dir={output_dir!r}  collab={collab_name!r}")
    if not dialog_name:
        print("[load] EARLY RETURN — dialog_name rỗng")
        empty = {}
        out = list(_render(empty))
        out[2] = gr.update(
            value="<div style='padding:20px;color:#dc2626;'>⚠️ Hãy chọn 1 file hội thoại.</div>"
        )
        return tuple(out)

    try:
        t0 = time.time()
        dialog_path = os.path.join(input_dir, dialog_name)
        wav_path = dialog_path.replace(".dialog", ".wav")
        print(f"[load] step 1: paths OK  wav={wav_path}")

        dialog = parse_dialog_file(dialog_path)
        if not dialog:
            print("[load] EARLY RETURN — dialog rỗng")
            empty = {}
            out = list(_render(empty))
            out[2] = gr.update(
                value="<div style='padding:20px;color:#dc2626;'>⚠️ File hội thoại trống.</div>"
            )
            return tuple(out)
        t1 = time.time()
        print(f"[load] step 2: parsed {len(dialog)} turns  ({1000*(t1-t0):.0f}ms)")

        user_audio_per_turn = segment_user_turns(wav_path, dialog)
        t2 = time.time()
        print(f"[load] step 3: segmented {len(user_audio_per_turn)} user turns  ({1000*(t2-t1):.0f}ms)")

        session_dir = session_output_dir(output_dir, collab_name, dialog_name)
        os.makedirs(session_dir, exist_ok=True)

        state = {
            "collab_name": collab_name,
            "dialog_name": dialog_name,
            "dialog_path": dialog_path,
            "wav_path": wav_path,
            "output_dir": session_dir,
            "dialog": dialog,
            "user_audio_per_turn": user_audio_per_turn,
            "recordings": {},
            "current_turn": 0,
        }
        print(f"[load] step 4: state ready, calling _render ...")
        out = _render(state)
        t3 = time.time()
        print(f"[load] DONE  parse={1000*(t1-t0):.0f}ms  "
              f"segment={1000*(t2-t1):.0f}ms  render={1000*(t3-t2):.0f}ms  "
              f"total={1000*(t3-t0):.0f}ms")
        return out
    except Exception as exc:
        import traceback
        print(f"[load] EXCEPTION: {exc}")
        traceback.print_exc()
        raise


def action_next(state: dict):
    dialog = state.get("dialog", [])
    idx = state.get("current_turn", 0)
    if idx >= len(dialog) or dialog[idx]["role"] != "user":
        # TRUE no-op cho trường hợp user_audio.stop bắn nhầm ở turn assistant
        return tuple([state] + [gr.update() for _ in range(13)])
    time.sleep(USER_PAUSE_SEC)
    state["current_turn"] = idx + 1
    return _render(state)


def action_recording_done(state: dict, mic_value):
    """gr.Audio.stop_recording event — chạy khi CTV bấm dừng trong component mic.

    `mic_value` là filepath (str) tới file .wav tạm do Gradio tạo từ recording.
    Pipeline:
      1) Đọc file, ép mono, downsample về 16kHz cho gọn nhẹ
      2) Lưu vào output dir
      3) Show preview audio + Lưu/Ghi lại
    """
    if mic_value is None:
        return tuple([state] + [gr.update() for _ in range(13)])

    # mic_value giờ là filepath string (type="filepath")
    if not isinstance(mic_value, str) or not os.path.exists(mic_value):
        print(f"[recording_done] unexpected mic value: {type(mic_value)} {mic_value!r}")
        return tuple([state] + [gr.update() for _ in range(13)])

    try:
        audio, sr = sf.read(mic_value, dtype="float32")
    except Exception as exc:
        print(f"[recording_done] sf.read lỗi: {exc}")
        return tuple([state] + [gr.update() for _ in range(13)])

    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    audio = np.clip(audio, -1.0, 1.0)

    # Bỏ trường hợp ghi quá ngắn (< 0.3s)
    if len(audio) < int(0.3 * sr):
        return (
            state,
            gr.update(), gr.update(), gr.update(),
            gr.update(visible=False),
            gr.update(),
            gr.update(visible=True, value=None),
            gr.update(visible=True),
            gr.update(value=(
                "<div style='padding:14px;border-radius:8px;background:#FEF3C7;"
                "color:#92400E;font-weight:600;'>"
                "⚠️ Bản ghi quá ngắn (&lt; 0.3s). Hãy thử ghi lại."
                "</div>"
            )),
            gr.update(value=None),
            gr.update(), gr.update(), gr.update(),
            gr.update(visible=False),
        )

    # Lưu int16 → file nhỏ gọn, không mất chất lượng speech
    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)

    idx = state["current_turn"]
    out_dir = state["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    # realpath để khớp với allowed_paths (tránh /var vs /private/var trên macOS)
    final_path = os.path.realpath(
        os.path.join(out_dir, f"turn_{idx:02d}_assistant.wav")
    )
    sf.write(final_path, audio_int16, sr)
    state["recordings"][idx] = final_path
    state.get("_audio_url_cache", {}).pop(("a", idx), None)
    print(f"[recording_done] saved {final_path}  ({len(audio)/sr:.1f}s @ {sr}Hz)")

    # Base64 audio cho preview ngay trên rec_status — không cần file route
    # của Gradio (đang flaky với absolute paths). 1-2s audio chỉ ~100KB.
    with open(final_path, "rb") as _f:
        _audio_b64 = _base64.b64encode(_f.read()).decode()
    _audio_data_url = f"data:audio/wav;base64,{_audio_b64}"

    return (
        state,
        gr.update(),                                # 1 progress
        gr.update(),                                # 2 chat
        gr.update(),                                # 3 current_card
        gr.update(visible=False),                   # 4 user_panel
        gr.update(),                                # 5 user_audio
        gr.update(visible=False),                   # 6 mic_audio (ẩn khi đã có bản ghi)
        gr.update(visible=True),                    # 7 recording_panel (hiện preview)
        gr.update(value=(
            "<div style='padding:14px;border-radius:8px;background:#D1FAE5;"
            "color:#065F46;font-weight:600;font-size:16px;'>"
            "✅ Đã ghi xong. Nghe lại bên dưới rồi xác nhận."
            "</div>"
            f"<audio controls preload='auto' src='{_audio_data_url}' "
            f"style='width:100%;margin-top:10px;border-radius:8px;'></audio>"
        )),                                         # 8 rec_status
        gr.update(value=None),                      # 9 rec_audio (ẩn / bỏ)
        gr.update(),                                # 10 future
        gr.update(),                                # 11 done_panel
        gr.update(),                                # 12 finish_msg
        gr.update(visible=False),                   # 13 stop_record_btn
    )


def action_rerecord(state: dict):
    """Bấm Ghi lại → ẩn preview + cho hiện mic_audio (reset value)."""
    return (
        state,
        gr.update(),                       # 1 progress
        gr.update(),                       # 2 chat
        gr.update(),                       # 3 current_card
        gr.update(),                       # 4 user_panel
        gr.update(),                       # 5 user_audio
        gr.update(visible=True, value=None),  # 6 mic_audio (reset, hiện lại)
        gr.update(visible=False),          # 7 recording_panel (ẩn preview)
        gr.update(value=""),               # 8 rec_status
        gr.update(value=None),             # 9 rec_audio
        gr.update(),                       # 10 future
        gr.update(),                       # 11 done_panel
        gr.update(),                       # 12 finish_msg
        gr.update(visible=False),          # 13 stop_record_btn
    )


def action_save_continue(state: dict):
    state["current_turn"] = state.get("current_turn", 0) + 1
    return _render(state)


def action_finish(state: dict):
    out_dir = state["output_dir"]
    dialog = state["dialog"]

    # 1) lưu user audio
    for i, t in enumerate(dialog):
        if t["role"] == "user":
            seg = state["user_audio_per_turn"].get(i)
            if seg is not None:
                sr, arr = seg
                sf.write(os.path.join(out_dir, f"turn_{i:02d}_user.wav"), arr, sr)

    # 2) lưu dialog đã chuẩn hoá
    with open(os.path.join(out_dir, "dialog_normalized.dialog"), "w", encoding="utf-8") as f:
        for t in dialog:
            f.write(f"{t['role']}: {t['text']}\n")

    # 3) lưu metadata
    meta = {
        "source_dialog": state["dialog_name"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sample_rate": SAMPLE_RATE,
        "turns": [
            {
                "index": i,
                "role": t["role"],
                "text_raw": t["text_raw"],
                "text_normalized": t["text"],
                "audio_file": (
                    f"turn_{i:02d}_user.wav"
                    if t["role"] == "user"
                    and state["user_audio_per_turn"].get(i) is not None
                    else (
                        os.path.basename(state["recordings"][i])
                        if i in state["recordings"]
                        else None
                    )
                ),
            }
            for i, t in enumerate(dialog)
        ],
    }
    with open(os.path.join(out_dir, "dialog.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return gr.update(
        visible=True,
        value=(
            f"### 🎉 Cảm ơn bạn đã hoàn thành!\n\n"
            f"Tất cả bản ghi đã được lưu vào thư mục:\n\n"
            f"📁 `{out_dir}`\n\n"
            f"Bạn có thể chọn cuộc hội thoại tiếp theo ở phía trên ☝️"
        ),
    )


# ---------- Build UI ----------
CSS = """
/* ============================================================
   CSS variables  —  hằng số layout responsive
   ============================================================ */
:root {
  --container-max: 1320px;
  --sidebar-w: clamp(240px, 22vw, 300px);
  --sidebar-gap: 20px;
  --avatar-w: 38px;
  --avatar-gap: 10px;
  --bubble-offset: calc(var(--avatar-w) + var(--avatar-gap));
  --bubble-w: clamp(320px, 60%, 620px);
  --font-bubble: clamp(15px, 0.6vw + 13px, 17px);
  --font-bubble-lg: clamp(18px, 0.6vw + 16px, 22px);
}

.gradio-container {
  max-width: var(--container-max) !important;
  margin: auto !important;
  padding: 8px clamp(12px, 1.5vw, 24px) !important;
}

/* ============================================================
   Buttons
   ============================================================ */
.big-btn button {
  font-size: clamp(15px, 0.4vw + 14px, 18px) !important;
  padding: clamp(12px, 1vw + 6px, 18px) clamp(20px, 2vw, 32px) !important;
  font-weight: 700 !important;
  min-height: 52px !important;
}
.huge-btn button {
  font-size: clamp(18px, 0.7vw + 14px, 22px) !important;
  padding: clamp(16px, 1.2vw + 8px, 22px) clamp(28px, 2.5vw, 40px) !important;
  font-weight: 800 !important;
  min-height: clamp(60px, 5vw + 30px, 76px) !important;
  box-shadow: 0 4px 14px rgba(16,185,129,.25) !important;
}

.app-title { text-align: center; padding: 6px 0 0; }
.app-title h1 { font-size: clamp(22px, 1.4vw + 16px, 30px) !important; }
.app-subtitle {
  text-align: center; color: #6B7280;
  font-size: clamp(13px, 0.4vw + 11px, 16px);
  margin: -10px 0 18px;
}

/* ============================================================
   Guide box (4 bước)
   ============================================================ */
.guide-box {
  background: linear-gradient(135deg, #EFF6FF 0%, #ECFDF5 100%);
  border: 1px solid #BFDBFE;
  border-radius: 14px;
  padding: clamp(12px, 1vw + 6px, 18px) clamp(16px, 1.5vw + 8px, 24px);
  margin: 4px 0 18px;
}
.guide-box h3 {
  margin: 0 0 8px;
  color: #1E3A8A;
  font-size: clamp(15px, 0.5vw + 13px, 18px);
}
.guide-box ol {
  margin: 0;
  padding-left: 22px;
  line-height: 1.75;
  font-size: clamp(13px, 0.3vw + 12px, 15px);
}
.guide-box li b { color: #065F46; }
.start-row { display: flex; gap: 12px; align-items: end; }

/* ===== Recording card + animations ===== */
.rec-card {
  padding: 22px 24px;
  border-radius: 16px;
  background: #FEF2F2;
  border: 3px solid #FCA5A5;
  transition: background .35s ease, border-color .35s ease, box-shadow .35s ease;
}
.rec-card.speaking {
  background: linear-gradient(135deg, #ECFDF5 0%, #D1FAE5 100%);
  border-color: #10B981;
  box-shadow: 0 0 0 0 rgba(16,185,129,.5);
  animation: card-glow 1.6s ease-in-out infinite;
}
@keyframes card-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(16,185,129,.4); }
  50%      { box-shadow: 0 0 0 14px rgba(16,185,129,0); }
}

.rec-card-head {
  display: flex; align-items: center; gap: 12px; margin-bottom: 18px;
}
.rec-title {
  font-size: 22px; font-weight: 800; color: #B91C1C;
  letter-spacing: .3px;
}
.rec-card.speaking .rec-title { color: #065F46; }
.rec-clock {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 20px; color: #374151; font-weight: 700;
}

.rec-pulse {
  display: inline-block;
  width: 18px; height: 18px; border-radius: 50%;
  background: #EF4444;
  box-shadow: 0 0 0 0 rgba(239,68,68,.7);
  animation: rec-pulse-anim 1.3s infinite;
}
.rec-card.speaking .rec-pulse {
  background: #10B981;
  animation: rec-pulse-green 0.7s infinite;
}
@keyframes rec-pulse-anim {
  0%   { box-shadow: 0 0 0 0 rgba(239,68,68,.7); transform: scale(1); }
  70%  { box-shadow: 0 0 0 18px rgba(239,68,68,0); transform: scale(1.15); }
  100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); transform: scale(1); }
}
@keyframes rec-pulse-green {
  0%   { box-shadow: 0 0 0 0 rgba(16,185,129,.7); transform: scale(1); }
  70%  { box-shadow: 0 0 0 14px rgba(16,185,129,0); transform: scale(1.2); }
  100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); transform: scale(1); }
}

/* ===== Equalizer bars ===== */
.eq-container {
  display: flex; align-items: flex-end; justify-content: center;
  gap: 6px; height: 96px; padding: 6px 0 4px;
  margin: 4px 0 16px;
}
.eq-bar {
  width: 14px;
  background: linear-gradient(to top, #9CA3AF, #D1D5DB);
  border-radius: 4px 4px 2px 2px;
  transition: height .12s ease-out, background .25s;
  min-height: 4px;
}
.eq-container.speaking .eq-bar {
  background: linear-gradient(to top, #059669, #34D399 60%, #6EE7B7);
  box-shadow: 0 0 6px rgba(16,185,129,.55);
}
/* nhịp nhảy nhẹ ngay cả giữa các tick để cảm giác "live" hơn */
.eq-container.speaking .eq-bar:nth-child(odd)  { animation: eq-jitter 0.34s ease-in-out infinite alternate; }
.eq-container.speaking .eq-bar:nth-child(even) { animation: eq-jitter 0.42s ease-in-out infinite alternate; }
.eq-container.speaking .eq-bar:nth-child(3n)   { animation-duration: 0.5s; }
@keyframes eq-jitter {
  0%   { transform: scaleY(0.92); }
  100% { transform: scaleY(1.08); }
}

.rec-speech-label {
  text-align: center;
  font-size: 16px;
  font-weight: 700;
  margin-top: 4px;
}
.rec-hint {
  text-align: center;
  margin-top: 10px;
  font-size: 13px;
  color: #6B7280;
}

/* ===== Chat-style conversation view ===== */
.chat-history {
  max-height: 460px;
  overflow-y: auto;
  padding: 16px 8px;
  background: #F9FAFB;
  border-radius: 14px;
  border: 1px solid #E5E7EB;
  margin: 8px 0 16px;
  scroll-behavior: smooth;
}
.chat-empty {
  padding: 60px 24px;
  text-align: center;
  color: #9CA3AF;
  font-size: 17px;
  background: #F9FAFB;
  border-radius: 14px;
  border: 2px dashed #D1D5DB;
}

.msg-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 10px 0;
}
.msg-row.user-side       { flex-direction: row; }
.msg-row.assistant-side  { flex-direction: row-reverse; }

.avatar {
  flex: 0 0 auto;
  width: 38px; height: 38px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px;
  background: #FFFFFF;
  border: 1px solid #E5E7EB;
  box-shadow: 0 1px 2px rgba(0,0,0,.05);
}

.bubble {
  max-width: 55%;
  padding: 12px 16px;
  border-radius: 16px;
  font-size: 16px;
  line-height: 1.55;
  position: relative;
  transition: box-shadow .25s, transform .25s;
}
.user-side .bubble {
  background: #DBEAFE;
  color: #1E3A8A;
  border-top-left-radius: 4px;
}
.assistant-side .bubble {
  background: #D1FAE5;
  color: #065F46;
  border-top-right-radius: 4px;
}
.bubble.past { opacity: .92; }
.bubble.future {
  opacity: .5;
  filter: grayscale(.4);
}
.bubble.current {
  box-shadow: 0 0 0 3px rgba(16,185,129,.35), 0 6px 20px rgba(16,185,129,.2);
  transform: scale(1.015);
}
.user-side .bubble.current {
  box-shadow: 0 0 0 3px rgba(59,130,246,.35), 0 6px 20px rgba(59,130,246,.2);
}

.role-label {
  font-size: 12px; font-weight: 700; opacity: .75;
  margin-bottom: 4px; letter-spacing: .2px;
}
.role-label .turn-num { font-weight: 500; opacity: .7; }

.bubble-text {
  font-size: 17px; font-weight: 500;
  margin: 4px 0 8px;
}
.assistant-side .bubble.current .bubble-text {
  font-size: 19px; font-weight: 600;
}

.bubble-audio {
  width: 100%;
  max-width: 320px;
  height: 36px;
  margin-top: 4px;
  display: block;
}
.bubble-marker {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(255,255,255,.5);
  font-size: 13px;
  font-weight: 600;
}
.bubble-marker.bubble-marker-action {
  background: #FFFFFF;
  color: #065F46;
  border: 1px dashed #10B981;
  font-size: 14px;
}
.bubble-marker.dim {
  background: transparent;
  color: #9CA3AF;
  font-style: italic;
  padding: 4px 8px;
}

/* Current-turn card (bubble lớn nằm ngoài chat-history) */
.msg-row.current-row {
  margin: 14px 0 4px;
}
.bubble.bubble-large {
  width: var(--bubble-w);
  max-width: var(--bubble-w);
  flex: 0 0 auto;
  padding: clamp(14px, 1vw + 8px, 20px) clamp(16px, 1.2vw + 10px, 24px);
  font-size: var(--font-bubble-lg);
  box-shadow: 0 4px 16px rgba(16,185,129,.18), 0 0 0 3px rgba(16,185,129,.25);
  box-sizing: border-box;
}
.user-side .bubble.bubble-large {
  box-shadow: 0 4px 16px rgba(59,130,246,.18), 0 0 0 3px rgba(59,130,246,.25);
}
.bubble-text.bubble-text-lg {
  font-size: var(--font-bubble-lg);
  font-weight: 600;
  line-height: 1.55;
}

/* Empty placeholder khi chưa có turn nào hoàn thành */
.chat-empty-soft {
  padding: 10px 14px;
  text-align: center;
  font-size: 13px;
  color: #9CA3AF;
  font-style: italic;
}

/* Indicator "Còn N câu nữa" ở phía dưới */
.future-counter {
  text-align: center;
  font-size: 14px;
  color: #6B7280;
  padding: 12px 0 4px;
  font-style: italic;
}
.future-counter b { color: #374151; font-style: normal; }

/* ===== Welcome screen ===== */
.welcome-screen {
  max-width: 580px !important;
  margin: clamp(20px, 4vh, 50px) auto !important;
  padding: clamp(8px, 1.5vw, 16px);
}
.welcome-card {
  text-align: center;
  padding: clamp(20px, 2vw + 14px, 32px) clamp(16px, 1.5vw + 10px, 28px) 8px;
  background: linear-gradient(135deg, #EFF6FF 0%, #ECFDF5 100%);
  border-radius: 18px;
  border: 1px solid #BFDBFE;
  margin-bottom: 16px;
  box-shadow: 0 6px 24px rgba(59,130,246,.10);
}
.welcome-emoji {
  font-size: clamp(36px, 3vw + 24px, 52px);
  margin-bottom: 4px;
}
.welcome-card h1 {
  color: #1E3A8A;
  font-size: clamp(22px, 1.6vw + 14px, 30px);
}
.welcome-card p {
  font-size: clamp(14px, 0.4vw + 12px, 17px);
}
.welcome-divider {
  height: 1px;
  background: rgba(0,0,0,.08);
  margin: 14px 0 10px;
}
.welcome-input-row {
  align-items: center !important;
  gap: 8px !important;
  flex-wrap: wrap !important;
}
#ctv-name-input textarea, #ctv-name-input input {
  font-size: clamp(15px, 0.5vw + 13px, 19px) !important;
  padding: clamp(12px, 1vw + 6px, 18px) !important;
  height: clamp(50px, 4vw + 32px, 60px) !important;
  border-radius: 14px !important;
  border: 2px solid #BFDBFE !important;
}
.welcome-input-row .huge-btn { flex: 0 0 auto; }
.welcome-input-row .huge-btn button {
  height: clamp(50px, 4vw + 32px, 60px) !important;
  padding: 0 clamp(20px, 2vw, 32px) !important;
  font-size: clamp(15px, 0.5vw + 13px, 18px) !important;
}

/* CTV banner ở đầu màn hình chính */
.ctv-banner {
  background: #F3F4F6;
  border-radius: 10px;
  padding: 8px 14px;
  font-size: 14px;
  color: #374151;
  display: inline-block;
  margin-bottom: 4px;
}
.ctv-banner b { color: #1E3A8A; }

/* ===== Layout 2 cột (cột phải = sidebar chọn hội thoại) ===== */
.main-2col {
  align-items: flex-start !important;
  gap: 18px !important;
}
.main-work-col {
  display: flex !important;
  flex-direction: column !important;
}

/* Sidebar luôn ở giữa viewport bên phải, không scroll theo content.
   right offset = max(sidebar-gap, lề trái/phải khi container centered).
   → Trên màn lớn (vw > container-max), sidebar bám đúng mép phải container.
   → Trên màn nhỏ, sidebar bám mép phải viewport với padding tối thiểu. */
.sidebar-col {
  position: fixed !important;
  right: max(var(--sidebar-gap), calc((100vw - var(--container-max)) / 2 + var(--sidebar-gap))) !important;
  top: 50% !important;
  transform: translateY(-50%) !important;
  width: var(--sidebar-w) !important;
  max-width: var(--sidebar-w) !important;
  overflow: visible !important;
  z-index: 100 !important;
  background: linear-gradient(135deg, #F0FDF4 0%, #ECFDF5 100%);
  border: 1px solid #BBF7D0;
  border-radius: 14px;
  padding: clamp(10px, 1vw, 16px) !important;
  box-shadow: 0 8px 28px rgba(16,185,129,.18);
}
/* Bảo đảm popup không bị wrapper clip nhưng RIÊNG popup vẫn scroll được */
.sidebar-col,
.sidebar-col > *,
.sidebar-col .gradio-dropdown,
.sidebar-col .form,
.sidebar-col .wrap,
#dialog-dropdown,
#dialog-dropdown > .wrap,
#dialog-dropdown > div {
  overflow: visible !important;
}

/* Popup options — cho phép scroll dọc, đè z-index lên trên */
#dialog-dropdown ul,
#dialog-dropdown .options,
#dialog-dropdown [role="listbox"],
.gradio-dropdown ul,
.gradio-dropdown .options,
.gradio-dropdown [role="listbox"] {
  z-index: 9999 !important;
  max-height: 320px !important;
  overflow-y: auto !important;
  overflow-x: hidden !important;
}
/* Mọi nội dung màn chính chừa khoảng cho sidebar fixed (sidebar-w + 2*gap). */
.main-screen {
  padding-right: calc(var(--sidebar-w) + var(--sidebar-gap) * 2) !important;
}
.main-work-col {
  margin-right: 0 !important;
}
.sidebar-header {
  font-weight: 800;
  font-size: 16px;
  color: #065F46;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #BBF7D0;
}
.sidebar-col .gradio-dropdown {
  margin-bottom: 8px !important;
}
.sidebar-col .big-btn button {
  width: 100% !important;
  height: 50px !important;
  min-height: 50px !important;
  font-size: 16px !important;
  margin: 4px 0 8px !important;
}
.sidebar-col .gradio-checkbox {
  margin: 6px 0 !important;
  font-size: 13px;
}
.sidebar-accordion { margin-top: 10px !important; }
.sidebar-accordion .label-wrap {
  font-size: 13px !important;
  color: #047857 !important;
}

/* ============================================================
   Action components — canh theo bubble role (parent flex-column)
   Width khớp --bubble-w, offset = --bubble-offset (avatar+gap)
   ============================================================ */

#record-btn,
#mic-audio,
#stop-record-btn,
.recording-row {
  align-self: flex-end !important;
  width: var(--bubble-w) !important;
  max-width: var(--bubble-w) !important;
  margin: 6px var(--bubble-offset) 14px 0 !important;
  box-sizing: border-box !important;
}
.user-action-row {
  align-self: flex-start !important;
  width: var(--bubble-w) !important;
  max-width: var(--bubble-w) !important;
  margin: 6px 0 14px var(--bubble-offset) !important;
  box-sizing: border-box !important;
}

#record-btn, #stop-record-btn { padding: 0 !important; }
.recording-row {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
}

/* Style nút Record (xanh emerald) */
#record-btn button {
  width: 100% !important;
  border-radius: 18px !important;
  font-size: clamp(18px, 0.7vw + 14px, 22px) !important;
  font-weight: 800 !important;
  padding: clamp(16px, 1.2vw + 8px, 22px) 24px !important;
  min-height: clamp(60px, 5vw + 30px, 76px) !important;
  background: linear-gradient(135deg, #10B981 0%, #059669 100%) !important;
  color: #fff !important;
  border: none !important;
  box-shadow: 0 4px 14px rgba(16,185,129,.30), 0 0 0 3px rgba(16,185,129,.18) !important;
  letter-spacing: .3px !important;
  transition: transform .15s ease, box-shadow .25s ease !important;
}
#record-btn button:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(16,185,129,.40), 0 0 0 4px rgba(16,185,129,.25) !important;
}

/* Style nút Stop (đỏ pulse) */
#stop-record-btn button {
  width: 100% !important;
  border-radius: 18px !important;
  font-size: clamp(18px, 0.7vw + 14px, 22px) !important;
  font-weight: 800 !important;
  padding: clamp(16px, 1.2vw + 8px, 22px) 24px !important;
  min-height: clamp(60px, 5vw + 30px, 76px) !important;
  background: linear-gradient(135deg, #DC2626 0%, #991B1B 100%) !important;
  color: #fff !important;
  border: none !important;
  box-shadow: 0 4px 14px rgba(220,38,38,.35), 0 0 0 3px rgba(220,38,38,.20) !important;
  animation: stop-pulse 1.4s ease-in-out infinite;
}
#stop-record-btn button:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(220,38,38,.45) !important;
}
@keyframes stop-pulse {
  0%, 100% { box-shadow: 0 4px 14px rgba(220,38,38,.35), 0 0 0 0 rgba(220,38,38,.30); }
  50%      { box-shadow: 0 4px 14px rgba(220,38,38,.35), 0 0 0 14px rgba(220,38,38,0); }
}

/* ============================================================
   Responsive breakpoints
   ============================================================ */

/* Laptop nhỏ ≤1200px: sidebar gọn hơn */
@media (max-width: 1200px) {
  :root {
    --sidebar-w: 240px;
    --bubble-w: clamp(280px, 65%, 540px);
  }
  .chat-history { max-height: 380px; }
}

/* Tablet ≤900px: sidebar chuyển xuống dưới content (1 cột) */
@media (max-width: 900px) {
  :root {
    --bubble-w: 78%;
    --bubble-offset: 0px;
  }
  .main-screen {
    padding-right: clamp(12px, 2vw, 24px) !important;
  }
  .sidebar-col {
    position: static !important;
    transform: none !important;
    width: 100% !important;
    max-width: 100% !important;
    margin: 12px 0 !important;
  }
  .main-2col {
    flex-direction: column !important;
  }
  .msg-row.user-side .bubble, .msg-row.assistant-side .bubble {
    max-width: 88%;
  }
  .bubble.bubble-large { width: 88% !important; max-width: 88% !important; }
  #record-btn, #mic-audio, #stop-record-btn,
  .recording-row, .user-action-row {
    width: 88% !important;
    max-width: 88% !important;
    margin-left: 0 !important;
    margin-right: 0 !important;
    align-self: center !important;
  }
  .avatar { width: 32px; height: 32px; font-size: 18px; }
}

/* Mobile ≤600px: thu gọn font, avatar, sidebar full width */
@media (max-width: 600px) {
  :root {
    --bubble-w: 92%;
  }
  .gradio-container { padding: 6px 8px !important; }
  .guide-box ol { padding-left: 18px; }
  .guide-box ul { padding-left: 16px; }
  .bubble { max-width: 92% !important; padding: 10px 12px; font-size: 15px; }
  .bubble.bubble-large { width: 92% !important; max-width: 92% !important; padding: 12px 14px; }
  .bubble-text { font-size: 15px; margin-bottom: 6px; }
  .bubble-text.bubble-text-lg { font-size: 16px; }
  .role-label { font-size: 11px; }
  .turn-num { display: block; }
  .avatar { width: 28px; height: 28px; font-size: 16px; }
  .chat-history { max-height: 320px; padding: 10px 4px; }
  .welcome-input-row { flex-direction: column !important; }
  .welcome-input-row #ctv-name-input,
  .welcome-input-row .huge-btn { width: 100% !important; }
  .welcome-card h1 { font-size: 22px; }
  .ctv-banner { font-size: 12px; padding: 6px 10px; }
}
"""

with gr.Blocks(
    theme=gr.themes.Soft(primary_hue="emerald", secondary_hue="blue"),
    title="Studio ghi âm trợ lý ảo",
    css=CSS,
) as app:

    # CTV state — giữ tên cộng tác viên qua các event
    collab_state = gr.State("")
    state = gr.State({})

    # ───── Màn hình Welcome (mặc định hiện) ─────
    with gr.Column(elem_classes=["welcome-screen"]) as welcome_panel:
        gr.HTML("""
<div class="welcome-card">
  <div class="welcome-emoji">🎙️</div>
  <h1 style="margin:8px 0 4px;">Studio ghi âm trợ lý ảo</h1>
  <p style="margin:0;color:#6B7280;">Dành cho cộng tác viên thu âm phần lời của trợ lý</p>
  <div class="welcome-divider"></div>
  <p style="margin:8px 0 14px;font-size:17px;">
    👋 Xin chào! Trước khi bắt đầu, vui lòng cho biết <b>tên của bạn</b>:
  </p>
</div>
""")
        with gr.Row(elem_classes=["welcome-input-row"]):
            name_input = gr.Textbox(
                placeholder="Ví dụ: Nguyễn Văn A",
                show_label=False,
                container=False,
                elem_id="ctv-name-input",
            )
            enter_btn = gr.Button(
                "Bắt đầu →",
                variant="primary",
                elem_classes=["huge-btn"],
            )
        name_error = gr.HTML(visible=False)

    # ───── Màn hình Main app (mặc định ẩn) ─────
    with gr.Column(visible=False, elem_classes=["main-screen"]) as main_panel:
        # Header nhỏ + hiện tên CTV
        ctv_banner = gr.HTML(
            "<div class='ctv-banner'>👤 <b>—</b></div>"
        )

        gr.HTML("""
<div class="app-title">
  <h1 style="margin:0;font-size:28px;">🎙️ Studio ghi âm trợ lý ảo</h1>
</div>
""")

        # Hướng dẫn 4 bước — luôn hiện ở đầu, không gấp lại
        gr.HTML("""
<div class="guide-box">
  <h3>📖 Bạn chỉ cần làm theo 4 bước:</h3>
  <ol>
    <li><b>Chọn 1 hội thoại</b> trong sidebar bên phải rồi bấm <b>▶️ Bắt đầu</b>.</li>
    <li>App sẽ chạy tuần tự từng câu, tự dừng lại khi đến <b>câu của bạn (Trợ lý)</b>:
      <ul style="margin:4px 0 4px 0;line-height:1.7;">
        <li>🧑 <b>Khách hàng nói</b> — bạn chỉ lắng nghe, audio tự phát rồi tự sang câu kế.</li>
        <li>🤖 <b>Đến lượt bạn</b> — đọc to & rõ câu trên màn hình.</li>
      </ul>
    </li>
    <li><b>Ghi âm</b> qua mic của trình duyệt:
      <ul style="margin:4px 0 4px 0;line-height:1.7;">
        <li>Bấm <b>🎤 nút mic</b> trong khung audio để bắt đầu thu.</li>
        <li>Đọc xong, bấm nút lớn màu đỏ <b>🛑 KẾT THÚC GHI ÂM</b> bên dưới để dừng.</li>
        <li>(Lần đầu trình duyệt sẽ hỏi cấp quyền dùng micro — chọn <i>Allow</i>.)</li>
      </ul>
    </li>
    <li><b>Nghe lại</b> bản ghi: ưng → <b>💾 Lưu & Sang câu tiếp</b>; chưa ưng → <b>🔄 Ghi lại</b>.</li>
  </ol>
</div>
""")

        # Layout 2 cột: trái = khu làm việc, phải = sidebar chọn hội thoại
        with gr.Row(elem_classes=["main-2col"]):

            # ═══════════ CỘT TRÁI — khu làm việc chính ═══════════
            with gr.Column(scale=3, elem_classes=["main-work-col"]):
                progress_box = gr.HTML(value=progress_html(0, 1))

                # Chat lịch sử — chỉ các câu ĐÃ XONG
                chat_box = gr.HTML(value=build_chat_html({}))

                # Thẻ câu hiện tại
                current_card = gr.HTML(value=build_current_card_html({}))

                # NOTE: tất cả panel init visible=True để mount DOM,
                # app.load() ẩn lại về trạng thái mặc định.

                with gr.Column(visible=True, elem_classes=["user-action-row"]) as user_panel:
                    user_audio = gr.Audio(
                        label="🔊 Đang phát audio khách hàng",
                        autoplay=True,
                        interactive=False,
                        elem_id="user-audio-player",
                    )
                    next_btn = gr.Button(
                        "▶️ Sang câu tiếp",
                        variant="secondary",
                        elem_classes=["big-btn"],
                    )

                # Micro client (browser) qua gr.Audio — dùng được với gradio.live.
                # CTV bấm nút "Record" sẵn có trong component để bắt đầu.
                # KHÔNG bật show_recording_waveform vì canvas rendering có thể gây lag.
                mic_audio = gr.Audio(
                    sources=["microphone"],
                    type="filepath",  # Gradio save trực tiếp ra temp file
                                       # → nhanh hơn việc convert sang numpy
                    label="🎤 Bấm nút mic để ghi âm",
                    interactive=True,
                    elem_id="mic-audio",
                    elem_classes=["mic-audio"],
                    visible=True,
                    format="wav",
                )

                # Nút lớn "Kết thúc" hiện sau khi CTV bắt đầu ghi —
                # gọi JS click lên nút dừng built-in của gr.Audio.
                stop_record_btn = gr.Button(
                    "🛑 KẾT THÚC GHI ÂM",
                    variant="stop",
                    visible=False,
                    elem_classes=["huge-btn"],
                    elem_id="stop-record-btn",
                )

                with gr.Column(visible=True, elem_classes=["recording-row"]) as recording_panel:
                    # rec_status nhận HTML có sẵn <audio> tag bên trong
                    # → không cần thêm gr.Audio component nữa
                    rec_status = gr.HTML()
                    # rec_audio giữ lại để tương thích outputs cũ (luôn ẩn)
                    rec_audio = gr.Audio(
                        label="🎧 Nghe lại bản ghi của bạn",
                        interactive=False,
                        type="filepath",
                        visible=False,
                    )
                    with gr.Row():
                        rerec_btn = gr.Button(
                            "🔄 Ghi lại", elem_classes=["big-btn"]
                        )
                        save_btn = gr.Button(
                            "💾 Lưu & Sang câu tiếp",
                            variant="primary",
                            elem_classes=["big-btn"],
                        )

                future_indicator = gr.HTML(value="")

                with gr.Column(visible=True) as done_panel:
                    finish_msg = gr.Markdown()
                    finish_btn = gr.Button(
                        "📦 Hoàn tất & Xuất kết quả",
                        variant="primary",
                        elem_classes=["huge-btn"],
                    )

            # ═══════════ CỘT PHẢI — sidebar chọn hội thoại + config ═══════════
            with gr.Column(scale=1, min_width=280, elem_classes=["sidebar-col"]):
                gr.HTML(
                    "<div class='sidebar-header'>📋 Chọn hội thoại</div>"
                )
                dialog_dropdown = gr.Dropdown(
                    label="Hội thoại",
                    choices=build_dropdown_choices(DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, True),
                    interactive=True,
                    elem_id="dialog-dropdown",
                )
                load_btn = gr.Button(
                    "▶️ Bắt đầu",
                    variant="primary",
                    elem_classes=["big-btn"],
                )
                hide_done_chk = gr.Checkbox(
                    label="Chỉ hiện chưa thu âm",
                    value=True,
                )
                refresh_btn = gr.Button("🔄 Quét lại", size="sm")

                with gr.Accordion(
                    "⚙️ Cấu hình nâng cao", open=False,
                    elem_classes=["sidebar-accordion"],
                ):
                    input_dir = gr.Textbox(
                        label="📂 Thư mục hội thoại",
                        value=DEFAULT_INPUT_DIR,
                    )
                    output_dir = gr.Textbox(
                        label="💾 Thư mục output",
                        value=DEFAULT_OUTPUT_DIR,
                    )


    main_outputs = [
        state,             # 0
        progress_box,      # 1  HTML
        chat_box,          # 2  HTML  past turns
        current_card,      # 3  HTML  current turn bubble
        user_panel,        # 4  Column visibility
        user_audio,        # 5  Audio value
        mic_audio,         # 6  gr.Audio — micro client để CTV ghi âm
        recording_panel,   # 7  Column visibility
        rec_status,        # 8  HTML value
        rec_audio,         # 9  Audio value
        future_indicator,  # 10 HTML value
        done_panel,        # 11 Column visibility
        finish_msg,        # 12 Markdown value
        stop_record_btn,   # 13 Button visibility (kết thúc ghi âm)
    ]

    def _refresh(in_dir, out_dir, hide_done, collab):
        return gr.update(
            choices=build_dropdown_choices(in_dir, out_dir, hide_done, collab),
            value=None,
        )

    refresh_btn.click(
        fn=_refresh,
        inputs=[input_dir, output_dir, hide_done_chk, collab_state],
        outputs=[dialog_dropdown],
    )
    hide_done_chk.change(
        fn=_refresh,
        inputs=[input_dir, output_dir, hide_done_chk, collab_state],
        outputs=[dialog_dropdown],
    )
    input_dir.submit(
        fn=_refresh,
        inputs=[input_dir, output_dir, hide_done_chk, collab_state],
        outputs=[dialog_dropdown],
    )
    output_dir.submit(
        fn=_refresh,
        inputs=[input_dir, output_dir, hide_done_chk, collab_state],
        outputs=[dialog_dropdown],
    )

    SCROLL_TO_CURRENT_JS = """
    () => {
      setTimeout(() => {
        const el = document.querySelector('.chat-history .bubble.current');
        if (el) el.scrollIntoView({behavior: 'smooth', block: 'center'});
      }, 120);
    }
    """

    # ───── Welcome → Main app: validate tên rồi chuyển màn hình ─────
    def enter_app(name: str):
        clean = sanitize_collaborator_name(name)
        if not clean:
            return (
                gr.update(visible=True, value=(
                    "<div style='color:#dc2626;padding:8px 0;font-weight:600;'>"
                    "⚠️ Vui lòng nhập tên của bạn trước khi bắt đầu."
                    "</div>"
                )),
                gr.update(visible=True),    # welcome stays
                gr.update(visible=False),   # main hidden
                "",                          # collab_state empty
                gr.update(),                 # ctv_banner unchanged
                gr.update(),                 # dialog_dropdown unchanged
            )
        return (
            gr.update(visible=False, value=""),  # clear error
            gr.update(visible=False),             # hide welcome
            gr.update(visible=True),              # show main
            clean,                                 # save name
            gr.update(value=(
                f"<div class='ctv-banner'>👤 Đang đăng nhập với tên: <b>{clean}</b></div>"
            )),
            gr.update(
                choices=build_dropdown_choices(
                    DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, True, clean
                ),
                value=None,
            ),
        )

    enter_btn.click(
        fn=enter_app,
        inputs=[name_input],
        outputs=[
            name_error, welcome_panel, main_panel,
            collab_state, ctv_banner, dialog_dropdown,
        ],
    )
    # Bấm Enter trong textbox cũng tương đương click
    name_input.submit(
        fn=enter_app,
        inputs=[name_input],
        outputs=[
            name_error, welcome_panel, main_panel,
            collab_state, ctv_banner, dialog_dropdown,
        ],
    )

    load_btn.click(
        fn=action_load,
        inputs=[input_dir, dialog_dropdown, output_dir, collab_state],
        outputs=main_outputs,
        show_progress="hidden",
    ).then(fn=None, js=SCROLL_TO_CURRENT_JS)
    next_btn.click(
        fn=action_next, inputs=[state], outputs=main_outputs,
        show_progress="hidden",
    ).then(fn=None, js=SCROLL_TO_CURRENT_JS)
    user_audio.stop(
        fn=action_next, inputs=[state], outputs=main_outputs,
        show_progress="hidden",
    ).then(fn=None, js=SCROLL_TO_CURRENT_JS)
    # Khi CTV bắt đầu ghi → hiện nút "Kết thúc ghi âm" lớn
    def _on_mic_start():
        print("[mic] start_recording event fired")
        return gr.update(visible=True)

    mic_audio.start_recording(
        fn=_on_mic_start,
        outputs=[stop_record_btn],
    )

    # Nút "Kết thúc ghi âm" → JS click trực tiếp nút dừng built-in của gr.Audio.
    # Việc click đó kích hoạt mic_audio.stop_recording → action_recording_done.
    STOP_RECORDING_JS = """
    () => {
      const root = document.querySelector('#mic-audio');
      if (!root) return;
      // Tìm nút stop của gr.Audio (Gradio đổi tên class qua các version)
      const candidates = root.querySelectorAll(
        'button.stop-button, button[aria-label*="Stop"], ' +
        'button[aria-label*="stop"], button[aria-label*="dừng"], ' +
        'button[title*="Stop"], button.icon-button'
      );
      for (const b of candidates) {
        const rect = b.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          b.click();
          return;
        }
      }
    }
    """
    stop_record_btn.click(fn=None, js=STOP_RECORDING_JS)

    # CTV bấm dừng (qua nút lớn hoặc nút built-in) → xử lý audio đã ghi
    # show_progress="hidden" → tắt overlay "processing" trên rec_status
    # (overlay đôi khi không clear gây UX rối; xử lý server-side đã rất nhanh)
    mic_audio.stop_recording(
        fn=action_recording_done,
        inputs=[state, mic_audio],
        outputs=main_outputs,
        show_progress="hidden",
    )
    rerec_btn.click(
        fn=action_rerecord, inputs=[state], outputs=main_outputs,
        show_progress="hidden",
    )
    save_btn.click(
        fn=action_save_continue, inputs=[state], outputs=main_outputs,
        show_progress="hidden",
    ).then(fn=None, js=SCROLL_TO_CURRENT_JS)
    finish_btn.click(
        fn=action_finish, inputs=[state], outputs=[finish_msg],
        show_progress="hidden",
    ).then(
        fn=_refresh,
        inputs=[input_dir, output_dir, hide_done_chk, collab_state],
        outputs=[dialog_dropdown],
    )

    # Khi trang load lần đầu → ẩn các panel/button đang init visible=True
    # về trạng thái mặc định.
    app.load(fn=lambda: _render({}), inputs=None, outputs=main_outputs)


if __name__ == "__main__":
    # SHARE=1 (mặc định) → tạo public link gradio.live cho CTV truy cập từ xa.
    # Đặt SHARE=0 nếu chỉ chạy local.
    share = os.environ.get("SHARE", "1") != "0"
    # Cho phép Gradio serve các file .wav từ thư mục output/input để
    # rec_audio component (truyền filepath) load được qua HTTP.
    here = os.path.dirname(os.path.abspath(__file__))
    app.queue().launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 7860)),
        share=share,
        show_error=True,
        allowed_paths=[
            os.path.realpath(os.path.join(here, "output")),
            os.path.realpath(os.path.join(here, "input")),
            os.path.realpath(DEFAULT_OUTPUT_DIR),
            os.path.realpath(DEFAULT_INPUT_DIR),
            USER_AUDIO_TMPDIR,
        ],
    )
