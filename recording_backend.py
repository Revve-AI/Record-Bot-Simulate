"""Pure helpers for the recording UI — dialog parsing, audio segmentation,
output paths. No Gradio imports. Anything UI-facing belongs in app.py."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

from text_utils import normalize_vietnamese_text

# ---------- Config ----------
SAMPLE_RATE = 16000
VAD_CHUNK = 512  # samples per VAD chunk @ 16 kHz

# ---------- VAD setup (load once at import) ----------
print("[recording_backend] Loading silero-vad...")
VAD_MODEL = load_silero_vad()

# Warm-up: JIT-compile graph so the first segment isn't slow
try:
    _warm = np.zeros(SAMPLE_RATE, dtype=np.float32)
    get_speech_timestamps(
        torch.from_numpy(_warm), VAD_MODEL, sampling_rate=SAMPLE_RATE,
        threshold=0.4, min_silence_duration_ms=400, min_speech_duration_ms=80,
    )
except Exception as exc:
    print(f"[recording_backend] Warm-up failed: {exc}")

# ---------- Regexes + role mapping ----------
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


# ---------- Functions ----------

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


def friendly_dialog_label(idx: int, name: str, done: bool) -> str:
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
        choices.append((friendly_dialog_label(idx, name, done), name))
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
