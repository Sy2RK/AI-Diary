from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.project_config import get_xhs_runtime_config, load_project_config, sync_legacy_configs
from crawler_rednotes.cli import crawl_profile
from crawler_rednotes.result_writer import create_run_dir, save_notes
from scripts.xhs.rednotes_api_tikhub import (
    _extract_cover_url,
    _extract_image_urls_from_note,
    _extract_share_url,
    _extract_tags,
    _extract_title_text,
    _extract_user_id_from_profile_url,
    _extract_video_media_urls,
    _get_primary_note_item,
    _iter_recent_note_metas,
    fetch_note_info,
    fetch_video_note_info,
)


def _parse_since(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid since format: {value}")


def _parse_publish_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _publish_time_from_note(note_json: Any) -> str:
    primary = _get_primary_note_item(note_json)
    ts = primary.get("time")
    if isinstance(ts, (int, float)) and int(ts) > 0:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    ts2 = primary.get("last_update_time")
    if isinstance(ts2, (int, float)) and int(ts2) > 0:
        return datetime.fromtimestamp(int(ts2)).strftime("%Y-%m-%d %H:%M:%S")
    return ""


@dataclass
class ProviderResult:
    provider: str
    notes: List[Dict[str, Any]]
    error: str = ""


def _fetch_with_tikhub(
    *,
    account_name: str,
    profile_url: str,
    api_key: str,
    limit: int,
    retries: int,
    timeout_sec: int,
    since_dt: Optional[datetime],
) -> ProviderResult:
    user_id = _extract_user_id_from_profile_url(profile_url)
    if not user_id:
        return ProviderResult(provider="tikhub", notes=[], error=f"invalid_profile_url:{profile_url}")
    if not api_key:
        return ProviderResult(provider="tikhub", notes=[], error="missing_tikhub_api_key")

    metas = _iter_recent_note_metas(user_id=user_id, api_key=api_key, limit=limit, retries=retries)
    out: List[Dict[str, Any]] = []
    errors: List[str] = []

    for meta in metas:
        note_id = str(meta.get("note_id") or meta.get("noteId") or meta.get("id") or "").strip()
        if not note_id:
            continue

        detail = None
        last_exc: Optional[Exception] = None
        for _ in range(max(1, retries)):
            try:
                detail = fetch_note_info(note_id=note_id, api_key=api_key, timeout_sec=timeout_sec)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

        if detail is None:
            errors.append(f"{note_id}:note_info:{last_exc}")
            continue

        publish_time = _publish_time_from_note(detail)
        if since_dt is not None:
            pub_dt = _parse_publish_time(publish_time)
            if pub_dt is not None and pub_dt <= since_dt:
                continue

        title, content_text = _extract_title_text(detail)
        source_url = _extract_share_url(detail) or f"https://www.xiaohongshu.com/explore/{note_id}"
        video_urls: List[str] = []
        try:
            video_detail = fetch_video_note_info(note_id=note_id, api_key=api_key, timeout_sec=timeout_sec)
            video_urls = _extract_video_media_urls(video_detail or detail)
        except Exception:
            video_urls = _extract_video_media_urls(detail)

        out.append(
            {
                "note_id": note_id,
                "title": title,
                "content_text": content_text,
                "publish_time": publish_time,
                "source_url": source_url,
                "cover_url": _extract_cover_url(detail),
                "image_urls": _extract_image_urls_from_note(detail),
                "video_urls": video_urls,
                "tags": _extract_tags(detail),
                "error": "",
                "account_name": account_name,
            }
        )

    return ProviderResult(provider="tikhub", notes=out, error="; ".join(errors).strip())


def _fetch_with_playwright(
    *,
    account_name: str,
    profile_url: str,
    storage_state: str,
    headless: bool,
    interval_sec: float,
    limit: int,
    since_dt: Optional[datetime],
) -> ProviderResult:
    try:
        notes = crawl_profile(
            profile_url=profile_url,
            storage_state=storage_state,
            since_note_id=None,
            limit=max(1, int(limit)),
            headless=bool(headless),
            interval_sec=max(0.0, float(interval_sec)),
            mock_data_file="",
        )
    except Exception as exc:  # noqa: BLE001
        return ProviderResult(provider="playwright", notes=[], error=str(exc))

    out: List[Dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        publish_time = str(note.get("publish_time") or "").strip()
        if since_dt is not None:
            dt = _parse_publish_time(publish_time)
            if dt is not None and dt <= since_dt:
                continue
        item = dict(note)
        item["account_name"] = account_name
        out.append(item)
    return ProviderResult(provider="playwright", notes=out, error="")


def run_pipeline(
    *,
    config_path: str,
    profile: str = "",
    outputs_root_override: str = "",
    no_download_images: bool = False,
    no_llm: bool = False,
    legacy_rednotes_path: str = "configs/legacy/rednotes.yaml",
) -> int:
    # Keep legacy scripts usable by syncing from unified config first.
    sync_legacy_configs(config_path, profile=profile, rednotes_out=legacy_rednotes_path)

    cfg, selected_profile = load_project_config(config_path, profile=profile)
    runtime = get_xhs_runtime_config(cfg)
    since_dt = _parse_since(runtime["since"]) if runtime["since"] else None

    outputs_root = Path(outputs_root_override.strip() or runtime["outputs_root"]) / "rednotes"
    run_dir = create_run_dir(outputs_root, date_str=datetime.now().strftime("%Y%m%d"))
    print(f"[xhs] profile={selected_profile or 'default'} run_dir={run_dir}")

    provider_order = runtime["provider_order"] or ["tikhub", "playwright"]
    fallback_enabled = bool(runtime["fallback_enabled"])
    fallback_retry_times = max(0, int(runtime["fallback_retry_times"]))

    all_notes: List[Dict[str, Any]] = []
    provider_manifest: List[Dict[str, Any]] = []

    for account in runtime["accounts"]:
        name = str(account.get("name") or "").strip()
        url = str(account.get("url") or "").strip()
        if not name or not url:
            continue
        print(f"[xhs] account={name} provider_order={provider_order}")

        account_notes: List[Dict[str, Any]] = []
        account_errors: List[str] = []
        provider_used = ""

        for idx, provider in enumerate(provider_order):
            retry_budget = 1
            if idx > 0 and fallback_enabled:
                retry_budget += fallback_retry_times
            elif idx > 0 and not fallback_enabled:
                break

            result = ProviderResult(provider=provider, notes=[], error=f"unsupported_provider:{provider}")
            for _ in range(retry_budget):
                if provider == "tikhub":
                    result = _fetch_with_tikhub(
                        account_name=name,
                        profile_url=url,
                        api_key=runtime["tikhub_api_key"],
                        limit=runtime["maxcrawl"],
                        retries=runtime["tikhub_retries"],
                        timeout_sec=runtime["tikhub_timeout_sec"],
                        since_dt=since_dt,
                    )
                elif provider == "playwright":
                    result = _fetch_with_playwright(
                        account_name=name,
                        profile_url=url,
                        storage_state=runtime["playwright_storage_state"],
                        headless=runtime["playwright_headless"],
                        interval_sec=runtime["playwright_interval_sec"],
                        limit=runtime["maxcrawl"],
                        since_dt=since_dt,
                    )
                if result.notes:
                    break

            if result.notes:
                account_notes = result.notes
                provider_used = result.provider
                break
            if result.error:
                account_errors.append(f"{result.provider}:{result.error}")

        all_notes.extend(account_notes)
        provider_manifest.append(
            {
                "account": name,
                "profile_url": url,
                "provider_used": provider_used,
                "notes_count": len(account_notes),
                "errors": account_errors,
            }
        )

    saved = save_notes(run_dir, all_notes, download_images_enabled=not no_download_images)
    (run_dir / "provider_manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "provider_order": provider_order,
                "fallback_enabled": fallback_enabled,
                "items": provider_manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"[xhs] saved={len(saved)} notes")

    if not no_llm:
        try:
            from scripts.xhs.llm_xhs_qwen import stage1_analyze_run, stage2_summarize_run

            stage1 = stage1_analyze_run(run_dir, legacy_rednotes_path, limit=0)
            stage2 = stage2_summarize_run(run_dir, legacy_rednotes_path)
            print(f"[xhs] stage1={stage1}")
            print(f"[xhs] stage2={stage2}")
        except Exception as exc:  # noqa: BLE001
            print(f"[xhs] llm_failed={exc}", file=sys.stderr)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified XHS pipeline (TikHub primary + Playwright fallback).")
    parser.add_argument("--config", default=str(Path("configs") / "config.yaml"))
    parser.add_argument("--profile", default="")
    parser.add_argument("--outputs", default="", help="Override outputs root (default from config.paths.outputs_root)")
    parser.add_argument("--legacy-rednotes", default="configs/legacy/rednotes.yaml", help="Path to generated legacy rednotes config")
    parser.add_argument("--no-download-images", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    try:
        return run_pipeline(
            config_path=args.config,
            profile=args.profile,
            outputs_root_override=args.outputs,
            no_download_images=bool(args.no_download_images),
            no_llm=bool(args.no_llm),
            legacy_rednotes_path=args.legacy_rednotes,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"pipeline_failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
