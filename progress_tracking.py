"""Per-conversation partial progress — written after each assistant-turn save.

Lives next to dialog.json in each session output dir. Removed when the
conversation is fully finished (action_finish).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


PROGRESS_FILENAME = "progress.json"
DIALOG_JSON = "dialog.json"


def write_progress(session_dir: str, last_recorded_turn: int, recorded_count: int) -> None:
    """Write progress.json into the given session directory. Idempotent."""
    os.makedirs(session_dir, exist_ok=True)
    payload = {
        "last_recorded_turn": int(last_recorded_turn),
        "recorded_count": int(recorded_count),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    path = os.path.join(session_dir, PROGRESS_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_progress(session_dir: str) -> dict | None:
    """Return the dict stored in progress.json, or None if missing/unreadable."""
    path = os.path.join(session_dir, PROGRESS_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def clear_progress(session_dir: str) -> None:
    """Remove progress.json if present. Idempotent."""
    path = os.path.join(session_dir, PROGRESS_FILENAME)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def list_partial(output_dir: str, collab_name: str) -> dict[str, int]:
    """Return {dialog_stem: last_recorded_turn} for every conversation with
    a progress.json but no dialog.json (i.e. started but not finished)."""
    base = Path(output_dir).expanduser() / collab_name
    if not base.exists():
        return {}
    result: dict[str, int] = {}
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if (child / DIALOG_JSON).exists():
            continue  # finished — not partial
        p = read_progress(str(child))
        if p:
            result[child.name] = p["last_recorded_turn"]
    return result


def suggest_next(
    output_dir: str, collab_name: str, all_dialog_names: list[str]
) -> dict | None:
    """Return the conversation the picker's hero CTA should resume.

    Order of preference:
      1. The most recently updated partial conversation (resume).
      2. The first unfinished, fresh conversation (kind="fresh").
      3. None if everything is done.
    """
    # Build map: dialog_stem -> dialog_name (preserve order)
    stem_to_name: dict[str, str] = {}
    for n in all_dialog_names:
        if n.endswith(".dialog"):
            stem_to_name[Path(n).stem] = n

    base = Path(output_dir).expanduser() / collab_name

    # Step 1 — find the most recently updated partial.
    most_recent: tuple[float, str, dict] | None = None
    if base.exists():
        for child in base.iterdir():
            if not child.is_dir() or (child / DIALOG_JSON).exists():
                continue
            p = read_progress(str(child))
            if not p:
                continue
            stat = (child / PROGRESS_FILENAME).stat().st_mtime
            if most_recent is None or stat > most_recent[0]:
                most_recent = (stat, child.name, p)

    if most_recent:
        _, stem, p = most_recent
        if stem in stem_to_name:
            return {
                "dialog_name": stem_to_name[stem],
                "kind": "resume",
                "last_recorded_turn": p["last_recorded_turn"],
                "recorded_count": p["recorded_count"],
            }

    # Step 2 — first fresh unfinished.
    for stem, name in stem_to_name.items():
        finished = base.exists() and (base / stem / DIALOG_JSON).exists()
        if not finished:
            return {
                "dialog_name": name,
                "kind": "fresh",
                "last_recorded_turn": None,
                "recorded_count": 0,
            }

    return None
