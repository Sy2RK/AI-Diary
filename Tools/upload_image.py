import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from Tools.feishu_utils import get_tenant_access_token, load_feishu_app, upload_image


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload local image to Feishu and print image_key")
    parser.add_argument("-c", "--config", default="configs/config.yaml", help="配置入口（统一配置或 legacy rednotes.yaml）")
    parser.add_argument("-i", "--image", required=True, help="Local image path")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(image_path)

    app = load_feishu_app(args.config)
    token = get_tenant_access_token(app.app_id, app.app_secret)
    image_key = upload_image(token, image_path)
    print("IMAGE_KEY =", image_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
