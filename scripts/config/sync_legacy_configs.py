from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.project_config import sync_legacy_configs


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync legacy config files from unified configs/config.yaml.")
    parser.add_argument("--config", default=str(Path("configs") / "config.yaml"), help="Unified config path")
    parser.add_argument("--profile", default="", help="Optional profile name (e.g., dev/prod)")
    parser.add_argument("--rednotes-out", default="configs/legacy/rednotes.yaml")
    parser.add_argument("--wechat-out", default="configs/legacy/wechat.yaml")
    parser.add_argument("--cover-out", default="configs/legacy/cover_gen.yaml")
    args = parser.parse_args()

    paths = sync_legacy_configs(
        args.config,
        profile=args.profile,
        rednotes_out=args.rednotes_out,
        wechat_out=args.wechat_out,
        cover_out=args.cover_out,
    )
    print("Synced config files:")
    print(f"- rednotes: {paths['rednotes']}")
    print(f"- wechat: {paths['wechat']}")
    print(f"- cover_gen: {paths['cover_gen']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
