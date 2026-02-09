from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.integrations.web_export import _normalize_date_input


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _index_images(images_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    images = images_payload.get("images") if isinstance(images_payload, dict) else []
    if not isinstance(images, list):
        return idx
    for it in images:
        if not isinstance(it, dict):
            continue
        source = str(it.get("source") or "").strip()
        unique_key = str(it.get("unique_key") or "").strip()
        if not source or not unique_key:
            continue
        idx[f"{source}:{unique_key}"] = it
    return idx


def export_news_with_images_json(
    documents_path: Path,
    images_path: Path,
    output_path: Path,
) -> Path:
    documents = _load_json(documents_path)
    images = _load_json(images_path)

    if not isinstance(documents, list):
        raise RuntimeError("report_documents.json must be a JSON array")

    image_index = _index_images(images)

    merged_items: List[Dict[str, Any]] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        source = str(doc.get("source") or "").strip()
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        unique_key = str(meta.get("unique_key") or "").strip()
        image_obj = image_index.get(f"{source}:{unique_key}") if source and unique_key else None

        score_val = meta.get("score")
        try:
            score = float(score_val) if score_val not in (None, "") else None
        except Exception:
            score = None

        cover_image = str((image_obj or {}).get("image_path") or "").strip()
        image_base64 = str((image_obj or {}).get("image_base64") or "").strip()
        if image_base64.startswith("data:") and ";base64," in image_base64:
            image_base64 = image_base64.split(";base64,", 1)[1]

        merged_items.append(
            {
                "title": str(doc.get("title") or "").strip(),
                "content": str(doc.get("content") or ""),
                "tags": doc.get("tags") if isinstance(doc.get("tags"), list) else [],
                "summary": str(doc.get("summary") or "").strip(),
                "score": score,
                "coverImage": cover_image,
                "meta": {
                    "source": source,
                    "date": str(doc.get("date") or "").strip(),
                    "time": str(doc.get("time") or "").strip(),
                    "url": str(meta.get("url") or "").strip(),
                    "rank": meta.get("rank"),
                    "unique_key": unique_key,
                    "image_path": cover_image,
                    "image_base64": image_base64,
                },
            }
        )

    payload = {"generated_at": _now_iso(), "feishu": {"documents": merged_items}}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Match report documents and image base64 into one JSON file.")
    parser.add_argument("--documents", default=str(Path("web_output") / "report_documents.json"), help="Path to report_documents.json")
    parser.add_argument("--images", default=str(Path("web_output") / "report_images.json"), help="Path to report_images.json")
    parser.add_argument("--output", default=str(Path("web_output") / "report_documents_with_images.json"), help="Output json path")
    parser.add_argument("--date", default="", help="Run date like 20260209 or 2026-02-09 (auto suffix output file)")
    args = parser.parse_args()

    docs = Path(args.documents)
    imgs = Path(args.images)
    out = Path(args.output)

    date_key = _normalize_date_input(args.date) if args.date else None
    if date_key and not out.stem.endswith(date_key):
        out = out.with_name(f"{out.stem}_{date_key}{out.suffix}")

    export_news_with_images_json(docs, imgs, out)
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
