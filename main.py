import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or default


def _run_py(rel_script: str, args: list[str]) -> int:
    script_path = Path(__file__).resolve().parent / rel_script
    if not script_path.exists():
        print(f"Script not found: {script_path}")
        return 2
    cmd = [sys.executable, str(script_path), *args]
    return subprocess.call(cmd)


def _run_module(module: str, args: list[str]) -> int:
    cmd = [sys.executable, "-m", module, *args]
    return subprocess.call(cmd)


def _find_latest_wechat_stage2_json(outputs_dir: str = "outputs") -> Optional[str]:
    root = Path(outputs_dir)
    if not root.exists():
        return None

    # Prefer new layout: outputs/wechat/**/_stage2.json
    wechat_root = root / "wechat"
    if not wechat_root.exists():
        return None

    candidates = list(wechat_root.rglob("*_stage2.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _try_bitable_sync(args: list[str]) -> None:
    try:
        code = _run_py("bitable_sync.py", args)
        if code == 0:
            print("Bitable sync: OK")
        else:
            print(f"Bitable sync: skipped/failed (exit={code})")
    except Exception as exc:  # noqa: BLE001
        print(f"Bitable sync: skipped/failed ({exc})")


def _try_cover_gen(args: list[str]) -> None:
    try:
        code = _run_py("cover_gen.py", args)
        if code == 0:
            print("Cover gen: OK")
        else:
            print(f"Cover gen: skipped/failed (exit={code})")
    except Exception as exc:  # noqa: BLE001
        print(f"Cover gen: skipped/failed ({exc})")


def _resolve_rednotes_run_dir(run_dir: str) -> str:
    """
    Accept either:
    - "" (latest)
    - full/relative path to outputs/rednotes/<run>
    - bare run folder name like "20260130-2"
    """
    s = (run_dir or "").strip()
    if not s:
        return ""
    p = Path(s)
    if p.exists():
        return str(p)
    candidate = Path("outputs") / "rednotes" / s
    if candidate.exists():
        return str(candidate)
    return s


def _build_bitable_link(app_token: str, table_id: str) -> str:
    at = (app_token or "").strip()
    tid = (table_id or "").strip()
    if not at:
        return ""
    # Public-facing base link works in most tenants; table_id helps opening the correct table directly.
    if tid:
        return f"https://www.feishu.cn/base/{at}?table={tid}"
    return f"https://www.feishu.cn/base/{at}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawler CLI (WeChat + Rednotes)")
    sub = parser.add_subparsers(dest="cmd")

    p_wc = sub.add_parser("wechat-crawl", help="WeChat crawl -> paper.json")
    p_wc.add_argument("-c", "--config", default="wechat.yaml")
    p_wc.add_argument("-o", "--output", default="paper.json")

    p_wl = sub.add_parser("wechat-llm", help="WeChat LLM (Qwen) -> outputs/wechat/YYYYMMDD[-N]/")
    p_wl.add_argument("-c", "--config", default="wechat.yaml")
    p_wl.add_argument("-i", "--input", default="paper.json")
    p_wl.add_argument("-o", "--output", default="")

    p_wp = sub.add_parser("wechat-feishu", help="Feishu push (from JSON)")
    p_wp.add_argument("-i", "--input", required=True)
    p_wp.add_argument("--webhook", default="")

    p_rl = sub.add_parser("rednotes-login", help="Rednotes login (save storage_state.json)")
    p_rl.add_argument(
        "-o",
        "--output",
        default=str(Path("crawler_rednotes") / "storage_state.json"),
    )

    p_rc = sub.add_parser("rednotes-crawl", help="Rednotes crawl profile -> outputs/rednotes/YYYYMMDD[-N]/")
    p_rc.add_argument("profile_url")
    p_rc.add_argument(
        "--storage-state",
        default=str(Path("crawler_rednotes") / "storage_state.json"),
    )
    p_rc.add_argument("--limit", type=int, default=50)
    p_rc.add_argument("--headless", action="store_true")
    p_rc.add_argument("--no-download-images", action="store_true")

    p_ra = sub.add_parser("rednotes-add", help="Rednotes add account profile to rednotes.yaml")
    p_ra.add_argument("url")
    p_ra.add_argument("name", nargs="?", default="")
    p_ra.add_argument("-c", "--config", default="rednotes.yaml")

    p_rd = sub.add_parser("rednotes-delete", help="Rednotes delete account profile by index")
    p_rd.add_argument("-c", "--config", default="rednotes.yaml")

    p_rall = sub.add_parser("rednotes-crawl-all", help="Rednotes crawl all profiles in rednotes.yaml")
    p_rall.add_argument("-c", "--config", default="rednotes.yaml")
    p_rall.add_argument(
        "--storage-state",
        default=str(Path("crawler_rednotes") / "storage_state.json"),
    )
    p_rall.add_argument("--headless", action="store_true")
    p_rall.add_argument("--no-download-images", action="store_true")
    p_rall.add_argument("--no-llm", action="store_true")

    p_wcom_wc = sub.add_parser("wecom-wechat", help="WeCom bot push (latest WeChat stage2)")
    p_wcom_wc.add_argument("--webhook", default="")
    p_wcom_wc.add_argument("--outputs", default="outputs")
    p_wcom_wc.add_argument("--limit", type=int, default=10)

    p_wcom_xhs = sub.add_parser("wecom-xhs", help="WeCom bot push (latest Rednotes stage2)")
    p_wcom_xhs.add_argument("--webhook", default="")
    p_wcom_xhs.add_argument("--outputs", default="outputs")
    p_wcom_xhs.add_argument("--limit", type=int, default=10)

    p_wcom_all = sub.add_parser("wecom-all", help="WeCom bot push (latest WeChat + Rednotes)")
    p_wcom_all.add_argument("--webhook", default="")
    p_wcom_all.add_argument("--outputs", default="outputs")
    p_wcom_all.add_argument("--limit", type=int, default=10)

    args, unknown = parser.parse_known_args()

    if args.cmd == "wechat-crawl":
        return _run_py("wechat_crawler.py", ["-c", args.config, "-o", args.output])
    if args.cmd == "wechat-llm":
        a = ["-c", args.config, "-i", args.input]
        if args.output:
            a += ["-o", args.output]
        return _run_py("llm_qwen.py", a)
    if args.cmd == "wechat-feishu":
        # If webhook is provided, keep legacy workflow (simple webhook post).
        if args.webhook:
            a = ["-i", args.input, "--webhook", args.webhook]
            return _run_py("feishu_formatter.py", a)

        # New workflow: generate per-item covers and send rich post via OpenAPI.
        _try_cover_gen(["wechat", "--stage2", args.input, "-c", "rednotes.yaml", "--cover-config", "cover_gen.yaml"])
        return _run_py("wechat_feishu_sender.py", ["--stage2", args.input, "-c", "rednotes.yaml"])
    if args.cmd == "rednotes-login":
        return _run_py(str(Path("crawler_rednotes") / "login_xhs.py"), ["-o", args.output])
    if args.cmd == "rednotes-crawl":
        a = [
            "crawl",
            args.profile_url,
            "--storage-state",
            args.storage_state,
            "--limit",
            str(args.limit),
        ]
        if args.headless:
            a.append("--headless")
        if args.no_download_images:
            a.append("--no-download-images")
        return _run_module("crawler_rednotes", a)
    if args.cmd == "rednotes-add":
        a = ["add", "-c", args.config, args.url]
        if args.name:
            a.append(args.name)
        return _run_module("crawler_rednotes", a)
    if args.cmd == "rednotes-delete":
        return _run_module("crawler_rednotes", ["delete", "-c", args.config])
    if args.cmd == "rednotes-crawl-all":
        a = ["crawl-all", "-c", args.config, "--storage-state", args.storage_state]
        if args.headless:
            a.append("--headless")
        if args.no_download_images:
            a.append("--no-download-images")
        if args.no_llm:
            a.append("--no-llm")
        return _run_module("crawler_rednotes", a)
    if args.cmd == "wecom-wechat":
        a = ["wechat", "--outputs", args.outputs, "--limit", str(int(args.limit))]
        if args.webhook:
            a += ["--webhook", args.webhook]
        return _run_py("wecom_sender.py", a)
    if args.cmd == "wecom-xhs":
        a = ["xhs", "--outputs", args.outputs, "--limit", str(int(args.limit))]
        if args.webhook:
            a += ["--webhook", args.webhook]
        return _run_py("wecom_sender.py", a)
    if args.cmd == "wecom-all":
        a = ["all", "--outputs", args.outputs, "--limit", str(int(args.limit))]
        if args.webhook:
            a += ["--webhook", args.webhook]
        return _run_py("wecom_sender.py", a)

    if args.cmd is not None:
        print(f"Unknown command: {args.cmd}", file=sys.stderr)
        return 2

    if not sys.stdin.isatty():
        parser.print_help()
        return 2

    while True:
        print("\n=== Crawler CLI ===")
        print("\n=== Tips：输入数字以选择，默认选项会标出，保持默认请直接按Enter ===")
        print("1) WeChat: 开始爬取微信公众号，结果将储存在paper.json中")
        print("2) WeChat: 开始调用AI进行分析 (Update to Qwen.Ver) ")
        print("3) WeChat: 推送到飞书 ")
        print("4) Rednotes: 请在打开的浏览器中登陆小红书账号，登陆完成后，请按回车键，浏览器会自动关闭")
        print("5) Rednotes: 添加小红书账户")
        print("6) Rednotes: 删除小红书账户")
        print("7) Rednotes: 开始爬取小红书，结果将存储在outputs文件夹中")
        print("8) Rednotes: AI二次分析并保存到本地")
        print("9) Rednotes: 推送到飞书(含图片，默认最新)")
        print("10) 同步多维表格（不推送消息）")
        print("11) 企业微信Bot: 推送最新(公众号+小红书)")
        print("12) Rednotes(API): 使用TikHub API按rednotes.yaml抓取最新内容（无需浏览器）")
        print("0) 退出")
        choice = _prompt("Select", default="0")

        if choice == "0":
            return 0

        if choice == "1":
            cfg = _prompt("wechat.yaml path", default="wechat.yaml")
            out = _prompt("output json filename", default="paper.json")
            code = _run_py("wechat_crawler.py", ["-c", cfg, "-o", out])
            print(f"Exit code: {code}")
            continue

        if choice == "2":
            cfg = _prompt("wechat.yaml path", default="wechat.yaml")
            inp = _prompt("input json filename", default="paper.json")
            out = _prompt("output filename (leave empty for default)", default="")
            args = ["-c", cfg, "-i", inp]
            if out:
                args += ["-o", out]
            code = _run_py("llm_qwen.py", args)
            print(f"Exit code: {code}")
            continue

        if choice == "3":
            inp = _find_latest_wechat_stage2_json("outputs")
            if not inp:
                print("No *_stage2.json found under outputs/wechat/. Run LLM stage2 first.")
                continue
            print(f"Using latest stage2 file: {inp}")

            # Generate cover images and send rich post via OpenAPI (same style as Rednotes).
            _try_cover_gen(["wechat", "--stage2", inp, "-c", "rednotes.yaml", "--cover-config", "cover_gen.yaml"])
            code = _run_py("wechat_feishu_sender.py", ["--stage2", inp, "-c", "rednotes.yaml"])
            print(f"Exit code: {code}")
            if code == 0:
                _try_bitable_sync(["wechat", "-c", "rednotes.yaml", "--stage2", inp])
            continue

        if choice == "4":
            out = _prompt(
                "登陆状态存储路径，默认路径已填入，请按回车继续",
                default=str(Path("crawler_rednotes") / "storage_state.json"),
            )
            code = _run_py(str(Path("crawler_rednotes") / "login_xhs.py"), ["-o", out])
            print(f"Exit code: {code}")
            continue

        if choice == "5":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")
            url = _prompt("profile url")
            name = _prompt("name (optional, can leave empty)", default="")
            args = ["add", "-c", cfg, url]
            if name:
                args.append(name)
            code = _run_module("crawler_rednotes", args)
            print(f"Exit code: {code}")
            continue

        if choice == "6":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")
            code = _run_module("crawler_rednotes", ["delete", "-c", cfg])
            print(f"Exit code: {code}")
            continue

        if choice == "7":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")
            storage = _prompt(
                "storage_state.json path",
                default=str(Path("crawler_rednotes") / "storage_state.json"),
            )
            headless = _prompt("headless? (y/N)", default="N").lower().startswith("y")
            no_dl = _prompt("download images? (Y/n)", default="Y").lower().startswith("n")
            no_llm = _prompt("run LLM analysis? (Y/n)", default="Y").lower().startswith("n")
            args = ["crawl-all", "-c", cfg, "--storage-state", storage]
            if headless:
                args.append("--headless")
            if no_dl:
                args.append("--no-download-images")
            if no_llm:
                args.append("--no-llm")
            code = _run_module("crawler_rednotes", args)
            print(f"Exit code: {code}")
            continue

        if choice == "8":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")
            run_dir = _prompt("run dir (leave empty for latest)", default="")
            args = ["-c", cfg]
            if run_dir:
                args += ["--run-dir", run_dir]
            code = _run_py("llm_xhs_qwen.py", args)
            print(f"Stage1/2 Exit code: {code}")
            continue

        if choice == "9":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")
            run_dir = _prompt("run dir (leave empty for latest)", default="")
            resolved_run_dir = _resolve_rednotes_run_dir(run_dir)

            # Ensure receive_id is set for Feishu open platform sending
            try:
                from crawler_rednotes.config_rednotes import load_rednotes_config
                from crawler_rednotes.yaml_patch import set_scalar as yaml_set_scalar

                cfg_obj, _raw = load_rednotes_config(cfg)
                if cfg_obj.feishu and not cfg_obj.feishu.receive_id:
                    rid = _prompt("feishu receive_id (chat_id)", default="")
                    if not rid:
                        print("Missing receive_id, skip send.")
                        continue
                    cfg_obj.feishu.receive_id = rid
                    yaml_set_scalar(cfg, ["feishu", "receive_id"], rid)
            except Exception:
                pass

            # Generate cover images for this run (best effort).
            cg_args = ["xhs", "-c", cfg, "--cover-config", "cover_gen.yaml"]
            if resolved_run_dir:
                cg_args += ["--run-dir", resolved_run_dir]
            _try_cover_gen(cg_args)

            args2 = ["-c", cfg]
            if resolved_run_dir:
                args2 += ["--run-dir", resolved_run_dir]
            code2 = _run_py("feishu_sender.py", args2)
            print(f"Send Exit code: {code2}")
            if code2 == 0:
                a = ["xhs", "-c", cfg]
                if resolved_run_dir:
                    a += ["--run-dir", resolved_run_dir]
                _try_bitable_sync(a)
            continue

        if choice == "10":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")

            # Ensure receive_id is set for Feishu open platform text sending
            try:
                from crawler_rednotes.config_rednotes import load_rednotes_config
                from crawler_rednotes.yaml_patch import set_scalar as yaml_set_scalar

                cfg_obj, _raw = load_rednotes_config(cfg)
                if cfg_obj.feishu and not cfg_obj.feishu.receive_id:
                    rid = _prompt("feishu receive_id (chat_id)", default="")
                    if not rid:
                        print("Missing receive_id, skip.")
                        continue
                    cfg_obj.feishu.receive_id = rid
                    yaml_set_scalar(cfg, ["feishu", "receive_id"], rid)
            except Exception:
                cfg_obj = None

            # Sync Bitable (best effort, no group message body)
            _try_bitable_sync(["init", "-c", cfg])
            _try_bitable_sync(["all", "-c", cfg, "--paper", "paper.json", "--no-ensure-fields"])

            continue

        if choice == "11":
            webhook = _prompt(
                "WeCom webhook (leave empty for default in script)",
                default="",
            )
            outputs_dir = _prompt("outputs dir", default="outputs")
            limit = _prompt("max items per source", default="10")
            args3 = ["all", "--outputs", outputs_dir, "--limit", str(int(limit) if str(limit).strip().isdigit() else 10)]
            if webhook:
                args3 += ["--webhook", webhook]
            code3 = _run_py("wecom_sender.py", args3)
            print(f"WeCom Exit code: {code3}")
            continue

        if choice == "12":
            cfg = _prompt("rednotes.yaml path", default="rednotes.yaml")
            api_key = _prompt("TikHub api key (leave empty to use hardcoded/env)", default="")
            limit = _prompt("max notes per profile (0 = use rednotes.yaml maxcrawl)", default="0")
            since = _prompt("since (YYYY-MM-DD, leave empty to use rednotes.yaml since)", default="")
            retries = _prompt("http retries (default 2)", default="2")
            no_llm = _prompt("run Qwen body+analysis? (Y/n)", default="Y").lower().startswith("n")
            no_dl = _prompt("download media? (Y/n)", default="Y").lower().startswith("n")

            args4 = ["--from-config", "--qwen-config", cfg]
            if api_key:
                args4 += ["--api-key", api_key]
            if str(limit).strip().isdigit():
                args4 += ["--limit", str(int(limit))]
            if since:
                args4 += ["--since", since]
            if str(retries).strip().isdigit():
                args4 += ["--retries", str(int(retries))]
            if no_llm:
                args4.append("--no-qwen-video-summary")
            if no_dl:
                args4.append("--no-download-media")

            code4 = _run_py("rednotes_api_tikhub.py", args4)
            print(f"Exit code: {code4}")
            continue

        print("Unknown choice.")


if __name__ == "__main__":
    raise SystemExit(main())
