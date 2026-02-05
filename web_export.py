from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_date_from_run_name(run_name: str) -> str:
    m = re.match(r"^(\d{8})", (run_name or "").strip())
    if not m:
        return ""
    s = m.group(1)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _fmt_time_from_mtime(path: Path) -> str:
    try:
        dt = datetime.fromtimestamp(path.stat().st_mtime)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _parse_tags(tag_str: str) -> List[str]:
    s = (tag_str or "").strip()
    if not s:
        return []
    parts = re.split(r"[、,，;/；\s]+", s)
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        t = p.strip()
        if not t:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _truncate(text: str, max_len: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1].rstrip() + "…"


def find_latest_wechat_stage2(outputs_root: Path = Path("outputs") / "wechat") -> Optional[Path]:
    if not outputs_root.exists():
        return None
    candidates = list(outputs_root.rglob("*_stage2.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_latest_xhs_stage2(outputs_root: Path = Path("outputs") / "rednotes") -> Optional[Path]:
    if not outputs_root.exists():
        return None
    candidates = list(outputs_root.rglob("analysis/stage2.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def to_report_document_wechat(stage2_path: Path) -> Dict[str, Any]:
    data = _read_json(stage2_path)
    if not isinstance(data, list):
        raise ValueError("wechat stage2 must be a JSON array")

    run_dir = stage2_path.parent
    date = _fmt_date_from_run_name(run_dir.name)
    time = _fmt_time_from_mtime(stage2_path)

    overall_summary = ""
    items = data
    if items and isinstance(items[0], dict) and "summary" in items[0] and "url" not in items[0]:
        overall_summary = str(items[0].get("summary") or "").strip()
        items = items[1:]

    lines: List[str] = []
    if overall_summary:
        lines.append("## 总览")
        lines.append(overall_summary)
        lines.append("")
    lines.append("## 条目")
    for idx, it in enumerate([x for x in items if isinstance(x, dict)], start=1):
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        summary = str(it.get("summary") or "").strip()
        score = it.get("score", None)
        tag_str = str(it.get("tag") or "").strip()

        lines.append(f"### {idx}. {title}".rstrip())
        if score not in (None, ""):
            lines.append(f"**评分**：{score}")
        if tag_str:
            lines.append(f"**标签**：{tag_str}")
        if summary:
            lines.append(f"**摘要**：{summary}")
        if url:
            lines.append(f"**链接**：[[原文]]({url})")
        lines.append("")

    content = "\n".join([ln for ln in lines if ln is not None]).strip() + "\n"

    return {
        "title": f"微信公众号AI日报（{date}）" if date else "微信公众号AI日报",
        "content": content,
        "tags": ["wechat", "公众号", "AI日报"],
        "date": date,
        "time": time,
        "source": "wechat",
        "summary": _truncate(overall_summary or (items[0].get("summary") if items and isinstance(items[0], dict) else "") or "", 140),
        "meta": {"stage2_path": str(stage2_path)},
    }


def to_report_document_xhs(stage2_path: Path) -> Dict[str, Any]:
    data = _read_json(stage2_path)
    if not isinstance(data, dict):
        raise ValueError("xhs stage2 must be a JSON object")

    run_dir = stage2_path.parent.parent  # .../<run>/analysis/stage2.json
    date = _fmt_date_from_run_name(run_dir.name)
    time = _fmt_time_from_mtime(stage2_path)

    overview = str(data.get("overview") or "").strip()
    top_items = data.get("top_items") or []
    other_items = data.get("other_items") or []

    lines: List[str] = []
    if overview:
        lines.append("## 总览")
        lines.append(overview)
        lines.append("")

    if isinstance(top_items, list) and top_items:
        lines.append("## Top 3")
        for idx, it in enumerate([x for x in top_items if isinstance(x, dict)], start=1):
            title = str(it.get("title") or "").strip()
            score = it.get("score", None)
            tag_str = str(it.get("tag") or "").strip()
            original = str(it.get("original_content") or "").strip()
            analysis = str(it.get("stage2_analysis") or "").strip()
            lines.append(f"### {idx}. {title}".rstrip())
            if score not in (None, ""):
                lines.append(f"**评分**：{score}")
            if tag_str:
                lines.append(f"**标签**：{tag_str}")
            if original:
                lines.append("")
                lines.append("**原文（节选/整理）**：")
                lines.append(original)
            if analysis:
                lines.append("")
                lines.append("**分析**：")
                lines.append(analysis)
            lines.append("")

    if isinstance(other_items, list) and other_items:
        lines.append("## 其他高分")
        for idx, it in enumerate([x for x in other_items if isinstance(x, dict)], start=1):
            title = str(it.get("title") or "").strip()
            score = it.get("score", None)
            summary = str(it.get("summary") or "").strip()
            analysis = str(it.get("stage2_analysis") or "").strip()
            lines.append(f"### {idx}. {title}".rstrip())
            if score not in (None, ""):
                lines.append(f"**评分**：{score}")
            if summary:
                lines.append(f"**摘要**：{summary}")
            if analysis:
                lines.append(f"**分析**：{analysis}")
            lines.append("")

    content = "\n".join([ln for ln in lines if ln is not None]).strip() + "\n"

    return {
        "title": f"小红书AI日报（{date}）" if date else "小红书AI日报",
        "content": content,
        "tags": ["xhs", "小红书", "AI日报"],
        "date": date,
        "time": time,
        "source": "xhs",
        "summary": _truncate(overview, 140),
        "meta": {"stage2_path": str(stage2_path)},
    }


def export_report_documents(
    output_path: Path,
    wechat_stage2: Optional[Path] = None,
    xhs_stage2: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []

    if wechat_stage2 is None:
        wechat_stage2 = find_latest_wechat_stage2()
    if xhs_stage2 is None:
        xhs_stage2 = find_latest_xhs_stage2()

    if wechat_stage2 is not None and wechat_stage2.exists():
        docs.append(to_report_document_wechat(wechat_stage2))
    if xhs_stage2 is not None and xhs_stage2.exists():
        docs.append(to_report_document_xhs(xhs_stage2))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(docs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return docs


def main() -> int:
    parser = argparse.ArgumentParser(description="Export WeChat + XHS stage2 into ReportDocument JSON for web")
    parser.add_argument("--wechat-stage2", default="", help="Path to wechat *_stage2.json (default: latest)")
    parser.add_argument("--xhs-stage2", default="", help="Path to outputs/rednotes/<run>/analysis/stage2.json (default: latest)")
    parser.add_argument("--output", default=str(Path("web_output") / "report_documents.json"), help="Output json path")
    args = parser.parse_args()

    wechat = Path(args.wechat_stage2) if args.wechat_stage2 else None
    xhs = Path(args.xhs_stage2) if args.xhs_stage2 else None
    out = Path(args.output)
    export_report_documents(out, wechat_stage2=wechat, xhs_stage2=xhs)
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

