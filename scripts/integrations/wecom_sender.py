from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_WEBHOOK = os.environ.get("WECOM_WEBHOOK_URL", "")
# WeCom markdown.content has a max length limit. Use a per-message cap and send multiple messages.
# Note: WeCom counts by bytes (UTF-8), not Python characters.
MAX_MARKDOWN_BYTES = 3600


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _post_json(url: str, payload: Dict[str, Any], timeout_sec: int = 15) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"HTTPError {exc.code}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"URLError: {exc}") from exc
    try:
        return json.loads(raw)
    except Exception:
        return {"raw_text": raw}


def send_markdown(webhook: str, content: str) -> Dict[str, Any]:
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    return _post_json(webhook, payload)


def _find_latest_path(root: Path, pattern: str, exclude_substr: str = "") -> Optional[Path]:
    if not root.exists():
        return None
    candidates = list(root.rglob(pattern))
    if exclude_substr:
        candidates = [p for p in candidates if exclude_substr not in str(p)]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _shorten(text: str, max_len: int = 900) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 20].rstrip() + "\n...[truncated]..."

def _utf8_len(text: str) -> int:
    return len((text or "").encode("utf-8"))


def _join_lines(lines: List[str]) -> str:
    return "\n".join(lines).strip()


def _fit_append(base_lines: List[str], extra_lines: List[str], max_bytes: int) -> bool:
    candidate = _join_lines(base_lines + extra_lines)
    if _utf8_len(candidate) > max_bytes:
        return False
    base_lines.extend(extra_lines)
    return True


def _chunk_markdown(
    header_lines: List[str],
    overview_line: str,
    item_blocks: List[List[str]],
    max_bytes: int = MAX_MARKDOWN_BYTES,
    cont_header: str = "## （续）",
) -> List[str]:
    messages: List[str] = []

    base: List[str] = []
    base.extend(header_lines)

    if overview_line:
        # Try add overview; shorten if needed.
        if not _fit_append(base, ["", overview_line], max_bytes=max_bytes):
            # Keep header, but clamp overview to fit.
            remaining = max_bytes - _utf8_len(_join_lines(base)) - _utf8_len("\n\n> ") - 10
            if remaining > 60:
                ov = _shorten(overview_line.lstrip("> ").strip(), max_len=200)
                # reduce further by bytes if necessary
                while _utf8_len("> " + ov) > max(60, remaining) and len(ov) > 30:
                    ov = ov[:-10]
                _fit_append(base, ["", f"> {ov}"], max_bytes=max_bytes)

    if not base or _utf8_len(_join_lines(base)) > max_bytes:
        raise RuntimeError("Cannot fit markdown header within max bytes")

    base.append("")

    for block in item_blocks:
        if not _fit_append(base, block + [""], max_bytes=max_bytes):
            # flush current message and start a new one
            messages.append(_join_lines(base)[:])
            base = [cont_header] + header_lines[1:]  # reuse source line
            if overview_line:
                base.append("> （续上）")
            base.append("")
            if not _fit_append(base, block + [""], max_bytes=max_bytes):
                # Block itself too large; hard truncate block lines.
                truncated: List[str] = []
                for ln in block:
                    truncated.append(ln)
                    if _utf8_len(_join_lines(base + truncated)) > max_bytes - 80:
                        truncated.pop()
                        break
                truncated.append("> （该条过长，已截断）")
                _fit_append(base, truncated + [""], max_bytes=max_bytes)

    if base:
        messages.append(_join_lines(base))

    # Ensure all messages are within max_bytes (UTF-8)
    final: List[str] = []
    for m in messages:
        if _utf8_len(m) <= max_bytes:
            final.append(m)
        else:
            # fallback hard slice by bytes
            raw = m.encode("utf-8")[:max_bytes]
            final.append(raw.decode("utf-8", errors="ignore"))
    return final


@dataclass
class WechatItem:
    title: str
    url: str
    summary: str
    score: Optional[float]
    tag: str


def _load_latest_wechat_stage2(outputs_root: Path, limit: int = 10) -> Tuple[Path, str, List[WechatItem]]:
    # Prefer new layout: outputs/wechat/**/_stage2.json
    stage2_path = _find_latest_path(outputs_root / "wechat", "*_stage2.json") if (outputs_root / "wechat").exists() else None
    if not stage2_path:
        raise FileNotFoundError("No WeChat *_stage2.json found under outputs/wechat/")

    data = _read_json(stage2_path)
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"WeChat stage2 must be a non-empty JSON array: {stage2_path}")

    overview = ""
    if isinstance(data[0], dict):
        overview = str(data[0].get("summary") or "").strip()

    items: List[WechatItem] = []
    for it in data[1:]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        summary = str(it.get("summary") or "").strip()
        tag = str(it.get("tag") or "").strip()
        score_val = it.get("score")
        try:
            score = float(score_val) if score_val is not None and str(score_val).strip() != "" else None
        except Exception:
            score = None
        if title and url:
            items.append(WechatItem(title=title, url=url, summary=summary, score=score, tag=tag))
        if len(items) >= limit:
            break

    return stage2_path, overview, items


@dataclass
class XhsItem:
    title: str
    url: str
    summary: str
    score: Optional[float]
    tag: str
    stage2_analysis: str
    cover_url: str


def _load_latest_xhs_stage2(outputs_root: Path, limit: int = 10) -> Tuple[Path, str, List[XhsItem]]:
    red_root = outputs_root / "rednotes"
    stage2_path = _find_latest_path(red_root, "analysis/stage2.json")
    if not stage2_path:
        raise FileNotFoundError("No Rednotes stage2.json found under outputs/rednotes/**/analysis/")

    run_dir = stage2_path.parent.parent
    stage1_path = run_dir / "analysis" / "stage1.json"
    notes_path = run_dir / "notes.json"

    stage2 = _read_json(stage2_path)
    if not isinstance(stage2, dict):
        raise RuntimeError(f"Rednotes stage2 must be a JSON object: {stage2_path}")
    overview = str(stage2.get("overview") or "").strip()

    stage1 = _read_json(stage1_path)
    if not isinstance(stage1, list):
        raise RuntimeError(f"Rednotes stage1 must be a JSON array: {stage1_path}")

    stage1_by_title: Dict[str, Dict[str, Any]] = {}
    for it in stage1:
        if not isinstance(it, dict):
            continue
        t = str(it.get("title") or "").strip()
        if t:
            stage1_by_title[t] = it

    raw_notes_by_id: Dict[str, Dict[str, Any]] = {}
    if notes_path.exists():
        notes = _read_json(notes_path) or {}
        raw_notes = notes.get("raw_notes") or []
        if isinstance(raw_notes, list):
            for n in raw_notes:
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("note_id") or "").strip()
                if nid:
                    raw_notes_by_id[nid] = n

    def build_item(it: Dict[str, Any]) -> Optional[XhsItem]:
        title = str(it.get("title") or "").strip()
        if not title:
            return None
        score_val = it.get("score")
        try:
            score = float(score_val) if score_val is not None and str(score_val).strip() != "" else None
        except Exception:
            score = None
        tag = str(it.get("tag") or "").strip()
        summary = str(it.get("summary") or it.get("original_content") or "").strip()
        stage2_analysis = str(it.get("stage2_analysis") or "").strip()

        s1 = stage1_by_title.get(title) or {}
        url = str(s1.get("url") or "").strip()
        note_id = str(s1.get("note_id") or "").strip()

        cover_url = ""
        raw_note = raw_notes_by_id.get(note_id) or {}
        cover_url = str(raw_note.get("cover_url") or "").strip()
        if not cover_url:
            cover_url = str(raw_note.get("cover") or "").strip()
        if not cover_url:
            cover_url = str(s1.get("cover_path") or "").strip()  # local path fallback

        return XhsItem(
            title=title,
            url=url,
            summary=summary,
            score=score,
            tag=tag,
            stage2_analysis=stage2_analysis,
            cover_url=cover_url,
        )

    items: List[XhsItem] = []
    merged: List[Dict[str, Any]] = []
    for k in ("top_items", "other_items"):
        block = stage2.get(k) or []
        if isinstance(block, list):
            merged.extend([x for x in block if isinstance(x, dict)])

    for it in merged:
        built = build_item(it)
        if built:
            items.append(built)
        if len(items) >= limit:
            break

    return stage2_path, overview, items


def render_wechat_markdown(stage2_path: Path, overview: str, items: List[WechatItem]) -> List[str]:
    _ = stage2_path  # keep signature stable; not shown in message
    header = ["📰 微信公众号"]
    overview_line = f"> 🧭 总览：{overview.strip()}" if overview else ""

    blocks: List[List[str]] = []
    for idx, it in enumerate(items, start=1):
        score_txt = f"{it.score:.1f}" if isinstance(it.score, (int, float)) else "-"
        b: List[str] = [f"**{idx}. {it.title}**"]
        b.append(f"- 🔗 原文：[点击查看]({it.url})")
        b.append(f"- ⭐ 评分：{score_txt}")
        if it.tag:
            b.append(f"- 🏷️ 标签：{it.tag}")
        if it.summary:
            b.append(f"- 📝 摘要：{_shorten(it.summary, 320)}")
        b.append("---")
        blocks.append(b)

    return _chunk_markdown(header, overview_line, blocks, max_bytes=MAX_MARKDOWN_BYTES, cont_header="⬇️ 接上条")


def render_xhs_markdown(stage2_path: Path, overview: str, items: List[XhsItem]) -> List[str]:
    _ = stage2_path  # keep signature stable; not shown in message
    header = ["📌 小红书"]
    overview_line = f"> 🧭 总览：{overview.strip()}" if overview else ""

    blocks: List[List[str]] = []
    for idx, it in enumerate(items, start=1):
        score_txt = f"{it.score:.1f}" if isinstance(it.score, (int, float)) else "-"
        b: List[str] = [f"**{idx}. {it.title}**"]
        if it.url:
            b.append(f"- 🔗 原文：[点击查看]({it.url})")
        b.append(f"- ⭐ 评分：{score_txt}")
        cover = (it.cover_url or "").strip()
        if cover.lower().startswith("http"):
            b.append(f"- 🖼️ 封面：[点击查看]({cover})")
        if it.tag:
            b.append(f"- 🏷️ 标签：{it.tag}")
        if it.stage2_analysis:
            b.append(f"- 🧠 观点：{_shorten(it.stage2_analysis, 220)}")
        elif it.summary:
            b.append(f"- 📝 摘要：{_shorten(it.summary, 280)}")
        b.append("---")
        blocks.append(b)

    return _chunk_markdown(header, overview_line, blocks, max_bytes=MAX_MARKDOWN_BYTES, cont_header="⬇️ 接上条")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send latest WeChat/XHS summary to WeCom group bot via webhook.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_wc = sub.add_parser("wechat", help="Send latest WeChat stage2 as markdown (text only)")
    p_wc.add_argument("--webhook", default=DEFAULT_WEBHOOK)
    p_wc.add_argument("--outputs", default="outputs")
    p_wc.add_argument("--limit", type=int, default=10)

    p_xhs = sub.add_parser("xhs", help="Send latest Rednotes stage2 as markdown (text + cover link)")
    p_xhs.add_argument("--webhook", default=DEFAULT_WEBHOOK)
    p_xhs.add_argument("--outputs", default="outputs")
    p_xhs.add_argument("--limit", type=int, default=10)

    p_all = sub.add_parser("all", help="Send both messages (wechat then xhs)")
    p_all.add_argument("--webhook", default=DEFAULT_WEBHOOK)
    p_all.add_argument("--outputs", default="outputs")
    p_all.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()
    outputs_root = Path(args.outputs)

    try:
        if args.cmd in ("wechat", "all"):
            p, overview, items = _load_latest_wechat_stage2(outputs_root, limit=max(1, int(args.limit)))
            messages = render_wechat_markdown(p, overview, items)
            for i, md in enumerate(messages, start=1):
                resp = send_markdown(args.webhook, md)
                print(f"[wecom] wechat resp ({i}/{len(messages)}):", resp)
                if isinstance(resp, dict) and int(resp.get("errcode") or 0) != 0:
                    return 1

        if args.cmd in ("xhs", "all"):
            p, overview, items = _load_latest_xhs_stage2(outputs_root, limit=max(1, int(args.limit)))
            messages = render_xhs_markdown(p, overview, items)
            for i, md in enumerate(messages, start=1):
                resp = send_markdown(args.webhook, md)
                print(f"[wecom] xhs resp ({i}/{len(messages)}):", resp)
                if isinstance(resp, dict) and int(resp.get("errcode") or 0) != 0:
                    return 1

        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[wecom] failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
