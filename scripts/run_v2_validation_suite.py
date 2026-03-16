"""Backwards-compatible wrapper for the v2 validation runner."""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.run_v2_validation_suite import main


if __name__ == "__main__":
    main()
