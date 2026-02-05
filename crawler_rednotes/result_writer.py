import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_FILENAME_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._ -]+")


def sanitize_filename(value: str, max_len: int = 80) -> str:
    name = (value or "").strip()
    name = _FILENAME_SAFE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "untitled"
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name


def next_available_dir(path: Path) -> Path:
    if not path.exists():
        return path
    parent = path.parent
    name = path.name
    for i in range(1, 10_000):
        candidate = parent / f"{name}-{i}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many existing folders, cannot find available name for {path}")


def create_run_dir(outputs_root: Path, date_str: Optional[str] = None) -> Path:
    date_str = date_str or datetime.now().strftime("%Y%m%d")
    base = outputs_root / date_str
    run_dir = next_available_dir(base)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "text").mkdir(parents=True, exist_ok=False)
    (run_dir / "images").mkdir(parents=True, exist_ok=False)
    return run_dir


def _infer_ext(url: str, content_type: str) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct == "image/png":
        return ".png"
    if ct == "image/webp":
        return ".webp"
    if ct == "image/gif":
        return ".gif"
    if ct == "image/heic":
        return ".heic"
    # fallback from url suffix
    lower = (url or "").lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"):
        if ext in lower:
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def download_image(url: str, dest_path: Path, timeout_sec: int = 30) -> Tuple[bool, str]:
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"missing_requests:{exc}"

    try:
        resp = requests.get(url, timeout=timeout_sec)
        resp.raise_for_status()
        ext = _infer_ext(url, resp.headers.get("content-type", ""))
        final_path = dest_path.with_suffix(ext)
        final_path.write_bytes(resp.content)
        return True, str(final_path)
    except Exception as exc:  # noqa: BLE001
        return False, f"download_error:{exc}"


@dataclass
class SavedNote:
    note_id: str
    title: str
    source_url: str
    content_text: str
    text_path: str
    cover_path: str
    image_paths: List[str]
    error: str


def save_notes(
    run_dir: Path,
    notes: Iterable[Dict[str, Any]],
    download_images_enabled: bool = True,
) -> List[SavedNote]:
    text_dir = run_dir / "text"
    images_dir = run_dir / "images"
    saved: List[SavedNote] = []

    notes_list = list(notes)
    for idx, note in enumerate(notes_list, start=1):
        note_id = str(note.get("note_id") or "").strip() or f"note_{idx}"
        title = str(note.get("title") or "").strip()
        source_url = str(note.get("source_url") or "").strip()
        content_text = str(note.get("content_text") or "").strip()
        account_name = str(note.get("account_name") or "").strip()

        prefix = f"{idx:03d}_{sanitize_filename(note_id, max_len=40)}"
        if account_name:
            prefix = f"{prefix}_{sanitize_filename(account_name, max_len=30)}"
        if title:
            prefix = f"{prefix}_{sanitize_filename(title, max_len=40)}"

        text_path = text_dir / f"{prefix}.txt"
        text_payload = [
            f"账号: {account_name}" if account_name else "",
            f"标题: {title}",
            f"链接: {source_url}",
            "",
            content_text,
            "",
            f"tags: {', '.join(note.get('tags') or [])}",
            f"error: {note.get('error') or ''}".rstrip(),
        ]
        text_path.write_text("\n".join([ln for ln in text_payload if ln != ""]).strip() + "\n", encoding="utf-8")

        per_note_img_dir = images_dir / prefix
        per_note_img_dir.mkdir(parents=True, exist_ok=True)

        cover_url = note.get("cover_url")
        cover_path = ""
        img_paths: List[str] = []
        download_errs: List[str] = []

        if download_images_enabled:
            if cover_url:
                ok, result = download_image(str(cover_url), per_note_img_dir / "cover")
                if ok:
                    cover_path = result
                else:
                    download_errs.append(f"cover:{result}")

            for img_idx, img_url in enumerate(note.get("image_urls") or [], start=1):
                ok, result = download_image(str(img_url), per_note_img_dir / f"{img_idx:02d}")
                if ok:
                    img_paths.append(result)
                else:
                    download_errs.append(f"img{img_idx}:{result}")

        saved.append(
            SavedNote(
                note_id=note_id,
                title=title,
                source_url=source_url,
                content_text=content_text,
                text_path=str(text_path),
                cover_path=cover_path,
                image_paths=img_paths,
                error="; ".join([str(note.get("error") or "").strip(), *download_errs]).strip("; ").strip(),
            )
        )

    # Save a machine-readable manifest for later processing
    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(saved),
        "items": [
            {
                "note_id": s.note_id,
                "title": s.title,
                "source_url": s.source_url,
                "text_path": s.text_path,
                "cover_path": s.cover_path,
                "image_paths": s.image_paths,
                "error": s.error,
            }
            for s in saved
        ],
        "raw_notes": notes_list,
    }
    (run_dir / "notes.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return saved
