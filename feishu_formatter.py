import argparse
import json
from pathlib import Path
from datetime import datetime

import requests


def build_feishu_payload(items) -> dict:
    """构造简版 post，仅包含 text，标题以超链接形式嵌入。"""
    parts = []
    header_match = "日报速递"
    header = "**日报速递**"
    intro = ""
    if items:
        first = items[0]
        first_title = str(first.get("title", "")).strip()
        first_url = first.get("url", "") or first.get("link", "")
        first_score = first.get("score", "")
        if first_title == header_match and not first_url and not first_score:
            intro = str(first.get("summary", "")).strip()
            items = items[1:]
    if header:
        parts.append(header)
    if intro:
        parts.append(intro)
    for idx, it in enumerate(items, 1):
        title = it.get("title", "")
        url = it.get("url", "") or it.get("link", "")
        summary = it.get("summary", "")
        score = it.get("score", "")
        tag = it.get("tag", "")
        if not tag:
            tag = it.get("tags", "")
        if url:
            first_line = f"{idx}. [{title}]({url})"
        else:
            first_line = f"{idx}. {title}"
        block_lines = [first_line]
        if score != "" and score is not None:
            block_lines.append(f"得分: {score}")
        if tag:
            if isinstance(tag, list):
                tag_text = "、".join([str(t).strip() for t in tag if str(t).strip()])
            else:
                tag_text = str(tag).strip()
            if tag_text:
                block_lines.append(f"标签: {tag_text}")
        if summary:
            block_lines.append(str(summary))
        parts.append("\n".join(block_lines))
    text = "\n\n".join([p for p in parts if p != ""])
    return {
        "msg_type": "post",
        "content": {
            "text": text,
        },
    }


def load_items_from_json(path: Path):
    """
    Load items from a JSON file.

    Supported JSON shapes:
    - a list of item dicts: [{"title": "...", "url": "...", ...}, ...]
    - a single item dict: {"title": "...", "url": "...", ...}  (wrapped into a list)
    - a dict with an "items" list: {"items": [...], ...}
    """
    raw = path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return []

    parsed = json.loads(stripped)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
        return parsed["items"]
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(f"Unsupported JSON structure in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert items JSON to Feishu post JSON (simple post).")
    parser.add_argument(
        "-i",
        "--input",
        default=f"{datetime.now().strftime('%Y%m%d')}.json",
        help="Path to JSON containing items (default: YYYYMMDD.json for current date)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="Output JSON file path (default: stdout)",
    )
    parser.add_argument(
        "--webhook",
        default="https://www.feishu.cn/flow/api/trigger-webhook/06e2681cec135fd293eb45a2d761f450",
        help="Feishu webhook URL; if provided, send the post payload directly (default: disabled)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        print("Tip: pass input explicitly, e.g. `python feishu_formatter.py -i paper.json`")
        raise SystemExit(2)

    items = load_items_from_json(input_path)
    payload = build_feishu_payload(items)
    data = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(data, encoding="utf-8")
    else:
        print(data)

    if args.webhook:
        resp = requests.post(args.webhook, json=payload, timeout=10)
        if resp.status_code == 200:
            print("Sent to Feishu webhook successfully.")
        else:
            print(f"Failed to send to Feishu webhook, status: {resp.status_code}")
            print(f"Response: {resp.text}")


if __name__ == "__main__":
    main()
