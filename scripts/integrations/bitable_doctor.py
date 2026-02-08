from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli

FEISHU_OPENAPI = "https://open.feishu.cn/open-apis"


@dataclass
class BitableSettings:
    app_id: str
    app_secret: str
    app_token: str
    table_id: str


def _load_settings(config_path: str) -> BitableSettings:
    from crawler_rednotes.config_rednotes import load_rednotes_config

    cfg, _raw = load_rednotes_config(config_path)
    if not cfg.feishu or not cfg.feishu.app_id or not cfg.feishu.app_secret:
        raise RuntimeError("Missing feishu.app_id/app_secret in config")
    if not cfg.bitable or not cfg.bitable.app_token or not cfg.bitable.table_id:
        raise RuntimeError("Missing bitable.app_token/table_id in config")
    return BitableSettings(
        app_id=cfg.feishu.app_id,
        app_secret=cfg.feishu.app_secret,
        app_token=cfg.bitable.app_token,
        table_id=cfg.bitable.table_id,
    )


def _get_token(app_id: str, app_secret: str) -> str:
    from crawler_common.feishu_api import get_tenant_access_token

    return get_tenant_access_token(app_id, app_secret)


def _req(method: str, url: str, token: str, params: Dict[str, Any] | None = None, body: Dict[str, Any] | None = None) -> Tuple[int, Any]:
    import requests

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.request(method, url, headers=headers, params=params, json=body, timeout=15)
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"raw_text": resp.text}
    return resp.status_code, data


def main() -> int:
    parser = argparse.ArgumentParser(description="Bitable doctor: verify read/write access for app_token/table_id.")
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_PROJECT_CONFIG,
        help="配置入口（统一配置 configs/config.yaml 或 legacy rednotes.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    args = parser.parse_args()
    try:
        cfg_path = resolve_legacy_config_for_cli(args.config, kind="rednotes", profile=args.profile)
    except Exception as exc:  # noqa: BLE001
        print(f"config resolve failed: {exc}", file=sys.stderr)
        return 2

    s = _load_settings(cfg_path)
    token = _get_token(s.app_id, s.app_secret)

    print(f"app_token={s.app_token}")
    print(f"table_id={s.table_id}")

    endpoints = [
        ("GET", f"{FEISHU_OPENAPI}/bitable/v1/apps/{s.app_token}", None, None),
        ("GET", f"{FEISHU_OPENAPI}/bitable/v1/apps/{s.app_token}/tables", {"page_size": 50}, None),
        ("GET", f"{FEISHU_OPENAPI}/bitable/v1/apps/{s.app_token}/tables/{s.table_id}/fields", {"page_size": 200}, None),
        ("GET", f"{FEISHU_OPENAPI}/bitable/v1/apps/{s.app_token}/tables/{s.table_id}/records", {"page_size": 1}, None),
        ("POST", f"{FEISHU_OPENAPI}/bitable/v1/apps/{s.app_token}/tables/{s.table_id}/records/batch_create", None, {"records": []}),
    ]

    for method, url, params, body in endpoints:
        status, data = _req(method, url, token, params=params, body=body)
        print("\n---")
        print(f"{method} {url}")
        if params:
            print("params:", json.dumps(params, ensure_ascii=False))
        if body is not None:
            print("body:", json.dumps(body, ensure_ascii=False))
        print("http:", status)
        print("resp:", json.dumps(data, ensure_ascii=False)[:2000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
