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


def _request_headers() -> List[Dict[str, str]]:
    # Some CDN image links require browser-like headers and referer to pass anti-hotlink checks.
    common = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    return [
        common,
        {
            **common,
            "Referer": "https://www.xiaohongshu.com/",
            "Origin": "https://www.xiaohongshu.com",
        },
        {
            **common,
            "Accept": "*/*",
            "Referer": "https://www.xiaohongshu.com/explore/",
            "Origin": "https://www.xiaohongshu.com",
        },
    ]


def _candidate_image_urls(url: str) -> List[str]:
    u = str(url or "").strip()
    if not u:
        return []
    out: List[str] = []

    def add(v: str) -> None:
        vv = str(v or "").strip()
        if not vv:
            return
        if vv in out:
            return
        out.append(vv)

    add(u)
    if u.startswith("http://"):
        add("https://" + u[len("http://") :])
    if "?" in u:
        add(u.split("?", 1)[0])
        if u.startswith("http://"):
            add("https://" + u[len("http://") :].split("?", 1)[0])
    return out


def download_image(url: str, dest_path: Path, timeout_sec: int = 30) -> Tuple[bool, str]:
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"missing_requests:{exc}"

    last_error = ""
    try:
        with requests.Session() as session:
            for try_url in _candidate_image_urls(url):
                for headers in _request_headers():
                    try:
                        resp = session.get(try_url, headers=headers, timeout=timeout_sec, allow_redirects=True)
                        # Retry with another url/header set on typical anti-hotlink and transient statuses.
                        if resp.status_code in (403, 405, 429, 500, 502, 503, 504):
                            last_error = f"http_{resp.status_code}:{try_url}"
                            continue
                        resp.raise_for_status()
                        ext = _infer_ext(try_url, resp.headers.get("content-type", ""))
                        final_path = dest_path.with_suffix(ext)
                        final_path.write_bytes(resp.content)
                        return True, str(final_path)
                    except Exception as exc:  # noqa: BLE001
                        last_error = str(exc)
                        continue

        # Fallback to urllib in case requests is blocked but basic GET still works.
        try:
            from urllib.request import Request, urlopen

            for try_url in _candidate_image_urls(url):
                try:
                    req = Request(try_url, headers=_request_headers()[-1], method="GET")
                    with urlopen(req, timeout=timeout_sec) as resp:
                        data = resp.read()
                        ctype = resp.headers.get("content-type", "")
                    ext = _infer_ext(try_url, ctype)
                    final_path = dest_path.with_suffix(ext)
                    final_path.write_bytes(data)
                    return True, str(final_path)
                except Exception as exc2:  # noqa: BLE001
                    last_error = str(exc2)
                    continue
        except Exception as exc2:  # noqa: BLE001
            return False, f"download_error:{last_error or exc2}; fallback_error:{exc2}"
        return False, f"download_error:{last_error or 'unknown'}"
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
