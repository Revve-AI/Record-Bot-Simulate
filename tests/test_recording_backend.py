"""Tests for recording_backend.py (extracted helpers from app.py)."""
from __future__ import annotations


def test_pytest_runs():
    """Smoke test: pytest can collect and run a test in this project."""
    assert 1 + 1 == 2


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
