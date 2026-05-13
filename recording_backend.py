"""Pure helpers for the recording UI — dialog parsing, audio segmentation,
output paths. No Gradio imports. Anything UI-facing belongs in app.py."""
from __future__ import annotations

import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

from text_utils import normalize_vietnamese_text

# ---------- Config ----------
SAMPLE_RATE = 16000

# ---------- Regexes + role mapping ----------
# Format dialog: "user: text"  hoặc  "assistant: text"
# Mỗi dòng PHẢI có `\tSTART\tEND` (sample index ở 16kHz) để cắt audio user.
# Turn nào thiếu timestamp sẽ bỏ qua audio (không có fallback nữa).
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

# Module-level cache cho WAV đã load. Mỗi entry giữ 1 mảng float32 mono
# (~10MB cho file 5 phút @16kHz). maxsize=8 → trần ~80MB RAM, đủ chứa
# vài hội thoại user đang xoay vòng giữa, tránh sf.read() lại mỗi click.
@lru_cache(maxsize=8)
def _load_wav_mono(wav_path: str) -> tuple[np.ndarray, int]:
    audio_np, sr = sf.read(wav_path, dtype="float32")
    if audio_np.ndim > 1:
        audio_np = audio_np.mean(axis=1)
    return audio_np, sr


def segment_user_turns(wav_path: str, dialog: list[dict]):
    """Tách audio user từ wav theo start/end samples trong dialog.

    Mỗi turn phải có `start_sample` / `end_sample` (sample index khớp SR file
    wav). Turn nào thiếu → trả về None cho turn đó.
    """
    if not wav_path or not os.path.exists(wav_path):
        return {i: None for i, t in enumerate(dialog) if t["role"] == "user"}

    try:
        audio_np, sr = _load_wav_mono(wav_path)
    except Exception as exc:
        print(f"[Warn] Không load được wav: {exc}")
        return {i: None for i, t in enumerate(dialog) if t["role"] == "user"}

    print(f"[segment] {os.path.basename(wav_path)}: "
          f"cắt theo timestamps (SR wav = {sr}Hz, cached)")

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
