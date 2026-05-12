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

from recording_backend import (
    SAMPLE_RATE,
    parse_dialog_file,
    list_dialogs,
    sanitize_collaborator_name,
    session_output_dir,
    is_dialog_done,
    build_dropdown_choices,
    trim_silences,
    segment_user_turns,
)

# ---------- Config (app-only) ----------
SILENCE_MS = 1500                # tự dừng khi im lặng 1.5s
USER_PAUSE_SEC = 0.6             # nghỉ ngắn sau turn user trước khi sang câu kế
MAX_RECORDING_SEC = 90           # an toàn: dừng cứng sau 90s
DEFAULT_INPUT_DIR = "./input"
DEFAULT_OUTPUT_DIR = "./output"


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
    studio_action_payload = gr.Textbox(
        visible=False,
        elem_id="studio-action-payload",
        elem_classes=["studio-hidden"],
    )
    studio_action_trigger = gr.Button(
        visible=False,
        elem_id="studio-action-trigger",
        elem_classes=["studio-hidden"],
    )
    studio_stored_name = gr.Textbox(
        visible=False,
        elem_id="studio-stored-name",
        elem_classes=["studio-hidden"],
    )

    # ───── Picker view (top-level Column) ─────
    with gr.Column(visible=True, elem_classes=["studio-picker"]) as picker_view:
        picker_html = gr.HTML(
            "<div style='padding:40px;text-align:center;color:#8f8a7a;'>Đang tải...</div>"
        )

    # ───── Recording view (hidden until user picks) ─────
    with gr.Column(visible=False) as recording_view:
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

        # No-op fallback: re-render whichever view is visible (Tasks 7+ fill in)
        return (
            view, collab, filt, st,
            gr.update(),  # picker_html
            gr.update(),  # recording_html
            gr.update(),  # mic_audio
            gr.update(visible=(view == "picker")),
            gr.update(visible=(view == "recording")),
        )

    studio_action_trigger.click(
        fn=studio_dispatch,
        inputs=[studio_action_payload, view_state, collab_state, filter_state, state],
        outputs=[
            view_state, collab_state, filter_state, state,
            picker_html, recording_html, mic_audio,
            picker_view, recording_view,
        ],
        show_progress="hidden",
    )

    # ───── Initial render — placeholder until Task 7 ─────
    def _initial_render():
        return gr.update(
            value="<div style='padding:40px;text-align:center;color:#8f8a7a;'>"
                  "Đang tải… (Task 7 sẽ điền danh sách hội thoại)</div>"
        )
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
