from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.project_config import load_project_config
from scripts.integrations.web_export import find_latest_wechat_stage2, find_latest_xhs_stage2


DEFAULT_TZ = "Asia/Shanghai"


def _now(tz) -> datetime:
    return datetime.now(tz)


def _zoneinfo(tz_name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        return None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _run(cmd: list[str], cwd: Path) -> int:
    import subprocess

    _log(f"[run] {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(cwd))


def _parse_push_time(text: str) -> dt_time:
    s = (text or "").strip()
    if not s:
        return dt_time(10, 0)
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError("Invalid push time, expected HH:MM")
    h = int(parts[0])
    m = int(parts[1])
    return dt_time(h, m)


def _wait_until(push_at: dt_time, tz) -> None:
    now = _now(tz)
    target = datetime.combine(now.date(), push_at, tzinfo=tz)
    if now >= target:
        return
    seconds = (target - now).total_seconds()
    _log(f"[wait] now={now.strftime('%Y-%m-%d %H:%M:%S %Z')} target={target.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    time.sleep(max(0, int(seconds)))


def _check_file(path: Optional[Path], label: str) -> None:
    if not path or not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def _load_cfg(config_path: str, profile: str) -> Tuple[Dict[str, Any], str]:
    cfg, selected = load_project_config(config_path, profile=profile)
    return cfg, selected


def _validate_cfg(cfg: Dict[str, Any]) -> None:
    # Ensure bitable write_mode is edit
    bitable = cfg.get("bitable") if isinstance(cfg.get("bitable"), dict) else {}
    write_mode = str(bitable.get("write_mode") or "").strip().lower()
    if write_mode and write_mode not in ("edit", "append", "upsert", "merge", "update"):
        raise RuntimeError(f"bitable.write_mode must be edit (current: {write_mode})")

    # Ensure TikHub API key exists
    xhs = cfg.get("xhs") if isinstance(cfg.get("xhs"), dict) else {}
    tikhub = xhs.get("tikhub") if isinstance(xhs.get("tikhub"), dict) else {}
    api_key = str(tikhub.get("api_key") or os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not api_key:
        _log("[warn] xhs.tikhub.api_key is empty; TikHub crawl may fail.")

    # Avoid Playwright fallback if configured
    provider_order = xhs.get("provider_order") or []
    if any(str(v).strip().lower() == "playwright" for v in provider_order):
        raise RuntimeError("xhs.provider_order includes playwright; remove it to avoid human verification.")
    fallback = xhs.get("fallback") if isinstance(xhs.get("fallback"), dict) else {}
    if str(fallback.get("enabled") or "").strip().lower() in ("1", "true", "yes", "y", "on"):
        raise RuntimeError("xhs.fallback.enabled is true; disable it to avoid Playwright fallback.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily automation runner (WeChat + XHS + Bitable + Feishu).")
    parser.add_argument("--config", default=str(Path("configs") / "config.yaml"))
    parser.add_argument("--profile", default="")
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument("--push-at", default="10:00", help="Push time in HH:MM (Asia/Shanghai by default)")
    parser.add_argument("--outputs", default="outputs")
    parser.add_argument("--paper", default="paper.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    tz = _zoneinfo(args.tz) or _zoneinfo(DEFAULT_TZ)
    if tz is None:
        _log("[warn] zoneinfo unavailable; using local time.")

    cfg, selected_profile = _load_cfg(args.config, args.profile)
    _validate_cfg(cfg)

    _log(f"[start] profile={selected_profile or 'default'} tz={args.tz} cwd={repo_root}")

    # 1) WeChat crawl (probe up to 3 times)
    wechat_ok = False
    for attempt in range(1, 4):
        _log(f"[wechat] crawl attempt {attempt}/3")
        code = _run(
            [sys.executable, "scripts/wechat/wechat_crawler.py", "-c", args.config, "-o", args.paper, *(["--profile", args.profile] if args.profile else [])],
            cwd=repo_root,
        )
        if code == 0:
            wechat_ok = True
            break
        time.sleep(5)

    if not wechat_ok:
        _log("[wechat] crawl failed 3 times. Please update cookie/token, then retry.")
        return 2

    # 2) WeChat analysis
    _log("[wechat] llm analysis")
    code = _run(
        [sys.executable, "scripts/wechat/llm_qwen.py", "-c", args.config, "-i", args.paper, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[wechat] llm failed (exit={code})")
        return 3

    # 3) XHS pipeline (TikHub only)
    _log("[xhs] pipeline start")
    code = _run(
        [sys.executable, "scripts/xhs/xhs_pipeline.py", "--config", args.config, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[xhs] pipeline failed (exit={code})")
        return 4

    # 4) Resolve outputs
    wechat_stage2 = find_latest_wechat_stage2(Path(args.outputs) / "wechat")
    xhs_stage2 = find_latest_xhs_stage2(Path(args.outputs) / "rednotes")
    _check_file(wechat_stage2, "WeChat stage2")
    _check_file(xhs_stage2, "XHS stage2")

    xhs_run_dir = xhs_stage2.parent.parent if xhs_stage2 else None
    _check_file(xhs_run_dir, "XHS run dir")

    # 5) Sync to Bitable (edit mode)
    _log("[bitable] sync wechat")
    code = _run(
        [sys.executable, "scripts/integrations/bitable_sync.py", "wechat", "-c", args.config, "--stage2", str(wechat_stage2), "--paper", args.paper, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[bitable] wechat sync failed (exit={code})")
        return 5

    _log("[bitable] sync xhs")
    code = _run(
        [sys.executable, "scripts/integrations/bitable_sync.py", "xhs", "-c", args.config, "--run-dir", str(xhs_run_dir), *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[bitable] xhs sync failed (exit={code})")
        return 6

    # 6) Wait until push time (Asia/Shanghai), then push to Feishu
    push_at = _parse_push_time(args.push_at)
    if tz is not None:
        _wait_until(push_at, tz)

    _log("[feishu] push wechat")
    code = _run(
        [sys.executable, "scripts/wechat/wechat_feishu_sender.py", "--stage2", str(wechat_stage2), "-c", args.config, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[feishu] wechat push failed (exit={code})")
        return 7

    _log("[feishu] push xhs")
    code = _run(
        [sys.executable, "scripts/xhs/feishu_sender.py", "-c", args.config, "--run-dir", str(xhs_run_dir), *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[feishu] xhs push failed (exit={code})")
        return 8

    _log("[done] all steps completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
