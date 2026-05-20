from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.xhs.tikhub_client import fetch_note_info, fetch_user_notes, fetch_user_notes_alt_userid, fetch_video_note_info
from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli

# --- Quick-test defaults (TEST ONLY) ---
# Provide the API key via `--api-key` or env `TIKHUB_API_KEY`.
HARDCODED_TIKHUB_API_KEY = ""
HARDCODED_NOTE_ID = "69450d1a000000000d034b6f"


def _now_date() -> str:
    return datetime.now().strftime("%Y%m%d")


def _next_available_dir(path: Path) -> Path:
    if not path.exists():
        return path
    parent = path.parent
    name = path.name
    for i in range(1, 10_000):
        cand = parent / f"{name}-{i}"
        if not cand.exists():
            return cand
    raise RuntimeError(f"Too many existing folders: {path}")


@dataclass
class RunDirs:
    run_dir: Path
    text_dir: Path
    images_dir: Path
    video_dir: Path
    raw_dir: Path


def ensure_run_dirs(outputs_root: Path, date_str: str = "") -> RunDirs:
    date_str = date_str or _now_date()
    base = outputs_root / date_str
    run_dir = _next_available_dir(base)
    run_dir.mkdir(parents=True, exist_ok=False)
    text_dir = run_dir / "text"
    images_dir = run_dir / "images"
    video_dir = run_dir / "video"
    raw_dir = run_dir / "raw"
    text_dir.mkdir(parents=True, exist_ok=False)
    images_dir.mkdir(parents=True, exist_ok=False)
    video_dir.mkdir(parents=True, exist_ok=False)
    raw_dir.mkdir(parents=True, exist_ok=False)
    return RunDirs(run_dir=run_dir, text_dir=text_dir, images_dir=images_dir, video_dir=video_dir, raw_dir=raw_dir)


_URL_RE = re.compile(r"https?://[^\s\"')]+", re.I)


def _walk_values(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_values(v)
    else:
        yield obj


def _collect_urls(obj: Any) -> List[str]:
    urls: List[str] = []
    for v in _walk_values(obj):
        if isinstance(v, str):
            for m in _URL_RE.findall(v):
                urls.append(m)
    # de-dup preserving order
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _classify_media_urls(urls: List[str]) -> Tuple[List[str], List[str]]:
    images: List[str] = []
    videos: List[str] = []
    for u in urls:
        lower = u.lower()
        if any(ext in lower for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"]):
            images.append(u)
            continue
        if any(ext in lower for ext in [".mp4", ".mov", ".m3u8", ".webm"]):
            videos.append(u)
            continue
        # Avoid treating share/landing pages as video sources.
    return images, videos


def _download(url: str, dest: Path, timeout_sec: int = 30) -> Path:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            data = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"HTTPError {exc.code}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"URLError: {exc}") from exc

    def _guess_suffix_from_bytes(payload: bytes, source_url: str) -> str:
        head = payload[:64]
        if head.startswith(b"\xFF\xD8\xFF"):
            return ".jpg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if head.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return ".webp"
        if len(head) >= 12 and head[4:8] == b"ftyp":
            brand = head[8:16].lower()
            if b"heic" in brand or b"heif" in brand or b"mif1" in brand:
                return ".heic"
            if b"avif" in brand:
                return ".avif"
            if b"mp4" in brand or b"isom" in brand or b"iso2" in brand:
                return ".mp4"
        text = payload[:256].decode("utf-8", errors="ignore").lstrip()
        if text.startswith("#EXTM3U"):
            return ".m3u8"
        lower_url = (source_url or "").lower()
        if "format/heif" in lower_url:
            return ".heic"
        if "format/jpg" in lower_url or "format/jpeg" in lower_url:
            return ".jpg"
        if "format/png" in lower_url:
            return ".png"
        return ""

    suffix = dest.suffix
    if not suffix:
        if ctype in ("image/jpeg", "image/jpg"):
            suffix = ".jpg"
        elif ctype == "image/png":
            suffix = ".png"
        elif ctype == "image/webp":
            suffix = ".webp"
        elif ctype == "image/gif":
            suffix = ".gif"
        elif ctype == "video/mp4":
            suffix = ".mp4"
        elif ctype in ("application/x-mpegurl", "application/vnd.apple.mpegurl"):
            suffix = ".m3u8"
        else:
            # fallback from url
            for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".mp4", ".mov", ".m3u8", ".webm", ".m4a"]:
                if ext in url.lower():
                    suffix = ".jpg" if ext == ".jpeg" else ext
                    break
            if not suffix:
                suffix = _guess_suffix_from_bytes(data, url)
    final = dest.with_suffix(suffix or ".bin")
    final.write_bytes(data)
    return final


def _sanitize_filename(value: str, max_len: int = 80) -> str:
    from crawler_rednotes.result_writer import sanitize_filename

    return sanitize_filename(value, max_len=max_len)

def _extract_user_id_from_profile_url(url: str) -> str:
    """
    Extract XHS user_id from profile url.
    Example:
      https://www.xiaohongshu.com/user/profile/5da29e450000000001007fae
    """
    u = (url or "").strip()
    m = re.search(r"/user/profile/([0-9a-zA-Z]+)", u)
    return m.group(1) if m else ""

def _extract_user_notes_list(resp: Any) -> Tuple[List[Dict[str, Any]], str, bool]:
    """
    Return (notes, cursor, has_more) from get_user_notes response (best-effort).
    """
    if not isinstance(resp, dict):
        return [], "", False
    data = resp.get("data")
    if isinstance(data, dict):
        # TikHub wrapper may nest payload at data.data.
        payload = data
        if isinstance(data.get("data"), dict):
            payload = data.get("data")  # type: ignore[assignment]

        cursor = str(payload.get("cursor") or payload.get("next_cursor") or payload.get("page_token") or "").strip()
        has_more = bool(payload.get("has_more") or payload.get("hasMore") or bool(cursor))

        # common: payload.notes
        notes = payload.get("notes")
        if isinstance(notes, list):
            notes_list = [n for n in notes if isinstance(n, dict)]
            if not cursor:
                # Some responses only provide per-note cursor.
                for it in reversed(notes_list):
                    c = str(it.get("cursor") or it.get("note_cursor") or "").strip()
                    if c:
                        cursor = c
                        break
                has_more = bool(payload.get("has_more") or payload.get("hasMore") or bool(cursor))
            return notes_list, cursor, has_more

        # variants: payload.items / payload.list / payload.data
        for key in ("items", "list", "data"):
            notes2 = payload.get(key)
            if isinstance(notes2, list):
                notes_list = [n for n in notes2 if isinstance(n, dict)]
                if not cursor:
                    for it in reversed(notes_list):
                        c = str(it.get("cursor") or it.get("note_cursor") or "").strip()
                        if c:
                            cursor = c
                            break
                    has_more = bool(payload.get("has_more") or payload.get("hasMore") or bool(cursor))
                return notes_list, cursor, has_more
    notes = resp.get("notes")
    cursor = str(resp.get("cursor") or "").strip()
    has_more = bool(resp.get("has_more"))
    if isinstance(notes, list):
        return [n for n in notes if isinstance(n, dict)], cursor, has_more
    # last resort: extract note ids from unknown shapes
    ids = _extract_note_ids_any(resp)
    if ids:
        return [{"note_id": nid} for nid in ids], "", False
    return [], "", False


_NOTE_ID_RE = re.compile(r"\b[0-9a-f]{24}\b", re.I)


def _extract_note_ids_any(obj: Any) -> List[str]:
    """
    Best-effort extraction of note IDs from unknown get_user_notes response shape.
    Prefer explicit dict keys; fall back to scanning URLs.
    """
    out: List[str] = []
    seen: set[str] = set()

    def add(nid: str) -> None:
        n = (nid or "").strip()
        if not n:
            return
        if not _NOTE_ID_RE.fullmatch(n):
            return
        if n in seen:
            return
        seen.add(n)
        out.append(n)

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "note_id" in x:
                add(str(x.get("note_id") or ""))
            if "noteId" in x:
                add(str(x.get("noteId") or ""))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            # Only accept IDs that appear in typical note URLs/params.
            for pat in (
                r"/explore/([0-9a-f]{24})",
                r"/discovery/item/([0-9a-f]{24})",
                r"note_id=([0-9a-f]{24})",
            ):
                for m in re.findall(pat, x, flags=re.I):
                    add(m)

    walk(obj)
    return out

def _iter_recent_note_metas(
    user_id: str,
    api_key: str,
    limit: int,
    dump_dir: Optional[Path] = None,
    retries: int = 2,
    timeout_sec: int = 20,
) -> List[Dict[str, Any]]:
    """
    Fetch recent note metas via get_user_notes pagination.
    """
    out: List[Dict[str, Any]] = []
    cursor = ""
    seen: set[str] = set()
    page = 0
    while True:
        last_exc: Optional[Exception] = None
        resp = None
        for _ in range(max(1, int(retries))):
            try:
                resp = fetch_user_notes(user_id=user_id, api_key=api_key, cursor=cursor, timeout_sec=timeout_sec)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                try:
                    resp = fetch_user_notes_alt_userid(
                        user_id=user_id,
                        api_key=api_key,
                        cursor=cursor,
                        timeout_sec=timeout_sec,
                    )
                    break
                except Exception as exc2:  # noqa: BLE001
                    # If TikHub doesn't accept `userid` param, keep original error.
                    msg = str(exc2)
                    if "HTTPError 422" in msg and "user_id" in msg and "Field required" in msg:
                        last_exc = exc
                    else:
                        last_exc = exc2
                    resp = None
        if resp is None:
            if last_exc:
                raise last_exc
            break

        if dump_dir is not None:
            try:
                dump_dir.mkdir(parents=True, exist_ok=True)
                (dump_dir / f"user_notes_{user_id}_{page:02d}.json").write_text(
                    json.dumps(resp, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        page += 1
        notes, new_cursor, has_more = _extract_user_notes_list(resp)
        if not notes:
            break
        for n in notes:
            nid = str(n.get("note_id") or n.get("noteId") or n.get("id") or "").strip()
            if not nid or nid in seen:
                continue
            seen.add(nid)
            out.append(n)
            if limit and len(out) >= limit:
                return out[:limit]
        cursor = str(new_cursor or "").strip()
        if not has_more or not cursor:
            break
    return out[:limit] if limit else out


def _build_prefix(idx: int, note_id: str, account_name: str, title: str) -> str:
    prefix = f"{idx:03d}_{_sanitize_filename(note_id, max_len=40)}"
    if account_name:
        prefix = f"{prefix}_{_sanitize_filename(account_name, max_len=30)}"
    if title:
        prefix = f"{prefix}_{_sanitize_filename(title, max_len=40)}"
    return prefix


def _extract_account_name(note_json: Any) -> str:
    note = _get_primary_note_item(note_json)
    user = note.get("user")
    if isinstance(user, dict):
        for key in ("name", "nickname"):
            v = str(user.get(key) or "").strip()
            if v:
                return v
    # wrapper has user at higher level
    data = note_json.get("data") if isinstance(note_json, dict) else {}
    if isinstance(data, dict):
        arr = data.get("data")
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict) and isinstance(first.get("user"), dict):
                u = first["user"]
                for key in ("name", "nickname"):
                    v = str(u.get(key) or "").strip()
                    if v:
                        return v
    return ""


def _extract_share_url(note_json: Any) -> str:
    note = _get_primary_note_item(note_json)
    nid = str(note.get("id") or "").strip()
    if nid:
        return f"https://www.xiaohongshu.com/explore/{nid}"

    si = note.get("share_info")
    if isinstance(si, dict):
        u = str(si.get("link") or "").strip()
        if u:
            # Strip volatile share params, keep a stable canonical link when possible.
            m = re.search(r"/(?:explore|discovery/item)/([0-9a-fA-F]+)", u)
            if m:
                return f"https://www.xiaohongshu.com/explore/{m.group(1)}"
            return u.split("?", 1)[0]
    return ""


def _extract_tags(note_json: Any) -> List[str]:
    note = _get_primary_note_item(note_json)
    tags: List[str] = []
    ht = note.get("hash_tag")
    if isinstance(ht, list):
        for it in ht:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if name:
                tags.append(name)
    # de-dup preserving order
    seen: set[str] = set()
    out: List[str] = []
    for t in tags:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _extract_cover_url(note_json: Any) -> str:
    note = _get_primary_note_item(note_json)
    # prefer share image (usually cover)
    si = note.get("share_info")
    if isinstance(si, dict):
        u = str(si.get("image") or "").strip()
        if u:
            return u
    # fallback to first image original/high
    imgs = _extract_image_urls_from_note(note_json)
    return imgs[0] if imgs else ""


def _save_notes_like_rednotes(
    run: RunDirs,
    notes: List[Dict[str, Any]],
    download_images: bool = True,
    download_videos: bool = True,
    video_mode: str = "best",
    qwen_summary: bool = True,
    qwen_cfg_path: str = "configs/legacy/rednotes.yaml",
    qwen_model: str = "qwen-vl-plus",
) -> None:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    items_out: List[Dict[str, Any]] = []
    raw_notes_out: List[Dict[str, Any]] = []

    for idx, n in enumerate(notes, start=1):
        note_id = str(n.get("note_id") or "").strip() or f"note_{idx}"
        title = str(n.get("title") or "").strip()
        account_name = str(n.get("account_name") or "").strip()
        source_url = str(n.get("source_url") or "").strip()
        original_text = str(n.get("content_text") or "").strip()
        tags = n.get("tags") if isinstance(n.get("tags"), list) else []

        prefix = _build_prefix(idx, note_id, account_name, title)
        text_path = run.text_dir / f"{prefix}.txt"

        per_note_img_dir = run.images_dir / prefix
        per_note_img_dir.mkdir(parents=True, exist_ok=True)
        cover_path = ""
        image_paths: List[str] = []
        errors: List[str] = []

        if download_images:
            cover_url = str(n.get("cover_url") or "").strip()
            if cover_url:
                try:
                    cover_path = str(_download(cover_url, per_note_img_dir / "cover"))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"cover:{cover_url}:{exc}")

            for img_idx, img_url in enumerate((n.get("image_urls") or []) if isinstance(n.get("image_urls"), list) else [], start=1):
                u = str(img_url or "").strip()
                if not u:
                    continue
                try:
                    p = _download(u, per_note_img_dir / f"{img_idx:02d}")
                    image_paths.append(str(p))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"img{img_idx}:{u}:{exc}")

        per_note_video_dir = run.video_dir / prefix
        per_note_video_dir.mkdir(parents=True, exist_ok=True)
        video_paths: List[str] = []

        video_urls = (n.get("video_urls") or []) if isinstance(n.get("video_urls"), list) else []
        if video_mode == "best" and video_urls:
            video_urls = _pick_best_video_urls([str(u) for u in video_urls])
        best_video_url = str(video_urls[0] if video_urls else "").strip()

        if download_videos:
            for vid_idx, vurl in enumerate(video_urls, start=1):
                u = str(vurl or "").strip()
                if not u:
                    continue
                try:
                    p = _download(u, per_note_video_dir / f"{vid_idx:02d}")
                    video_paths.append(str(p))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"video{vid_idx}:{u}:{exc}")

        body_text = original_text
        analysis_obj: Optional[Dict[str, Any]] = None

        is_video_note = bool(best_video_url)

        # 1) 生成“正文”：仅对视频笔记做“文字+图片+视频”综合总结（纯文本）
        #    对非视频笔记：直接保留原文+配图下载，不走视频总结流程（更省额度也更稳定）。
        if qwen_summary and is_video_note:
            try:
                body_text = _qwen_vl_generate_body_text(
                    cfg_path=qwen_cfg_path,
                    model=qwen_model,
                    title=title,
                    source_url=source_url,
                    original_text=original_text,
                    cover_path=cover_path,
                    image_paths=image_paths,
                    video_url=best_video_url,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"qwen_body:{exc}")

            # 2) 基于正文再做一次 AI 分析（JSON）
            try:
                analysis_obj = _qwen_vl_analyze_body(
                    cfg_path=qwen_cfg_path,
                    model=qwen_model,
                    title=title,
                    body_text=body_text,
                    cover_path=cover_path,
                    image_paths=image_paths,
                    video_url=best_video_url,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"qwen_analysis:{exc}")

        if not str(body_text or "").strip():
            # Keep the txt readable even if the source has empty desc and Qwen is unavailable.
            body_text = str(original_text or "").strip() or (f"{title}\n（原始正文缺失）" if title else "（原始正文缺失）")

        # 写入 txt（格式与原先图文 rednotes 一致）
        err_str = "; ".join([str(n.get("error") or "").strip(), *[e for e in errors if e]]).strip("; ").strip()
        text_payload = [
            f"账号: {account_name}" if account_name else "",
            f"标题: {title}",
            f"链接: {source_url}",
            "",
            (body_text or "").strip(),
            "",
            f"tags: {', '.join([str(t) for t in tags if str(t).strip()])}",
            f"error: {err_str}".rstrip(),
        ]
        text_path.write_text("\n".join([ln for ln in text_payload if ln != ""]).strip() + "\n", encoding="utf-8")
        if analysis_obj is not None:
            with text_path.open("a", encoding="utf-8") as f:
                f.write("\n\n[AI分析]\n")
                f.write(json.dumps(analysis_obj, ensure_ascii=False, indent=2) + "\n")

        items_out.append(
            {
                "note_id": note_id,
                "title": title,
                "source_url": source_url,
                "text_path": str(text_path),
                "cover_path": cover_path,
                "image_paths": image_paths,
                "error": err_str,
                "video_paths": video_paths,
            }
        )

        raw_notes_out.append(
            {
                "note_id": note_id,
                "title": title,
                "source_url": source_url,
                "account_name": account_name,
                "publish_time": str(n.get("publish_time") or "").strip(),
                "content_text": (body_text or original_text),
                "tags": tags,
                "cover_url": str(n.get("cover_url") or "").strip(),
                "image_urls": n.get("image_urls") or [],
                "video_urls": n.get("video_urls") or [],
            }
        )

    manifest = {"created_at": created_at, "count": len(items_out), "items": items_out, "raw_notes": raw_notes_out}
    (run.run_dir / "notes.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _qwen_vl_generate_body_text(
    cfg_path: str,
    model: str,
    title: str,
    source_url: str,
    original_text: str,
    cover_path: str,
    image_paths: List[str],
    video_url: str,
) -> str:
    """
    Generate a readable body text by summarizing the note objectively using text + images + video.
    Output is plain text (not JSON).
    """
    from crawler_rednotes.config_rednotes import load_rednotes_config
    from crawler_rednotes.llm.qwen_client import QwenClient, image_file_to_data_url

    cfg, _raw = load_rednotes_config(cfg_path)
    api_key = (
        (cfg.qwen.api_key if cfg.qwen else "").strip()
        or os.environ.get("QWEN_API_KEY", "").strip()
        or os.environ.get("DASHSCOPE_API_KEY", "").strip()
    )
    base_url = (
        (cfg.qwen.base_url if cfg.qwen else "").strip()
        or os.environ.get("QWEN_API_URL", "").strip()
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    timeout_sec = int(cfg.qwen.timeout_sec if cfg.qwen else 120)
    # Video inputs can take much longer to process; increase client timeout to reduce read timeouts.
    if (video_url or "").strip():
        timeout_sec = max(timeout_sec, 360)
    resolved_model = (model or "").strip() or (cfg.qwen.model if cfg.qwen else "").strip() or "qwen3-vl-plus"
    if not api_key:
        raise RuntimeError("Missing Qwen API key. Set xhs.qwen.api_key in config or env QWEN_API_KEY/DASHSCOPE_API_KEY")

    prompt = (
        "你是一位严谨的内容编辑。\n"
        "请综合“原始文字 + 图片 + 视频内容”，把这条视频笔记改写成一段可直接阅读的正文。\n"
        "要求：\n"
        "- 必须客观、具体，只写从输入可推断出的内容，不要臆测\n"
        "- 不要写免责声明/提示词/过程\n"
        "- 输出为纯文本（不要 JSON、不要 Markdown 标题）\n"
        "- 重点覆盖：主题/关键信息点/关键数据/结论或建议（如果原内容包含）\n"
        "- 长度建议 450-900 字\n"
    )
    user_text = f"标题: {title}\n链接: {source_url}\n\n原始文字/简介:\n{original_text}\n"

    content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    vurl = (video_url or "").strip()
    if vurl:
        content.append({"type": "video_url", "video_url": {"url": vurl}})

    def is_image_file(p: str) -> bool:
        ext = Path(p).suffix.lower()
        return ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")

    imgs: List[str] = []
    if cover_path and is_image_file(cover_path) and Path(cover_path).exists():
        imgs.append(cover_path)
    for p in [p for p in image_paths if p]:
        if is_image_file(p) and Path(p).exists():
            imgs.append(p)

    # Avoid very large base64 payloads; keep only reasonably sized images.
    filtered_imgs: List[str] = []
    for p in imgs:
        try:
            if Path(p).stat().st_size <= 1_500_000:
                filtered_imgs.append(p)
        except Exception:
            continue

    for p in filtered_imgs[:6]:
        try:
            content.append({"type": "image_url", "image_url": {"url": image_file_to_data_url(p)}})
        except Exception:
            continue

    client = QwenClient(api_key=api_key, base_url=base_url, model=resolved_model, timeout_sec=timeout_sec)
    resp = client.chat_vl([{"role": "system", "content": prompt}, {"role": "user", "content": content}], enable_thinking=False)
    txt = str(resp.get("content") or "").strip()
    if txt.startswith("```"):
        txt = txt.strip().strip("`").strip()
    return txt.strip()


def _qwen_vl_analyze_body(
    cfg_path: str,
    model: str,
    title: str,
    body_text: str,
    cover_path: str,
    image_paths: List[str],
    video_url: str,
) -> Dict[str, Any]:
    """
    Analyze the generated body text and output JSON in the same shape as existing image-text notes.
    """
    from crawler_rednotes.config_rednotes import load_rednotes_config
    from crawler_rednotes.llm.qwen_client import QwenClient, image_file_to_data_url

    cfg, _raw = load_rednotes_config(cfg_path)
    api_key = (
        (cfg.qwen.api_key if cfg.qwen else "").strip()
        or os.environ.get("QWEN_API_KEY", "").strip()
        or os.environ.get("DASHSCOPE_API_KEY", "").strip()
    )
    base_url = (
        (cfg.qwen.base_url if cfg.qwen else "").strip()
        or os.environ.get("QWEN_API_URL", "").strip()
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    timeout_sec = int(cfg.qwen.timeout_sec if cfg.qwen else 120)
    if (video_url or "").strip():
        timeout_sec = max(timeout_sec, 360)
    resolved_model = (model or "").strip() or (cfg.qwen.model if cfg.qwen else "").strip() or "qwen3-vl-plus"
    if not api_key:
        raise RuntimeError("Missing Qwen API key. Set xhs.qwen.api_key in config or env QWEN_API_KEY/DASHSCOPE_API_KEY")

    system_prompt = (
        "你是内容分析助手。输出严格 JSON: "
        '{"summary":"<不超过100字>","key_points":["要点1","要点2","要点3"],"tags":["标签1","标签2","标签3"]}'
    )
    parts: List[Dict[str, Any]] = [{"type": "text", "text": f"标题: {title}\n正文: {body_text}\n请总结，限制摘要100字内。"}]
    vurl = (video_url or "").strip()
    if vurl:
        parts.append({"type": "video_url", "video_url": {"url": vurl}})
    def is_image_file(p: str) -> bool:
        ext = Path(p).suffix.lower()
        return ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")

    imgs: List[str] = []
    if cover_path and is_image_file(cover_path) and Path(cover_path).exists():
        imgs.append(cover_path)
    for p in [p for p in image_paths if p]:
        if is_image_file(p) and Path(p).exists():
            imgs.append(p)

    filtered_imgs: List[str] = []
    for p in imgs:
        try:
            if Path(p).stat().st_size <= 1_500_000:
                filtered_imgs.append(p)
        except Exception:
            continue

    for p in filtered_imgs[:6]:
        try:
            parts.append({"type": "image_url", "image_url": {"url": image_file_to_data_url(p)}})
        except Exception:
            continue

    client = QwenClient(api_key=api_key, base_url=base_url, model=resolved_model, timeout_sec=timeout_sec)
    resp = client.chat_vl([{"role": "system", "content": system_prompt}, {"role": "user", "content": parts}], enable_thinking=False)
    txt = str(resp.get("content") or "").strip()
    if txt.startswith("```"):
        txt = txt.strip().strip("`").strip()
    obj = json.loads(txt)
    if not isinstance(obj, dict):
        raise RuntimeError("qwen analysis response is not json object")
    return obj

def _get_primary_note_item(note_json: Any) -> Dict[str, Any]:
    """
    Best-effort extraction of the primary note dict from TikHub response.
    Supports both:
    - wrapper response with data.data[0].note_list[0]
    - flattened note objects (future)
    """
    if not isinstance(note_json, dict):
        return {}

    # Wrapper form used in our saved raw JSON
    data = note_json.get("data")
    if isinstance(data, dict):
        arr = data.get("data")
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict):
                nl = first.get("note_list")
                if isinstance(nl, list) and nl:
                    it = nl[0]
                    if isinstance(it, dict):
                        return it

    # Flattened fallback
    if any(k in note_json for k in ("note_id", "title", "desc", "type")):
        return note_json
    return {}


def _extract_image_urls_from_note(note_json: Any) -> List[str]:
    note = _get_primary_note_item(note_json)
    urls: List[str] = []
    images_list = note.get("images_list")
    if images_list is None:
        images_list = note.get("image_list")
    if images_list is None:
        images_list = note.get("images")
    if isinstance(images_list, list):
        for it in images_list:
            if not isinstance(it, dict):
                continue
            # Prefer original if present, then high
            for key in ("original", "url"):
                u = str(it.get(key) or "").strip()
                if u:
                    urls.append(u)
                    break
            uml = it.get("url_multi_level")
            if isinstance(uml, dict):
                u = str(uml.get("high") or uml.get("medium") or "").strip()
                if u:
                    urls.append(u)

    # de-dup and drop obvious non-note icon urls
    seen: set[str] = set()
    out: List[str] = []
    for u in urls:
        if not u or "ci.xiaohongshu.com" in u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_video_media_urls(note_json: Any) -> List[str]:
    """
    Try to find direct media URLs for video notes.
    We only accept URLs that look like actual media files/streams (mp4/m3u8/etc),
    and we avoid share/landing pages.
    """
    urls = _collect_urls(note_json)
    out: List[str] = []
    for u in urls:
        lower = u.lower()
        if "/discovery/item/" in lower or "/explore/" in lower or "xsec_token=" in lower:
            continue
        if any(ext in lower for ext in [".mp4", ".mov", ".m3u8", ".webm"]):
            out.append(u)
            continue
        # Some CDN links are extension-less but still point to stream assets.
        if "/stream/" in lower and ("rednotecdn.com" in lower or "xhscdn.com" in lower):
            out.append(u)

    # De-dup by media identity (ignore host differences like sns-v8 vs sns-v10).
    # Example duplicates:
    # - http://sns-v8.../stream/.../01e8..._258.mp4
    # - http://sns-v10.../stream/.../01e8..._258.mp4
    def media_key(u: str) -> str:
        # Prefer path tail after "/stream/" (stable across hosts).
        m = re.search(r"/stream/.*", u, flags=re.I)
        return (m.group(0) if m else u).lower()

    seen: set[str] = set()
    dedup: List[str] = []
    for u in out:
        k = media_key(u)
        if k in seen:
            continue
        seen.add(k)
        dedup.append(u)
    return dedup


def _extract_meta_fallback(meta: Any, note_id: str) -> Dict[str, Any]:
    m = meta if isinstance(meta, dict) else {}
    title, text = _extract_title_text(m)
    source_url = _extract_share_url(m) or (f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "")
    tags = _extract_tags(m)
    cover_url = _extract_cover_url(m)
    image_urls = _extract_image_urls_from_note(m)
    video_urls = _extract_video_media_urls(m)
    note_type = str(m.get("type") or "").strip().lower()

    publish_time = ""
    for k in ("time", "last_update_time", "create_time"):
        ts = m.get(k)
        if isinstance(ts, (int, float)) and int(ts) > 0:
            publish_time = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
            break

    return {
        "title": title,
        "content_text": text,
        "source_url": source_url,
        "tags": tags,
        "cover_url": cover_url,
        "image_urls": image_urls,
        "video_urls": video_urls,
        "publish_time": publish_time,
        "note_type": note_type,
    }


def _pick_best_video_urls(video_urls: List[str]) -> List[str]:
    """
    TikHub may return multiple mp4 urls for the same video with different qualities (e.g. _258/_98)
    and multiple CDN hosts (sns-v8/sns-v10). Default behavior is to pick the best quality only.
    """
    urls = [str(u).strip() for u in (video_urls or []) if str(u).strip()]
    if not urls:
        return []

    # Prefer mp4; fall back to the first url otherwise.
    mp4s = [u for u in urls if u.lower().endswith(".mp4") or ".mp4?" in u.lower()]
    cand = mp4s or urls

    def quality_score(u: str) -> int:
        # Many XHS mp4 urls include a trailing _<n>.mp4 where n correlates with quality.
        m = re.search(r"_(\d+)\.mp4", u, flags=re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 0
        # Some include /110/<n>/ in the path.
        m2 = re.search(r"/110/(\d+)/", u)
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                return 0
        return 0

    best = max(cand, key=quality_score)
    return [best]


def _extract_title_text(note_json: Any) -> Tuple[str, str]:
    note = _get_primary_note_item(note_json)
    title = str(note.get("title") or note.get("note_title") or note.get("name") or "").strip()
    text = str(note.get("desc") or note.get("content") or note.get("text") or note.get("note_desc") or "").strip()

    # Some responses put title/content under share_info.
    if isinstance(note.get("share_info"), dict):
        si = note["share_info"]
        if not title:
            title = str(si.get("title") or "").strip()
        if not text:
            text = str(si.get("content") or "").strip()
    if title or text:
        return title, text

    if isinstance(note_json, dict):
        title = str(note_json.get("title") or note_json.get("name") or "").strip()
        text = str(note_json.get("desc") or note_json.get("content") or note_json.get("text") or "").strip()
    return title, text


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xiaohongshu via TikHub API and save outputs in rednotes format")
    parser.add_argument("--api-key", default="", help="TikHub API key (or set env TIKHUB_API_KEY / hardcoded constant)")
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument("--note-id", default="", help="XHS note_id (single note; default uses HARDCODED_NOTE_ID)")
    src.add_argument("--profile-url", default="", help="XHS profile url (crawl recent notes from this user)")
    src.add_argument("--from-config", action="store_true", help="Read accounts+maxcrawl from config and crawl all")
    parser.add_argument("--outputs", default=str(Path("outputs") / "rednotes"), help="Outputs root dir")
    parser.add_argument("--date", default="", help="Run date folder (YYYYMMDD), default: today")
    parser.add_argument("--limit", type=int, default=0, help="Max notes per profile for --profile-url/--from-config (0 uses config maxcrawl)")
    parser.add_argument("--since", default="", help="Only keep notes newer than this date (YYYY-MM-DD)")
    parser.add_argument("--retries", type=int, default=2, help="HTTP retries for TikHub list/detail requests (default: 2)")
    parser.add_argument("--timeout-sec", type=int, default=0, help="HTTP timeout seconds for TikHub requests (0=auto)")
    parser.add_argument("--refetch-empty", type=int, default=1, help="Refetch note detail when parsed content is empty (default: 1)")
    parser.add_argument(
        "--video-mode",
        choices=["best", "all"],
        default="best",
        help="Video download mode: best=download best quality only (default), all=download all variants",
    )
    parser.add_argument(
        "--qwen-config",
        default=DEFAULT_PROJECT_CONFIG,
        help="配置入口（统一配置 configs/config.yaml 或 legacy rednotes.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    parser.add_argument(
        "--qwen-model",
        default="",
        help="Qwen multimodal model name (default: use config qwen.model; recommended for video: qwen3-vl-plus)",
    )
    parser.add_argument(
        "--no-qwen-video-summary",
        action="store_true",
        help="Disable Qwen body generation + AI analysis (default: enabled)",
    )
    parser.add_argument(
        "--no-download-media",
        action="store_true",
        help="Disable media download (default: download images/videos to local folders)",
    )
    # Backward-compatible alias (kept for convenience). No-op because download is enabled by default.
    parser.add_argument("--download-media", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        resolved_qwen_cfg = resolve_legacy_config_for_cli(args.qwen_config, kind="rednotes", profile=args.profile)
    except Exception as exc:  # noqa: BLE001
        print(f"[tikhub] config resolve failed: {exc}", file=sys.stderr)
        return 2

    api_key = (
        (args.api_key or "").strip()
        or os.environ.get("TIKHUB_API_KEY", "").strip()
        or (HARDCODED_TIKHUB_API_KEY or "").strip()
    )
    if not api_key:
        print("Missing TikHub API key. Pass --api-key or set env TIKHUB_API_KEY", file=sys.stderr)
        return 2

    # Resolve timeout: CLI > env > default 40
    try:
        timeout_sec = int(args.timeout_sec or 0)
    except Exception:
        timeout_sec = 0
    if timeout_sec <= 0:
        try:
            timeout_sec = int(os.environ.get("TIKHUB_TIMEOUT_SEC", "0"))
        except Exception:
            timeout_sec = 0
    if timeout_sec <= 0:
        timeout_sec = 40

    out_root = Path(args.outputs)
    run = ensure_run_dirs(out_root, date_str=args.date.strip())
    print(f"[tikhub] run_dir={run.run_dir}")

    # Build base note list (single note OR recent notes from one/many profiles).
    notes_to_fetch: List[Dict[str, Any]] = []
    since_dt: Optional[datetime] = None

    if bool(args.from_config):
        from crawler_rednotes.config_rednotes import load_rednotes_config

        cfg, _raw = load_rednotes_config(resolved_qwen_cfg)
        accounts = cfg.accounts or []
        per_limit = int(args.limit or 0) if int(args.limit or 0) > 0 else int(cfg.maxcrawl or 20)
        since_str = (str(args.since or "").strip() or str(cfg.since or "").strip())
        if since_str:
            try:
                since_dt = datetime.strptime(since_str, "%Y-%m-%d")
            except ValueError:
                print(f"[tikhub] invalid since format: {since_str} (expected YYYY-MM-DD)", file=sys.stderr)
                return 2
        if not accounts:
            print(f"[tikhub] no accounts in {resolved_qwen_cfg}", file=sys.stderr)
            return 2
        for acc in accounts:
            name = str(acc.get("name") or "").strip()
            url = str(acc.get("url") or "").strip()
            uid = _extract_user_id_from_profile_url(url)
            if not uid or not name:
                continue
            print(f"[tikhub] profile={name} url={url} user_id={uid} limit={per_limit}")
            try:
                metas = _iter_recent_note_metas(
                    user_id=uid,
                    api_key=api_key,
                    limit=per_limit,
                    dump_dir=run.raw_dir,
                    retries=int(args.retries),
                    timeout_sec=timeout_sec,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[tikhub] profile={name} list_failed: {exc}", file=sys.stderr)
                continue
            if not metas:
                print(f"[tikhub] profile={name} got 0 metas (get_user_notes response shape may differ).", file=sys.stderr)
            for m in metas:
                nid = str(m.get("note_id") or m.get("noteId") or m.get("id") or "").strip()
                if not nid:
                    continue
                notes_to_fetch.append({"note_id": nid, "account_name": name, "_meta": m})

    elif str(args.profile_url or "").strip():
        profile_url = str(args.profile_url).strip()
        uid = _extract_user_id_from_profile_url(profile_url)
        if not uid:
            print(f"[tikhub] invalid profile url (cannot extract user_id): {profile_url}", file=sys.stderr)
            return 2
        per_limit = int(args.limit or 0)
        if per_limit <= 0:
            from crawler_rednotes.config_rednotes import load_rednotes_config

            cfg, _raw = load_rednotes_config(resolved_qwen_cfg)
            per_limit = int(cfg.maxcrawl or 20)
        since_str = str(args.since or "").strip()
        if since_str:
            try:
                since_dt = datetime.strptime(since_str, "%Y-%m-%d")
            except ValueError:
                print(f"[tikhub] invalid since format: {since_str} (expected YYYY-MM-DD)", file=sys.stderr)
                return 2
        print(f"[tikhub] profile user_id={uid} limit={per_limit}")
        metas = _iter_recent_note_metas(
            user_id=uid,
            api_key=api_key,
            limit=per_limit,
            dump_dir=run.raw_dir,
            retries=int(args.retries),
            timeout_sec=timeout_sec,
        )
        for m in metas:
            nid = str(m.get("note_id") or m.get("noteId") or m.get("id") or "").strip()
            if not nid:
                continue
            notes_to_fetch.append({"note_id": nid, "account_name": uid, "_meta": m})

    else:
        note_id = (args.note_id or "").strip() or (HARDCODED_NOTE_ID or "").strip()
        if not note_id:
            print("Missing note_id. Pass --note-id or set HARDCODED_NOTE_ID in rednotes_api_tikhub.py", file=sys.stderr)
            return 2
        notes_to_fetch = [{"note_id": note_id, "account_name": ""}]

    # Enrich each note with detail info.
    enriched: List[Dict[str, Any]] = []
    failed_bases: List[Dict[str, Any]] = []

    for i, base in enumerate(notes_to_fetch, start=1):
        nid = str(base.get("note_id") or "").strip()
        if not nid:
            continue
        meta_fb = _extract_meta_fallback(base.get("_meta"), nid)

        # Cache note detail to reduce repeated paid requests and allow retries on flaky network.
        cache_note = run.raw_dir / f"{nid}.json"
        note = None
        if cache_note.exists():
            try:
                note = json.loads(cache_note.read_text(encoding="utf-8"))
            except Exception:
                note = None

        last_exc: Optional[Exception] = None
        if note is None:
            for _ in range(max(1, int(args.retries))):
                try:
                    note = fetch_note_info(nid, api_key=api_key)
                    try:
                        cache_note.write_text(json.dumps(note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    except Exception:
                        pass
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    note = None

        if note is None:
            # Defer retries to a second pass to reduce the chance that transient network issues
            # wipe out a large chunk of the batch.
            base2 = dict(base)
            base2["_note_info_error"] = str(last_exc)
            failed_bases.append(base2)
            continue

        video_note = None
        video_fetch_error = ""
        try:
            primary = _get_primary_note_item(note)
            note_type = str(primary.get("type") or "").strip().lower()
            if note_type == "video" or meta_fb.get("note_type") == "video":
                cache_video = run.raw_dir / f"{nid}_video.json"
                if cache_video.exists():
                    try:
                        video_note = json.loads(cache_video.read_text(encoding="utf-8"))
                    except Exception:
                        video_note = None
                if video_note is None:
                    video_note = fetch_video_note_info(nid, api_key=api_key)
                    try:
                        cache_video.write_text(json.dumps(video_note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001
            video_note = None
            video_fetch_error = str(exc)

        title, text = _extract_title_text(note if isinstance(note, dict) else {})
        if not title:
            title = str(meta_fb.get("title") or "").strip()
        if not text:
            text = str(meta_fb.get("content_text") or "").strip()
        publish_time = ""
        # Try to extract publish time for filtering / notes.json.
        try:
            primary = _get_primary_note_item(note)
            ts = primary.get("time")
            if isinstance(ts, (int, float)) and int(ts) > 0:
                dt = datetime.fromtimestamp(int(ts))
                publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                if since_dt is not None and dt <= since_dt:
                    continue
            elif isinstance(primary.get("last_update_time"), (int, float)) and int(primary.get("last_update_time") or 0) > 0:
                dt2 = datetime.fromtimestamp(int(primary.get("last_update_time")))
                publish_time = dt2.strftime("%Y-%m-%d %H:%M:%S")
                if since_dt is not None and dt2 <= since_dt:
                    continue
        except Exception:
            pass
        account_name = str(base.get("account_name") or "").strip() or _extract_account_name(note)
        source_url = _extract_share_url(note) or str(meta_fb.get("source_url") or "") or f"https://www.xiaohongshu.com/explore/{nid}"
        tags = _extract_tags(note) or (meta_fb.get("tags") or [])
        cover_url = _extract_cover_url(note) or str(meta_fb.get("cover_url") or "")
        image_urls = _extract_image_urls_from_note(note) or (meta_fb.get("image_urls") or [])
        video_urls = _extract_video_media_urls(video_note or note) or (meta_fb.get("video_urls") or [])

        # If the note looks empty, try refetching once (network/cache may have returned incomplete data).
        if int(args.refetch_empty) > 0 and not title and not text and not image_urls and not video_urls:
            refetched = False
            for _ in range(int(args.refetch_empty)):
                try:
                    note2 = fetch_note_info(nid, api_key=api_key)
                    cache_note.write_text(json.dumps(note2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    note = note2
                    refetched = True
                    break
                except Exception:
                    continue
            if refetched:
                title, text = _extract_title_text(note if isinstance(note, dict) else {})
                if not title:
                    title = str(meta_fb.get("title") or "").strip()
                if not text:
                    text = str(meta_fb.get("content_text") or "").strip()
                tags = _extract_tags(note)
                cover_url = _extract_cover_url(note)
                image_urls = _extract_image_urls_from_note(note)
                if not tags:
                    tags = meta_fb.get("tags") or []
                if not cover_url:
                    cover_url = str(meta_fb.get("cover_url") or "")
                if not image_urls:
                    image_urls = meta_fb.get("image_urls") or []

        err = []
        if video_fetch_error:
            err.append(f"video_info:{video_fetch_error}")
        error_text = "; ".join(err).strip()

        enriched.append(
            {
                "note_id": nid,
                "title": title,
                "source_url": source_url,
                "content_text": text,
                "account_name": account_name,
                "tags": tags,
                "cover_url": cover_url,
                "image_urls": image_urls,
                "video_urls": video_urls,
                "publish_time": publish_time,
                "error": error_text,
            }
        )
        if i % 5 == 0 or i == len(notes_to_fetch):
            print(f"[tikhub] enriched {i}/{len(notes_to_fetch)}")

    # Second pass: retry failed note_info fetches.
    if failed_bases:
        print(f"[tikhub] retrying failed note_info: {len(failed_bases)}")
    for base in failed_bases:
        nid = str(base.get("note_id") or "").strip()
        if not nid:
            continue
        meta_fb = _extract_meta_fallback(base.get("_meta"), nid)
        cache_note = run.raw_dir / f"{nid}.json"
        note = None
        for _ in range(max(1, int(args.retries))):
            try:
                note = fetch_note_info(nid, api_key=api_key)
                try:
                    cache_note.write_text(json.dumps(note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                except Exception:
                    pass
                break
            except Exception as exc:  # noqa: BLE001
                base["_note_info_error"] = str(exc)
                note = None
        if note is None:
            meta_source_url = str(meta_fb.get("source_url") or "") or f"https://www.xiaohongshu.com/explore/{nid}"
            meta_error = f"note_info:{base.get('_note_info_error')}"
            enriched.append(
                {
                    "note_id": nid,
                    "title": str(meta_fb.get("title") or ""),
                    "source_url": meta_source_url,
                    "content_text": str(meta_fb.get("content_text") or ""),
                    "account_name": str(base.get("account_name") or "").strip(),
                    "tags": meta_fb.get("tags") or [],
                    "cover_url": str(meta_fb.get("cover_url") or ""),
                    "image_urls": meta_fb.get("image_urls") or [],
                    "video_urls": meta_fb.get("video_urls") or [],
                    "publish_time": str(meta_fb.get("publish_time") or ""),
                    "error": meta_error,
                }
            )
            continue

        video_note = None
        video_fetch_error = ""
        try:
            primary = _get_primary_note_item(note)
            note_type = str(primary.get("type") or "").strip().lower()
            if note_type == "video" or meta_fb.get("note_type") == "video":
                cache_video = run.raw_dir / f"{nid}_video.json"
                if cache_video.exists():
                    try:
                        video_note = json.loads(cache_video.read_text(encoding="utf-8"))
                    except Exception:
                        video_note = None
                if video_note is None:
                    video_note = fetch_video_note_info(nid, api_key=api_key)
                    try:
                        cache_video.write_text(json.dumps(video_note, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    except Exception:
                        pass
        except Exception as exc:
            video_note = None
            video_fetch_error = str(exc)

        title, text = _extract_title_text(note if isinstance(note, dict) else {})
        if not title:
            title = str(meta_fb.get("title") or "").strip()
        if not text:
            text = str(meta_fb.get("content_text") or "").strip()
        publish_time = ""
        try:
            primary = _get_primary_note_item(note)
            ts = primary.get("time")
            if isinstance(ts, (int, float)) and int(ts) > 0:
                dt = datetime.fromtimestamp(int(ts))
                publish_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                if since_dt is not None and dt <= since_dt:
                    continue
        except Exception:
            pass

        account_name = str(base.get("account_name") or "").strip() or _extract_account_name(note)
        source_url = _extract_share_url(note) or str(meta_fb.get("source_url") or "") or f"https://www.xiaohongshu.com/explore/{nid}"
        tags = _extract_tags(note) or (meta_fb.get("tags") or [])
        cover_url = _extract_cover_url(note) or str(meta_fb.get("cover_url") or "")
        image_urls = _extract_image_urls_from_note(note) or (meta_fb.get("image_urls") or [])
        video_urls = _extract_video_media_urls(video_note or note) or (meta_fb.get("video_urls") or [])

        err = []
        if video_fetch_error:
            err.append(f"video_info:{video_fetch_error}")
        error_text = "; ".join(err).strip()

        enriched.append(
            {
                "note_id": nid,
                "title": title,
                "source_url": source_url,
                "content_text": text,
                "account_name": account_name,
                "tags": tags,
                "cover_url": cover_url,
                "image_urls": image_urls,
                "video_urls": video_urls,
                "publish_time": publish_time,
                "error": error_text,
            }
        )

    download_media = not bool(args.no_download_media)
    _save_notes_like_rednotes(
        run,
        enriched,
        download_images=download_media,
        download_videos=download_media,
        video_mode=str(args.video_mode),
        qwen_summary=not bool(args.no_qwen_video_summary),
        qwen_cfg_path=resolved_qwen_cfg,
        qwen_model=str(args.qwen_model),
    )

    print(f"[tikhub] done saved={len(enriched)} (saved to {run.run_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
