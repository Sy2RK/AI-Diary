from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3-pro-image-preview"
DEFAULT_PROMPT = "Create a modern, minimal, tech-themed cover image."
DEFAULT_ASPECT_RATIO = "1:1"


def _safe_suffix_from_mime(mime: str) -> str:
    m = (mime or "").lower().strip()
    if m == "image/png":
        return ".png"
    if m in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if m == "image/webp":
        return ".webp"
    return ".png"


def _safe_filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name
    return name or f"openrouter_{int(time.time())}.png"


def _unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for i in range(2, 10_000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many existing files for: {target}")


def _extract_images(payload: Any) -> list[str]:
    urls: list[str] = []
    if not isinstance(payload, dict):
        return urls

    choices = payload.get("choices")
    if not isinstance(choices, list):
        return urls

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue

        images = message.get("images")
        if isinstance(images, list):
            for item in images:
                if not isinstance(item, dict):
                    continue
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                    if isinstance(url, str) and url.strip():
                        urls.append(url.strip())

        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "image_url":
                    continue
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                    if isinstance(url, str) and url.strip():
                        urls.append(url.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    return deduped


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    match = re.match(r"^data:([^;]+);base64,(.+)$", data_url, flags=re.DOTALL)
    if not match:
        raise ValueError("Invalid data URL format")
    mime = match.group(1).strip()
    encoded = match.group(2).strip()
    raw = base64.b64decode(encoded)
    return raw, _safe_suffix_from_mime(mime)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate images via OpenRouter (Nano Banana Pro by default).")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Text prompt")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model id")
    parser.add_argument("--aspect-ratio", default=DEFAULT_ASPECT_RATIO, help="Image aspect ratio, e.g. 1:1, 16:9")
    parser.add_argument("--n", type=int, default=1, help="Requested number of images")
    parser.add_argument("--seed", type=int, default=None, help="Optional seed")
    parser.add_argument("--out-dir", default="openrouter_out", help="Output directory")
    parser.add_argument("--out-prefix", default="openrouter", help="Output filename prefix")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenRouter chat completions endpoint")
    parser.add_argument(
        "--api-key",
        default="",
        help="OpenRouter API key; fallback to OPENROUTER_API_KEY environment variable",
    )
    parser.add_argument("--http-referer", default="", help="Optional HTTP-Referer header")
    parser.add_argument("--x-title", default="", help="Optional X-Title header")
    parser.add_argument("--verbose", action="store_true", help="Print verbose response details")
    args = parser.parse_args()

    api_key = (args.api_key or os.environ.get("OPENROUTER_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY (env/--api-key).")

    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency: requests.\n"
            f"Current python: {sys.executable}\n"
            "Install into THIS interpreter:\n"
            f"  {sys.executable} -m pip install requests\n"
        ) from e

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    http_referer = (args.http_referer or os.environ.get("OPENROUTER_HTTP_REFERER", "")).strip()
    if http_referer:
        headers["HTTP-Referer"] = http_referer
    x_title = (args.x_title or os.environ.get("OPENROUTER_APP_NAME", "")).strip()
    if x_title:
        headers["X-Title"] = x_title

    body: dict[str, Any] = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "modalities": ["image", "text"],
        "stream": False,
        "image_config": {"aspect_ratio": args.aspect_ratio},
        "n": int(args.n),
    }
    if args.seed is not None:
        body["seed"] = int(args.seed)

    print("---- request, please wait a moment ----")
    resp = requests.post(args.base_url, headers=headers, json=body, timeout=180)
    try:
        data = resp.json()
    except Exception:
        data = {"_raw_text": resp.text}

    if args.verbose:
        print("http:", resp.status_code)
        print("response:", json.dumps(data, ensure_ascii=False, indent=2)[:20_000])

    if resp.status_code != 200:
        print("request failed, http_status: %s" % resp.status_code)
        if isinstance(data, dict):
            print("error:", data.get("error") or data)
        return 2

    images = _extract_images(data)
    if not images:
        print("no image found in response")
        return 3

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for idx, raw in enumerate(images, start=1):
        if raw.startswith("data:"):
            content, suffix = _decode_data_url(raw)
            target = _unique_path(out_dir / f"{args.out_prefix}_{idx}{suffix}")
            target.write_bytes(content)
            saved += 1
            print(f"saved: {target}")
            continue

        if raw.startswith("http://") or raw.startswith("https://"):
            file_name = _safe_filename_from_url(raw)
            target = _unique_path(out_dir / file_name)
            r = requests.get(raw, timeout=180)
            r.raise_for_status()
            target.write_bytes(r.content)
            saved += 1
            print(f"saved: {target}")
            continue

        if args.verbose:
            print("skip unsupported image payload:", raw[:120])

    print(f"done: {saved} file(s)")
    return 0 if saved > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
