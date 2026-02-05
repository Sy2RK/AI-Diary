from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _repo_root() -> Path:
    # aily/refresh_aily_token.py -> repo root is parent of "aily"
    return Path(__file__).resolve().parent.parent


def _ensure_repo_root_on_path() -> None:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _resolve_path(p: str) -> str:
    s = str(p or "").strip()
    if not s:
        return s
    direct = Path(s)
    if direct.exists():
        return str(direct)
    candidate = _repo_root() / s
    return str(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Feishu tenant_access_token and write into aily_cover.yaml.")
    parser.add_argument("--project-config", default="rednotes.yaml", help="Project config containing feishu.app_id/app_secret")
    parser.add_argument("--aily-config", default="aily_cover.yaml", help="Target aily_cover.yaml to patch")
    parser.add_argument("--print", dest="print_token", action="store_true", help="Print token to stdout")
    args = parser.parse_args()

    try:
        _ensure_repo_root_on_path()
        from crawler_rednotes.config_rednotes import load_rednotes_config
        from crawler_rednotes.yaml_patch import set_scalar
    except Exception as exc:  # noqa: BLE001
        print(f"[refresh_aily_token] missing local modules: {exc}", file=sys.stderr)
        return 2

    project_config_path = _resolve_path(args.project_config)
    aily_config_path = _resolve_path(args.aily_config)

    cfg, _raw = load_rednotes_config(project_config_path)
    if not cfg.feishu or not cfg.feishu.app_id or not cfg.feishu.app_secret:
        print("[refresh_aily_token] missing feishu.app_id/app_secret in project config", file=sys.stderr)
        return 2

    try:
        from Tools.feishu_utils import get_tenant_access_token
    except Exception:
        # Fallback: local requests call
        import requests

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": cfg.feishu.app_id, "app_secret": cfg.feishu.app_secret}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            print(f"[refresh_aily_token] get token failed: {data}", file=sys.stderr)
            return 1
        token = str(data.get("tenant_access_token") or "").strip()
    else:
        token = get_tenant_access_token(cfg.feishu.app_id, cfg.feishu.app_secret, timeout_sec=15)

    if not token:
        print("[refresh_aily_token] empty tenant_access_token", file=sys.stderr)
        return 1

    # NOTE: Aily OpenAPI often uses user_access_token. We keep the YAML key name for convenience.
    set_scalar(aily_config_path, ["aily", "user_access_token"], token)

    if args.print_token:
        print(token)
    else:
        print(f"[refresh_aily_token] updated {aily_config_path} aily.user_access_token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
