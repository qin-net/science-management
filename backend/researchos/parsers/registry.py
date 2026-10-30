"""Which parser commands this deployment can actually run. A parser is reported as
available only when its interpreter exists; nothing is assumed from configuration alone."""

import os
import sys
from pathlib import Path

from researchos.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DOCLING_RUNNER = Path(__file__).with_name("docling_runner.py")


def docling_status() -> str:
    python = get_settings().docling_python
    if not python:
        return "not_configured"
    return "available" if os.access(python, os.X_OK) else "unavailable"


def parser_commands() -> dict[str, list[str]]:
    """Maps a parser name to the argv prefix; `<pdf> <output.json>` is appended."""
    commands = {"pdfium": [sys.executable, "-m", "researchos.parsers.cli", "pdfium"]}
    if docling_status() == "available":
        commands["docling"] = [get_settings().docling_python, str(DOCLING_RUNNER)]
    return commands


def parser_env() -> dict[str, str]:
    """Environment for parser processes. Deliberately minimal: a parser handles untrusted
    files, so it gets neither DATABASE_URL nor any other server configuration."""
    env = {key: os.environ[key] for key in ("PATH", "LANG", "HOME", "HF_HOME") if key in os.environ}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env
