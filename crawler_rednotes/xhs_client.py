import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .url_utils import normalize_profile_url


class NoteItem:
    def __init__(self, data: Dict[str, Any]):
        self.note_id = data.get("note_id")
        self.title = data.get("title")
        self.content_text = data.get("content_text")
        self.publish_time = data.get("publish_time")
        self.source_url = data.get("source_url")
        self.cover_url = data.get("cover_url")
        self.image_urls = data.get("image_urls", [])
        self.detail_url = data.get("detail_url")
        self.tags = data.get("tags", [])
        self.error = data.get("error")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "content_text": self.content_text,
            "publish_time": self.publish_time,
            "source_url": self.source_url,
            "cover_url": self.cover_url,
            "image_urls": self.image_urls,
            "detail_url": self.detail_url,
            "tags": self.tags,
            "error": self.error,
        }


class BaseXHSClient:
    def fetch_notes(self, profile_url: str, since_note_id: Optional[str]) -> List[NoteItem]:
        raise NotImplementedError


class MockXHSClient(BaseXHSClient):
    def __init__(self, data_file: str):
        self.data_file = data_file

    def fetch_notes(self, profile_url: str, since_note_id: Optional[str]) -> List[NoteItem]:
        with open(self.data_file, "r", encoding="ascii") as f:
            raw = json.load(f)
        notes = raw.get("accounts", {}).get(profile_url, [])
        items = [NoteItem(n) for n in notes]
        if since_note_id is None:
            return items
        result = []
        found = False
        for item in items:
            if found:
                result.append(item)
            if item.note_id == since_note_id:
                found = True
        return result


class APIXHSClient(BaseXHSClient):
    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url
        self.token = token

    def fetch_notes(self, profile_url: str, since_note_id: Optional[str]) -> List[NoteItem]:
        raise NotImplementedError("API client is not implemented. Use mock or browser mode.")


class BrowserXHSClient(BaseXHSClient):
    def __init__(self, storage_state_path: str, headless: bool = False, interval_sec: float = 1.5):
        self.storage_state_path = storage_state_path
        self.headless = headless
        self.interval_sec = max(0.0, float(interval_sec))

    def fetch_notes(self, profile_url: str, since_note_id: Optional[str], limit: int = 100) -> List[NoteItem]:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Missing dependency 'playwright'. Install it with: pip install playwright") from exc

        url = normalize_profile_url(profile_url)
        notes: List[NoteItem] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=self.storage_state_path)
            page = context.new_page()
            page.set_default_navigation_timeout(60000)
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
            if "login" in page.url:
                context.close()
                browser.close()
                raise RuntimeError("requires_login")
            page.wait_for_timeout(3000)
            items = []
            for _ in range(20):
                _auto_scroll(page, steps=1, step_px=800)
                page.wait_for_timeout(1200)
                items = page.query_selector_all("#userPostedFeeds section.note-item")
                if items:
                    break
            if not items:
                context.close()
                browser.close()
                raise RuntimeError("no_content")

            seen_ids = set()
            for sec in items:
                link = sec.query_selector("a.cover.mask.ld")
                if not link:
                    continue
                detail_href = link.get_attribute("href") or ""
                path_part = detail_href.split("?", 1)[0].strip("/")
                parts = path_part.split("/")
                note_id = parts[-1] if parts else ""
                if not note_id or note_id in seen_ids:
                    continue
                img = link.query_selector("img")
                cover_url = ""
                if img:
                    cover_url = (
                        img.get_attribute("src")
                        or img.get_attribute("data-src")
                        or img.get_attribute("data-original")
                        or ""
                    )
                title_node = sec.query_selector(".footer a.title span")
                title = ""
                if title_node:
                    title = (title_node.inner_text() or "").strip()
                seen_ids.add(note_id)
                notes.append(
                    NoteItem(
                        {
                            "note_id": note_id,
                            "title": title,
                            "content_text": "",
                            "publish_time": None,
                            "source_url": f"https://www.xiaohongshu.com/explore/{note_id}",
                            "cover_url": cover_url or None,
                            "image_urls": [],
                            "detail_url": detail_href,
                        }
                    )
                )
                if since_note_id and note_id == since_note_id:
                    break
                if len(notes) >= limit:
                    break

            self._enrich_details(context, notes)
            context.close()
            browser.close()
        return notes

    def _enrich_details(
        self,
        context,
        notes: List[NoteItem],
        timeout_ms: int = 30000,
        max_slide_steps: int = 80,
        stable_rounds: int = 3,
    ) -> None:
        for note in notes:
            if self.interval_sec:
                time.sleep(self.interval_sec)
            detail_href = note.detail_url or f"/explore/{note.note_id}"
            if detail_href.startswith("http"):
                url = detail_href
            else:
                url = f"https://www.xiaohongshu.com{detail_href}"
            page = context.new_page()
            page.set_default_navigation_timeout(timeout_ms)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1000)
                # 去掉评论容器，避免正文掺杂评论
                page.evaluate(
                    "() => { const c = document.querySelector('.comments-container'); if (c) c.remove(); }"
                )
                try:
                    page.wait_for_selector("span.note-text, .note-slider img", timeout=5000)
                except Exception:
                    pass
                content_text = _extract_text(page)
                tags = _extract_tags(page)
                publish_time = _extract_publish_time(page)
                images = _collect_slider_images(page, max_steps=max_slide_steps, stable_rounds=stable_rounds)
                note.content_text = content_text
                note.tags = tags
                if publish_time:
                    note.publish_time = publish_time
                note.image_urls = images or []
                pt = (note.publish_time or "").strip() if isinstance(note.publish_time, str) else str(note.publish_time or "")
                print(
                    f"[detail] note={note.note_id} time={pt} imgs={len(images)} tags={len(tags)} text_len={len(content_text)}"
                )
            except Exception as exc:
                note.error = f"detail_error:{exc}"
                print(f"[detail] note={note.note_id} failed: {exc}")
            finally:
                page.close()


def _auto_scroll(page, steps: int = 5, step_px: int = 800):
    for _ in range(steps):
        try:
            page.evaluate(f"() => window.scrollBy(0, {step_px})")
        except Exception:
            break
        page.wait_for_timeout(1000)


def _extract_text(page) -> str:
    containers = page.query_selector_all("span.note-text")
    tag_texts = set([t.strip() for t in _extract_tags(page)])
    parts = []
    for c in containers:
        spans = c.query_selector_all("span")
        if spans:
            for sp in spans:
                # 跳过嵌套在评论里的文本
                parent = sp.evaluate_handle("node => node.closest('.comments-container')")
                try:
                    if parent and parent.json_value():
                        continue
                except Exception:
                    pass
                txt = (sp.inner_text() or "").strip()
                if txt:
                    parts.append(txt)
        else:
            raw = (c.inner_text() or "").strip()
            if raw:
                parts.append(raw)
    cleaned = []
    for p in parts:
        if p in tag_texts:
            continue
        cleaned.append(p)
    content = "\n\n".join([seg.strip() for seg in cleaned if seg.strip()])
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    return "\n\n".join(lines)


def _extract_publish_time(page) -> str:
    """
    Best-effort publish time extraction.

    Returns a string that may be parsed later (prefer YYYY-MM-DD HH:MM:SS or YYYY-MM-DD).
    """
    selectors = [
        "span.date",
        "span.time",
        ".date",
        ".time",
        "time",
    ]
    for sel in selectors:
        try:
            node = page.query_selector(sel)
        except Exception:
            node = None
        if node:
            txt = (node.inner_text() or "").strip()
            if txt:
                normalized = _normalize_xhs_time_text(txt, now=datetime.now())
                if normalized:
                    return normalized
                dt = _pick_datetime_from_text(txt)
                if dt:
                    return dt

    try:
        body = (page.inner_text("body") or "").strip()
    except Exception:
        body = ""
    dt = _pick_datetime_from_text(body)
    return dt or ""


def _pick_datetime_from_text(text: str) -> str:
    if not text:
        return ""
    normalized = _normalize_xhs_time_text(text, now=datetime.now())
    if normalized:
        return normalized
    # Common patterns: 2026-01-15 12:34, 2026/01/15, 2026.01.15
    patterns = [
        r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})\s+(\d{1,2}:\d{2}(?::\d{2})?)",
        r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        if m.lastindex and m.lastindex >= 2:
            date_part = m.group(1)
            time_part = m.group(2)
            date_part = date_part.replace("/", "-").replace(".", "-")
            if len(time_part.split(":")) == 2:
                time_part = f"{time_part}:00"
            return f"{date_part} {time_part}"
        date_part = m.group(1).replace("/", "-").replace(".", "-")
        return date_part
    return ""


def _normalize_xhs_time_text(text: str, now: datetime) -> str:
    """
    Normalize Xiaohongshu relative/short time strings to absolute:

    - n分钟前 / n小时前
    - 昨天 HH:MM
    - n天前
    - MM-DD (assume current year; if in future, use last year)
    - YYYY-MM-DD
    """
    if not text:
        return ""
    s = str(text).strip()

    # Remove location or other suffix after a newline; keep first line.
    s = s.splitlines()[0].strip()

    m = re.search(r"(\d+)\s*分钟前", s)
    if m:
        minutes = int(m.group(1))
        dt = now - timedelta(minutes=minutes)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        hours = int(m.group(1))
        dt = now - timedelta(hours=hours)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    m = re.search(r"昨天\s*(\d{1,2}:\d{2})", s)
    if m:
        time_part = m.group(1)
        dt = (now - timedelta(days=1)).replace(
            hour=int(time_part.split(":")[0]),
            minute=int(time_part.split(":")[1]),
            second=0,
            microsecond=0,
        )
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    m = re.search(r"(\d+)\s*天前", s)
    if m:
        days = int(m.group(1))
        dt = (now - timedelta(days=days)).date()
        return dt.strftime("%Y-%m-%d")

    # YYYY-MM-DD (also allow / and . as separators)
    m = re.search(r"(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return ""

    # MM-DD (current year)
    m = re.search(r"\b(\d{1,2})[-/\.](\d{1,2})\b", s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        year = now.year
        try:
            dt = datetime(year, mo, d)
        except ValueError:
            return ""
        # If date is in the future (e.g., now is Jan and text is 12-31), treat it as last year.
        if dt.date() > now.date():
            try:
                dt = datetime(year - 1, mo, d)
            except ValueError:
                return ""
        return dt.strftime("%Y-%m-%d")

    return ""


def _extract_tags(page) -> List[str]:
    tags_nodes = page.query_selector_all("span.note-text a.tag")
    tags: List[str] = []
    for node in tags_nodes:
        t = (node.inner_text() or "").strip()
        if t:
            tags.append(t)
    return tags


def _collect_slider_images(page, max_steps: int = 80, stable_rounds: int = 3) -> List[str]:
    imgs: List[str] = []
    seen = set()
    no_growth = 0
    for _ in range(max_steps):
        nodes = page.query_selector_all(".note-slider img")
        changed = False
        for node in nodes:
            src = node.get_attribute("src") or ""
            if not src or src.startswith(("data:", "blob:")):
                continue
            if src not in seen:
                seen.add(src)
                imgs.append(src)
                changed = True
        if changed:
            no_growth = 0
        else:
            no_growth += 1
        if no_growth >= stable_rounds:
            break
        moved = False
        arrow = page.query_selector(".arrow-controller.right")
        if arrow:
            try:
                arrow.click()
                moved = True
            except Exception:
                moved = False
        if not moved:
            try:
                page.keyboard.press("ArrowRight")
                moved = True
            except Exception:
                moved = False
        page.wait_for_timeout(300)
    return imgs


__all__ = [
    "NoteItem",
    "BaseXHSClient",
    "MockXHSClient",
    "APIXHSClient",
    "BrowserXHSClient",
]
