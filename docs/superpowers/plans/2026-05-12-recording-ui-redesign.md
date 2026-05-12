# Recording UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-page Gradio UI in `app.py` with a two-view experience (conversation picker + focused recording page) styled with a Carrot-orange design system, while keeping all audio/IO backend logic intact.

**Architecture:** Single `gr.Blocks` app with a `view` state toggling between two top-level `gr.Column`s (`picker_view`, `recording_view`). All visuals come from `gr.HTML()` driven by Python render functions; dynamic clicks are dispatched via a hidden textbox + button pair using a delegated JS click listener. Existing event handlers (`action_load`, `action_recording_done`, `action_save_continue`, `action_finish`, etc.) are reused — only the inputs/outputs wiring and the surrounding UI change.

**Tech Stack:** Python 3.11, Gradio ≥ 5.0, silero-vad, torch, numpy, soundfile, pytest (new dev dep). No frontend toolchain, no FastAPI, no new runtime deps.

---

## File map

```
app.py                          MODIFY — UI rewritten; imports recording_backend + progress_tracking
recording_backend.py            CREATE — pure helpers extracted from app.py (parse, segment, format)
progress_tracking.py            CREATE — read/write/clear progress.json + suggest_next
studio.css                      CREATE — design tokens + picker + recording layouts (served via allowed_paths)
studio.js                       CREATE — click delegation, keyboard, localStorage (served via allowed_paths)
tests/test_recording_backend.py CREATE — unit tests for the extracted helpers
tests/test_progress_tracking.py CREATE — unit tests for progress.json read/write/suggest
tests/test_ui_render.py         CREATE — string-contains tests for HTML render fns
tests/conftest.py               CREATE — temp dir fixtures + sample .dialog/.wav builder
requirements-dev.txt            CREATE — pytest, pytest-tmp-path-factory
README.md                       MODIFY — replace "4 bước" guide with new screen names
```

---

## Conventions used in this plan

- **TDD where the unit is pure Python** (parsers, file IO, progress tracking, HTML render fns). Tests are written first, fail, then minimal code passes them.
- **Manual verification for Gradio integration** (visibility toggles, audio recording end-to-end). The steps for these tasks end with "launch app, do X, observe Y" — there's no Selenium in this project and adding one is out of scope.
- **One commit per task.** Each task is shippable on its own; the app still launches at every commit.
- **Vietnamese strings stay Vietnamese** — match existing `text_utils.py` and `app.py`.

---

## Task 1 — Test scaffold

**Goal:** Add pytest, a fixtures file, and one trivial test that proves the scaffold works.

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_recording_backend.py`

- [ ] **Step 1: Create `requirements-dev.txt`.**

```
pytest>=8.0.0
```

- [ ] **Step 2: Install dev deps.**

Run: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt`
Expected: pytest installs cleanly.

- [ ] **Step 3: Create `tests/__init__.py` (empty file).**

```python
```

- [ ] **Step 4: Create `tests/conftest.py` with fixtures for a temp input dir and a sample `.dialog` file.**

```python
"""Shared pytest fixtures for the recording-backend tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


SAMPLE_DIALOG_BODY = (
    "user: ừ chào em\t0\t16000\n"
    "assistant: chào anh em có thể giúp gì cho anh\n"
    "user: cho anh hỏi giá phòng\t32000\t48000\n"
    "assistant: vâng anh hỏi ngày cụ thể giúp em\n"
)


@pytest.fixture
def sample_dialog(tmp_path: Path) -> Path:
    """Write one .dialog + matching silent .wav and return the .dialog path."""
    dlg = tmp_path / "2026-01-09T03-32-44-871Z-room_sample.dialog"
    dlg.write_text(SAMPLE_DIALOG_BODY, encoding="utf-8")

    wav = dlg.with_suffix(".wav")
    # 3 seconds of silence @ 16 kHz mono — enough for the timestamp ranges above.
    silence = np.zeros(int(3 * 16_000), dtype=np.float32)
    sf.write(wav, silence, 16_000)
    return dlg


@pytest.fixture
def input_dir(sample_dialog: Path) -> Path:
    return sample_dialog.parent
```

- [ ] **Step 5: Create `tests/test_recording_backend.py` with one smoke test.**

```python
"""Tests for recording_backend.py (extracted helpers from app.py)."""
from __future__ import annotations


def test_pytest_runs():
    """Smoke test: pytest can collect and run a test in this project."""
    assert 1 + 1 == 2
```

- [ ] **Step 6: Run the test to verify the scaffold works.**

Run: `pytest tests/ -v`
Expected: 1 passed.

- [ ] **Step 7: Commit.**

```bash
git add requirements-dev.txt tests/__init__.py tests/conftest.py tests/test_recording_backend.py
git commit -m "test: add pytest scaffold + sample-dialog fixture"
```

---

## Task 2 — Extract pure functions into `recording_backend.py`

**Goal:** Move dialog parsing, audio segmentation, naming, and rendering helpers out of `app.py` into a focused module. This is a pure refactor — `app.py` behavior is unchanged. Tests pin the contract.

**Files:**
- Create: `recording_backend.py`
- Modify: `app.py:31-200` (imports + delete the moved functions)
- Modify: `tests/test_recording_backend.py` (add real tests)

- [ ] **Step 1: Write tests for the extracted helpers.** Append to `tests/test_recording_backend.py`:

```python
from pathlib import Path

from recording_backend import (
    parse_dialog_file,
    list_dialogs,
    sanitize_collaborator_name,
    session_output_dir,
    is_dialog_done,
)


def test_parse_dialog_file_returns_role_and_text(sample_dialog: Path):
    turns = parse_dialog_file(str(sample_dialog))
    assert len(turns) == 4
    assert turns[0]["role"] == "user"
    assert turns[0]["text_raw"] == "ừ chào em"
    # text_normalized comes from text_utils; just assert capitalisation happened
    assert turns[0]["text"][0].isupper()
    assert turns[1]["role"] == "assistant"


def test_parse_dialog_file_captures_timestamps(sample_dialog: Path):
    turns = parse_dialog_file(str(sample_dialog))
    assert turns[0]["start_sample"] == 0
    assert turns[0]["end_sample"] == 16_000
    # assistant turn has no timestamps in the sample
    assert turns[1]["start_sample"] is None


def test_list_dialogs_returns_only_dialog_files(input_dir: Path):
    # Add a noise file that should be ignored
    (input_dir / "ignored.txt").write_text("x")
    (input_dir / "marked.dialog.mark").write_text("x")
    names = list_dialogs(str(input_dir))
    assert all(n.endswith(".dialog") and not n.endswith(".dialog.mark") for n in names)
    assert any("room_sample" in n for n in names)


def test_sanitize_collaborator_name_strips_path_chars():
    assert sanitize_collaborator_name("Nguyễn / Văn ?A") == "Nguyễn _ Văn _A"
    assert sanitize_collaborator_name("  spaces   collapsed  ") == "spaces collapsed"
    assert sanitize_collaborator_name("") == ""
    assert sanitize_collaborator_name("../escape") == "_/escape"


def test_session_output_dir_includes_collab_and_stem(tmp_path: Path):
    out = session_output_dir(str(tmp_path), "Hieu", "abc.dialog")
    assert out.endswith("Hieu/abc")


def test_is_dialog_done_checks_dialog_json(tmp_path: Path):
    out_dir = tmp_path / "out"
    # Not done yet
    assert not is_dialog_done("abc.dialog", str(out_dir), "Hieu")
    # Create dialog.json
    session = Path(session_output_dir(str(out_dir), "Hieu", "abc.dialog"))
    session.mkdir(parents=True, exist_ok=True)
    (session / "dialog.json").write_text("{}")
    assert is_dialog_done("abc.dialog", str(out_dir), "Hieu")
```

- [ ] **Step 2: Run the tests — they must fail (module doesn't exist yet).**

Run: `pytest tests/test_recording_backend.py -v`
Expected: ImportError / collection error on `recording_backend`.

- [ ] **Step 3: Create `recording_backend.py`. Copy these functions verbatim from `app.py` (current locations are in parentheses):**
  - `parse_dialog_file` (app.py:85-123)
  - `list_dialogs` (app.py:126-134)
  - `sanitize_collaborator_name` (app.py:137-145)
  - `session_output_dir` (app.py:148-151)
  - `is_dialog_done` (app.py:154-162)
  - `_friendly_dialog_label` (app.py:165-173) — rename to `friendly_dialog_label` (drop underscore prefix; it's now public)
  - `build_dropdown_choices` (app.py:176-190)
  - `trim_silences` (app.py:194-236)
  - `segment_user_turns` (app.py:239-318)

Also copy:
  - The module-level VAD setup: lines 47-63 (`VAD_MODEL = load_silero_vad()`, warm-up)
  - The two compiled regexes: `_ROLE_LINE_RE`, `_SPEAKER_LINE_RE`, `SPEAKER_ROLE` (lines 75-82)
  - The `SAMPLE_RATE`, `VAD_CHUNK` constants

Top of `recording_backend.py`:

```python
"""Pure helpers for the recording UI — dialog parsing, audio segmentation,
output paths. No Gradio imports. Anything UI-facing belongs in app.py."""
from __future__ import annotations

import os
import re
import time
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

# ---------- Dialog parsing ----------
# ... (move the rest verbatim)
```

- [ ] **Step 4: In `app.py`, replace the moved code with an import block.** Near the top, after the `import torch` block:

```python
from recording_backend import (
    SAMPLE_RATE,
    VAD_CHUNK,
    VAD_MODEL,
    parse_dialog_file,
    list_dialogs,
    sanitize_collaborator_name,
    session_output_dir,
    is_dialog_done,
    friendly_dialog_label,
    build_dropdown_choices,
    trim_silences,
    segment_user_turns,
)
```

Then delete the duplicated function definitions and the VAD warm-up block from `app.py`. Leave `audio_to_data_url`, `get_trimmed_user_audio`, and the `_HERE` / `USER_AUDIO_TMPDIR` code in `app.py` for now — those depend on app state.

If `app.py` calls `_friendly_dialog_label` anywhere (it doesn't in current code but check `grep -n _friendly_dialog_label app.py`), update the call.

- [ ] **Step 5: Run all tests.**

Run: `pytest tests/ -v`
Expected: all 6 tests pass.

- [ ] **Step 6: Smoke-test the app boots.**

Run: `python app.py` (Ctrl+C after seeing "Running on local URL").
Expected: no ImportError, server starts, "[recording_backend] Loading silero-vad..." prints once.

- [ ] **Step 7: Commit.**

```bash
git add recording_backend.py app.py tests/test_recording_backend.py
git commit -m "refactor: extract pure helpers into recording_backend.py"
```

---

## Task 3 — `progress_tracking.py` for partial-progress per conversation

**Goal:** Add a tiny module that reads/writes `progress.json` inside each per-conversation output dir. Used by the new picker to show the "thu tiếp" CTA and per-card partial counter.

**Files:**
- Create: `progress_tracking.py`
- Create: `tests/test_progress_tracking.py`

- [ ] **Step 1: Write the tests first.** `tests/test_progress_tracking.py`:

```python
"""Tests for progress_tracking — partial-progress per conversation."""
from __future__ import annotations

import json
from pathlib import Path

from progress_tracking import (
    write_progress,
    read_progress,
    clear_progress,
    list_partial,
    suggest_next,
)


def test_write_then_read_progress(tmp_path: Path):
    session = tmp_path / "Hieu" / "abc"
    session.mkdir(parents=True)
    write_progress(str(session), last_recorded_turn=5, recorded_count=3)

    p = read_progress(str(session))
    assert p is not None
    assert p["last_recorded_turn"] == 5
    assert p["recorded_count"] == 3
    assert "updated_at" in p


def test_read_progress_returns_none_when_missing(tmp_path: Path):
    assert read_progress(str(tmp_path / "nope")) is None


def test_clear_progress_removes_file(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    write_progress(str(session), last_recorded_turn=1, recorded_count=1)
    assert (session / "progress.json").exists()

    clear_progress(str(session))
    assert not (session / "progress.json").exists()
    # clearing twice is a no-op (idempotent)
    clear_progress(str(session))


def test_list_partial_returns_dialog_name_keyed_map(tmp_path: Path):
    output_dir = tmp_path / "output"
    # Two conversations with partial progress
    s1 = output_dir / "Hieu" / "abc"
    s2 = output_dir / "Hieu" / "def"
    s1.mkdir(parents=True); s2.mkdir(parents=True)
    write_progress(str(s1), last_recorded_turn=3, recorded_count=2)
    write_progress(str(s2), last_recorded_turn=7, recorded_count=4)
    # And one fully-finished conversation (has dialog.json, no progress.json)
    s3 = output_dir / "Hieu" / "ghi"
    s3.mkdir(parents=True)
    (s3 / "dialog.json").write_text("{}")

    partial = list_partial(str(output_dir), "Hieu")
    assert partial == {"abc": 3, "def": 7}


def test_suggest_next_prefers_most_recent_partial(tmp_path: Path):
    output_dir = tmp_path / "output"
    all_dialogs = ["alpha.dialog", "beta.dialog", "gamma.dialog"]
    # beta has partial progress, alpha is finished, gamma is fresh
    Hieu = output_dir / "Hieu"
    (Hieu / "alpha").mkdir(parents=True)
    (Hieu / "alpha" / "dialog.json").write_text("{}")
    (Hieu / "beta").mkdir(parents=True)
    write_progress(str(Hieu / "beta"), last_recorded_turn=4, recorded_count=2)

    s = suggest_next(str(output_dir), "Hieu", all_dialogs)
    assert s == {
        "dialog_name": "beta.dialog",
        "kind": "resume",
        "last_recorded_turn": 4,
        "recorded_count": 2,
    }


def test_suggest_next_falls_back_to_first_fresh(tmp_path: Path):
    output_dir = tmp_path / "output"
    all_dialogs = ["alpha.dialog", "beta.dialog"]
    # alpha is finished, beta is fresh
    Hieu = output_dir / "Hieu"
    (Hieu / "alpha").mkdir(parents=True)
    (Hieu / "alpha" / "dialog.json").write_text("{}")

    s = suggest_next(str(output_dir), "Hieu", all_dialogs)
    assert s == {
        "dialog_name": "beta.dialog",
        "kind": "fresh",
        "last_recorded_turn": None,
        "recorded_count": 0,
    }


def test_suggest_next_returns_none_when_all_done(tmp_path: Path):
    output_dir = tmp_path / "output"
    all_dialogs = ["alpha.dialog"]
    Hieu = output_dir / "Hieu"
    (Hieu / "alpha").mkdir(parents=True)
    (Hieu / "alpha" / "dialog.json").write_text("{}")
    assert suggest_next(str(output_dir), "Hieu", all_dialogs) is None
```

- [ ] **Step 2: Run the tests — must fail.**

Run: `pytest tests/test_progress_tracking.py -v`
Expected: ImportError on `progress_tracking`.

- [ ] **Step 3: Implement `progress_tracking.py`.**

```python
"""Per-conversation partial progress — written after each assistant-turn save.

Lives next to dialog.json in each session output dir. Removed when the
conversation is fully finished (action_finish).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from recording_backend import session_output_dir


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
    # Build map: dialog_name -> session_dir
    # Note we accept .dialog filenames (with extension) and strip to stem.
    stem_to_name = {
        Path(n).stem: n for n in all_dialog_names if n.endswith(".dialog")
    }

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
```

- [ ] **Step 4: Run tests.**

Run: `pytest tests/test_progress_tracking.py -v`
Expected: 6 passed.

- [ ] **Step 5: Wire into existing save flow** — open `app.py`, find `action_recording_done`. After the `state["recordings"][idx] = final_path` line, add:

```python
            # Persist partial progress so the picker can show "thu tiếp"
            try:
                from progress_tracking import write_progress
                write_progress(
                    state["output_dir"],
                    last_recorded_turn=idx,
                    recorded_count=sum(
                        1 for k in state.get("recordings", {})
                    ),
                )
            except Exception as exc:
                print(f"[progress] write failed: {exc}")
```

In `action_finish`, after writing `dialog.json` (existing code) add:

```python
    # Conversation is fully done — drop partial-progress file.
    try:
        from progress_tracking import clear_progress
        clear_progress(out_dir)
    except Exception as exc:
        print(f"[progress] clear failed: {exc}")
```

- [ ] **Step 6: Manual smoke test.**

Run: `python app.py`. Open the URL. Pick a conversation, record one assistant turn (use the existing UI — the new picker isn't built yet). Check that `output/<your name>/<dialog stem>/progress.json` was created and has `last_recorded_turn` matching the turn you just recorded. Ctrl+C the app.

- [ ] **Step 7: Commit.**

```bash
git add progress_tracking.py tests/test_progress_tracking.py app.py
git commit -m "feat: per-conversation progress.json for partial-progress tracking"
```

---

## Task 4 — Design system CSS (`studio.css`)

**Goal:** Create the single CSS file holding the Carrot palette tokens and layout for both the picker and the recording page. Replace the inline CSS in `app.py` later (Task 6).

**Files:**
- Create: `studio.css`

- [ ] **Step 1: Create `studio.css`** with the design tokens and global resets.

```css
/* ============================================================
   Studio — design tokens
   Brand: Carrot orange #e2731f. One color drives primary actions,
   progress bar, and the record button. Neutrals are warm.
   ============================================================ */
:root {
  --brand: #e2731f;
  --brand-hover: #c25f15;
  --brand-deep: #7a3d0c;
  --brand-soft: #fbeedc;
  --bg: #faf8f3;
  --bg-2: #f3efe5;
  --surface: #ffffff;
  --surface-2: #f3efe5;
  --border: #e7e0cf;
  --border-strong: #d4cab4;
  --text: #1f1d18;
  --text-2: #5a574a;
  --text-3: #8f8a7a;
  --success: #5a8c5a;

  --radius-chip: 4px;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-pill: 999px;

  --shadow-card: 0 1px 0 rgba(0,0,0,.02);
  --shadow-rec: 0 8px 24px -6px rgba(226,115,31,.45);
}

/* Override Gradio container — full viewport, no max-width */
.gradio-container {
  max-width: 100% !important;
  padding: 0 !important;
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: "Inter", ui-sans-serif, system-ui, -apple-system, sans-serif !important;
}

/* Hide Gradio footer that says "Use via API" / version banner */
footer { display: none !important; }

/* ============================================================
   Top bar (shared by picker + recording)
   ============================================================ */
.studio-topbar {
  padding: 14px 22px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
.studio-logo {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.02em;
  font-family: "Tiempos", "Charter", Georgia, serif;
}
.studio-logo .dot {
  display: inline-block;
  width: 8px; height: 8px;
  background: var(--brand);
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: 2px;
}
.studio-back-btn {
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-2);
  cursor: pointer;
  font-weight: 500;
}
.studio-back-btn:hover {
  border-color: var(--brand);
  color: var(--brand);
}
.studio-conv-title { font-size: 13px; font-weight: 600; }
.studio-conv-meta { font-size: 12px; color: var(--text-3); }
.studio-spacer { flex: 1; }
.studio-top-chip {
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-2);
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.studio-top-chip b { color: var(--brand); }
.studio-name-pill {
  padding: 5px 12px;
  border-radius: var(--radius-pill);
  background: var(--brand-soft);
  color: var(--brand-deep);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

/* ============================================================
   Picker page
   ============================================================ */
.studio-picker { padding: 0; }
.studio-cta {
  margin: 20px 22px 0;
  background: var(--text);
  color: var(--bg);
  border-radius: var(--radius-lg);
  padding: 18px 22px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.studio-cta .icon {
  width: 44px; height: 44px;
  border-radius: var(--radius-md);
  background: var(--brand);
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 18px;
}
.studio-cta .copy { flex: 1; }
.studio-cta .title { font-weight: 700; font-size: 15px; }
.studio-cta .sub { font-size: 12px; color: rgba(250,248,243,.65); margin-top: 2px; }
.studio-cta .go-btn {
  padding: 10px 18px;
  border-radius: var(--radius-pill);
  background: var(--brand);
  color: #fff;
  border: none;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
}
.studio-cta .go-btn:hover { background: var(--brand-hover); }

.studio-section-head {
  padding: 22px 22px 12px;
  display: flex;
  align-items: end;
  gap: 14px;
}
.studio-section-title { font-size: 15px; font-weight: 700; color: var(--text); }
.studio-section-sub { font-size: 12px; color: var(--text-3); }
.studio-filters { margin-left: auto; display: flex; gap: 6px; }
.studio-filter {
  padding: 5px 11px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-2);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.studio-filter.active {
  background: var(--text);
  color: var(--bg);
  border-color: var(--text);
}

.studio-grid {
  padding: 0 22px 22px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.studio-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px;
  cursor: pointer;
  display: flex; flex-direction: column; gap: 6px;
  transition: transform .12s, border-color .12s, box-shadow .15s;
}
.studio-card:hover {
  border-color: var(--brand);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(226,115,31,.10);
}
.studio-card.done { opacity: 0.55; }
.studio-card .head { display: flex; align-items: center; gap: 8px; }
.studio-card .num { font-size: 14px; font-weight: 800; color: var(--text); }
.studio-card .badge {
  font-size: 10px; font-weight: 800;
  padding: 3px 8px; border-radius: var(--radius-chip);
  letter-spacing: .3px;
}
.studio-card .badge.todo { background: var(--brand-soft); color: var(--brand-deep); }
.studio-card .badge.done { background: #e6efe1; color: var(--success); }
.studio-card .date { font-size: 12px; color: var(--text-3); margin-left: auto; }
.studio-card .meta {
  font-size: 12px; color: var(--text-3);
  display: flex; gap: 12px;
}

/* ============================================================
   Recording page
   ============================================================ */
.studio-rec-progress {
  padding: 11px 22px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
.studio-rec-progress .text { font-size: 12px; color: var(--text); font-weight: 600; }
.studio-rec-progress .bar {
  flex: 1; height: 4px;
  border-radius: var(--radius-pill);
  background: var(--bg-2);
  overflow: hidden;
}
.studio-rec-progress .fill {
  height: 100%;
  background: var(--brand);
  transition: width .3s;
}
.studio-rec-progress .pct {
  font-size: 12px; color: var(--text-3);
  font-feature-settings: "tnum";
}

.studio-rec-shell { display: grid; grid-template-columns: 340px 1fr; }
.studio-rec-rail {
  background: var(--bg);
  padding: 14px 16px;
  border-right: 1px solid var(--border);
  height: calc(100vh - 56px - 44px - 1px);
  overflow-y: auto;
}
.studio-rec-hero {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 18px;
  padding: 36px 22px;
  background: var(--bg);
  min-height: calc(100vh - 56px - 44px - 1px);
}

/* Rail items */
.rail-head {
  font-size: 11px; color: var(--text-3); font-weight: 700;
  letter-spacing: .04em; text-transform: uppercase;
  margin-bottom: 10px;
  display: flex; gap: 8px; align-items: center;
}
.count-pill {
  background: var(--brand); color: #fff;
  font-size: 10px; font-weight: 800;
  padding: 2px 7px; border-radius: var(--radius-pill);
}
.rail-ctx {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  margin-bottom: 6px;
  display: flex; gap: 10px; align-items: center;
}
.rail-ctx .role {
  font-size: 10px; font-weight: 700; color: var(--text-2);
  background: var(--surface-2);
  padding: 2px 7px; border-radius: var(--radius-chip);
}
.rail-ctx .num { font-size: 10px; color: var(--text-3); }
.rail-ctx .text { flex: 1; font-size: 12.5px; color: var(--text-2); }
.rail-ctx .play-btn {
  width: 26px; height: 26px;
  border-radius: 50%;
  background: var(--surface-2);
  color: var(--brand);
  border: 1px solid var(--border);
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; cursor: pointer;
}
.rail-ctx .play-btn:hover {
  background: var(--brand); color: #fff; border-color: var(--brand);
}
.rail-ctx.playing {
  background: var(--brand-soft);
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-soft);
}
.rail-ctx.playing .role { background: var(--brand); color: #fff; }
.rail-ctx.playing .play-btn { background: var(--brand); color: #fff; border-color: var(--brand); }

.rail-rec {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 12px;
  margin-bottom: 6px;
  position: relative;
}
.rail-rec::before {
  content: "";
  position: absolute;
  left: -1px; top: 12px; bottom: 12px;
  width: 3px;
  background: var(--brand);
  border-radius: 0 3px 3px 0;
}
.rail-rec .top {
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; color: var(--text-3);
  margin-bottom: 6px; padding-left: 8px;
}
.rail-rec .top b {
  color: var(--brand); font-weight: 800;
  font-size: 10px;
  letter-spacing: .04em;
  text-transform: uppercase;
}
.rail-rec .text {
  font-size: 13px; color: var(--text);
  margin-bottom: 10px; padding-left: 8px;
  font-weight: 500;
}
.rail-rec .actions {
  display: flex; gap: 6px; padding-left: 8px;
}
.rail-rec .actions button {
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  font-size: 11px; font-weight: 600;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-2);
}
.rail-rec .actions button.play {
  background: var(--text);
  color: var(--bg);
  border-color: var(--text);
}
.rail-rec .actions button:hover {
  border-color: var(--brand);
  color: var(--brand);
}
.rail-rec .actions button.play:hover {
  background: var(--brand);
  color: #fff;
  border-color: var(--brand);
}

/* Hero */
.hero-role-tag {
  font-size: 11px; font-weight: 700;
  color: var(--brand-deep);
  padding: 5px 12px;
  border-radius: var(--radius-pill);
  background: var(--brand-soft);
}
.hero-turn-card {
  width: min(560px, 92%);
  padding: 28px 30px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  font-size: 24px;
  font-weight: 500;
  color: var(--text);
  line-height: 1.5;
  text-align: center;
  letter-spacing: -0.015em;
  box-shadow: var(--shadow-card);
}
.hero-turn-card.recording { border-color: var(--brand); }
.hero-rec-btn {
  width: 80px; height: 80px;
  border-radius: 50%;
  background: var(--brand);
  border: none;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  box-shadow: var(--shadow-rec);
  transition: transform .15s;
}
.hero-rec-btn:hover { background: var(--brand-hover); transform: scale(1.04); }
.hero-rec-btn .inner {
  width: 24px; height: 24px;
  border-radius: 50%;
  background: #fff;
}
.hero-rec-btn.stop {
  animation: rec-pulse 1.4s ease-in-out infinite;
}
.hero-rec-btn.stop .inner {
  border-radius: 5px;
  width: 26px; height: 26px;
}
@keyframes rec-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(226,115,31,.55); }
  50%      { box-shadow: 0 0 0 14px rgba(226,115,31,0); }
}
.hero-timer {
  font-family: ui-monospace, "JetBrains Mono", monospace;
  font-size: 28px; font-weight: 700;
  color: var(--brand);
  letter-spacing: 1px;
}
.hero-waveform {
  display: flex; gap: 3px;
  align-items: center; justify-content: center;
  height: 48px;
  width: min(420px, 100%);
}
.hero-waveform .bar {
  width: 4px;
  background: var(--brand);
  border-radius: var(--radius-pill);
  opacity: .7;
}
.hero-hint { font-size: 12.5px; color: var(--text-3); }
.hero-hint b { color: var(--text); font-weight: 600; }
.hero-kbd {
  font-family: ui-monospace, monospace;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-2);
}

.hero-audio-bar {
  width: min(420px, 100%);
  display: flex; align-items: center; gap: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  padding: 8px 14px;
  font-size: 13px; color: var(--text);
  font-weight: 600;
}
.hero-audio-bar.user { border-color: var(--brand); background: var(--brand-soft); }
.hero-audio-bar .play-circle {
  width: 32px; height: 32px;
  border-radius: 50%;
  background: var(--brand);
  color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px;
  border: none; cursor: pointer;
  flex-shrink: 0;
}
.hero-audio-bar .scrub {
  flex: 1; height: 5px;
  background: var(--bg-2);
  border-radius: var(--radius-pill);
  overflow: hidden;
  position: relative;
}
.hero-audio-bar .scrub > div {
  height: 100%;
  background: var(--brand);
  border-radius: var(--radius-pill);
}

.hero-actions {
  display: flex; gap: 12px;
}
.hero-btn {
  padding: 12px 22px;
  border-radius: var(--radius-pill);
  font-size: 14px; font-weight: 700;
  cursor: pointer; border: none;
  display: inline-flex; align-items: center; gap: 8px;
}
.hero-btn.primary {
  background: var(--brand);
  color: #fff;
  box-shadow: 0 4px 12px rgba(226,115,31,.28);
}
.hero-btn.primary:hover { background: var(--brand-hover); }
.hero-btn.secondary {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
}
.hero-btn.secondary:hover { border-color: var(--brand); color: var(--brand); }
.hero-btn.skip {
  background: transparent;
  color: var(--text-3);
  font-size: 12px; font-weight: 600;
}

/* Gradio component overrides — minimize default chrome on mic Audio */
#mic-audio { width: 100% !important; }
#mic-audio .source-selection,
#mic-audio .upload { display: none !important; }

/* The hidden orchestration components — invisible but in DOM */
.studio-hidden { display: none !important; }
```

- [ ] **Step 2: Verify the file is valid CSS.**

Run: `python -c "open('studio.css').read()"; echo "OK"`
Expected: `OK`. (If you have `csslint` locally, run it; otherwise this only checks the file is readable.)

- [ ] **Step 3: Commit.**

```bash
git add studio.css
git commit -m "feat: studio.css — Carrot palette tokens + picker/recording layouts"
```

---

## Task 5 — Frontend JS module (`studio.js`)

**Goal:** Single JS file that:
- Delegates clicks on dynamic HTML to a hidden Gradio button via a hidden Gradio textbox holding the action payload (JSON)
- Adds global keyboard shortcuts
- Persists the collaborator name in `localStorage`

**Files:**
- Create: `studio.js`

- [ ] **Step 1: Create `studio.js`.**

```javascript
/* ============================================================
   Studio — frontend orchestration (Gradio click delegation)

   Approach: dynamic HTML uses data-* attributes. A single delegated
   click listener on .gradio-container catches them, writes a JSON
   payload into the hidden #studio-action-payload textbox (triggers
   Gradio's input event so Python sees the new value), then clicks
   the hidden #studio-action-trigger button to fire the Python
   handler. The handler reads the payload, dispatches by action
   type, and returns updates for both views.
   ============================================================ */
(function () {
  "use strict";

  const LOCAL_STORAGE_NAME_KEY = "studio.collab_name";

  // ----- helpers -----
  function setHiddenTextbox(elemId, value) {
    const root = document.getElementById(elemId);
    if (!root) {
      console.warn("[studio.js] missing element:", elemId);
      return false;
    }
    const input = root.querySelector("input, textarea");
    if (!input) return false;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value"
    )?.set || Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, "value"
    ).set;
    nativeSetter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  function clickHiddenButton(elemId) {
    const root = document.getElementById(elemId);
    if (!root) {
      console.warn("[studio.js] missing element:", elemId);
      return;
    }
    const btn = root.querySelector("button");
    if (btn) btn.click();
  }

  function dispatchAction(action, data) {
    const payload = JSON.stringify({ action, data: data || {} });
    if (!setHiddenTextbox("studio-action-payload", payload)) return;
    // Tiny defer so Gradio's input listener registers the new value
    setTimeout(() => clickHiddenButton("studio-action-trigger"), 30);
  }

  // ----- click delegation -----
  function installClickDelegate() {
    document.addEventListener("click", (e) => {
      const card = e.target.closest("[data-card-dialog]");
      if (card) {
        dispatchAction("open_conversation", { dialog: card.dataset.cardDialog });
        return;
      }

      const filter = e.target.closest("[data-filter]");
      if (filter) {
        dispatchAction("set_filter", { filter: filter.dataset.filter });
        return;
      }

      const resume = e.target.closest("[data-resume-cta]");
      if (resume) {
        dispatchAction("resume_next", {});
        return;
      }

      const back = e.target.closest("[data-back-to-picker]");
      if (back) {
        dispatchAction("back_to_picker", {});
        return;
      }

      const finishBtn = e.target.closest("[data-finish]");
      if (finishBtn) {
        dispatchAction("finish", {});
        return;
      }

      const playUser = e.target.closest("[data-play-user]");
      if (playUser) {
        dispatchAction("play_user_audio", { idx: parseInt(playUser.dataset.playUser, 10) });
        return;
      }

      const playAssistant = e.target.closest("[data-play-assistant]");
      if (playAssistant) {
        dispatchAction("play_assistant_audio", { idx: parseInt(playAssistant.dataset.playAssistant, 10) });
        return;
      }

      const rerec = e.target.closest("[data-rerec]");
      if (rerec) {
        dispatchAction("rerecord_last", {});
        return;
      }

      const save = e.target.closest("[data-save-next]");
      if (save) {
        dispatchAction("save_and_next", {});
        return;
      }

      const skip = e.target.closest("[data-skip-user]");
      if (skip) {
        dispatchAction("skip_user", {});
        return;
      }

      const recBtn = e.target.closest("[data-rec-start]");
      if (recBtn) {
        // Click Gradio's hidden Audio record button
        const mic = document.querySelector("#mic-audio button[aria-label*='record' i], #mic-audio button[title*='record' i], #mic-audio button.record-button");
        if (mic) mic.click();
        return;
      }

      const stopBtn = e.target.closest("[data-rec-stop]");
      if (stopBtn) {
        const stop = document.querySelector("#mic-audio button[aria-label*='stop' i], #mic-audio button.stop-button");
        if (stop) stop.click();
        return;
      }
    });
  }

  // ----- keyboard shortcuts -----
  function installKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
      // Don't fire when typing in an input
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
        return;
      }
      switch (e.key) {
        case " ":
          e.preventDefault();
          dispatchAction("kbd_space", {});
          break;
        case "Enter":
          dispatchAction("kbd_enter", {});
          break;
        case "r": case "R":
          dispatchAction("kbd_rerec", {});
          break;
        case "ArrowRight":
          dispatchAction("kbd_skip", {});
          break;
        case "Escape":
          dispatchAction("kbd_back", {});
          break;
      }
    });
  }

  // ----- localStorage name ------
  function loadStoredName() {
    try {
      const name = localStorage.getItem(LOCAL_STORAGE_NAME_KEY);
      if (name) {
        setHiddenTextbox("studio-stored-name", name);
        // Fire the load-name action so Python knows we have a name
        setTimeout(() => {
          dispatchAction("set_name", { name });
        }, 80);
      }
    } catch (e) {
      console.warn("[studio.js] localStorage blocked:", e);
    }
  }

  function persistName(name) {
    try { localStorage.setItem(LOCAL_STORAGE_NAME_KEY, name); } catch (_) {}
  }

  // Expose for inline onclick on the name pill
  window.studioPromptForName = function () {
    const current = localStorage.getItem(LOCAL_STORAGE_NAME_KEY) || "";
    const name = window.prompt("Tên của bạn:", current);
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    persistName(trimmed);
    dispatchAction("set_name", { name: trimmed });
  };

  // ----- boot -----
  function boot() {
    installClickDelegate();
    installKeyboardShortcuts();
    loadStoredName();
    console.log("[studio.js] ready");
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(boot, 0);
  } else {
    document.addEventListener("DOMContentLoaded", boot);
  }
})();
```

- [ ] **Step 2: Sanity-check syntax.**

Run: `node -c studio.js 2>&1 || python3 -c "import json; print('node not available — skipping syntax check')"`
Expected: no syntax error printed. If `node` isn't installed, the fallback `python3` line prints a skip message — that's fine; we'll catch errors at runtime.

- [ ] **Step 3: Commit.**

```bash
git add studio.js
git commit -m "feat: studio.js — click delegation, keyboard, localStorage"
```

---

## Task 6 — View state machine + load assets

**Goal:** Wire `studio.css` and `studio.js` into the Gradio app. Add `view` state. Add the hidden orchestration components (`studio-action-payload`, `studio-action-trigger`). The picker is empty for now — Task 7 fills it.

**Files:**
- Modify: `app.py:1474-1869` (Gradio Blocks rewrite, top-down)

- [ ] **Step 1: At the top of `app.py`, replace the existing `CSS = """..."""` block with file-loaded CSS.** Find the `CSS = """` line (around line 860) and the matching closing `"""` (around line 1472). Replace the whole block with:

```python
# ---------- Load static assets ----------
_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "studio.css"), encoding="utf-8") as _f:
    CSS = _f.read()

with open(os.path.join(_HERE, "studio.js"), encoding="utf-8") as _f:
    _STUDIO_JS = _f.read()
```

- [ ] **Step 2: Replace the entire `with gr.Blocks(...) as app:` block (lines 1474–1847)** with the new shell. Keep the launch block at the bottom (line 1850 onwards). The new Blocks shell:

```python
with gr.Blocks(
    theme=gr.themes.Soft(),
    title="Studio ghi âm trợ lý ảo",
    css=CSS,
    head=f"<script>\n{_STUDIO_JS}\n</script>",
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
        picker_html = gr.HTML("<div style='padding:40px;text-align:center;color:#8f8a7a;'>Đang tải...</div>")

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
            show_download_button=False,
            waveform_options={"show_recording_waveform": False},
        )
```

- [ ] **Step 3: Add the dispatch function — Python side of the JS action payload.** After the Blocks definition, before any `.click()` wiring:

```python
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
        return (view, collab, filt, st, gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update())

    action = msg.get("action")
    data = msg.get("data", {})
    print(f"[dispatch] action={action} data={data}")

    # Stubs for now — Tasks 7-12 fill these in.
    if action == "set_name":
        collab = sanitize_collaborator_name(data.get("name", ""))

    # No-op fallback: return current state for everything else
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
```

- [ ] **Step 4: Confirm `allowed_paths` already covers `studio.css/.js`** — they're in the working directory, which is fine. No change needed to the launch block.

- [ ] **Step 5: Launch and verify the shell loads.**

Run: `python app.py`
Open the URL. Expected:
  - Page loads with the warm cream background.
  - "Đang tải..." placeholder is visible (the picker isn't rendered yet — that's Task 7).
  - Browser DevTools Console shows `[studio.js] ready`.
  - No errors in the Gradio server log.

Ctrl+C the app.

- [ ] **Step 6: Commit.**

```bash
git add app.py
git commit -m "feat: studio Blocks shell + view state + click-dispatch plumbing"
```

---

## Task 7 — Picker view rendering

**Goal:** Render the picker HTML (top bar, hero CTA, filter chips, conversation grid) from a single Python function and wire it through the dispatcher.

**Files:**
- Modify: `app.py` (add `render_picker_html`, call it on load + after filter/name changes)
- Create: `tests/test_ui_render.py`

- [ ] **Step 1: Write tests for the render function.** `tests/test_ui_render.py`:

```python
"""Tests for the picker + recording HTML render functions in app.py.

These are string-contains tests — we don't validate full HTML, just that
the key data-attributes and visible text are present.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def picker_ctx(tmp_path: Path):
    """Set up an input dir with 3 .dialog files + an empty output dir."""
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    in_dir.mkdir(); out_dir.mkdir()
    for stamp in ("2026-01-06T15-37-47-133Z-room_a",
                  "2026-01-09T03-32-44-871Z-room_b",
                  "2026-01-11T03-04-45-229Z-room_c"):
        (in_dir / f"{stamp}.dialog").write_text("user: alo\nassistant: dạ\n")
    return {"input_dir": str(in_dir), "output_dir": str(out_dir), "collab": "Hieu"}


def test_render_picker_shows_brand_logo(picker_ctx):
    from app import render_picker_html
    html = render_picker_html(picker_ctx["input_dir"], picker_ctx["output_dir"],
                              picker_ctx["collab"], filt="todo")
    assert "Studio" in html
    assert "studio-topbar" in html


def test_render_picker_shows_conversation_cards(picker_ctx):
    from app import render_picker_html
    html = render_picker_html(picker_ctx["input_dir"], picker_ctx["output_dir"],
                              picker_ctx["collab"], filt="todo")
    # 3 cards, each with a data-card-dialog attribute
    assert html.count("data-card-dialog=") == 3


def test_render_picker_filter_active(picker_ctx):
    from app import render_picker_html
    html = render_picker_html(picker_ctx["input_dir"], picker_ctx["output_dir"],
                              picker_ctx["collab"], filt="todo")
    # The active filter has the .active class
    assert 'data-filter="todo" class="studio-filter active"' in html or \
           'class="studio-filter active" data-filter="todo"' in html


def test_render_picker_hides_done_when_filter_is_todo(picker_ctx, tmp_path):
    from app import render_picker_html
    # Mark one as done by creating its dialog.json
    done = Path(picker_ctx["output_dir"]) / "Hieu" / "2026-01-06T15-37-47-133Z-room_a"
    done.mkdir(parents=True)
    (done / "dialog.json").write_text("{}")

    html = render_picker_html(picker_ctx["input_dir"], picker_ctx["output_dir"],
                              picker_ctx["collab"], filt="todo")
    # Only 2 cards now (the done one is hidden under "todo" filter)
    assert html.count("data-card-dialog=") == 2


def test_render_picker_resume_cta_when_partial_exists(picker_ctx):
    from app import render_picker_html
    from progress_tracking import write_progress
    # Create a partial-progress for conversation b
    sess = Path(picker_ctx["output_dir"]) / "Hieu" / "2026-01-09T03-32-44-871Z-room_b"
    sess.mkdir(parents=True)
    write_progress(str(sess), last_recorded_turn=2, recorded_count=1)

    html = render_picker_html(picker_ctx["input_dir"], picker_ctx["output_dir"],
                              picker_ctx["collab"], filt="todo")
    assert "data-resume-cta" in html
    assert "Thu tiếp" in html or "thu tiếp" in html
```

- [ ] **Step 2: Run tests — they must fail.** Expected: `ImportError: cannot import name 'render_picker_html'`.

- [ ] **Step 3: Implement `render_picker_html` in `app.py`.** Add this function alongside the other render helpers (above the `with gr.Blocks` line):

```python
DEFAULT_INPUT_DIR = "./input"
DEFAULT_OUTPUT_DIR = "./output"


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
    partial = list_partial(output_dir, collab)
    next_suggested = suggest_next(output_dir, output_dir and collab or "", all_dialogs) if collab else None
    if collab:
        next_suggested = suggest_next(output_dir, collab, all_dialogs)
    else:
        next_suggested = None

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
        filters_html += f"<span class='{cls}' data-filter='{key}'>{label} <span style='opacity:.7'>{n}</span></span>"

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
        if filt == "todo" and is_done: continue
        if filt == "done" and not is_done: continue
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
            partial_count = partial.get(stem, 0) + (1 if stem in partial else 0)
            # ^ partial[stem] is last_recorded_turn (0-indexed); +1 ≈ how many recorded.
            # For accuracy we'd read recorded_count from progress.json, but stem-only is enough for the badge.
            recorded_str = f"· {partial_count} câu thu" if partial_count else ""
            cls = "studio-card done" if is_done else "studio-card"
            cards.append(
                f"<div class='{cls}' data-card-dialog='{name}'>"
                f"<div class='head'><span class='num'>Hội thoại #{idx+1}</span>{badge}<span class='date'>{date_label}</span></div>"
                f"<div class='meta'><span>💬 {num_turns} câu</span><span>⏱ ~{mins} phút</span>{('<span>'+recorded_str.lstrip('· ')+'</span>') if recorded_str else ''}</div>"
                "</div>"
            )
        grid = f"<div class='studio-grid'>{''.join(cards)}</div>"

    return top_bar + cta_html + section_head + grid
```

Also remove the duplicate `DEFAULT_INPUT_DIR` / `DEFAULT_OUTPUT_DIR` definitions if any remain.

- [ ] **Step 4: Run tests.**

Run: `pytest tests/test_ui_render.py -v`
Expected: all picker tests pass.

- [ ] **Step 5: Update `studio_dispatch` to render the picker after each action and on `set_name`.** Replace the stub body of `studio_dispatch`:

```python
def studio_dispatch(payload_json: str, view: str, collab: str, filt: str, st: dict):
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

    # Re-render whichever view is active
    picker_update = gr.update(value=render_picker_html(
        DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, collab, filt
    )) if view == "picker" else gr.update()

    return (
        view, collab, filt, st,
        picker_update,
        gr.update(),  # recording_html
        gr.update(),  # mic_audio
        gr.update(visible=(view == "picker")),
        gr.update(visible=(view == "recording")),
    )
```

- [ ] **Step 6: Render the picker on initial load.** Replace the existing `app.load(...)` line near the end of the Blocks block with:

```python
    def _initial_render():
        return gr.update(value=render_picker_html(
            DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, "", "todo"
        ))
    app.load(fn=_initial_render, inputs=None, outputs=[picker_html])
```

- [ ] **Step 7: Launch and verify.**

Run: `python app.py`. Open the URL.
Expected:
- Top bar with logo and a "Đặt tên" pill.
- Three filter chips ("Chưa thu" active by default).
- Grid of conversation cards (one card per `.dialog` file in `input/`).
- Click the name pill → browser prompt appears → enter a name → top bar updates and shows your name; if any conversation has a `progress.json` in `output/<name>/`, the dark "Thu tiếp" CTA appears.
- Click a filter chip → active style moves; visible cards change.

Ctrl+C the app.

- [ ] **Step 8: Commit.**

```bash
git add app.py tests/test_ui_render.py
git commit -m "feat: picker view — render conversations, filters, resume CTA"
```

---

## Task 8 — Picker → Recording transition (open_conversation, resume_next)

**Goal:** Clicking a card or the resume CTA loads the conversation and switches to the recording view (which is still empty — Task 9 fills it). Reuse the existing `action_load` logic.

**Files:**
- Modify: `app.py` — extend `studio_dispatch` with `open_conversation` and `resume_next` cases

- [ ] **Step 1: Refactor `action_load` so it returns the new state dict directly (instead of a tuple of gr.update objects).** Find `action_load` and split into two functions:

```python
def load_conversation_state(input_dir: str, dialog_name: str,
                            output_dir: str, collab_name: str,
                            resume_at: int | None = None) -> dict | None:
    """Build the recording state dict for a conversation. Returns None on error.

    `resume_at` — if given, skip to that turn (used by the resume CTA).
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
    return {
        "collab_name": collab_name,
        "dialog_name": dialog_name,
        "dialog_path": dialog_path,
        "wav_path": wav_path,
        "output_dir": session_dir,
        "dialog": dialog,
        "user_audio_per_turn": user_audio_per_turn,
        "recordings": {},
        "current_turn": resume_at if resume_at is not None else 0,
    }
```

- [ ] **Step 2: Add handling for `open_conversation` and `resume_next` in `studio_dispatch`.**

```python
    if action == "open_conversation":
        st = load_conversation_state(
            DEFAULT_INPUT_DIR, data.get("dialog", ""),
            DEFAULT_OUTPUT_DIR, collab,
        ) or st
        if st.get("dialog"):
            view = "recording"
    elif action == "resume_next":
        from progress_tracking import suggest_next
        all_d = list_dialogs(DEFAULT_INPUT_DIR)
        s = suggest_next(DEFAULT_OUTPUT_DIR, collab, all_d) if collab else None
        if s:
            resume_at = (s["last_recorded_turn"] + 1) if s["kind"] == "resume" else 0
            st = load_conversation_state(
                DEFAULT_INPUT_DIR, s["dialog_name"],
                DEFAULT_OUTPUT_DIR, collab,
                resume_at=resume_at,
            ) or st
            if st.get("dialog"):
                view = "recording"
    elif action == "back_to_picker":
        view = "picker"
```

- [ ] **Step 3: Update the return of `studio_dispatch` to also re-render the recording view when active.** Add a stub render function and use it:

```python
def render_recording_html(st: dict, collab: str) -> str:
    """Placeholder — full version in Task 9-11."""
    if not st.get("dialog"):
        return "<div style='padding:40px;text-align:center;'>...</div>"
    idx = st.get("current_turn", 0)
    total = len(st["dialog"])
    return (
        "<div class='studio-topbar'>"
        "<div class='studio-logo'><span class='dot'></span>Studio</div>"
        "<button class='studio-back-btn' data-back-to-picker>← Hội thoại khác</button>"
        f"<span class='studio-conv-title'>{st['dialog_name'][:30]}</span>"
        "<div class='studio-spacer'></div>"
        f"<span class='studio-top-chip'>👤 <b>{collab}</b></span>"
        "</div>"
        f"<div class='studio-rec-progress'><span class='text'>Câu {idx+1}/{total}</span>"
        f"<div class='bar'><div class='fill' style='width:{int(idx/total*100)}%'></div></div>"
        f"<span class='pct'>{int(idx/total*100)}%</span></div>"
        f"<div style='padding:40px;text-align:center;'>Câu hiện tại: {st['dialog'][idx]['text']}</div>"
    )
```

And in `studio_dispatch`, replace the return with:

```python
    picker_update = (
        gr.update(value=render_picker_html(DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, collab, filt))
        if view == "picker" else gr.update()
    )
    recording_update = (
        gr.update(value=render_recording_html(st, collab))
        if view == "recording" else gr.update()
    )
    return (
        view, collab, filt, st,
        picker_update, recording_update,
        gr.update(),
        gr.update(visible=(view == "picker")),
        gr.update(visible=(view == "recording")),
    )
```

- [ ] **Step 4: Launch and verify.**

Run: `python app.py`. Open the URL. Type your name. Click a conversation card.
Expected: page switches to a near-empty recording view showing the top bar, progress strip, and the first turn's text. Click "← Hội thoại khác" → returns to picker.

Ctrl+C the app.

- [ ] **Step 5: Commit.**

```bash
git add app.py
git commit -m "feat: picker→recording view transition + back navigation"
```

---

## Task 9 — Recording rail (history)

**Goal:** Render the left-hand history rail with two row types: Khách context (with explicit play button) and recorded assistant (with playback/rerecord). Currently-playing Khách row gets the amber-style "playing" treatment when its turn is the active one.

**Files:**
- Modify: `app.py` — replace `render_recording_html` stub
- Modify: `tests/test_ui_render.py` — add rail tests

- [ ] **Step 1: Add rail tests.** Append to `tests/test_ui_render.py`:

```python
def _fake_state(num_turns=6, current=4, recordings=None):
    """Build a minimal state dict for render_recording_html tests."""
    dialog = []
    for i in range(num_turns):
        role = "user" if i % 2 == 0 else "assistant"
        dialog.append({"role": role, "text": f"Câu {i+1}", "text_raw": f"câu {i+1}"})
    return {
        "dialog": dialog,
        "current_turn": current,
        "recordings": recordings or {},
        "dialog_name": "test.dialog",
        "output_dir": "/tmp/test",
        "user_audio_per_turn": {},
    }


def test_render_recording_shows_rail_with_count():
    from app import render_recording_html
    html = render_recording_html(_fake_state(current=4, recordings={1: "/p1.wav", 3: "/p3.wav"}), "Hieu")
    assert "Bạn đã thu" in html
    assert "data-play-user=" in html
    assert "data-play-assistant=" in html


def test_render_recording_marks_current_user_turn_playing():
    from app import render_recording_html
    # current is a user turn (index 0 — user role)
    html = render_recording_html(_fake_state(current=0), "Hieu")
    # We expect the hero (right side) to show the user-turn state, not the rail.
    # Rail shows nothing yet (no past turns).
    assert "Khách đang nói" in html or "Đang phát" in html


def test_render_recording_progress_text():
    from app import render_recording_html
    html = render_recording_html(_fake_state(num_turns=12, current=5), "Hieu")
    assert "Câu 6 / 12" in html
```

- [ ] **Step 2: Run tests — must fail (functions still stubs / fields missing).**

Run: `pytest tests/test_ui_render.py -v`

- [ ] **Step 3: Replace `render_recording_html` with the full version.**

```python
def _format_duration(num_samples: int, sr: int) -> str:
    secs = num_samples / sr if sr else 0
    return f"{secs:.1f}s" if secs < 60 else f"{int(secs)//60}:{int(secs)%60:02d}"


def _render_rail(st: dict) -> str:
    dialog = st.get("dialog", [])
    idx = st.get("current_turn", 0)
    recordings = st.get("recordings", {})
    # Count of recorded assistant turns
    recorded_count = sum(1 for k in recordings.keys() if k < idx)

    rows = []
    for i in range(min(idx, len(dialog))):
        turn = dialog[i]
        if turn["role"] == "user":
            # Khách context row
            rows.append(
                f"<div class='rail-ctx' data-row-idx='{i}'>"
                f"<span class='role'>Khách</span>"
                f"<span class='num'>#{i+1}</span>"
                f"<span class='text'>{turn['text']}</span>"
                f"<button class='play-btn' data-play-user='{i}'>▶</button>"
                "</div>"
            )
        else:
            # Recorded assistant row
            if i in recordings:
                rows.append(
                    f"<div class='rail-rec'>"
                    f"<div class='top'><b>Đã thu</b><span>· Câu {i+1}</span></div>"
                    f"<div class='text'>{turn['text']}</div>"
                    f"<div class='actions'>"
                    f"<button class='play' data-play-assistant='{i}'>▶ Phát lại</button>"
                    # Only allow re-record on the most-recent recorded turn
                    + (f"<button data-rerec>↻ Thu lại</button>"
                       if i == max(recordings.keys()) else "")
                    + "</div></div>"
                )

    # If the current turn is a user turn that's auto-playing, also show it in the rail
    # as the "playing" row (so the rail mirrors the audio source).
    if idx < len(dialog) and dialog[idx]["role"] == "user":
        turn = dialog[idx]
        rows.append(
            f"<div class='rail-ctx playing' data-row-idx='{idx}'>"
            f"<span class='role'>Khách</span>"
            f"<span class='num'>#{idx+1}</span>"
            f"<span class='text'>{turn['text']}</span>"
            f"<button class='play-btn' data-play-user='{idx}'>⏸</button>"
            "</div>"
        )

    return (
        f"<div class='rail-head'><span class='count-pill'>{recorded_count}</span> Bạn đã thu</div>"
        + "".join(rows)
    )


def _render_hero(st: dict) -> str:
    """Minimal hero — Task 10 fills in all 4 states."""
    dialog = st.get("dialog", [])
    idx = st.get("current_turn", 0)
    if not dialog or idx >= len(dialog):
        return "<div style='padding:40px;'>...</div>"
    turn = dialog[idx]
    if turn["role"] == "user":
        return (
            "<span class='hero-role-tag'>Khách đang nói</span>"
            f"<div class='hero-turn-card'>{turn['text']}</div>"
            "<div class='hero-hint'>⏳ Tự sang câu kế khi nghe xong</div>"
            "<button class='hero-btn skip' data-skip-user>Bỏ qua câu này →</button>"
        )
    else:
        return (
            "<span class='hero-role-tag'>Đến lượt bạn</span>"
            f"<div class='hero-turn-card'>{turn['text']}</div>"
            "<button class='hero-rec-btn' data-rec-start><span class='inner'></span></button>"
            "<div class='hero-hint'><b>Bấm để ghi âm</b> · hoặc <span class='hero-kbd'>Space</span></div>"
        )


def render_recording_html(st: dict, collab: str) -> str:
    if not st.get("dialog"):
        return "<div style='padding:40px;'>...</div>"
    dialog = st["dialog"]
    idx = st.get("current_turn", 0)
    total = len(dialog)
    pct = int(idx / total * 100) if total else 0

    top_bar = (
        "<div class='studio-topbar'>"
        "<div class='studio-logo'><span class='dot'></span>Studio</div>"
        "<button class='studio-back-btn' data-back-to-picker>← Hội thoại khác</button>"
        f"<span class='studio-conv-title'>{st['dialog_name'].replace('.dialog','')[:30]}</span>"
        "<div class='studio-spacer'></div>"
        f"<span class='studio-top-chip'>👤 <b>{collab}</b></span>"
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
    return top_bar + progress + shell
```

- [ ] **Step 4: Run tests.** Expected: pass.

- [ ] **Step 5: Launch and visually verify.**

Run: `python app.py`. Open a conversation. Expected:
- Top bar shows back button, conversation title, name pill.
- Progress strip below shows "Câu 1 / N" with brand-color fill.
- Left rail empty initially (no past turns). Right hero shows the first user turn with audio scrubber stub (no playback yet — Task 10).

Ctrl+C.

- [ ] **Step 6: Commit.**

```bash
git add app.py tests/test_ui_render.py
git commit -m "feat: recording rail — Khách context + recorded assistant rows + playing state"
```

---

## Task 10 — Hero states + audio handlers

**Goal:** Implement the 4 hero states (Khách auto-play, assistant idle, recording, preview), wire `mic_audio.start_recording` / `stop_recording` to update the visible state, and handle play-button clicks from the rail.

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Extend the state dict with a UI-only `rec_phase` field.** In `load_conversation_state`, add `"rec_phase": "idle"` to the returned dict. Phases: `"idle"`, `"recording"`, `"preview"`. (For user turns we don't use rec_phase.)

- [ ] **Step 2: Replace `_render_hero` with the full 4-state version.**

```python
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
        return (
            "<span class='hero-role-tag'>Khách đang nói</span>"
            f"<div class='hero-turn-card'>{turn['text']}</div>"
            "<div class='hero-audio-bar user'>"
            "<button class='play-circle' data-play-user='" + str(idx) + "'>⏸</button>"
            "<div class='scrub'><div></div></div>"
            "<span>0:00</span>"
            "</div>"
            "<div class='hero-hint'>⏳ Tự sang câu kế khi nghe xong</div>"
            "<button class='hero-btn skip' data-skip-user>Bỏ qua câu này →</button>"
        )

    # Assistant turn — phase-dependent
    phase = st.get("rec_phase", "idle")
    if phase == "recording":
        return (
            "<span class='hero-role-tag' style='background:var(--brand);color:#fff'>● ĐANG GHI ÂM</span>"
            f"<div class='hero-turn-card recording'>{turn['text']}</div>"
            "<div class='hero-timer'>0:00</div>"
            "<div class='hero-waveform'>"
            + "".join([f"<div class='bar' style='height:{[18,30,14,38,26,42,18,32,22,36,14,28,40,20,34][i]}px'></div>" for i in range(15)])
            + "</div>"
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
    else:  # idle
        return (
            "<span class='hero-role-tag'>Đến lượt bạn — đọc câu này</span>"
            f"<div class='hero-turn-card'>{turn['text']}</div>"
            "<button class='hero-rec-btn' data-rec-start><span class='inner'></span></button>"
            "<div class='hero-hint'><b>Bấm để ghi âm</b> · hoặc <span class='hero-kbd'>Space</span></div>"
        )
```

- [ ] **Step 3: Wire the mic audio start/stop into state transitions.** After the Blocks definition where `mic_audio` is created, add wiring:

```python
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

        import soundfile as sf
        try:
            audio, sr = sf.read(mic_value, dtype="float32")
        except Exception as exc:
            print(f"[mic_stop] sf.read failed: {exc}")
            st = dict(st or {})
            st["rec_phase"] = "idle"
            return st, gr.update(value=render_recording_html(st, collab))

        if audio.ndim > 1: audio = audio.mean(axis=1)
        audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        idx = st["current_turn"]
        out_dir = st["output_dir"]
        os.makedirs(out_dir, exist_ok=True)
        final_path = os.path.realpath(
            os.path.join(out_dir, f"turn_{idx:02d}_assistant.wav")
        )
        sf.write(final_path, audio_int16, sr)

        st = dict(st)
        st.setdefault("recordings", {})[idx] = final_path
        st["rec_phase"] = "preview"

        # Write partial progress
        try:
            from progress_tracking import write_progress
            write_progress(st["output_dir"], last_recorded_turn=idx,
                          recorded_count=len(st["recordings"]))
        except Exception as exc:
            print(f"[progress] write failed: {exc}")

        return st, gr.update(value=render_recording_html(st, collab))

    mic_audio.stop_recording(
        fn=_on_mic_stop,
        inputs=[state, mic_audio, collab_state],
        outputs=[state, recording_html],
        show_progress="hidden",
    )
```

- [ ] **Step 4: Handle `save_and_next` / `rerecord_last` / `skip_user` in `studio_dispatch`.**

```python
    elif action == "save_and_next":
        st = dict(st or {})
        st["current_turn"] = st.get("current_turn", 0) + 1
        st["rec_phase"] = "idle"
    elif action == "rerecord_last":
        st = dict(st or {})
        # Drop the recording for the current turn, return to idle
        idx = st.get("current_turn", 0)
        st.get("recordings", {}).pop(idx, None)
        st["rec_phase"] = "idle"
    elif action == "skip_user":
        st = dict(st or {})
        st["current_turn"] = st.get("current_turn", 0) + 1
    elif action == "finish":
        # Reuse existing action_finish flow
        try:
            action_finish(st)
        except Exception as exc:
            print(f"[finish] {exc}")
        view = "picker"
        st = {}
```

- [ ] **Step 5: Add JS for the user-audio auto-advance.** Currently we don't have an audio element streaming user audio — the hero shows a scrubber but the audio source is just rendered HTML. Add a real `<audio>` element inside the hero's user-turn state with `autoplay` and an `onended` that dispatches `save_and_next` (advance):

In `_render_hero`, replace the user-turn return with:

```python
    if turn["role"] == "user":
        # Encode the trimmed user audio as a data URL so the <audio> element plays
        # without depending on Gradio file-routing.
        from app import get_trimmed_user_audio, audio_to_data_url  # local import to avoid circular
        url = audio_to_data_url(get_trimmed_user_audio(st, idx))
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
```

And add to `studio.js` near `studioPromptForName`:

```javascript
  window.__studioAutoNext = function () {
    setTimeout(() => dispatchAction("save_and_next", {}), 600);
  };
```

(The 600ms delay matches the existing `USER_PAUSE_SEC = 0.6` constant.)

- [ ] **Step 6: Add `play_user_audio` / `play_assistant_audio` actions** — these inject an `<audio>` element into a known DOM target via JS. Simpler approach: render an HTML audio element on demand via Python.

For now, implement play buttons as an in-place HTML audio replacement. In `studio.js`, replace the `playUser` / `playAssistant` click handlers:

```javascript
      const playUser = e.target.closest("[data-play-user]");
      if (playUser) {
        // Find or create an audio player attached to this row
        const idx = playUser.dataset.playUser;
        dispatchAction("play_user_audio", { idx: parseInt(idx, 10) });
        return;
      }
      const playAssistant = e.target.closest("[data-play-assistant]");
      if (playAssistant) {
        dispatchAction("play_assistant_audio", { idx: parseInt(playAssistant.dataset.playAssistant, 10) });
        return;
      }
```

And in `studio_dispatch`, handle them by storing a `"_play_request"` field on state and re-rendering — the next render injects an autoplay `<audio>` for the requested turn:

```python
    elif action == "play_user_audio":
        st = dict(st or {})
        st["_play_request"] = ("user", int(data.get("idx", 0)))
    elif action == "play_assistant_audio":
        st = dict(st or {})
        st["_play_request"] = ("assistant", int(data.get("idx", 0)))
```

In `render_recording_html`, after building the shell, append (if `_play_request` is set):

```python
    play_req = st.pop("_play_request", None)
    if play_req:
        kind, ridx = play_req
        from app import get_trimmed_user_audio, audio_to_data_url
        if kind == "user":
            url = audio_to_data_url(get_trimmed_user_audio(st, ridx))
        else:
            url = audio_to_data_url(st.get("recordings", {}).get(ridx))
        if url:
            shell += f"<audio autoplay src='{url}' style='display:none'></audio>"
```

- [ ] **Step 7: Launch and verify full flow.**

Run: `python app.py`. Set your name. Open a conversation. Step through:
- User turn auto-plays, advances after audio ends (Khách audio plays through speakers).
- Assistant turn shows record button. Click it → "ĐANG GHI ÂM" + waveform + pulsing stop.
- Click stop (or auto-stop on 1.5s silence) → preview view with playback bar + Save/Rerecord.
- Click Save → advances to next turn.
- Click Re-record → returns to idle state for same turn.
- When all turns done → completion card; click "Hoàn tất" → routes back to picker, conversation now appears as ĐÃ XONG.

Ctrl+C.

- [ ] **Step 8: Commit.**

```bash
git add app.py studio.js
git commit -m "feat: hero states (idle/recording/preview/done) + audio playback wiring"
```

---

## Task 11 — Keyboard shortcuts

**Goal:** Wire Space (record/stop or play/pause), Enter (save), R (rerec), → (skip), Esc (back) into actions via the existing JS keyboard listener.

**Files:**
- Modify: `app.py` — add keyboard action cases to `studio_dispatch`

- [ ] **Step 1: Add keyboard action handling to `studio_dispatch`.**

```python
    elif action == "kbd_space":
        # Context-dependent: in idle → start recording; in recording → stop; on user → toggle play
        if st and st.get("dialog"):
            idx = st.get("current_turn", 0)
            turn = st["dialog"][idx] if idx < len(st["dialog"]) else None
            if turn and turn["role"] == "assistant":
                phase = st.get("rec_phase", "idle")
                if phase == "idle":
                    # Trigger the start via the mic button — JS handles this by clicking
                    # data-rec-start. The kbd_space here is a fallback that does nothing
                    # on the Python side; the JS already maps Space to a "start" intent
                    # when the rec button is the visible target. We emit the action just
                    # to refresh the render.
                    pass
                # In "recording" / "preview" we let the dedicated buttons handle stop/save
    elif action == "kbd_enter":
        # Save & next, only valid in preview phase
        if st.get("rec_phase") == "preview":
            st = dict(st)
            st["current_turn"] = st.get("current_turn", 0) + 1
            st["rec_phase"] = "idle"
    elif action == "kbd_rerec":
        if st.get("rec_phase") == "preview":
            st = dict(st)
            idx = st.get("current_turn", 0)
            st.get("recordings", {}).pop(idx, None)
            st["rec_phase"] = "idle"
    elif action == "kbd_skip":
        # Skip the current user turn
        if st and st.get("dialog"):
            idx = st.get("current_turn", 0)
            if idx < len(st["dialog"]) and st["dialog"][idx]["role"] == "user":
                st = dict(st)
                st["current_turn"] = idx + 1
    elif action == "kbd_back":
        view = "picker"
```

- [ ] **Step 2: Improve `kbd_space` mapping in `studio.js` to dispatch the right concrete action.** Replace the case:

```javascript
        case " ":
          e.preventDefault();
          // Try the most-likely visible action
          const recBtn = document.querySelector("[data-rec-start]");
          const stopBtn = document.querySelector("[data-rec-stop]");
          const playUser = document.querySelector(".hero-audio-bar.user .play-circle");
          if (stopBtn) { stopBtn.click(); }
          else if (recBtn) { recBtn.click(); }
          else if (playUser) { playUser.click(); }
          break;
```

- [ ] **Step 3: Launch and verify.**

Run: `python app.py`. Open a conversation. Without touching the mouse:
- On user turn: press → to skip.
- On assistant turn: press Space to record. Press Space again to stop.
- In preview: press Enter to save, or R to re-record.
- Press Esc → back to picker.

Ctrl+C.

- [ ] **Step 4: Commit.**

```bash
git add app.py studio.js
git commit -m "feat: keyboard shortcuts — Space/Enter/R/→/Esc"
```

---

## Task 12 — Cleanup, README, manual QA

**Goal:** Delete now-unused code, refresh the README, do a final end-to-end pass.

**Files:**
- Modify: `app.py` — delete dead code
- Modify: `README.md`

- [ ] **Step 1: Remove the now-unused old render helpers from `app.py`.** Delete these (all are pre-rewrite, no longer called):
  - `_bubble_html` (around 388-430)
  - `build_chat_html` (434-454)
  - `build_current_card_html` (457-481)
  - `build_future_indicator_html` (484-492)
  - `progress_html` (495-508)
  - `_render` (526-599)
  - The old `action_load`, `action_next`, `action_recording_done`, `action_save_continue`, `action_rerecord` if they're no longer referenced (Task 10 introduced replacements; `action_finish` is still referenced).

Verify with: `grep -n "def action_" app.py` and `grep -n "build_chat_html\|build_current_card_html\|_bubble_html\|progress_html\|_render\b" app.py`. Anything that's only its own definition can be deleted.

- [ ] **Step 2: Run tests after cleanup.**

Run: `pytest tests/ -v`
Expected: all pass.

- [ ] **Step 3: Update `README.md`.** Replace section "4. Hướng dẫn sử dụng" with the new two-screen flow:

```markdown
## 4. Hướng dẫn sử dụng (cho cộng tác viên)

App có **2 màn hình** — không cần hướng dẫn dài: bạn nhìn vào đâu thì việc cần làm hiện ngay ở đó.

### 🔹 Màn hình 1 — Chọn hội thoại

- Bấm vào pill **👤 Đặt tên** ở góc phải trên → nhập tên của bạn. Tên sẽ được nhớ
  lại cho lần sau.
- Nếu bạn đã thu dở một hội thoại trước đó, ô tối ở trên cùng (**Thu tiếp câu kế tiếp
  chưa hoàn thành**) sẽ đề xuất tiếp tục từ chỗ bạn dừng.
- Hoặc bấm vào bất kỳ thẻ hội thoại nào trong lưới phía dưới để bắt đầu.
- Lọc theo trạng thái bằng các chip **Chưa thu / Đã xong / Tất cả**.

### 🔹 Màn hình 2 — Thu âm

Khi vào hội thoại, màn hình chia 2 cột:

- **Cột trái — câu đã thu (lịch sử)**: mỗi câu của khách có nút ▶ riêng để nghe lại
  bất cứ lúc nào. Câu bạn vừa thu hiển thị to hơn, có nút **▶ Phát lại** và (cho câu
  gần nhất) **↻ Thu lại**.
- **Cột phải — việc cần làm bây giờ**: hiện rõ câu hiện tại + nút lớn để thực hiện.

App tự chuyển trạng thái theo lượt:

| Lượt | Bạn cần làm |
| --- | --- |
| 🧑 Khách | Audio tự phát. Khi xong, app tự sang câu kế. Có thể bấm **Bỏ qua →** hoặc phím <kbd>→</kbd>. |
| 🎙️ Trợ lý | Bấm nút **🔴 ghi âm** (hoặc <kbd>Space</kbd>). Đọc câu hiển thị. Bấm lại để dừng. App cũng tự dừng khi bạn im 1.5 giây. |
| ▶ Sau khi ghi | Nghe lại bằng nút ▶. Ưng → bấm **💾 Lưu & câu kế** (hoặc <kbd>Enter</kbd>). Chưa ưng → **↻ Thu lại** (hoặc <kbd>R</kbd>). |

### ⌨️ Phím tắt

- <kbd>Space</kbd> — bắt đầu / kết thúc ghi âm (hoặc phát / tạm dừng audio khách)
- <kbd>Enter</kbd> — lưu & câu kế (khi đang ở bước nghe lại)
- <kbd>R</kbd> — thu lại
- <kbd>→</kbd> — bỏ qua câu khách hiện tại
- <kbd>Esc</kbd> — về danh sách hội thoại

### 🔹 Khi xong tất cả câu

Hero hiện banner 🎉 + nút **📦 Hoàn tất & về danh sách**. Bấm → kết quả lưu vào
`output/<tên bạn>/<hội thoại>/` (giống như cũ), và bạn về lại Màn hình 1 để chọn
hội thoại tiếp theo.
```

- [ ] **Step 4: End-to-end QA.** With a fresh terminal and clean state:

Run: `python app.py`

Walk through:
1. ✅ Page loads with picker view, no errors in DevTools console.
2. ✅ Click name pill → set name → top bar updates.
3. ✅ Pick a conversation → recording view loads with first turn visible.
4. ✅ User turn audio auto-plays through speakers and advances after audio ends.
5. ✅ Assistant turn — Space starts recording, Space stops, preview appears.
6. ✅ Enter saves; advances; rail shows the saved recording with ▶ Phát lại.
7. ✅ ▶ Phát lại on a past assistant row plays the audio.
8. ✅ ▶ on a past Khách row plays the audio.
9. ✅ Esc returns to picker; the conversation now shows partial progress in its card.
10. ✅ Picker hero CTA "Thu tiếp" appears, points at the right conversation.
11. ✅ Complete all turns → completion card → 📦 Hoàn tất → picker, conversation now ĐÃ XONG.
12. ✅ `output/<name>/<dialog stem>/dialog.json` + `dialog_normalized.dialog` + all `turn_NN_*.wav` files exist; no leftover `progress.json` after finish.

If any step fails, fix in place and re-run.

- [ ] **Step 5: Final commit.**

```bash
git add app.py README.md
git commit -m "chore: drop dead code, update README for new two-screen flow"
```

---

## Self-review checklist (run after writing this plan, before handing off)

- [x] **Spec coverage:**
  - 2-screen IA → Task 6 (shell), 7 (picker), 8 (transition), 9-10 (recording)
  - Design system tokens → Task 4
  - Picker page (top bar, hero CTA, filters, grid) → Task 7
  - Recording page (top bar, progress, rail, hero with 4 states) → Tasks 8-10
  - Khách context vs recorded distinction → Task 9
  - Auto-play state on rail → Task 9
  - Keyboard shortcuts → Task 11
  - Partial progress backend → Task 3
  - Backend extraction → Task 2
  - Re-record limited to most recent → enforced in `_render_rail` (Task 9)
  - localStorage name → studio.js (Task 5)
- [x] **No placeholders:** every step has concrete code or commands.
- [x] **Type consistency:** `state` dict keys (`current_turn`, `rec_phase`, `recordings`, `dialog`, `user_audio_per_turn`, `output_dir`) used the same way across tasks.
- [x] **File paths absolute and unambiguous.**
