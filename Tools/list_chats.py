import argparse
import sys
from pathlib import Path

from Tools.feishu_utils import get_tenant_access_token, list_chats, load_feishu_app


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="List Feishu chats (token auto from rednotes.yaml)")
    parser.add_argument("-c", "--config", default="rednotes.yaml")
    parser.add_argument("--page-size", type=int, default=50)
    args = parser.parse_args()

    app = load_feishu_app(args.config)
    token = get_tenant_access_token(app.app_id, app.app_secret)
    try:
        chats = list_chats(token, page_size=int(args.page_size))
        for chat in chats:
            print(chat.get("chat_id"), chat.get("name"))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"List failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
