from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli


FEISHU_OPENAPI = "https://open.feishu.cn/open-apis"


@dataclass
class FeishuSettings:
    app_id: str
    app_secret: str
    chat_id: str


@dataclass
class FeishuApp:
    app_id: str
    app_secret: str


def load_feishu_app(config_path: str = DEFAULT_PROJECT_CONFIG, profile: str = "") -> FeishuApp:
    from crawler_rednotes.config_rednotes import load_rednotes_config

    cfg_path = resolve_legacy_config_for_cli(config_path, kind="rednotes", profile=profile)
    cfg, _raw = load_rednotes_config(cfg_path)
    if not cfg.feishu or not cfg.feishu.app_id or not cfg.feishu.app_secret:
        raise RuntimeError("Missing feishu.app_id/app_secret in config")
    return FeishuApp(app_id=cfg.feishu.app_id, app_secret=cfg.feishu.app_secret)


def load_feishu_settings(config_path: str = DEFAULT_PROJECT_CONFIG, chat_id: str = "", profile: str = "") -> FeishuSettings:
    from crawler_rednotes.config_rednotes import load_rednotes_config

    cfg_path = resolve_legacy_config_for_cli(config_path, kind="rednotes", profile=profile)
    cfg, _raw = load_rednotes_config(cfg_path)
    app = load_feishu_app(cfg_path, profile=profile)
    resolved_chat_id = (chat_id or cfg.feishu.receive_id or "").strip()
    if not resolved_chat_id:
        raise RuntimeError("Missing feishu.receive_id (chat_id) in config, or pass --chat-id")
    return FeishuSettings(app_id=app.app_id, app_secret=app.app_secret, chat_id=resolved_chat_id)


def get_tenant_access_token(app_id: str, app_secret: str, timeout_sec: int = 10) -> str:
    import requests

    url = f"{FEISHU_OPENAPI}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=timeout_sec)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"get_token failed: {data}")
    return data["tenant_access_token"]


def upload_image(token: str, image_path: Path, timeout_sec: int = 20) -> str:
    import requests

    url = f"{FEISHU_OPENAPI}/image/v4/put/"
    with image_path.open("rb") as f:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"image": f},
            data={"image_type": "message"},
            timeout=timeout_sec,
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"upload_image failed: {data}")
    return data["data"]["image_key"]


def send_text(token: str, chat_id: str, text: str, timeout_sec: int = 10) -> Dict[str, Any]:
    import requests

    url = f"{FEISHU_OPENAPI}/im/v1/messages?receive_id_type=chat_id"
    payload = {"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_sec,
    )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"raw_text": resp.text}
    if resp.status_code >= 400:
        raise RuntimeError(f"send_text http={resp.status_code} resp={data}")
    if isinstance(data, dict) and data.get("code") != 0:
        raise RuntimeError(f"send_text failed: {data}")
    return data  # type: ignore[return-value]


def send_image(token: str, chat_id: str, image_key: str, timeout_sec: int = 10) -> Dict[str, Any]:
    import requests

    url = f"{FEISHU_OPENAPI}/im/v1/messages?receive_id_type=chat_id"
    payload = {"receive_id": chat_id, "msg_type": "image", "content": json.dumps({"image_key": image_key})}
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_sec,
    )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"raw_text": resp.text}
    if resp.status_code >= 400:
        raise RuntimeError(f"send_image http={resp.status_code} resp={data}")
    if isinstance(data, dict) and data.get("code") != 0:
        raise RuntimeError(f"send_image failed: {data}")
    return data  # type: ignore[return-value]


def send_post(token: str, chat_id: str, title: str, blocks: List[List[Dict[str, Any]]], timeout_sec: int = 10) -> Dict[str, Any]:
    import requests

    url = f"{FEISHU_OPENAPI}/im/v1/messages?receive_id_type=chat_id"
    content = {"zh_cn": {"title": title, "content": blocks}}
    payload = {"receive_id": chat_id, "msg_type": "post", "content": json.dumps(content, ensure_ascii=False)}
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout_sec,
    )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"raw_text": resp.text}
    if resp.status_code >= 400:
        raise RuntimeError(f"send_post http={resp.status_code} resp={data}")
    if isinstance(data, dict) and data.get("code") != 0:
        raise RuntimeError(f"send_post failed: {data}")
    return data


def list_chats(token: str, page_size: int = 50, timeout_sec: int = 10) -> List[Dict[str, Any]]:
    import requests

    url = f"{FEISHU_OPENAPI}/im/v1/chats"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"page_size": page_size},
        timeout=timeout_sec,
    )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"raw_text": resp.text}
    if resp.status_code >= 400:
        raise RuntimeError(f"list_chats http={resp.status_code} resp={data}")
    if isinstance(data, dict) and data.get("code") != 0:
        raise RuntimeError(f"list_chats failed: {data}")
    items = data.get("data", {}).get("items") or []
    return [it for it in items if isinstance(it, dict)]
