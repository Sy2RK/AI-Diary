from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.project_config import load_project_config


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        obj = yaml.safe_load(text) or {}
        return obj if isinstance(obj, dict) else {}
    except Exception:
        import json

        obj = json.loads(text or "{}")
        return obj if isinstance(obj, dict) else {}


def _dump_yaml(data: Dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    except Exception:
        import json

        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _ensure_dict(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    obj = data.get(key)
    if not isinstance(obj, dict):
        obj = {}
        data[key] = obj
    return obj


def migrate(
    *,
    unified_config: str,
    rednotes_config: str,
    wechat_config: str,
    cover_config: str,
) -> None:
    base_cfg, _profile = load_project_config(unified_config)
    cfg = copy.deepcopy(base_cfg)

    red = _read_yaml(Path(rednotes_config))
    wc = _read_yaml(Path(wechat_config))
    cover = _read_yaml(Path(cover_config))

    # WeChat section
    wechat = _ensure_dict(cfg, "wechat")
    for key in ("cookie", "user_agent", "token", "since", "accounts"):
        if key in wc:
            wechat[key] = wc.get(key)
    wc_prompts = _ensure_dict(wechat, "prompts")
    if "prompt" in wc:
        wc_prompts["stage1_wechat"] = wc.get("prompt")
    if "prompt_stage2" in wc:
        wc_prompts["stage2_wechat"] = wc.get("prompt_stage2")

    # XHS section
    xhs = _ensure_dict(cfg, "xhs")
    for key in ("since", "maxcrawl", "interval_sec", "accounts"):
        if key in red:
            xhs[key] = red.get(key)

    qwen = _ensure_dict(xhs, "qwen")
    red_qwen = red.get("qwen") if isinstance(red.get("qwen"), dict) else {}
    for key in ("api_key", "base_url", "model", "timeout_sec", "enable_thinking"):
        if key in red_qwen:
            qwen[key] = red_qwen.get(key)

    xhs_prompts = _ensure_dict(xhs, "prompts")
    if "prompt_stage1_xhs" in red:
        xhs_prompts["stage1_xhs"] = red.get("prompt_stage1_xhs")
    if "prompt_stage2_xhs" in red:
        xhs_prompts["stage2_xhs"] = red.get("prompt_stage2_xhs")
    if "prompt_bitable_one_liner" in red:
        xhs_prompts["bitable_one_liner"] = red.get("prompt_bitable_one_liner")
    if "prompt_bitable_one_liner_analysis" in red:
        xhs_prompts["bitable_one_liner_analysis"] = red.get("prompt_bitable_one_liner_analysis")

    # Feishu and Bitable
    feishu = _ensure_dict(cfg, "feishu")
    red_feishu = red.get("feishu") if isinstance(red.get("feishu"), dict) else {}
    for key in ("app_id", "app_secret", "receive_id_type", "receive_id"):
        if key in red_feishu:
            feishu[key] = red_feishu.get(key)

    bitable = _ensure_dict(cfg, "bitable")
    red_bitable = red.get("bitable") if isinstance(red.get("bitable"), dict) else {}
    for key in ("use_views", "write_mode", "app_token", "table_id"):
        if key in red_bitable:
            bitable[key] = red_bitable.get(key)
    if isinstance(red_bitable.get("tables"), dict):
        bitable["tables"] = red_bitable.get("tables")
    else:
        tables = _ensure_dict(bitable, "tables")
        if red_bitable.get("wechat_table_id"):
            tables["wechat"] = {"name": "wechat", "table_id": red_bitable.get("wechat_table_id")}
        if red_bitable.get("xhs_table_id"):
            tables["xhs"] = {"name": "xhs", "table_id": red_bitable.get("xhs_table_id")}

    # Cover
    cover_section = _ensure_dict(cfg, "cover")
    if isinstance(cover.get("wanx"), dict):
        cover_section["wanx"] = cover.get("wanx")
    if isinstance(cover.get("prompt"), dict):
        cover_section["prompt"] = cover.get("prompt")
    if isinstance(cover.get("output"), dict):
        cover_section["output"] = cover.get("output")

    out_path = Path(unified_config)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_dump_yaml(cfg), encoding="utf-8")
    print(f"Migrated unified config: {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy yaml configs into unified configs/config.yaml")
    parser.add_argument("--config", default=str(Path("configs") / "config.yaml"), help="Unified config path")
    parser.add_argument("--rednotes", default="configs/legacy/rednotes.yaml")
    parser.add_argument("--wechat", default="configs/legacy/wechat.yaml")
    parser.add_argument("--cover", default="configs/legacy/cover_gen.yaml")
    args = parser.parse_args()

    migrate(
        unified_config=args.config,
        rednotes_config=args.rednotes,
        wechat_config=args.wechat,
        cover_config=args.cover,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
