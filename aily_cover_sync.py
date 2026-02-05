from __future__ import annotations

import sys
from pathlib import Path


def _ensure_repo_root_on_path() -> None:
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main() -> int:
    # Compatibility shim: script moved to aily/aily_cover_sync.py
    _ensure_repo_root_on_path()
    from aily.aily_cover_sync import main as real_main  # type: ignore

    return int(real_main())


if __name__ == "__main__":
    raise SystemExit(main())
