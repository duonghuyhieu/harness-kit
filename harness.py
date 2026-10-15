#!/usr/bin/env python3
"""Launcher: run harness-kit without installing it.  python D:/harness/harness.py audit <project>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_kit.cli import main  # noqa: E402

sys.exit(main())
