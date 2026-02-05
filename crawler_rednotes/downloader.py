import hashlib
import os
import requests
from typing import Tuple


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_image(url: str, media_dir: str) -> Tuple[str, str]:
    os.makedirs(media_dir, exist_ok=True)
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    content = resp.content
    digest = _sha256_bytes(content)
    ext = "jpg"
    filename = f"{digest}.{ext}"
    path = os.path.join(media_dir, filename)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(content)
    return path, digest
