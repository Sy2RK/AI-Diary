import argparse
import sys
from pathlib import Path

from Tools.feishu_utils import get_tenant_access_token, load_feishu_settings, send_image, upload_image


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="Send an image to Feishu chat (upload then send)")
    parser.add_argument("-c", "--config", default="rednotes.yaml")
    parser.add_argument("--chat-id", default="", help="Override chat_id (default: rednotes.yaml feishu.receive_id)")
    parser.add_argument("--image-key", default="", help="Existing image_key (skip upload)")
    parser.add_argument("--image-path", default="", help="Local image path to upload")
    args = parser.parse_args()

    settings = load_feishu_settings(args.config, chat_id=args.chat_id)
    token = get_tenant_access_token(settings.app_id, settings.app_secret)

    image_key = args.image_key.strip()
    if not image_key:
        if not args.image_path:
            raise SystemExit("Provide --image-key or --image-path")
        p = Path(args.image_path)
        if not p.exists():
            raise FileNotFoundError(p)
        image_key = upload_image(token, p)

    send_image(token, settings.chat_id, image_key)
    print("Sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


