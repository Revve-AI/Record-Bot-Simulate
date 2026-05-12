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


def test_list_partial_returns_dialog_stem_keyed_map(tmp_path: Path):
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
