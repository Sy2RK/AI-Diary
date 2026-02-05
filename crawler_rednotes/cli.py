import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config_rednotes import RednotesConfig, load_rednotes_config
from .result_writer import create_run_dir, save_notes
from .xhs_client import BrowserXHSClient, MockXHSClient
from .yaml_patch import add_account as yaml_add_account
from .yaml_patch import delete_account_by_index as yaml_delete_account_by_index
from .yaml_patch import set_scalar as yaml_set_scalar


def _resolve_default_storage_state() -> str:
    return str(Path(__file__).resolve().parent / "storage_state.json")


def crawl_profile(
    profile_url: str,
    storage_state: str,
    since_note_id: Optional[str] = None,
    limit: int = 50,
    headless: bool = False,
    interval_sec: float = 1.5,
    mock_data_file: str = "",
) -> List[Dict[str, Any]]:
    if mock_data_file:
        client = MockXHSClient(mock_data_file)
    else:
        client = BrowserXHSClient(storage_state_path=storage_state, headless=headless, interval_sec=interval_sec)

    notes = client.fetch_notes(profile_url, since_note_id=since_note_id, limit=limit)
    return [n.to_dict() for n in notes]


def _parse_since(value: str) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    raise ValueError("invalid_since")


def _parse_publish_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip()
    # If we only have a date (no time), treat it as end-of-day to avoid dropping same-day items.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        dt = datetime.strptime(v, "%Y-%m-%d")
        return dt.replace(hour=23, minute=59, second=59)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _resolve_default_config_path() -> str:
    # Crawler/rednotes.yaml (repo root under Crawler/)
    return str(Path(__file__).resolve().parent.parent / "rednotes.yaml")


def _print_accounts(accounts) -> None:
    if not accounts:
        print("No accounts in rednotes.yaml")
        return
    for idx, item in enumerate(accounts, start=1):
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        print(f"{idx}. {name}: {url}")


def main(_unused: object = None) -> int:
    parser = argparse.ArgumentParser(description="Xiaohongshu (Rednotes) crawler - terminal mode")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add account profile (name + url) to rednotes.yaml")
    p_add.add_argument("-c", "--config", default=_resolve_default_config_path())
    p_add.add_argument("url")
    p_add.add_argument("name", nargs="?", default="")

    p_del = sub.add_parser("delete", help="Delete account profile by index from rednotes.yaml")
    p_del.add_argument("-c", "--config", default=_resolve_default_config_path())

    p_list = sub.add_parser("list", help="List account profiles in rednotes.yaml")
    p_list.add_argument("-c", "--config", default=_resolve_default_config_path())

    p_crawl = sub.add_parser("crawl", help="Crawl a profile and save text/images to outputs/")
    p_crawl.add_argument("profile_url", help="Xiaohongshu profile url (must include /user/profile/<id>)")
    p_crawl.add_argument(
        "--storage-state",
        default=_resolve_default_storage_state(),
        help="Playwright storage_state.json path (default: crawler_rednotes/storage_state.json)",
    )
    p_crawl.add_argument("--since-note-id", default="", help="Only return notes after the given note_id (optional)")
    p_crawl.add_argument("--limit", type=int, default=50, help="Max notes to fetch (default: 50)")
    p_crawl.add_argument("--headless", action="store_true", help="Run browser in headless mode (default: false)")
    p_crawl.add_argument("--no-download-images", action="store_true", help="Do not download images; save text only")
    p_crawl.add_argument("--mock-data", default="", help="Use mock JSON data file instead of browser (dev only)")
    p_crawl.add_argument(
        "--outputs-dir",
        default="outputs/rednotes",
        help="Outputs root folder (default: outputs/rednotes)",
    )

    p_crawl_all = sub.add_parser("crawl-all", help="Crawl all profiles in rednotes.yaml (ordered)")
    p_crawl_all.add_argument("-c", "--config", default=_resolve_default_config_path())
    p_crawl_all.add_argument(
        "--storage-state",
        default=_resolve_default_storage_state(),
        help="Playwright storage_state.json path (default: crawler_rednotes/storage_state.json)",
    )
    p_crawl_all.add_argument("--headless", action="store_true", help="Run browser in headless mode (default: false)")
    p_crawl_all.add_argument("--no-download-images", action="store_true", help="Do not download images; save text only")
    p_crawl_all.add_argument(
        "--outputs-dir",
        default="outputs/rednotes",
        help="Outputs root folder (default: outputs/rednotes)",
    )
    p_crawl_all.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM analysis (default: run multimodal analysis and append to text)",
    )

    args = parser.parse_args()

    if args.cmd == "add":
        url = str(args.url).strip()
        if not url:
            print("Profile url is empty.")
            return 2
        name = str(args.name).strip()
        if not name:
            try:
                name = input("Enter name for this profile: ").strip()
            except EOFError:
                return 2
        if not name:
            print("Name is empty.")
            return 2
        yaml_add_account(args.config, name=name, url=url)
        print(f"Added: {name} -> {url}")
        return 0

    if args.cmd == "list":
        cfg, _raw = load_rednotes_config(args.config)
        _print_accounts(cfg.accounts or {})
        return 0

    if args.cmd == "delete":
        cfg, _raw = load_rednotes_config(args.config)
        accounts = cfg.accounts or []
        if not accounts:
            print("No accounts to delete.")
            return 0
        _print_accounts(accounts)
        try:
            choice = input("Enter index to delete: ").strip()
        except EOFError:
            return 2
        if not choice.isdigit():
            print("Invalid index.")
            return 2
        idx = int(choice)
        if idx < 1 or idx > len(accounts):
            print("Index out of range.")
            return 2
        removed = accounts[idx - 1]
        name = str(removed.get("name") or "")
        url = str(removed.get("url") or "")
        if not yaml_delete_account_by_index(args.config, idx):
            print("Delete failed (accounts not found or index mismatch).", file=sys.stderr)
            return 1
        print(f"Deleted: {name} -> {url}")
        return 0

    if args.cmd == "crawl":
        since_note_id = args.since_note_id.strip() or None
        outputs_root = Path(args.outputs_dir)
        date_str = datetime.now().strftime("%Y%m%d")
        run_dir = create_run_dir(outputs_root, date_str=date_str)
        print(f"Outputs folder: {run_dir}")

        try:
            notes = crawl_profile(
                profile_url=args.profile_url,
                storage_state=args.storage_state,
                since_note_id=since_note_id,
                limit=max(1, int(args.limit)),
                headless=bool(args.headless),
                mock_data_file=args.mock_data,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Crawl failed: {exc}", file=sys.stderr)
            return 1

        saved = save_notes(run_dir, notes, download_images_enabled=not args.no_download_images)
        print(f"Saved {len(saved)} notes to {run_dir}")
        return 0

    if args.cmd == "crawl-all":
        cfg, _raw = load_rednotes_config(args.config)
        cfg.accounts = cfg.accounts or []
        cfg.qwen = cfg.qwen or RednotesConfig().qwen
        if not cfg.accounts:
            print(f"No accounts configured in {args.config}. Use: python -m crawler_rednotes add <url> <name>")
            return 2

        try:
            since_in = input(f"Enter since (YYYY-MM-DD) [{cfg.since or ''}]: ").strip()
        except EOFError:
            return 2
        if since_in:
            cfg.since = since_in
            yaml_set_scalar(args.config, ["since"], since_in)

        try:
            max_in = input(f"Enter maxcrawl [{cfg.maxcrawl}]: ").strip()
        except EOFError:
            return 2
        if max_in:
            try:
                cfg.maxcrawl = int(max_in)
            except ValueError:
                print("Invalid maxcrawl, must be integer.", file=sys.stderr)
                return 2
            yaml_set_scalar(args.config, ["maxcrawl"], str(cfg.maxcrawl))

        try:
            interval_in = input(f"Enter interval_sec [{cfg.interval_sec}]: ").strip()
        except EOFError:
            return 2
        if interval_in:
            try:
                cfg.interval_sec = float(interval_in)
            except ValueError:
                print("Invalid interval_sec, must be number.", file=sys.stderr)
                return 2
            yaml_set_scalar(args.config, ["interval_sec"], str(cfg.interval_sec))

        if cfg.qwen and not cfg.qwen.api_key and not args.no_llm:
            try:
                cfg.qwen.api_key = input("Enter Qwen api_key: ").strip()
            except EOFError:
                return 2
            if cfg.qwen.api_key:
                yaml_set_scalar(args.config, ["qwen", "api_key"], cfg.qwen.api_key)

        if cfg.qwen and not args.no_llm:
            os.environ["QWEN_API_KEY"] = cfg.qwen.api_key
            os.environ["DASHSCOPE_API_KEY"] = cfg.qwen.api_key
            os.environ["QWEN_API_URL"] = cfg.qwen.base_url
            os.environ["QWEN_MODEL"] = cfg.qwen.model
            os.environ["QWEN_TIMEOUT_SEC"] = str(cfg.qwen.timeout_sec)

        outputs_root = Path(args.outputs_dir)
        date_str = datetime.now().strftime("%Y%m%d")
        run_dir = create_run_dir(outputs_root, date_str=date_str)
        print(f"Outputs folder: {run_dir}")

        since_dt = None
        try:
            since_dt = _parse_since(cfg.since)
        except ValueError:
            print("Invalid `since` format in rednotes.yaml, expected YYYY-MM-DD", file=sys.stderr)
            return 2

        all_notes: List[Dict[str, Any]] = []
        for item in cfg.accounts:
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if not name or not url:
                continue
            print(f"\n[crawl] {name}: {url}")
            try:
                notes = crawl_profile(
                    profile_url=url,
                    storage_state=args.storage_state,
                    since_note_id=None,
                    limit=max(1, int(cfg.maxcrawl)),
                    headless=bool(args.headless),
                    interval_sec=max(0.0, float(cfg.interval_sec)),
                    mock_data_file="",
                )
            except Exception as exc:  # noqa: BLE001
                print(f"Crawl failed for {name}: {exc}", file=sys.stderr)
                continue
            if since_dt is not None:
                filtered = []
                skipped_no_time = 0
                skipped_before_since = 0
                for n in notes:
                    dt = _parse_publish_time(str(n.get("publish_time") or ""))
                    if dt is None:
                        skipped_no_time += 1
                        continue
                    if dt > since_dt:
                        filtered.append(n)
                    else:
                        skipped_before_since += 1
                notes = filtered
                print(
                    f"[filter] since={cfg.since} kept={len(notes)} skipped_before={skipped_before_since} skipped_no_time={skipped_no_time}"
                )
            for n in notes:
                n["account_name"] = name
            all_notes.extend(notes)

        saved = save_notes(run_dir, all_notes, download_images_enabled=not args.no_download_images)
        print(f"Saved {len(saved)} notes to {run_dir}")

        if not args.no_llm:
            from .llm.analyzer import analyze_note, analyze_note_multimodal

            for s in saved:
                text_path = Path(s.text_path)
                image_paths = []
                if s.cover_path:
                    image_paths.append(s.cover_path)
                image_paths.extend(s.image_paths)
                try:
                    if image_paths:
                        result = analyze_note_multimodal(s.note_id, s.title, s.content_text, image_paths)
                    else:
                        result = analyze_note(s.title, s.content_text)
                except Exception as exc:  # noqa: BLE001
                    text_path.open("a", encoding="utf-8").write(f"\n\n[AI分析失败] {exc}\n")
                    continue
                text_path.open("a", encoding="utf-8").write("\n\n[AI分析]\n")
                text_path.open("a", encoding="utf-8").write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
