import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_rednotes.config_rednotes import load_rednotes_config
from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli
from crawler_common.feishu_api import get_tenant_access_token, send_post, upload_image


def find_latest_rednotes_run(outputs_root: str = "outputs/rednotes") -> Optional[Path]:
    root = Path(outputs_root)
    if not root.exists():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def _send_post(token: str, chat_id: str, title: str, blocks: List[List[Dict[str, Any]]]) -> None:
    send_post(token, chat_id, title=title, blocks=blocks)


def load_stage2(run_dir: Path) -> Dict[str, Any]:
    p = run_dir / "analysis" / "stage2.json"
    if not p.exists():
        raise FileNotFoundError(f"stage2.json not found: {p}")
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError("stage2.json must be a JSON object")
    return obj


def load_stage1_items(run_dir: Path) -> List[Dict[str, Any]]:
    p = run_dir / "analysis" / "stage1.json"
    if not p.exists():
        raise FileNotFoundError(f"stage1.json not found: {p}")
    obj = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj, list):
        raise RuntimeError("stage1.json must be a JSON array")
    return [it for it in obj if isinstance(it, dict)]


def load_notes(run_dir: Path) -> Dict[str, Any]:
    p = run_dir / "notes.json"
    if not p.exists():
        return {}
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def pick_generated_cover_path(run_dir: Path, stage1_items: List[Dict[str, Any]], title: str) -> str:
    """
    Prefer generated cover under <run_dir>/covers/<note_id>.* when available.
    """
    note_id = _note_id_by_title(stage1_items, title)
    if not note_id:
        return ""

    covers_dir = run_dir / "covers"
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = covers_dir / f"{note_id}{ext}"
        if p.exists():
            return str(p)
    return ""


def pick_cover_path(stage1_items: List[Dict[str, Any]], title: str) -> str:
    for it in stage1_items:
        if str(it.get("title") or "").strip() == str(title).strip():
            return str(it.get("cover_path") or "").strip()
    return ""


def _note_id_by_title(stage1_items: List[Dict[str, Any]], title: str) -> str:
    t = str(title or "").strip()
    if not t:
        return ""
    for it in stage1_items:
        if str(it.get("title") or "").strip() != t:
            continue
        return str(it.get("note_id") or it.get("id") or "").strip()
    return ""


def _build_has_video_by_note_id(notes: Dict[str, Any]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}

    items = notes.get("items") or []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("note_id") or "").strip()
            if not nid:
                continue
            vps = it.get("video_paths")
            if isinstance(vps, list) and any(str(x).strip() for x in vps):
                out[nid] = True
            else:
                out.setdefault(nid, False)

    raw_notes = notes.get("raw_notes") or []
    if isinstance(raw_notes, list):
        for rn in raw_notes:
            if not isinstance(rn, dict):
                continue
            nid = str(rn.get("note_id") or "").strip()
            if not nid:
                continue
            vus = rn.get("video_urls")
            if isinstance(vus, list) and any(str(x).strip() for x in vus):
                out[nid] = True
            elif str(rn.get("video_url") or "").strip():
                out[nid] = True
            else:
                out.setdefault(nid, False)

    return out


def build_post_blocks(
    stage2: Dict[str, Any],
    stage1_items: List[Dict[str, Any]],
    image_key_by_title: Dict[str, str],
    has_video_by_note_id: Dict[str, bool],
) -> List[List[Dict[str, Any]]]:
    blocks: List[List[Dict[str, Any]]] = []
    overview = str(stage2.get("overview") or "").strip()
    if overview:
        blocks.append([{"tag": "text", "text": "📌【概览】"}])
        blocks.append([{"tag": "text", "text": overview}])

    def add_item(item: Dict[str, Any]) -> None:
        title = str(item.get("title") or "").strip()
        score = item.get("score", "")
        tag = str(item.get("tag") or "").strip()
        analysis = str(item.get("stage2_analysis") or "").strip()
        summary = str(item.get("summary") or "").strip()
        note_id = _note_id_by_title(stage1_items, title)
        has_video = bool(has_video_by_note_id.get(note_id, False)) if note_id else False

        url = ""
        for it in stage1_items:
            if str(it.get("title") or "").strip() == title:
                url = str(it.get("url") or "").strip()
                break

        # Title (Feishu post text doesn't reliably support bold style; use clear markers)
        blocks.append([{"tag": "text", "text": f"🔷【{title}】"}])

        # Link
        if url:
            blocks.append(
                [
                    {"tag": "text", "text": "🔗 原文链接："},
                    {"tag": "a", "text": "点击打开", "href": url},
                ]
            )

        # Video marker (separate, easy to scan)
        blocks.append([{"tag": "text", "text": f"🎞 内含视频：{'是' if has_video else '否'}"}])

        # Score / Tag / Analysis / Summary with bold labels
        if score != "":
            blocks.append(
                [
                    {"tag": "text", "text": f"⭐ 得分：{score}"},
                ]
            )
        if tag:
            blocks.append(
                [
                    {"tag": "text", "text": f"🏷️ 标签：{tag}"},
                ]
            )
        if analysis:
            blocks.append(
                [
                    {"tag": "text", "text": f"🧠 观点：{analysis}"},
                ]
            )
        if summary:
            blocks.append(
                [
                    {"tag": "text", "text": f"📝 摘要：{summary}"},
                ]
            )

        img_key = image_key_by_title.get(title) or ""
        if img_key:
            blocks.append([{"tag": "img", "image_key": img_key}])

    max_items = 10
    count = 0
    for it in stage2.get("top_items") or []:
        if not isinstance(it, dict):
            continue
        if count >= max_items:
            break
        add_item(it)
        count += 1
    for it in stage2.get("other_items") or []:
        if not isinstance(it, dict):
            continue
        if count >= max_items:
            break
        add_item(it)
        count += 1

    return blocks


def send_latest_rednotes_stage2(config_path: str, run_dir: Optional[str] = None) -> None:
    cfg, _raw = load_rednotes_config(config_path)
    if not cfg.feishu or not cfg.feishu.app_id or not cfg.feishu.app_secret:
        raise RuntimeError("Missing feishu.app_id/app_secret in XHS config")
    chat_id = str(cfg.feishu.receive_id or "").strip()
    if not chat_id:
        raise RuntimeError("Missing feishu.receive_id (chat_id) in XHS config")

    rd = Path(run_dir) if run_dir else find_latest_rednotes_run()
    if not rd:
        raise RuntimeError("No rednotes run folder found under outputs/rednotes")

    stage2 = load_stage2(rd)
    stage1_items = load_stage1_items(rd)
    notes = load_notes(rd)
    has_video_by_note_id = _build_has_video_by_note_id(notes)

    token = get_tenant_access_token(cfg.feishu.app_id, cfg.feishu.app_secret)

    # Upload cover.webp for every item that appears in stage2
    titles: List[str] = []
    for it in stage2.get("top_items") or []:
        if isinstance(it, dict):
            titles.append(str(it.get("title") or "").strip())
    for it in stage2.get("other_items") or []:
        if isinstance(it, dict):
            titles.append(str(it.get("title") or "").strip())

    image_key_by_title: Dict[str, str] = {}
    for title in titles:
        if not title or title in image_key_by_title:
            continue
        cover_path = pick_generated_cover_path(rd, stage1_items, title) or pick_cover_path(stage1_items, title)
        if not cover_path:
            continue
        p = Path(cover_path)
        if not p.exists():
            continue
        image_key_by_title[title] = upload_image(token, p)

    blocks = build_post_blocks(stage2, stage1_items, image_key_by_title, has_video_by_note_id)
    date_str = datetime.now().strftime("%Y-%m-%d")
    _send_post(token, chat_id, title=f"Rednotes 日报 {date_str}", blocks=blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send Rednotes stage2.json to Feishu (Tools-style post: upload then send)")
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_PROJECT_CONFIG,
        help="配置入口（统一配置 configs/config.yaml 或 legacy rednotes.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    parser.add_argument("--run-dir", default="", help="outputs/rednotes/<run> folder (default: latest)")
    args = parser.parse_args()
    try:
        cfg_path = resolve_legacy_config_for_cli(args.config, kind="rednotes", profile=args.profile)
        send_latest_rednotes_stage2(cfg_path, run_dir=args.run_dir or None)
        print("Sent successfully.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Send failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
