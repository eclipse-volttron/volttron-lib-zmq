"""Tests suite for `auth`."""

from pathlib import Path
import sys

source_path = Path(__file__).parent.parent.joinpath("src").as_posix()

if source_path not in sys.path:
    sys.path.insert(0, source_path)

