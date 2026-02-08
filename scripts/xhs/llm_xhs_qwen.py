import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_rednotes.config_rednotes import load_rednotes_config
from crawler_rednotes.llm.qwen_client import QwenClient, parse_json_output
from crawler_rednotes.llm.qwen_client import image_file_to_data_url as shared_image_file_to_data_url
from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli


def strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip().strip("`").strip()
    return s


def _resolve_qwen_settings(cfg) -> Tuple[str, str, str, int, bool]:
    api_key = (cfg.qwen.api_key if cfg.qwen else "").strip() or os.environ.get("QWEN_API_KEY", "").strip() or os.environ.get("DASHSCOPE_API_KEY", "").strip()
    base_url = (cfg.qwen.base_url if cfg.qwen else "").strip() or os.environ.get("QWEN_API_URL", "").strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = (cfg.qwen.model if cfg.qwen else "").strip() or os.environ.get("QWEN_MODEL", "").strip() or "qwen3-vl-plus"
    timeout_sec = int(cfg.qwen.timeout_sec if cfg.qwen else int(os.environ.get("QWEN_TIMEOUT_SEC", "120")))
    enable_thinking = bool(cfg.qwen.enable_thinking) if cfg.qwen is not None else False
    if not api_key:
        raise RuntimeError("Missing Qwen API key. Set xhs.qwen.api_key in unified config or env QWEN_API_KEY/DASHSCOPE_API_KEY")
    return api_key, base_url, model, timeout_sec, enable_thinking


def parse_json_obj(text: str) -> Dict[str, Any]:
    # Use shared robust parser (handles ``` fences).
    obj = parse_json_output(text)
    if not isinstance(obj, dict):
        raise ValueError("Expected JSON object")
    return obj


def parse_json_array(text: str) -> List[Any]:
    s = strip_code_fences(text)
    obj = json.loads(s)
    if not isinstance(obj, list):
        raise ValueError("Expected JSON array")
    return obj


def find_latest_rednotes_run(outputs_root: str = "outputs/rednotes") -> Optional[Path]:
    root = Path(outputs_root)
    if not root.exists():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def load_saved_notes(run_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    notes_path = run_dir / "notes.json"
    if not notes_path.exists():
        raise FileNotFoundError(f"notes.json not found in {run_dir}")
    data = json.loads(notes_path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    raw_notes = data.get("raw_notes") or []
    if not isinstance(items, list):
        items = []
    if not isinstance(raw_notes, list):
        raw_notes = []
    raw_by_id: Dict[str, Dict[str, Any]] = {}
    for rn in raw_notes:
        if not isinstance(rn, dict):
            continue
        nid = str(rn.get("note_id") or "").strip()
        if nid:
            raw_by_id[nid] = rn
    return [it for it in items if isinstance(it, dict)], raw_by_id


def _pick_best_video_url(video_urls: Any) -> str:
    urls: List[str] = []
    if isinstance(video_urls, list):
        urls = [str(u).strip() for u in video_urls if str(u).strip()]
    if not urls:
        return ""

    # Prefer mp4 candidates.
    mp4s = [u for u in urls if ".mp4" in u.lower()]
    cand = mp4s or urls

    def quality(u: str) -> int:
        m = re.search(r"_(\\d+)\\.mp4", u, flags=re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return 0
        m2 = re.search(r"/110/(\\d+)/", u)
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                return 0
        return 0

    best = max(cand, key=quality)
    return str(best).strip()


def build_stage1_messages(
    system_prompt: str,
    user_text: str,
    cover_path: Optional[str],
    video_url: str = "",
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if not cover_path and not (video_url or "").strip():
        messages.append({"role": "user", "content": user_text})
        return messages
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    try:
        if cover_path:
            content.append({"type": "image_url", "image_url": {"url": shared_image_file_to_data_url(cover_path)}})
    except Exception:
        pass
    vurl = (video_url or "").strip()
    if vurl:
        content.append({"type": "video_url", "video_url": {"url": vurl}})
    messages.append({"role": "user", "content": content})
    return messages


def ensure_analysis_dir(run_dir: Path) -> Path:
    analysis = run_dir / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    return analysis


def stage1_analyze_run(
    run_dir: Path,
    cfg_path: str,
    limit: int = 0,
) -> Path:
    cfg, _raw = load_rednotes_config(cfg_path)
    prompt = (cfg.prompt_stage1_xhs or "").strip()
    if not prompt:
        raise RuntimeError("prompt_stage1_xhs is empty in XHS config")
    api_key, base_url, model, timeout_sec, enable_thinking = _resolve_qwen_settings(cfg)
    client = QwenClient(api_key=api_key, base_url=base_url, model=model, timeout_sec=timeout_sec)

    analysis_dir = ensure_analysis_dir(run_dir)
    out_path = analysis_dir / "stage1.json"

    notes, raw_by_id = load_saved_notes(run_dir)
    if limit and limit > 0:
        notes = notes[:limit]

    results: List[Dict[str, Any]] = []
    for idx, it in enumerate(notes, start=1):
        text_path = str(it.get("text_path") or "")
        cover_path = str(it.get("cover_path") or "")
        title = str(it.get("title") or "").strip()
        url = str(it.get("source_url") or "").strip()
        note_id = str(it.get("note_id") or "").strip()
        raw_note = raw_by_id.get(note_id) or {}
        video_url = _pick_best_video_url(raw_note.get("video_urls"))

        if not text_path or not Path(text_path).exists():
            continue

        user_text = Path(text_path).read_text(encoding="utf-8")
        messages = build_stage1_messages(
            prompt,
            user_text,
            cover_path if cover_path and Path(cover_path).exists() else None,
            video_url=video_url,
        )

        try:
            resp = client.chat_vl(messages, enable_thinking=enable_thinking)
            parsed = parse_json_obj(str(resp.get("content") or ""))
        except Exception as exc:  # noqa: BLE001
            parsed = {"summary": f"跳过原因：分析失败 {exc}", "score": 0, "tag": "分析失败"}

        obj = {
            "note_id": note_id,
            "title": parsed.get("title", title),
            "url": parsed.get("url", url) or url,
            "summary": str(parsed.get("summary", "")).strip(),
            "score": parsed.get("score", 0),
            "tag": str(parsed.get("tag", "")).strip(),
            "cover_path": cover_path,
            "text_path": text_path,
        }
        results.append(obj)
        suffix = " (video)" if video_url else ""
        print(f"[stage1] {idx}/{len(notes)} done: {title}{suffix}")

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def stage2_summarize_run(run_dir: Path, cfg_path: str) -> Path:
    cfg, _raw = load_rednotes_config(cfg_path)
    prompt = (cfg.prompt_stage2_xhs or "").strip()
    if not prompt:
        raise RuntimeError("prompt_stage2_xhs is empty in XHS config")
    api_key, base_url, model, timeout_sec, enable_thinking = _resolve_qwen_settings(cfg)
    client = QwenClient(api_key=api_key, base_url=base_url, model=model, timeout_sec=timeout_sec)

    analysis_dir = ensure_analysis_dir(run_dir)
    stage1_path = analysis_dir / "stage1.json"
    if not stage1_path.exists():
        raise FileNotFoundError(f"stage1.json not found: {stage1_path}")

    stage1_items = json.loads(stage1_path.read_text(encoding="utf-8"))
    content = json.dumps(stage1_items, ensure_ascii=False, indent=2)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]

    resp = client.chat_vl(messages, enable_thinking=enable_thinking)
    stage2 = parse_json_obj(str(resp.get("content") or ""))
    out_path = analysis_dir / "stage2.json"
    out_path.write_text(json.dumps(stage2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Qwen stage1+stage2 analysis for Rednotes outputs")
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_PROJECT_CONFIG,
        help="配置入口（统一配置 configs/config.yaml 或 legacy rednotes.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    parser.add_argument("--run-dir", default="", help="outputs/rednotes/<run> folder (default: latest)")
    parser.add_argument("--limit", type=int, default=0, help="limit notes for stage1 (0 = all)")
    parser.add_argument("--stage2-only", action="store_true", help="only run stage2 (requires stage1.json)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else find_latest_rednotes_run()
    if not run_dir:
        print("No rednotes run folder found under outputs/rednotes", file=sys.stderr)
        return 2
    print(f"Using run_dir: {run_dir}")

    try:
        cfg_path = resolve_legacy_config_for_cli(args.config, kind="rednotes", profile=args.profile)
        if not args.stage2_only:
            stage1_path = stage1_analyze_run(run_dir, cfg_path, limit=args.limit)
            print(f"Saved stage1 to {stage1_path}")
        stage2_path = stage2_summarize_run(run_dir, cfg_path)
        print(f"Saved stage2 to {stage2_path}")
        # Best-effort: export unified web JSON (ReportDocument)
        try:
            from scripts.integrations.web_export import export_report_documents

            export_report_documents(Path("web_output") / "report_documents.json", xhs_stage2=stage2_path)
        except Exception:
            pass
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"XHS LLM failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
