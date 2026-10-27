"""Operator-controlled executable selection; never interpret a command or search PATH."""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_FFMPEG_EXECUTABLE = "/usr/bin/ffmpeg"


class FfmpegUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Audio transcoder unavailable: install FFmpeg or configure "
                         "FFMPEG_EXECUTABLE as an absolute executable path")


def validate_ffmpeg_executable(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise FfmpegUnavailable()
    path = Path(value)
    if (not path.is_absolute() or not re.fullmatch(r"ffmpeg(?:-[A-Za-z0-9_.-]+)?", path.name)
            or not path.is_file() or not os.access(path, os.X_OK)):
        raise FfmpegUnavailable()
    return str(path)
