"""Preserve the Windows bootstrap command while sharing installation orchestration."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.setup_project import main

if __name__ == '__main__':
    raise SystemExit(main(expected_platform='win32'))
