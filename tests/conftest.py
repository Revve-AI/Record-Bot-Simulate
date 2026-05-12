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
