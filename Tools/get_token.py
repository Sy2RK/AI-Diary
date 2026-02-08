import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from Tools.feishu_utils import get_tenant_access_token, load_feishu_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Get Feishu tenant_access_token from config")
    parser.add_argument("-c", "--config", default="configs/config.yaml", help="配置入口（统一配置或 legacy rednotes.yaml）")
    args = parser.parse_args()

    app = load_feishu_app(args.config)
    token = get_tenant_access_token(app.app_id, app.app_secret)
    print("TOKEN =", token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
