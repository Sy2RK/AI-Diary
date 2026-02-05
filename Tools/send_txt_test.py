import argparse
import sys
from pathlib import Path

from Tools.feishu_utils import get_tenant_access_token, load_feishu_settings, send_text


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="Send a text message to Feishu chat (token auto from rednotes.yaml)")
    parser.add_argument("-c", "--config", default="rednotes.yaml")
    parser.add_argument("--chat-id", default="", help="Override chat_id (default: rednotes.yaml feishu.receive_id)")
    parser.add_argument("--text", default="AI日报文本推送测试")
    args = parser.parse_args()

    settings = load_feishu_settings(args.config, chat_id=args.chat_id)
    token = get_tenant_access_token(settings.app_id, settings.app_secret)
    try:
        send_text(token, settings.chat_id, args.text)
        print(f"Sent. chat_id={settings.chat_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Send failed. chat_id={settings.chat_id} err={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
