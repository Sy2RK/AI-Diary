from __future__ import annotations

import argparse
import base64
import mimetypes
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.integrations.web_export import (
    find_latest_wechat_stage2,
    find_latest_wechat_stage2_for_date,
    find_latest_xhs_stage2,
    find_latest_xhs_stage2_for_date,
    _normalize_date_input,
)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json(path: Path) -> Any:
    return Path(path).read_text(encoding="utf-8")


def _load_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _parse_wechat_source_id(url: str) -> str:
    import re

    u = (url or "").strip()
    if not u:
        return ""
    biz = re.search(r"(?:\\?|&)__biz=([^&]+)", u)
    mid = re.search(r"(?:\\?|&)mid=(\\d+)", u)
    idx = re.search(r"(?:\\?|&)idx=(\\d+)", u)
    if biz and mid and idx:
        return f"{biz.group(1)}_{mid.group(1)}_{idx.group(1)}"
    # fallback hash
    import hashlib

    return hashlib.sha1(u.encode("utf-8")).hexdigest()


@dataclass
class ImageItem:
    source: str
    title: str
    url: str
    image_path: str
    image_base64: str
    unique_key: str
    run_dir: str


def _file_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _pick_file_by_stem(folder: Path, stem: str) -> Optional[Path]:
    if not folder.exists():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".bmp"):
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _load_wechat_items(stage2_path: Path) -> List[Dict[str, Any]]:
    data = _load_json(stage2_path)
    if not isinstance(data, list):
        raise RuntimeError("wechat stage2 must be a JSON array")
    # skip first summary item if it has no url
    if data and isinstance(data[0], dict) and ("url" not in data[0] and "link" not in data[0]):
        return [it for it in data[1:] if isinstance(it, dict)]
    return [it for it in data if isinstance(it, dict)]


def _load_xhs_items(stage2_path: Path) -> List[Dict[str, Any]]:
    data = _load_json(stage2_path)
    if not isinstance(data, dict):
        raise RuntimeError("xhs stage2 must be a JSON object")
    items: List[Dict[str, Any]] = []
    top_items = data.get("top_items") or []
    other_items = data.get("other_items") or []
    if isinstance(top_items, list):
        items.extend([it for it in top_items if isinstance(it, dict)])
    if isinstance(other_items, list):
        items.extend([it for it in other_items if isinstance(it, dict)])
    return items


def _load_stage1_index(run_root: Path) -> Dict[str, Dict[str, Any]]:
    stage1_path = run_root / "analysis" / "stage1.json"
    if not stage1_path.exists():
        return {}
    data = _load_json(stage1_path)
    if not isinstance(data, list):
        return {}
    index: Dict[str, Dict[str, Any]] = {}
    # Keep first match by title; ties are ignored.
    for it in data:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title or title in index:
            continue
        index[title] = it
    return index


def export_images_json(
    *,
    wechat_stage2: Optional[Path],
    xhs_stage2: Optional[Path],
    output_path: Path,
) -> Path:
    items: List[ImageItem] = []

    if wechat_stage2 is not None and wechat_stage2.exists():
        wechat_items = _load_wechat_items(wechat_stage2)
        run_root = wechat_stage2.parent
        covers_dir = run_root / "covers"
        for it in wechat_items:
            title = str(it.get("title") or "").strip()
            url = str(it.get("url") or it.get("link") or "").strip()
            uid = _parse_wechat_source_id(url)
            cover = _pick_file_by_stem(covers_dir, uid)
            if not cover:
                continue
            items.append(
                ImageItem(
                    source="wechat",
                    title=title,
                    url=url,
                    image_path=str(cover),
                    image_base64=_file_to_data_url(cover),
                    unique_key=uid,
                    run_dir=str(run_root),
                )
            )

    if xhs_stage2 is not None and xhs_stage2.exists():
        xhs_items = _load_xhs_items(xhs_stage2)
        run_root = xhs_stage2.parent.parent
        stage1_index = _load_stage1_index(run_root)
        covers_dir = run_root / "covers"
        for it in xhs_items:
            title = str(it.get("title") or "").strip()
            url = str(it.get("url") or it.get("source_url") or "").strip()
            stage1 = stage1_index.get(title, {})
            note_id = str(stage1.get("note_id") or "").strip()
            cover = _pick_file_by_stem(covers_dir, note_id) if note_id else None
            if not cover:
                cover_path = str(stage1.get("cover_path") or "").strip()
                if cover_path:
                    p = Path(cover_path)
                    if not p.is_absolute():
                        p = _repo_root() / p
                    if p.exists():
                        cover = p
            if not cover:
                continue
            unique_key = note_id or _sha1(url)
            items.append(
                ImageItem(
                    source="xhs",
                    title=title,
                    url=url,
                    image_path=str(cover),
                    image_base64=_file_to_data_url(cover),
                    unique_key=unique_key,
                    run_dir=str(run_root),
                )
            )

    payload = {
        "generated_at": _now_iso(),
        "images": [
            {
                "source": it.source,
                "title": it.title,
                "url": it.url,
                "image_path": it.image_path,
                "image_base64": it.image_base64,
                "unique_key": it.unique_key,
                "run_dir": it.run_dir,
            }
            for it in items
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export image base64 to a standalone JSON file.")
    parser.add_argument("--wechat-stage2", default="", help="Path to wechat *_stage2.json (default: latest)")
    parser.add_argument("--xhs-stage2", default="", help="Path to outputs/rednotes/<run>/analysis/stage2.json (default: latest)")
    parser.add_argument("--output", default=str(Path("web_output") / "report_images.json"), help="Output json path")
    parser.add_argument("--date", default="", help="Run date like 20260209 or 2026-02-09")
    args = parser.parse_args()

    wechat = Path(args.wechat_stage2) if args.wechat_stage2 else None
    xhs = Path(args.xhs_stage2) if args.xhs_stage2 else None

    date_key = _normalize_date_input(args.date) if args.date else None
    if (wechat is None or xhs is None) and date_key:
        if wechat is None:
            wechat = find_latest_wechat_stage2_for_date(date_key)
        if xhs is None:
            xhs = find_latest_xhs_stage2_for_date(date_key)
    if wechat is None and not args.wechat_stage2:
        wechat = find_latest_wechat_stage2()
    if xhs is None and not args.xhs_stage2:
        xhs = find_latest_xhs_stage2()

    out = Path(args.output)
    if date_key and not out.stem.endswith(date_key):
        out = out.with_name(f"{out.stem}_{date_key}{out.suffix}")

    export_images_json(wechat_stage2=wechat, xhs_stage2=xhs, output_path=out)
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
