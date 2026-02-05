from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from crawler_rednotes.config_rednotes import load_rednotes_config
from Tools.feishu_utils import get_tenant_access_token, send_post, upload_image


def _send_post(token: str, chat_id: str, title: str, blocks: List[List[Dict[str, Any]]]) -> None:
    send_post(token, chat_id, title=title, blocks=blocks)


def _sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _parse_wechat_source_id(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    biz = re.search(r"(?:\\?|&)__biz=([^&]+)", u)
    mid = re.search(r"(?:\\?|&)mid=(\\d+)", u)
    idx = re.search(r"(?:\\?|&)idx=(\\d+)", u)
    if biz and mid and idx:
        return f"{biz.group(1)}_{mid.group(1)}_{idx.group(1)}"
    return _sha1(u)


def load_stage2_items(stage2_path: Path) -> List[Dict[str, Any]]:
    obj = json.loads(stage2_path.read_text(encoding="utf-8"))
    if not isinstance(obj, list):
        raise RuntimeError(f"wechat stage2 must be a JSON array: {stage2_path}")
    return [it for it in obj if isinstance(it, dict)]


def pick_generated_cover(run_root: Path, url: str) -> str:
    uid = _parse_wechat_source_id(url)
    if not uid:
        return ""
    covers_dir = run_root / "covers"
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = covers_dir / f"{uid}{ext}"
        if p.exists():
            return str(p)
    return ""


def build_post_blocks(items: List[Dict[str, Any]], image_key_by_uid: Dict[str, str]) -> List[List[Dict[str, Any]]]:
    blocks: List[List[Dict[str, Any]]] = []

    intro = ""
    if items:
        first = items[0]
        first_title = str(first.get("title") or "").strip()
        first_url = str(first.get("url") or first.get("link") or "").strip()
        first_score = first.get("score", "")
        if first_title == "日报速递" and not first_url and (first_score == "" or first_score is None):
            intro = str(first.get("summary") or "").strip()
            items = items[1:]

    blocks.append([{"tag": "text", "text": "📌【日报速递】"}])
    if intro:
        blocks.append([{"tag": "text", "text": intro}])

    max_items = 10
    for idx, it in enumerate(items[:max_items], 1):
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or it.get("link") or "").strip()
        score = it.get("score", "")
        tag = it.get("tag") or it.get("tags") or ""
        summary = str(it.get("summary") or "").strip()

        blocks.append([{"tag": "text", "text": f"🔷【{idx}. {title}】"}])
        if url:
            blocks.append([{"tag": "text", "text": "🔗 原文链接："}, {"tag": "a", "text": "点击打开", "href": url}])

        if score != "" and score is not None:
            blocks.append([{"tag": "text", "text": f"⭐ 得分：{score}"}])
        if tag:
            if isinstance(tag, list):
                tag_text = "、".join([str(t).strip() for t in tag if str(t).strip()])
            else:
                tag_text = str(tag).strip()
            if tag_text:
                blocks.append([{"tag": "text", "text": f"🏷️ 标签：{tag_text}"}])
        if summary:
            blocks.append([{"tag": "text", "text": f"📝 摘要：{summary}"}])

        if url:
            uid = _parse_wechat_source_id(url)
            img_key = image_key_by_uid.get(uid) or ""
            if img_key:
                blocks.append([{"tag": "img", "image_key": img_key}])

    return blocks


def send_wechat_stage2_to_feishu(config_path: str, stage2_path: str) -> None:
    cfg, _raw = load_rednotes_config(config_path)
    if not cfg.feishu or not cfg.feishu.app_id or not cfg.feishu.app_secret:
        raise RuntimeError("Missing feishu.app_id/app_secret in rednotes.yaml")
    chat_id = str(cfg.feishu.receive_id or "").strip()
    if not chat_id:
        raise RuntimeError("Missing feishu.receive_id (chat_id) in rednotes.yaml")

    p = Path(stage2_path)
    if not p.exists():
        raise FileNotFoundError(p)
    run_root = p.parent

    items = load_stage2_items(p)
    token = get_tenant_access_token(cfg.feishu.app_id, cfg.feishu.app_secret)

    image_key_by_uid: Dict[str, str] = {}
    for it in items:
        url = str(it.get("url") or it.get("link") or "").strip()
        if not url:
            continue
        uid = _parse_wechat_source_id(url)
        if not uid or uid in image_key_by_uid:
            continue
        cover_path = pick_generated_cover(run_root, url)
        if not cover_path:
            continue
        fp = Path(cover_path)
        if fp.exists():
            image_key_by_uid[uid] = upload_image(token, fp)

    blocks = build_post_blocks(items, image_key_by_uid)
    date_str = datetime.now().strftime("%Y-%m-%d")
    _send_post(token, chat_id, title=f"WeChat 日报 {date_str}", blocks=blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send WeChat stage2.json to Feishu as rich post (with generated covers).")
    parser.add_argument("-c", "--config", default="rednotes.yaml")
    parser.add_argument("--stage2", required=True, help="outputs/wechat/<run>/*_stage2.json")
    args = parser.parse_args()
    try:
        send_wechat_stage2_to_feishu(args.config, stage2_path=args.stage2)
        print("Sent successfully.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Send failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

