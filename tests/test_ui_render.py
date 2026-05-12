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
    # The active filter has the .active class — accept either attribute ordering
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
    # The rail header changed to show the full conversation overview, not
    # just recorded turns. Now shows "Hội thoại" with an N/total chip.
    assert "Hội thoại" in html
    assert "2/" in html  # 2 recorded out of N total turns
    assert "data-play-user=" in html
    assert "data-play-assistant=" in html


def test_render_recording_marks_current_user_turn_playing():
    from app import render_recording_html
    # current=0 is a user turn (index 0 → role "user" by _fake_state's modulo logic)
    html = render_recording_html(_fake_state(current=0), "Hieu")
    # The current user turn should be rendered with the .playing class in the rail
    # (or anywhere — we just want to confirm "playing" state shows up)
    assert "rail-ctx playing" in html or "Đang phát" in html or "Khách đang nói" in html


def test_render_recording_progress_text():
    from app import render_recording_html
    html = render_recording_html(_fake_state(num_turns=12, current=5), "Hieu")
    assert "Câu 6 / 12" in html
