import argparse
import sys
from pathlib import Path

from Tools.feishu_utils import get_tenant_access_token, load_feishu_settings, send_post, upload_image


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="Send a rich post (text + optional image) to Feishu chat")
    parser.add_argument("-c", "--config", default="rednotes.yaml")
    parser.add_argument("--chat-id", default="", help="Override chat_id (default: rednotes.yaml feishu.receive_id)")
    parser.add_argument("--title", default="📰 AI 日报")
    parser.add_argument("--text", required=True, help="Main text block")
    parser.add_argument("--image-path", default="", help="Local image path to upload and include")
    parser.add_argument("--footer", default="来源：自动爬取 + AI 分析")
    args = parser.parse_args()

    settings = load_feishu_settings(args.config, chat_id=args.chat_id)
    token = get_tenant_access_token(settings.app_id, settings.app_secret)

    blocks = [[{"tag": "text", "text": args.text}]]
    if args.image_path:
        p = Path(args.image_path)
        image_key = upload_image(token, p)
        blocks.append([{"tag": "img", "image_key": image_key}])
    if args.footer:
        blocks.append([{"tag": "text", "text": args.footer}])

    send_post(token, settings.chat_id, title=args.title, blocks=blocks)
    print("Sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
