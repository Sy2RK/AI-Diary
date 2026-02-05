import re
from urllib.parse import urlparse


PROFILE_PATTERN = re.compile(r"/user/profile/[^/?#]+")


def normalize_profile_url(raw: str) -> str:
    """
    Normalize Xiaohongshu profile URL:
    - require domain contains xiaohongshu.com
    - path must include /user/profile/<id>
    - strip query/fragment
    """
    if not raw:
        raise ValueError("profile_url is required")
    parsed = urlparse(raw.strip())
    if "xiaohongshu.com" not in parsed.netloc:
        raise ValueError("profile_url must be on xiaohongshu.com")
    match = PROFILE_PATTERN.search(parsed.path)
    if not match:
        raise ValueError("profile_url must contain /user/profile/<id>")
    normalized_path = match.group(0)
    normalized = f"https://{parsed.netloc}{normalized_path}"
    return normalized
