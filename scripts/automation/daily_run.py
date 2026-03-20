from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, time as dt_time, timedelta
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
    # If already past today's push time, wait until next day's push time.
    if now >= target:
        target = target + timedelta(days=1)
    seconds = (target - now).total_seconds()
    _log(f"[wait] now={now.strftime('%Y-%m-%d %H:%M:%S %Z')} target={target.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    time.sleep(max(0, int(seconds)))


def _wait_until_start(push_at: dt_time, tz, offset_min: int) -> None:
    now = _now(tz)
    push_target = datetime.combine(now.date(), push_at, tzinfo=tz)
    # If past today's push time, start next day.
    if now >= push_target:
        push_target = push_target + timedelta(days=1)
    target = push_target - timedelta(minutes=offset_min)
    # If within today's window (start <= now < push), start immediately.
    if now >= target and now < push_target:
        return
    seconds = (target - now).total_seconds()
    _log(
        f"[wait] now={now.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"start={target.strftime('%Y-%m-%d %H:%M:%S %Z')} (offset={offset_min}m)"
    )
    time.sleep(max(0, int(seconds)))


def _check_file(path: Optional[Path], label: str) -> None:
    if not path or not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def _load_cfg(config_path: str, profile: str) -> Tuple[Dict[str, Any], str]:
    cfg, selected = load_project_config(config_path, profile=profile)
    return cfg, selected


def _validate_cfg(cfg: Dict[str, Any]) -> Dict[str, bool]:
    # Ensure bitable write_mode is edit
    bitable = cfg.get("bitable") if isinstance(cfg.get("bitable"), dict) else {}
    write_mode = str(bitable.get("write_mode") or "").strip().lower()
    if write_mode and write_mode not in ("edit", "append", "upsert", "merge", "update"):
        _log(f"[warn] bitable.write_mode is '{write_mode}', forcing edit for this run.")

    # Ensure TikHub API key exists
    xhs = cfg.get("xhs") if isinstance(cfg.get("xhs"), dict) else {}
    tikhub = xhs.get("tikhub") if isinstance(xhs.get("tikhub"), dict) else {}
    api_key = str(tikhub.get("api_key") or os.environ.get("TIKHUB_API_KEY") or "").strip()
    if not api_key:
        _log("[warn] xhs.tikhub.api_key is empty; TikHub crawl may fail.")

    # Avoid Playwright fallback if configured
    provider_order = xhs.get("provider_order") or []
    has_playwright = any(str(v).strip().lower() == "playwright" for v in provider_order)
    fallback = xhs.get("fallback") if isinstance(xhs.get("fallback"), dict) else {}
    fallback_enabled = str(fallback.get("enabled") or "").strip().lower() in ("1", "true", "yes", "y", "on")
    if has_playwright:
        _log("[warn] xhs.provider_order includes playwright; will override to TikHub-only for this run.")
    if fallback_enabled:
        _log("[warn] xhs.fallback.enabled is true; will disable fallback for this run.")

    return {"force_tikhub_only": bool(has_playwright or fallback_enabled)}


def _build_sanitized_config(cfg: Dict[str, Any], tz: str, *, since_date: str) -> Dict[str, Any]:
    """Build a sanitized config in memory (no temp files)."""
    data = dict(cfg)
    xhs = data.get("xhs") if isinstance(data.get("xhs"), dict) else {}
    xhs = dict(xhs)
    xhs["provider_order"] = ["tikhub"]
    xhs["since"] = since_date
    fallback = xhs.get("fallback") if isinstance(xhs.get("fallback"), dict) else {}
    fallback = dict(fallback)
    fallback["enabled"] = False
    xhs["fallback"] = fallback
    tikhub = xhs.get("tikhub") if isinstance(xhs.get("tikhub"), dict) else {}
    tikhub = dict(tikhub)
    try:
        timeout_val = int(tikhub.get("timeout_sec") or 0)
    except Exception:
        timeout_val = 0
    if timeout_val < 40:
        tikhub["timeout_sec"] = 40
    xhs["tikhub"] = tikhub
    data["xhs"] = xhs

    app = data.get("app") if isinstance(data.get("app"), dict) else {}
    app = dict(app)
    app.setdefault("timezone", tz)
    data["app"] = app

    wechat = data.get("wechat") if isinstance(data.get("wechat"), dict) else {}
    wechat = dict(wechat)
    wechat["since"] = f"{since_date} 00:00:00"
    data["wechat"] = wechat

    return data


def _write_inline_config(config_path: str, data: Dict[str, Any]) -> str:
    """Overwrite the original config file with JSON content and return original text."""
    path = Path(config_path)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return original


def _restore_config(config_path: str, original_text: str) -> None:
    path = Path(config_path)
    if original_text:
        path.write_text(original_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily automation runner (WeChat + XHS + Bitable + Feishu).")
    parser.add_argument("--config", default=str(Path("configs") / "config.yaml"))
    parser.add_argument("--profile", default="")
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument("--push-at", default="10:00", help="Push time in HH:MM (Asia/Shanghai by default)")
    parser.add_argument("--start-offset-min", type=int, default=60, help="Start offset minutes before push-at")
    parser.add_argument("--outputs", default="outputs")
    parser.add_argument("--paper", default="paper.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    tz = _zoneinfo(args.tz) or _zoneinfo(DEFAULT_TZ)
    if tz is None:
        _log("[warn] zoneinfo unavailable; using local time.")

    cfg, selected_profile = _load_cfg(args.config, args.profile)
    flags = _validate_cfg(cfg)

    _log(f"[start] profile={selected_profile or 'default'} tz={args.tz} cwd={repo_root}")

    # 0) Wait until start time (default: push_at - 60 minutes)
    push_at = _parse_push_time(args.push_at)
    _wait_until_start(push_at, tz, offset_min=max(0, int(args.start_offset_min)))

    # Build a sanitized config for this run (force yesterday since + TikHub-only)
    now = _now(tz) if tz is not None else datetime.now()
    since_date = (now.date() - timedelta(days=1)).strftime("%Y-%m-%d")
    sanitized = _build_sanitized_config(cfg, args.tz, since_date=since_date)
    original_cfg_text = _write_inline_config(args.config, sanitized)
    run_config = args.config

    # 1) WeChat crawl (probe up to 3 times)
    wechat_ok = False
    for attempt in range(1, 4):
        _log(f"[wechat] crawl attempt {attempt}/3")
        code = _run(
        [sys.executable, "scripts/wechat/wechat_crawler.py", "-c", run_config, "-o", args.paper, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
        if code == 0:
            wechat_ok = True
            break
        time.sleep(5)

    if not wechat_ok:
        _log("[wechat] crawl failed 3 times. Please update cookie/token, then retry.")
        _restore_config(args.config, original_cfg_text)
        return 2

    # 2) WeChat analysis
    _log("[wechat] llm analysis")
    code = _run(
        [sys.executable, "scripts/wechat/llm_qwen.py", "-c", run_config, "-i", args.paper, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[wechat] llm failed (exit={code})")
        _restore_config(args.config, original_cfg_text)
        return 3

    # 3) XHS pipeline (TikHub only)
    _log("[xhs] pipeline start")
    xhs_config = run_config
    if flags.get("force_tikhub_only"):
        _log("[xhs] using TikHub-only config (inline)")
    code = _run(
        [sys.executable, "scripts/xhs/xhs_pipeline.py", "--config", xhs_config, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[xhs] pipeline failed (exit={code})")
        _restore_config(args.config, original_cfg_text)
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
        [sys.executable, "scripts/integrations/bitable_sync.py", "wechat", "-c", run_config, "--stage2", str(wechat_stage2), "--paper", args.paper, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[bitable] wechat sync failed (exit={code})")
        _restore_config(args.config, original_cfg_text)
        return 5

    _log("[bitable] sync xhs")
    code = _run(
        [sys.executable, "scripts/integrations/bitable_sync.py", "xhs", "-c", run_config, "--run-dir", str(xhs_run_dir), *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[bitable] xhs sync failed (exit={code})")
        _restore_config(args.config, original_cfg_text)
        return 6

    # 6) Wait until push time (Asia/Shanghai), then push to Feishu
    _wait_until(push_at, tz)

    _log("[feishu] push wechat")
    code = _run(
        [sys.executable, "scripts/wechat/wechat_feishu_sender.py", "--stage2", str(wechat_stage2), "-c", run_config, *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[feishu] wechat push failed (exit={code})")
        _restore_config(args.config, original_cfg_text)
        return 7

    _log("[feishu] push xhs")
    code = _run(
        [sys.executable, "scripts/xhs/feishu_sender.py", "-c", run_config, "--run-dir", str(xhs_run_dir), *(["--profile", args.profile] if args.profile else [])],
        cwd=repo_root,
    )
    if code != 0:
        _log(f"[feishu] xhs push failed (exit={code})")
        _restore_config(args.config, original_cfg_text)
        return 8

    _restore_config(args.config, original_cfg_text)
    _log("[done] all steps completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
