"""Studio ghi âm trợ lý ảo — Gradio app.

Đọc các file .dialog trong thư mục đầu vào, phát audio user theo từng turn
(cắt thẳng từ wav gốc theo timestamp), ghi âm phần trợ lý qua micro của
trình duyệt (gr.Audio sources=microphone) — CTV bấm nút mic để bắt đầu thu,
bấm nút "🛑 KẾT THÚC GHI ÂM" lớn để dừng.
"""
from __future__ import annotations

import base64 as _base64
import hashlib
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

from recording_backend import (
    SAMPLE_RATE,
    parse_dialog_file,
    list_dialogs,
    sanitize_collaborator_name,
    session_output_dir,
    is_dialog_done,
    build_dropdown_choices,
    segment_user_turns,
)

# ---------- Config (app-only) ----------
USER_PAUSE_SEC = 0.6             # nghỉ ngắn sau turn user trước khi sang câu kế
MAX_RECORDING_SEC = 90           # an toàn: dừng cứng sau 90s
DEFAULT_INPUT_DIR = "./input"
DEFAULT_OUTPUT_DIR = "./output"


# ---------- UI rendering helpers ----------


# Thư mục lưu user audio tạm để serve qua HTTP.
# Đặt cạnh output/ (cùng thư mục app) thay vì /tmp — vì /tmp trên macOS là
# /var/folders/.../T/ có symlink /var → /private/var khiến Gradio `abs_path.exists()`
# có thể lệch giữa allowed_paths và real path.
_HERE = os.path.dirname(os.path.abspath(__file__))
USER_AUDIO_TMPDIR = os.path.realpath(os.path.join(_HERE, "output", ".user_audio_cache"))
os.makedirs(USER_AUDIO_TMPDIR, exist_ok=True)


def _user_audio_file_path(wav_path: str, idx: int) -> str:
    """Đường dẫn ổn định cho 1 segment user. Cùng (wav_path, idx)
    → cùng file → browser cache được. Hash giúp tránh va chạm khi 2 dialog
    có prefix giống nhau."""
    stem = Path(wav_path).stem
    h = hashlib.md5(wav_path.encode("utf-8")).hexdigest()[:8]
    return os.path.join(USER_AUDIO_TMPDIR, f"{stem[:32]}_{h}_u{idx:03d}.wav")


def _write_audio_tuple(path: str, audio_tuple) -> bool:
    if audio_tuple is None:
        return False
    sr, arr = audio_tuple
    try:
        arr_int16 = np.clip(np.asarray(arr) * 32767.0, -32768, 32767).astype(np.int16)
        sf.write(path, arr_int16, sr, format="WAV")
        return True
    except Exception as exc:
        print(f"[_write_audio_tuple] {exc}")
        return False


def file_static_url(path: str | None) -> str | None:
    """URL Gradio static cho 1 file có sẵn trên disk (assistant recordings).

    Append `?t=<mtime_ns>` để bust browser cache khi CTV thu lại — file ghi đè
    cùng đường dẫn nên không có nonce thì browser phát bản cũ đã cache.
    """
    if not path or not os.path.exists(path):
        return None
    real = os.path.realpath(path)
    try:
        mtime = os.path.getmtime(real)
    except OSError:
        mtime = 0
    return (
        f"/gradio_api/file={urllib.parse.quote(real)}"
        f"?t={int(mtime * 1000)}"
    )


def user_audio_static_url(state: dict, idx: int) -> str | None:
    """URL stream cho 1 user-turn. Ghi xuống disk lần đầu, sau đó chỉ trả
    URL (browser tự cache theo URL). Thay base64 inline để HTML response
    nhỏ + browser stream khi play, không decode đồng bộ."""
    raw = state.get("user_audio_per_turn", {}).get(idx)
    if raw is None:
        return None
    wav_path = state.get("wav_path") or ""
    if not wav_path:
        # Không có key ổn định → fallback base64 cho an toàn.
        return audio_to_data_url(raw)
    out_path = _user_audio_file_path(wav_path, idx)
    if not os.path.exists(out_path):
        if not _write_audio_tuple(out_path, raw):
            return None
    return f"/gradio_api/file={urllib.parse.quote(out_path)}"


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
            # User audio: segment cắt thẳng từ wav gốc.
            # Assistant: lấy filepath đã ghi.
            src = (
                state.get("user_audio_per_turn", {}).get(i)
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
    # Mark thời điểm render — dùng để guard action_next khỏi advance nhầm
    # khi user_audio.stop event bắn ảo do DOM remount.
    state["_last_render_t"] = time.time()
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
        audio = state.get("user_audio_per_turn", {}).get(idx)
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

    # GUARD: bỏ qua nếu user_audio.stop fire ngay sau khi vừa render
    # (DOM re-mount audio element → fire stop ảo, audio chưa kịp phát xong).
    # Yêu cầu cách render hiện tại ≥ 1.5s mới được advance.
    last_render = state.get("_last_render_t", 0.0)
    if time.time() - last_render < 1.5:
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
    # Persist partial progress so the picker can show "thu tiếp"
    try:
        from progress_tracking import write_progress
        write_progress(
            state["output_dir"],
            last_recorded_turn=idx,
            recorded_count=len(state.get("recordings", {})),
        )
    except Exception as exc:
        print(f"[progress] write failed: {exc}")
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

    # Conversation fully done — drop partial-progress file.
    try:
        from progress_tracking import clear_progress
        clear_progress(out_dir)
    except Exception as exc:
        print(f"[progress] clear failed: {exc}")

    return gr.update(
        visible=True,
        value=(
            f"### 🎉 Cảm ơn bạn đã hoàn thành!\n\n"
            f"Tất cả bản ghi đã được lưu vào thư mục:\n\n"
            f"📁 `{out_dir}`\n\n"
            f"Bạn có thể chọn cuộc hội thoại tiếp theo ở phía trên ☝️"
        ),
    )


# ---------- Picker render helpers ----------

_TURN_FILE_RE = re.compile(r"^turn_(\d+)_assistant\.wav$")


def _load_existing_recordings(session_dir: str) -> dict[int, str]:
    """Scan the session dir for previously recorded turns.

    gr.State lives in memory and is wiped on page refresh, so without this the
    user appears to lose all their work. The .wav files are still on disk —
    rebuild the recordings dict from them.
    """
    if not os.path.isdir(session_dir):
        return {}
    out: dict[int, str] = {}
    for name in os.listdir(session_dir):
        m = _TURN_FILE_RE.match(name)
        if m:
            out[int(m.group(1))] = os.path.realpath(os.path.join(session_dir, name))
    return out


def load_conversation_state(input_dir: str, dialog_name: str,
                            output_dir: str, collab_name: str,
                            resume_at: int | None = None) -> dict | None:
    """Build the recording state dict for a conversation. Returns None on error.

    `resume_at` — if given, skip to that turn (used by the resume CTA).
    Otherwise auto-resume past any turns already recorded on disk.
    """
    if not dialog_name:
        return None
    dialog_path = os.path.join(input_dir, dialog_name)
    wav_path = dialog_path.replace(".dialog", ".wav")
    dialog = parse_dialog_file(dialog_path)
    if not dialog:
        return None
    user_audio_per_turn = segment_user_turns(wav_path, dialog)
    session_dir = session_output_dir(output_dir, collab_name, dialog_name)
    os.makedirs(session_dir, exist_ok=True)

    recordings = _load_existing_recordings(session_dir)
    if resume_at is not None:
        current_turn = resume_at
    elif recordings:
        current_turn = min(max(recordings.keys()) + 1, len(dialog))
    else:
        current_turn = 0

    state = {
        "collab_name": collab_name,
        "dialog_name": dialog_name,
        "dialog_path": dialog_path,
        "wav_path": wav_path,
        "output_dir": session_dir,
        "dialog": dialog,
        "user_audio_per_turn": user_audio_per_turn,
        "recordings": recordings,
        "current_turn": current_turn,
        "rec_phase": "idle",
    }
    return state


def _render_rail(st: dict) -> str:
    dialog = st.get("dialog", [])
    idx = st.get("current_turn", 0)
    recordings = st.get("recordings", {})
    recorded_count = len(recordings)
    total = len(dialog)

    rows = []
    for i, turn in enumerate(dialog):
        is_user = turn["role"] == "user"
        is_current = i == idx
        is_future = i > idx

        if is_user:
            classes = "rail-ctx"
            if is_current:
                classes += " current playing"
            elif is_future:
                classes += " future"
            play_icon = "⏸" if is_current else "▶"
            rows.append(
                f"<div class='{classes}' data-row-idx='{i}'>"
                f"<span class='role'>Khách</span>"
                f"<span class='num'>#{i+1}</span>"
                f"<span class='text'>{turn['text']}</span>"
                f"<button class='play-btn' data-play-user='{i}'>{play_icon}</button>"
                "</div>"
            )
        else:
            # Assistant turn — three states: recorded / current / future
            if i in recordings:
                rows.append(
                    f"<div class='rail-rec' data-row-idx='{i}'>"
                    f"<div class='top'><b>Đã thu</b><span>· Câu {i+1}</span></div>"
                    f"<div class='text'>{turn['text']}</div>"
                    f"<div class='actions'>"
                    f"<button class='play' data-play-assistant='{i}'>▶ Phát lại</button>"
                    f"<button data-rerec='{i}'>↻ Thu lại</button>"
                    "</div></div>"
                )
            elif is_current:
                rows.append(
                    f"<div class='rail-rec current' data-row-idx='{i}'>"
                    f"<div class='top'><b>Đang thu</b><span>· Câu {i+1}</span></div>"
                    f"<div class='text'>{turn['text']}</div>"
                    "</div>"
                )
            else:  # future assistant turn — clickable, jumps to that turn
                rows.append(
                    f"<div class='rail-rec future' data-row-idx='{i}' "
                    f"data-jump-to='{i}' role='button' tabindex='0'>"
                    f"<div class='top'><b>Chưa thu</b><span>· Câu {i+1}</span></div>"
                    f"<div class='text'>{turn['text']}</div>"
                    f"<div class='actions'>"
                    f"<button data-jump-to='{i}'>→ Thu câu này</button>"
                    "</div></div>"
                )

    # Auto-scroll the rail so the current turn is centred. Uses the same
    # <img onerror> trick as play_tag: fires synchronously when the broken
    # src 404s, and runs on every render (the rail HTML is regenerated when
    # current_turn changes, so the <img> is fresh every time).
    scroll_trigger = (
        f"<img src='/studio-rail-scroll/{time.time_ns()}.gif' "
        "style='display:none' "
        "onerror=\""
        "var el=document.querySelector('.studio-rec-rail .current');"
        "if(el)el.scrollIntoView({block:'center',behavior:'smooth'});\">"
    )
    return (
        f"<div class='rail-head'>"
        f"<span class='count-pill'>{recorded_count}/{total}</span>"
        f" Hội thoại</div>"
        + "".join(rows)
        + scroll_trigger
    )


def _render_hero(st: dict) -> str:
    dialog = st.get("dialog", [])
    idx = st.get("current_turn", 0)
    if not dialog or idx >= len(dialog):
        # Completion state
        return (
            "<span class='hero-role-tag'>🎉 Hoàn thành</span>"
            f"<div class='hero-turn-card'>Bạn đã thu xong <b>{len(dialog)} câu</b> của hội thoại này.</div>"
            "<button class='hero-btn primary' data-finish>📦 Hoàn tất & về danh sách</button>"
        )

    turn = dialog[idx]
    if turn["role"] == "user":
        # Auto-playing user turn — browser streams qua /gradio_api/file=
        # thay vì base64 inline. HTML response nhỏ + ít block khi mạng yếu.
        url = user_audio_static_url(st, idx)
        audio_tag = (
            f"<audio autoplay onended=\"window.__studioAutoNext && window.__studioAutoNext()\" "
            f"src=\"{url}\" style=\"display:none\"></audio>" if url else ""
        )
        return (
            "<span class='hero-role-tag'>Khách đang nói</span>"
            f"<div class='hero-turn-card'>{turn['text']}</div>"
            f"{audio_tag}"
            "<div class='hero-hint'>⏳ Tự sang câu kế khi nghe xong</div>"
            "<button class='hero-btn skip' data-skip-user>Bỏ qua câu này →</button>"
        )

    # Assistant turn — phase-dependent
    phase = st.get("rec_phase", "idle")
    if phase == "recording":
        bar_heights = [18, 30, 14, 38, 26, 42, 18, 32, 22, 36, 14, 28, 40, 20, 34]
        bars_html = "".join(
            f"<div class='bar' style='height:{h}px'></div>" for h in bar_heights
        )
        return (
            "<span class='hero-role-tag' style='background:var(--brand);color:#fff'>● ĐANG GHI ÂM</span>"
            f"<div class='hero-turn-card recording'>{turn['text']}</div>"
            "<div class='hero-timer'>0:00</div>"
            f"<div class='hero-waveform'>{bars_html}</div>"
            "<button class='hero-rec-btn stop' data-rec-stop><span class='inner'></span></button>"
            "<div class='hero-hint'>Bấm để <b>kết thúc</b> · hoặc <span class='hero-kbd'>Space</span> · tự dừng khi im lặng 1.5s</div>"
        )
    elif phase == "preview":
        return (
            "<span class='hero-role-tag'>✅ Đã thu xong — nghe lại</span>"
            f"<div class='hero-turn-card'>{turn['text']}</div>"
            "<div class='hero-audio-bar'>"
            f"<button class='play-circle' data-play-assistant='{idx}'>▶</button>"
            "<div class='scrub'><div></div></div>"
            "<span>0:00</span>"
            "</div>"
            "<div class='hero-actions'>"
            "<button class='hero-btn secondary' data-rerec>↻ Thu lại</button>"
            "<button class='hero-btn primary' data-save-next>💾 Lưu & câu kế →</button>"
            "</div>"
            "<div class='hero-hint'><span class='hero-kbd'>Enter</span> để lưu · <span class='hero-kbd'>R</span> để thu lại</div>"
        )
    # idle
    return (
        "<span class='hero-role-tag'>Đến lượt bạn — đọc câu này</span>"
        f"<div class='hero-turn-card'>{turn['text']}</div>"
        "<button class='hero-rec-btn' data-rec-start><span class='inner'></span></button>"
        "<div class='hero-hint'><b>Bấm để ghi âm</b> · hoặc <span class='hero-kbd'>Space</span></div>"
    )


def render_recording_html(st: dict, collab: str) -> str:
    if not st.get("dialog"):
        return "<div style='padding:40px;text-align:center;'>Đang tải hội thoại…</div>"
    dialog = st["dialog"]
    idx = st.get("current_turn", 0)
    total = len(dialog)
    pct = int(idx / total * 100) if total else 0

    top_bar = (
        "<div class='studio-topbar'>"
        "<div class='studio-logo'><span class='dot'></span>Studio</div>"
        "<button class='studio-back-btn' data-back-to-picker>← Hội thoại khác</button>"
        "<button class='studio-back-btn studio-listen-all' data-play-all>"
        "▶ Nghe toàn bộ</button>"
        f"<span class='studio-conv-title'>"
        f"{st['dialog_name'].replace('.dialog','')[:30]}</span>"
        "<div class='studio-spacer'></div>"
        f"<span class='studio-top-chip'>👤 <b>{collab or '—'}</b></span>"
        "</div>"
    )
    progress = (
        f"<div class='studio-rec-progress'>"
        f"<span class='text'>Câu {min(idx+1, total)} / {total}</span>"
        f"<div class='bar'><div class='fill' style='width:{pct}%'></div></div>"
        f"<span class='pct'>{pct}%</span>"
        "</div>"
    )
    shell = (
        "<div class='studio-rec-shell'>"
        f"<div class='studio-rec-rail'>{_render_rail(st)}</div>"
        f"<div class='studio-rec-hero'>{_render_hero(st)}</div>"
        "</div>"
    )

    # Inject on-demand playback (set by play_user_audio / play_assistant_audio actions)
    play_req = st.pop("_play_request", None)
    play_tag = ""
    if play_req:
        kind, ridx = play_req
        if kind == "user":
            url = user_audio_static_url(st, ridx)
        else:
            url = file_static_url(st.get("recordings", {}).get(ridx))
        if url:
            # Unique id + <img onerror> trick to force a fresh play every
            # click. data-nonce on <audio autoplay> alone doesn't work because
            # Svelte's morphdom reuses the existing element when only an
            # attribute differs, so autoplay only fires on initial mount.
            # Giving the audio a unique id forces a real re-mount, and the
            # <img onerror> handler fires synchronously when its broken src
            # is parsed — guaranteeing .play() runs on each render.
            play_id = f"studio-play-{time.time_ns()}"
            play_tag = (
                f"<audio id='{play_id}' src='{url}' style='display:none'></audio>"
                f"<img src='/studio-play-trigger/{play_id}.gif' "
                f"style='display:none' "
                f"onerror=\"var a=document.getElementById('{play_id}');"
                f"if(a){{a.currentTime=0;a.play();}}\">"
            )

    # Whole-conversation playback: when "Nghe toàn bộ" is clicked, build a
    # JSON list of {idx, url} for every turn that has audio, base64-encode it,
    # and ship a one-shot <img onerror> trigger that hands it to
    # window.studioStartPlaylist for client-side sequential playback. After
    # the trigger fires once we drop the marker from state.
    playlist_tag = ""
    if st.pop("_playlist_request", False):
        items = []
        for i, turn in enumerate(dialog):
            if turn["role"] == "user":
                url = user_audio_static_url(st, i)
            else:
                url = file_static_url(st.get("recordings", {}).get(i))
            if url:
                items.append({"idx": i, "url": url})
        if items:
            payload_b64 = _base64.b64encode(
                json.dumps(items).encode("utf-8")
            ).decode()
            trigger_id = f"studio-playlist-{time.time_ns()}"
            playlist_tag = (
                f"<img id='{trigger_id}' "
                f"src='/studio-playlist-trigger/{trigger_id}.gif' "
                "style='display:none' "
                f"data-playlist='{payload_b64}' "
                "onerror=\"window.studioStartPlaylist && "
                "window.studioStartPlaylist(this.dataset.playlist)\">"
            )

    return top_bar + progress + shell + play_tag + playlist_tag


def _estimate_duration_min(num_turns: int) -> int:
    """Rough estimate — 25s per turn average."""
    return max(1, round(num_turns * 25 / 60))


def _count_turns(input_dir: str, dialog_name: str) -> int:
    try:
        return len(parse_dialog_file(os.path.join(input_dir, dialog_name)))
    except Exception:
        return 0


def render_picker_html(
    input_dir: str, output_dir: str, collab: str, filt: str
) -> str:
    """Build the full picker page as a single HTML string."""
    from progress_tracking import list_partial, suggest_next

    all_dialogs = list_dialogs(input_dir)
    done_set = {n for n in all_dialogs if is_dialog_done(n, output_dir, collab)}
    partial = list_partial(output_dir, collab) if collab else {}
    next_suggested = suggest_next(output_dir, collab, all_dialogs) if collab else None

    total = len(all_dialogs)
    done_count = len(done_set)
    todo_count = total - done_count

    # ----- Top bar -----
    name_label = collab or "Đặt tên"
    top_bar = (
        "<div class='studio-topbar'>"
        "<div class='studio-logo'><span class='dot'></span>Studio</div>"
        "<div class='studio-spacer'></div>"
        f"<span class='studio-top-chip'>Tổng <b>{done_count}/{total}</b></span>"
        f"<span class='studio-name-pill' onclick='studioPromptForName()'>👤 {name_label}</span>"
        "</div>"
    )

    # ----- Hero CTA — only if we have a collab name and a suggestion -----
    cta_html = ""
    if next_suggested:
        n = next_suggested
        idx = all_dialogs.index(n["dialog_name"]) + 1
        if n["kind"] == "resume":
            title = "Thu tiếp câu kế tiếp chưa hoàn thành"
            sub = (
                f"Hội thoại #{idx} · dừng ở câu {n['last_recorded_turn'] + 1}"
            )
        else:
            title = "Bắt đầu hội thoại tiếp theo"
            sub = f"Hội thoại #{idx} · {_count_turns(input_dir, n['dialog_name'])} câu"
        cta_html = (
            "<div class='studio-cta' data-resume-cta>"
            "<div class='icon'>▶</div>"
            f"<div class='copy'><div class='title'>{title}</div>"
            f"<div class='sub'>{sub}</div></div>"
            "<button class='go-btn'>Bắt đầu →</button>"
            "</div>"
        )

    # ----- Section + filters -----
    filters_html = ""
    for key, label, n in [
        ("todo", "Chưa thu", todo_count),
        ("done", "Đã xong", done_count),
        ("all", "Tất cả", total),
    ]:
        cls = "studio-filter active" if filt == key else "studio-filter"
        filters_html += (
            f'<span data-filter="{key}" class="{cls}">{label} '
            f'<span style="opacity:.7">{n}</span></span>'
        )

    section_head = (
        "<div class='studio-section-head'>"
        "<div>"
        "<div class='studio-section-title'>Chọn hội thoại</div>"
        f"<div class='studio-section-sub'>Đã xong {done_count} · Chưa thu {todo_count} · Tổng {total}</div>"
        "</div>"
        f"<div class='studio-filters'>{filters_html}</div>"
        "</div>"
    )

    # ----- Grid -----
    visible = []
    for idx, name in enumerate(all_dialogs):
        is_done = name in done_set
        if filt == "todo" and is_done:
            continue
        if filt == "done" and not is_done:
            continue
        visible.append((idx, name, is_done))

    if not visible:
        if total == 0:
            grid = (
                "<div style='padding:40px 22px;text-align:center;color:#8f8a7a;'>"
                "Không tìm thấy hội thoại nào trong <code>input/</code>."
                "</div>"
            )
        elif filt == "todo":
            grid = (
                "<div style='padding:40px 22px;text-align:center;color:#8f8a7a;'>"
                "🎉 Bạn đã thu xong tất cả các hội thoại. Cảm ơn!"
                "</div>"
            )
        else:
            grid = "<div style='padding:40px 22px;text-align:center;color:#8f8a7a;'>Trống.</div>"
    else:
        cards = []
        for idx, name, is_done in visible:
            stem = Path(name).stem
            try:
                dt = datetime.strptime(name[:19], "%Y-%m-%dT%H-%M-%S")
                date_label = dt.strftime("%d/%m · %H:%M")
            except Exception:
                date_label = name[:10]
            num_turns = _count_turns(input_dir, name)
            mins = _estimate_duration_min(num_turns)
            badge = ("<span class='badge done'>ĐÃ XONG</span>"
                     if is_done else "<span class='badge todo'>CHƯA THU</span>")
            # partial[stem] is last_recorded_turn (0-indexed); approximate
            # recorded count as (last_recorded_turn + 1) when present.
            partial_count = (partial.get(stem, -1) + 1) if stem in partial else 0
            recorded_str = f"· {partial_count} câu thu" if partial_count else ""
            cls = "studio-card done" if is_done else "studio-card"
            extra_meta = (f"<span>{recorded_str.lstrip('· ')}</span>"
                          if recorded_str else "")
            cards.append(
                f"<div class='{cls}' data-card-dialog='{name}'>"
                f"<div class='head'><span class='num'>Hội thoại #{idx+1}</span>"
                f"{badge}<span class='date'>{date_label}</span></div>"
                f"<div class='meta'><span>💬 {num_turns} câu</span>"
                f"<span>⏱ ~{mins} phút</span>{extra_meta}</div>"
                "</div>"
            )
        grid = f"<div class='studio-grid'>{''.join(cards)}</div>"

    return top_bar + cta_html + section_head + grid


# ---------- Load static assets ----------
with open(os.path.join(_HERE, "studio.css"), encoding="utf-8") as _f:
    CSS = _f.read()

with open(os.path.join(_HERE, "studio.js"), encoding="utf-8") as _f:
    _STUDIO_JS = _f.read()

with gr.Blocks(
    title="Studio ghi âm trợ lý ảo",
) as app:

    # ───── Top-level state ─────
    # view: "picker" | "recording"
    view_state = gr.State("picker")
    # collaborator name (synced with localStorage via JS)
    collab_state = gr.State("")
    # filter on picker page: "todo" | "done" | "all"
    filter_state = gr.State("todo")
    # current recording state (the dict used by all action_* fns)
    state = gr.State({})

    # ───── Hidden orchestration components ─────
    # NOTE: visible=True + CSS-hidden so the DOM elements stay reachable from
    # studio.js via document.getElementById. Gradio drops the markup entirely
    # when visible=False, which breaks click delegation.
    studio_action_payload = gr.Textbox(
        elem_id="studio-action-payload",
        elem_classes=["studio-hidden"],
        show_label=False,
    )
    studio_stored_name = gr.Textbox(
        elem_id="studio-stored-name",
        elem_classes=["studio-hidden"],
        show_label=False,
    )
    # View marker — studio.js MutationObserver đọc data-studio-view-marker
    # rồi cập nhật <body data-studio-view> để CSS toggle picker/recording.
    studio_view_marker = gr.HTML(
        "<span data-studio-view-marker='picker' style='display:none'></span>",
        elem_id="studio-view-marker",
    )

    # ───── Picker view (top-level Column) ─────
    # CẢ HAI Column luôn visible=True. Hiển thị/ẩn được điều khiển bằng
    # `data-studio-view` trên <body> (đặt bởi studio.js + CSS rules):
    # - body[data-studio-view="picker"]    → ẩn .studio-recording
    # - body[data-studio-view="recording"] → ẩn .studio-picker
    # Lý do: gr.update(visible=...) trên Column ở Gradio 6.14 đôi khi không
    # apply đồng bộ với value-update của child HTML — gây cảnh user thấy
    # trắng màn rồi phải nhấn Enter để force re-render.
    with gr.Column(visible=True, elem_classes=["studio-picker"]) as picker_view:
        picker_html = gr.HTML(
            "<div style='padding:40px;text-align:center;color:#8f8a7a;'>Đang tải...</div>"
        )

    with gr.Column(visible=True, elem_classes=["studio-recording"]) as recording_view:
        recording_html = gr.HTML("")
        # The mic Audio component — kept as the only "real" Gradio input.
        # CSS minimises its chrome; clicks come from the rendered HTML.
        mic_audio = gr.Audio(
            sources=["microphone"],
            type="filepath",
            interactive=True,
            elem_id="mic-audio",
            visible=True,
            format="wav",
            show_label=False,
            buttons=[],
            waveform_options={"show_recording_waveform": False},
        )

    def _skip_recorded_assistant(st: dict) -> dict:
        """Skip past assistant turns that already have a recording.

        After re-recording an earlier turn and advancing forward, the user can
        otherwise get parked on a turn they've already recorded (e.g. recorded
        turns 1+3, came back to redo 1, after saving the next assistant turn
        is 3 — but they don't want to redo 3). Walk forward until we hit an
        un-recorded assistant turn, a user turn (always re-playable), or the
        end of the dialog.
        """
        dialog = st.get("dialog", [])
        recordings = st.get("recordings", {})
        idx = st.get("current_turn", 0)
        while idx < len(dialog):
            turn = dialog[idx]
            if turn["role"] == "assistant" and idx in recordings:
                idx += 1
            else:
                break
        st["current_turn"] = idx
        return st

    # ───── Dispatcher (single Python entry point for all dynamic JS actions) ─────
    def studio_dispatch(payload_json: str, view: str, collab: str, filt: str, st: dict):
        """Single Python entry point fired by studio.js for every dynamic action.

        Reads the JSON payload, dispatches to the right sub-handler, returns
        a tuple of all outputs (view_state, collab_state, filter_state, state,
        picker_html, recording_html, mic_audio, picker_view, recording_view).
        """
        import json as _json
        try:
            msg = _json.loads(payload_json or "{}")
        except Exception:
            msg = {}
        action = msg.get("action")
        data = msg.get("data", {})
        print(f"[dispatch] action={action} data={data}")

        if action == "set_name":
            collab = sanitize_collaborator_name(data.get("name", ""))
        elif action == "set_filter":
            new = data.get("filter")
            if new in ("todo", "done", "all"):
                filt = new
        elif action == "open_conversation":
            new_st = load_conversation_state(
                DEFAULT_INPUT_DIR, data.get("dialog", ""),
                DEFAULT_OUTPUT_DIR, collab,
            )
            if new_st and new_st.get("dialog"):
                st = new_st
                view = "recording"
        elif action == "resume_next":
            if collab:
                from progress_tracking import suggest_next
                all_d = list_dialogs(DEFAULT_INPUT_DIR)
                s = suggest_next(DEFAULT_OUTPUT_DIR, collab, all_d)
                if s:
                    resume_at = (s["last_recorded_turn"] + 1) if s["kind"] == "resume" else 0
                    new_st = load_conversation_state(
                        DEFAULT_INPUT_DIR, s["dialog_name"],
                        DEFAULT_OUTPUT_DIR, collab,
                        resume_at=resume_at,
                    )
                    if new_st and new_st.get("dialog"):
                        st = new_st
                        view = "recording"
        elif action == "back_to_picker":
            view = "picker"
        elif action == "save_and_next":
            st = dict(st or {})
            st["current_turn"] = st.get("current_turn", 0) + 1
            st["rec_phase"] = "idle"
            st = _skip_recorded_assistant(st)
        elif action == "jump_to":
            # User clicked a "Chưa thu" row in the rail — go straight to that
            # turn. Honest jump: no skipping, no auto-advance. Works for any
            # idx in range (assistant or user), so it could be reused later
            # for arbitrary navigation.
            target = data.get("idx")
            if target is not None and st and st.get("dialog"):
                idx2 = max(0, min(int(target), len(st["dialog"]) - 1))
                st = dict(st)
                st["current_turn"] = idx2
                st["rec_phase"] = "idle"
        elif action == "rerecord_last":
            st = dict(st or {})
            # Rail's "Thu lại" passes data.idx (most-recent-recorded turn);
            # hero's "Thu lại" in preview phase passes no idx (means "redo
            # the take I just made", i.e. current_turn).
            target = data.get("idx")
            idx2 = int(target) if target is not None else st.get("current_turn", 0)
            recs = dict(st.get("recordings", {}))
            recs.pop(idx2, None)
            st["recordings"] = recs
            st["current_turn"] = idx2  # jump back so hero shows that turn
            st["rec_phase"] = "idle"
        elif action == "skip_user":
            st = dict(st or {})
            st["current_turn"] = st.get("current_turn", 0) + 1
            st = _skip_recorded_assistant(st)
        elif action == "play_user_audio":
            st = dict(st or {})
            st["_play_request"] = ("user", int(data.get("idx", 0)))
        elif action == "play_assistant_audio":
            st = dict(st or {})
            st["_play_request"] = ("assistant", int(data.get("idx", 0)))
        elif action == "play_all":
            # Mark state so render_recording_html can build the full playlist
            # of data-URLs and inject a one-shot JS trigger to play them in
            # sequence client-side.
            st = dict(st or {})
            st["_playlist_request"] = True
        elif action == "finish":
            try:
                action_finish(st)
            except Exception as exc:
                print(f"[finish] {exc}")
            view = "picker"
            st = {}
        elif action == "kbd_enter":
            # Save & next, only valid in preview phase
            if st.get("rec_phase") == "preview":
                st = dict(st)
                st["current_turn"] = st.get("current_turn", 0) + 1
                st["rec_phase"] = "idle"
                st = _skip_recorded_assistant(st)
        elif action == "kbd_rerec":
            # Re-record current turn, only valid in preview phase
            if st.get("rec_phase") == "preview":
                st = dict(st)
                idx2 = st.get("current_turn", 0)
                recs = dict(st.get("recordings", {}))
                recs.pop(idx2, None)
                st["recordings"] = recs
                st["rec_phase"] = "idle"
        elif action == "kbd_skip":
            # Skip the current user turn (no-op on assistant turns)
            if st and st.get("dialog"):
                idx2 = st.get("current_turn", 0)
                if idx2 < len(st["dialog"]) and st["dialog"][idx2]["role"] == "user":
                    st = dict(st)
                    st["current_turn"] = idx2 + 1
                    st = _skip_recorded_assistant(st)
        elif action == "kbd_back":
            view = "picker"
        elif action == "kbd_space":
            # Space is context-sensitive — handled client-side in studio.js
            # by clicking the right visible button. No Python action needed;
            # this branch exists just to swallow the action.
            pass

        # Re-render whichever view is active
        picker_update = (
            gr.update(value=render_picker_html(
                DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, collab, filt
            ))
            if view == "picker" else gr.update()
        )
        recording_update = (
            gr.update(value=render_recording_html(st, collab))
            if view == "recording" else gr.update()
        )

        # Reset mic_audio whenever the turn changes (so the previous take
        # is cleared from the component and the record button reappears).
        # Don't reset on view-switching actions (open_conversation,
        # resume_next, back_to_picker, finish) — those mount/unmount the
        # recording view, so the component is already in its fresh state and
        # an extra value=None update can leave it in a transient state where
        # the record-button briefly isn't in the DOM.
        mic_reset_actions = {
            "save_and_next", "rerecord_last", "skip_user", "jump_to",
            "kbd_enter", "kbd_rerec", "kbd_skip",
        }
        mic_update = (
            gr.update(value=None) if action in mic_reset_actions else gr.update()
        )

        # View toggle giờ làm bằng `data-studio-view` trên <body> (do studio.js
        # đọc `view` qua marker này). KHÔNG dùng gr.update(visible=...) trên
        # Column nữa — Gradio 6.14 hay không apply kịp khiến UI trắng cho tới
        # khi có event thứ 2. Marker là 1 HTML invisible mà JS đọc bằng
        # MutationObserver để cập nhật `body[data-studio-view]`.
        view_marker = (
            f"<span data-studio-view-marker='{view}' style='display:none'></span>"
        )

        return (
            view, collab, filt, st,
            picker_update,
            recording_update,
            mic_update,
            gr.update(value=view_marker),
        )

    # Fire dispatcher on textbox change — every dispatchAction() write to
    # studio-action-payload includes a fresh nonce so the value always changes.
    # This is more reliable than clicking a hidden button (Gradio's button
    # markup can lose its elem_id in some 6.x renderings).
    studio_action_payload.change(
        fn=studio_dispatch,
        inputs=[studio_action_payload, view_state, collab_state, filter_state, state],
        outputs=[
            view_state, collab_state, filter_state, state,
            picker_html, recording_html, mic_audio,
            studio_view_marker,
        ],
        show_progress="hidden",
    )

    # ───── Mic event wirings ─────
    def _on_mic_start(st: dict, collab: str):
        st = dict(st or {})
        st["rec_phase"] = "recording"
        return st, gr.update(value=render_recording_html(st, collab))

    mic_audio.start_recording(
        fn=_on_mic_start,
        inputs=[state, collab_state],
        outputs=[state, recording_html],
        show_progress="hidden",
    )

    def _on_mic_stop(st: dict, mic_value, collab: str):
        """Audio recorded → save file, store, switch to preview phase."""
        if mic_value is None or not isinstance(mic_value, str) or not os.path.exists(mic_value):
            st = dict(st or {})
            st["rec_phase"] = "idle"
            return st, gr.update(value=render_recording_html(st, collab))

        try:
            audio, sr = sf.read(mic_value, dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
            idx2 = st["current_turn"]
            out_dir = st["output_dir"]
            os.makedirs(out_dir, exist_ok=True)
            final_path = os.path.realpath(
                os.path.join(out_dir, f"turn_{idx2:02d}_assistant.wav")
            )
            sf.write(final_path, audio_int16, sr)
        except Exception as exc:
            # Any failure in the read/process/write pipeline leaves no usable
            # recording. Reset to idle so the user can try again.
            print(f"[mic_stop] save failed: {exc}")
            st = dict(st or {})
            st["rec_phase"] = "idle"
            return st, gr.update(value=render_recording_html(st, collab))

        st = dict(st)
        recs = dict(st.get("recordings", {}))
        recs[idx2] = final_path
        st["recordings"] = recs
        st["rec_phase"] = "preview"

        # Write partial progress
        try:
            from progress_tracking import write_progress
            write_progress(
                st["output_dir"],
                last_recorded_turn=idx2,
                recorded_count=len(st["recordings"]),
            )
        except Exception as exc:
            print(f"[progress] write failed: {exc}")

        return st, gr.update(value=render_recording_html(st, collab))

    mic_audio.stop_recording(
        fn=_on_mic_stop,
        inputs=[state, mic_audio, collab_state],
        outputs=[state, recording_html],
        show_progress="hidden",
    )

    # ───── Initial render ─────
    def _initial_render():
        return gr.update(value=render_picker_html(
            DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, "", "todo"
        ))
    app.load(fn=_initial_render, inputs=None, outputs=[picker_html])


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
        theme=gr.themes.Soft(),
        css=CSS,
        head=f"<script>\n{_STUDIO_JS}\n</script>",
        allowed_paths=[
            os.path.realpath(os.path.join(here, "output")),
            os.path.realpath(os.path.join(here, "input")),
            os.path.realpath(DEFAULT_OUTPUT_DIR),
            os.path.realpath(DEFAULT_INPUT_DIR),
            USER_AUDIO_TMPDIR,
        ],
    )
