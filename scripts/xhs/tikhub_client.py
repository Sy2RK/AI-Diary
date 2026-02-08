from __future__ import annotations

import json
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE = "https://api.tikhub.io"
NOTE_INFO_PATH = "/api/v1/xiaohongshu/app/get_note_info"
VIDEO_NOTE_INFO_PATH = "/api/v1/xiaohongshu/app/get_video_note_info"
USER_NOTES_PATH = "/api/v1/xiaohongshu/app/get_user_notes"


def _http_json(url: str, headers: Dict[str, str], timeout_sec: int = 20) -> Any:
    # TikHub is behind Cloudflare; the default urllib User-Agent can be blocked.
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", "curl/8.16.0")
    hdrs.setdefault("Accept", "application/json")
    req = Request(url, headers=hdrs, method="GET")
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"HTTPError {exc.code}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"URLError: {exc}") from exc
    return json.loads(raw)


def fetch_note_info(note_id: str, api_key: str, timeout_sec: int = 20) -> Any:
    nid = (note_id or "").strip()
    if not nid:
        raise ValueError("note_id is empty")
    url = f"{API_BASE}{NOTE_INFO_PATH}?note_id={nid}"
    headers = {"Authorization": f"Bearer {api_key}"}
    return _http_json(url, headers=headers, timeout_sec=timeout_sec)


def fetch_video_note_info(note_id: str, api_key: str, timeout_sec: int = 20) -> Any:
    nid = (note_id or "").strip()
    if not nid:
        raise ValueError("note_id is empty")
    url = f"{API_BASE}{VIDEO_NOTE_INFO_PATH}?note_id={nid}"
    headers = {"Authorization": f"Bearer {api_key}"}
    return _http_json(url, headers=headers, timeout_sec=timeout_sec)


def fetch_user_notes(user_id: str, api_key: str, cursor: str = "", timeout_sec: int = 20) -> Any:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is empty")
    cur = (cursor or "").strip()
    url = f"{API_BASE}{USER_NOTES_PATH}?user_id={uid}"
    if cur:
        url += f"&cursor={cur}"
    headers = {"Authorization": f"Bearer {api_key}"}
    return _http_json(url, headers=headers, timeout_sec=timeout_sec)


def fetch_user_notes_alt_userid(user_id: str, api_key: str, cursor: str = "", timeout_sec: int = 20) -> Any:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is empty")
    cur = (cursor or "").strip()
    url = f"{API_BASE}{USER_NOTES_PATH}?userid={uid}"
    if cur:
        url += f"&cursor={cur}"
    headers = {"Authorization": f"Bearer {api_key}"}
    return _http_json(url, headers=headers, timeout_sec=timeout_sec)

