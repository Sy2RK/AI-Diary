import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from crawler_common.project_config import sync_legacy_configs

LEGACY_CONFIG_DIR = Path("configs") / "legacy"


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or default


def _run_py(rel_script: str, args: list[str]) -> int:
    script_path = Path(__file__).resolve().parent / rel_script
    if not script_path.exists():
        print(f"脚本不存在: {script_path}")
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
        code = _run_py(str(Path("scripts") / "integrations" / "bitable_sync.py"), args)
        if code == 0:
            print("多维表格同步完成")
        else:
            print(f"多维表格同步失败/跳过 (exit={code})")
    except Exception as exc:  # noqa: BLE001
        print(f"多维表格同步失败/跳过 ({exc})")


def _try_cover_gen(args: list[str]) -> None:
    try:
        code = _run_py(str(Path("scripts") / "integrations" / "cover_gen.py"), args)
        if code == 0:
            print("封面生成完成")
        else:
            print(f"封面生成失败/跳过 (exit={code})")
    except Exception as exc:  # noqa: BLE001
        print(f"封面生成失败/跳过 ({exc})")


def _resolve_rednotes_run_dir(run_dir: str) -> str:
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


def _legacy_config_paths() -> dict[str, str]:
    return {
        "rednotes": str(LEGACY_CONFIG_DIR / "rednotes.yaml"),
        "wechat": str(LEGACY_CONFIG_DIR / "wechat.yaml"),
        "cover_gen": str(LEGACY_CONFIG_DIR / "cover_gen.yaml"),
    }


def _sync_unified_config(project_config: str, profile: str) -> dict[str, str]:
    out = _legacy_config_paths()
    cfg = Path(project_config)
    if not cfg.exists():
        return out
    try:
        sync_legacy_configs(
            str(cfg),
            profile=profile,
            rednotes_out=out["rednotes"],
            wechat_out=out["wechat"],
            cover_out=out["cover_gen"],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[配置] 统一配置同步跳过: {exc}")
    return out


def _menu_wechat(project_config: str, profile: str) -> None:
    while True:
        print("\n=== 微信公众号 ===")
        print("1) 抓取公众号文章（仅抓取）")
        print("2) AI 分析公众号文章（仅分析）")
        print("3) 生成封面（仅生成封面）")
        print("4) 推送到飞书（仅推送）")
        print("5) 推送到企业微信（仅推送）")
        print("0) 返回上一级")
        choice = _prompt("请选择", default="0")

        if choice == "0":
            return

        if choice == "1":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            out = _prompt("抓取输出文件名", default="paper.json")
            args2 = ["-c", pcfg, "-o", out]
            if prof:
                args2 += ["--profile", prof]
            code = _run_py(str(Path("scripts") / "wechat" / "wechat_crawler.py"), args2)
            print(f"退出码: {code}")
            continue

        if choice == "2":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            inp = _prompt("输入文件", default="paper.json")
            out = _prompt("输出文件名（留空使用默认）", default="")
            args = ["-c", pcfg, "-i", inp]
            if out:
                args += ["-o", out]
            if prof:
                args += ["--profile", prof]
            code = _run_py(str(Path("scripts") / "wechat" / "llm_qwen.py"), args)
            print(f"退出码: {code}")
            continue

        if choice == "3":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            latest = _find_latest_wechat_stage2_json("outputs") or ""
            stage2 = _prompt("stage2 文件路径", default=latest)
            if not stage2:
                print("未找到 stage2 文件，请先执行分析。")
                continue
            args3 = ["wechat", "--stage2", stage2, "-c", pcfg]
            if prof:
                args3 += ["--profile", prof]
            _try_cover_gen(args3)
            continue

        if choice == "4":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            latest = _find_latest_wechat_stage2_json("outputs") or ""
            stage2 = _prompt("stage2 文件路径", default=latest)
            if not stage2:
                print("未找到 stage2 文件，请先执行分析。")
                continue
            args4 = ["--stage2", stage2, "-c", pcfg]
            if prof:
                args4 += ["--profile", prof]
            code = _run_py(str(Path("scripts") / "wechat" / "wechat_feishu_sender.py"), args4)
            print(f"退出码: {code}")
            continue

        if choice == "5":
            webhook = _prompt(
                "企业微信 webhook（留空使用默认）",
                default=os.getenv("WECOM_WEBHOOK_URL", ""),
            ).strip()
            if not webhook:
                print("未填写 webhook，已取消。")
                continue
            outputs_root = _prompt("outputs 根目录", default="outputs").strip()
            limit = _prompt("推送条数上限", default="10").strip()
            args5 = ["wechat", "--webhook", webhook]
            if outputs_root:
                args5 += ["--outputs", outputs_root]
            if limit.isdigit():
                args5 += ["--limit", str(int(limit))]
            code = _run_py(str(Path("scripts") / "integrations" / "wecom_sender.py"), args5)
            print(f"退出码: {code}")
            continue

        print("无效选项，请重试。")


def _menu_xhs(project_config: str, profile: str) -> None:
    while True:
        print("\n=== 小红书 ===")
        print("1) 登录小红书账号（更新 storage_state）")
        print("2) 抓取小红书内容（仅抓取，不做AI分析）")
        print("3) 小红书AI分析（仅分析）")
        print("4) 生成封面（仅生成封面）")
        print("5) 推送到飞书（仅推送）")
        print("6) 推送到企业微信（仅推送）")
        print("7) TikHub API 抓取（仅抓取，不做Qwen分析）")
        print("0) 返回上一级")
        choice = _prompt("请选择", default="0")

        if choice == "0":
            return

        if choice == "1":
            out = _prompt(
                "storage_state 输出路径",
                default=str(Path("crawler_rednotes") / "storage_state.json"),
            )
            code = _run_py(str(Path("crawler_rednotes") / "login_xhs.py"), ["-o", out])
            print(f"退出码: {code}")
            continue

        if choice == "2":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            no_dl = _prompt("下载图片? (Y/n)", default="Y").lower().startswith("n")
            args2 = ["--config", pcfg, "--no-llm"]
            if prof:
                args2 += ["--profile", prof]
            if no_dl:
                args2.append("--no-download-images")
            code = _run_py(str(Path("scripts") / "xhs" / "xhs_pipeline.py"), args2)
            print(f"退出码: {code}")
            continue

        if choice == "3":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            run_dir = _prompt("run 目录（留空默认最新）", default="")
            args = ["-c", pcfg]
            if run_dir:
                args += ["--run-dir", run_dir]
            if prof:
                args += ["--profile", prof]
            code = _run_py(str(Path("scripts") / "xhs" / "llm_xhs_qwen.py"), args)
            print(f"退出码: {code}")
            continue

        if choice == "4":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            run_dir = _prompt("run 目录（留空默认最新）", default="")
            resolved_run_dir = _resolve_rednotes_run_dir(run_dir)
            cg_args = ["xhs", "-c", pcfg]
            if resolved_run_dir:
                cg_args += ["--run-dir", resolved_run_dir]
            if prof:
                cg_args += ["--profile", prof]
            _try_cover_gen(cg_args)
            continue

        if choice == "5":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            run_dir = _prompt("run 目录（留空默认最新）", default="")
            resolved_run_dir = _resolve_rednotes_run_dir(run_dir)
            args2 = ["-c", pcfg]
            if resolved_run_dir:
                args2 += ["--run-dir", resolved_run_dir]
            if prof:
                args2 += ["--profile", prof]
            code = _run_py(str(Path("scripts") / "xhs" / "feishu_sender.py"), args2)
            print(f"退出码: {code}")
            continue

        if choice == "6":
            webhook = _prompt(
                "企业微信 webhook（留空使用默认）",
                default=os.getenv("WECOM_WEBHOOK_URL", ""),
            ).strip()
            if not webhook:
                print("未填写 webhook，已取消。")
                continue
            outputs_root = _prompt("outputs 根目录", default="outputs").strip()
            limit = _prompt("推送条数上限", default="10").strip()
            args6 = ["xhs", "--webhook", webhook]
            if outputs_root:
                args6 += ["--outputs", outputs_root]
            if limit.isdigit():
                args6 += ["--limit", str(int(limit))]
            code = _run_py(str(Path("scripts") / "integrations" / "wecom_sender.py"), args6)
            print(f"退出码: {code}")
            continue

        if choice == "7":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            api_key = _prompt("TikHub API Key（留空使用配置/环境变量）", default="")
            limit = _prompt("每账号抓取上限(0=使用配置)", default="0")
            since = _prompt("since (YYYY-MM-DD，留空使用配置)", default="")
            retries = _prompt("HTTP重试次数", default="2")
            no_dl = _prompt("下载媒体? (Y/n)", default="Y").lower().startswith("n")

            args4 = ["--from-config", "--qwen-config", pcfg, "--no-qwen-video-summary"]
            if api_key:
                args4 += ["--api-key", api_key]
            if str(limit).strip().isdigit():
                args4 += ["--limit", str(int(limit))]
            if since:
                args4 += ["--since", since]
            if str(retries).strip().isdigit():
                args4 += ["--retries", str(int(retries))]
            if no_dl:
                args4.append("--no-download-media")
            if prof:
                args4 += ["--profile", prof]

            code7 = _run_py(str(Path("scripts") / "xhs" / "rednotes_api_tikhub.py"), args4)
            print(f"退出码: {code7}")
            continue

        print("无效选项，请重试。")


def _menu_bitable(project_config: str, profile: str) -> None:
    while True:
        print("\n=== 多维表格 ===")
        print("1) 初始化表结构（仅初始化）")
        print("2) 同步微信公众号数据（仅同步公众号）")
        print("3) 同步小红书数据（仅同步小红书）")
        print("4) 同步全部数据（公众号+小红书）")
        print("0) 返回上一级")
        choice = _prompt("请选择", default="0")

        if choice == "0":
            return

        if choice == "1":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            args2 = ["init", "-c", pcfg]
            if prof:
                args2 += ["--profile", prof]
            _try_bitable_sync(args2)
            continue

        if choice == "2":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            latest = _find_latest_wechat_stage2_json("outputs") or ""
            stage2 = _prompt("公众号 stage2 文件", default=latest)
            if not stage2:
                print("未找到公众号 stage2 文件，请先执行分析。")
                continue
            args2 = ["wechat", "-c", pcfg, "--stage2", stage2]
            if prof:
                args2 += ["--profile", prof]
            _try_bitable_sync(args2)
            continue

        if choice == "3":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            run_dir = _prompt("小红书 run 目录（留空默认最新）", default="")
            resolved_run_dir = _resolve_rednotes_run_dir(run_dir)
            args = ["xhs", "-c", pcfg]
            if resolved_run_dir:
                args += ["--run-dir", resolved_run_dir]
            if prof:
                args += ["--profile", prof]
            _try_bitable_sync(args)
            continue

        if choice == "4":
            pcfg = _prompt("统一配置路径", default=project_config)
            prof = _prompt("配置环境(profile)", default=profile)
            paper = _prompt("公众号源文件(paper.json)", default="paper.json")
            args2 = ["all", "-c", pcfg, "--paper", paper]
            if prof:
                args2 += ["--profile", prof]
            _try_bitable_sync(args2)
            continue

        print("无效选项，请重试。")


def _menu_web_export(project_config: str, profile: str) -> None:
    while True:
        print("\n=== Web Export ===")
        print("1) 仅输出内容 (report_documents.json)")
        print("2) 仅输出图片 (report_images.json)")
        print("3) 内容 + 图片 + 匹配文件（一起生成）")
        print("0) 返回上一级")
        choice = _prompt("请选择", default="0")

        if choice == "0":
            return

        date_key = _prompt("输出日期 (YYYY-MM-DD 或 YYYYMMDD，留空=最新)", default="").strip()
        content_out = _prompt("内容输出路径（留空默认）", default="").strip()
        images_out = _prompt("图片输出路径（留空默认）", default="").strip()
        merged_out = _prompt("匹配输出路径（留空默认）", default="").strip()

        def _run_content() -> None:
            args = []
            if content_out:
                args += ["--output", content_out]
            if date_key:
                args += ["--date", date_key]
            code = _run_py(str(Path("scripts") / "integrations" / "web_export.py"), args)
            print(f"退出码: {code}")

        def _run_images() -> None:
            args = []
            if images_out:
                args += ["--output", images_out]
            if date_key:
                args += ["--date", date_key]
            code = _run_py(str(Path("scripts") / "integrations" / "export_images_json.py"), args)
            print(f"退出码: {code}")

        def _run_merged() -> None:
            args = []
            if content_out:
                args += ["--documents", content_out]
            if images_out:
                args += ["--images", images_out]
            if merged_out:
                args += ["--output", merged_out]
            if date_key:
                args += ["--date", date_key]
            code = _run_py(str(Path("scripts") / "integrations" / "export_news_with_images_json.py"), args)
            print(f"退出码: {code}")

        if choice == "1":
            _run_content()
            continue
        if choice == "2":
            _run_images()
            continue
        if choice == "3":
            _run_content()
            _run_images()
            _run_merged()
            continue

        print("无效选项，请重试。")


def _run_interactive_cli(args: argparse.Namespace) -> int:
    _sync_unified_config(args.project_config, args.profile)

    while True:
        print("\n=== 主菜单 ===")
        print("1) 微信公众号")
        print("2) 小红书")
        print("3) 多维表格")
        print("4) Web Export")
        print("5) 一键执行全部信息收集与分析（预留）")
        print("0) 退出")
        choice = _prompt("请选择", default="0")

        if choice == "0":
            return 0
        if choice == "1":
            _menu_wechat(project_config=args.project_config, profile=args.profile)
            continue
        if choice == "2":
            _menu_xhs(project_config=args.project_config, profile=args.profile)
            continue
        if choice == "3":
            _menu_bitable(project_config=args.project_config, profile=args.profile)
            continue
        if choice == "4":
            _menu_web_export(project_config=args.project_config, profile=args.profile)
            continue
        if choice == "5":
            pcfg = _prompt("统一配置路径", default=args.project_config)
            prof = _prompt("配置环境(profile)", default=args.profile)
            push_at = _prompt("推送时间(HH:MM)", default="10:00")
            tz = _prompt("时区", default="Asia/Shanghai")
            args2 = ["--config", pcfg, "--push-at", push_at, "--tz", tz]
            if prof:
                args2 += ["--profile", prof]
            code = _run_py(str(Path("scripts") / "automation" / "daily_run.py"), args2)
            print(f"退出码: {code}")
            continue

        print("无效选项，请重试。")


def main() -> int:
    parser = argparse.ArgumentParser(description="内容采集CLI（微信公众号 / 小红书 / 多维表格）")
    parser.add_argument(
        "--project-config",
        default=str(Path("configs") / "config.yaml"),
        help="统一配置文件路径（用于生成 configs/legacy/*.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，如 dev/prod")
    parser.add_argument("--sync-config", action="store_true", help="执行前先同步统一配置到 configs/legacy")
    sub = parser.add_subparsers(dest="cmd")

    p_wc = sub.add_parser("wechat-crawl", help="抓取公众号文章（仅抓取）")
    p_wc.add_argument("-c", "--config", default="", help="配置入口（统一配置或公众号 legacy 配置；留空使用 --project-config）")
    p_wc.add_argument("-o", "--output", default="paper.json")

    p_wl = sub.add_parser("wechat-llm", help="公众号AI分析（仅分析）")
    p_wl.add_argument("-c", "--config", default="", help="配置入口（统一配置或公众号 legacy 配置；留空使用 --project-config）")
    p_wl.add_argument("-i", "--input", default="paper.json")
    p_wl.add_argument("-o", "--output", default="")

    p_wp = sub.add_parser("wechat-feishu", help="公众号结果推送飞书（仅推送）")
    p_wp.add_argument("-i", "--input", required=True)
    p_wp.add_argument("--webhook", default="")

    p_rl = sub.add_parser("rednotes-login", help="登录小红书并保存 storage_state")
    p_rl.add_argument("-o", "--output", default=str(Path("crawler_rednotes") / "storage_state.json"))

    p_rc = sub.add_parser("rednotes-crawl", help="抓取单个小红书主页（仅抓取）")
    p_rc.add_argument("profile_url")
    p_rc.add_argument("--storage-state", default=str(Path("crawler_rednotes") / "storage_state.json"))
    p_rc.add_argument("--limit", type=int, default=50)
    p_rc.add_argument("--headless", action="store_true")
    p_rc.add_argument("--no-download-images", action="store_true")

    p_rall = sub.add_parser("rednotes-crawl-all", help="抓取小红书账号列表")
    p_rall.add_argument("-c", "--config", default="", help="配置入口（统一配置或小红书 legacy 配置；留空使用 --project-config）")
    p_rall.add_argument("--storage-state", default=str(Path("crawler_rednotes") / "storage_state.json"))
    p_rall.add_argument("--headless", action="store_true")
    p_rall.add_argument("--no-download-images", action="store_true")
    p_rall.add_argument("--no-llm", action="store_true")
    p_rall.add_argument("--legacy", action="store_true", help="使用 legacy crawler_rednotes 流程")
    p_rall.add_argument("--outputs", default="", help="覆盖 outputs 根目录")

    p_wcom_wc = sub.add_parser("wecom-wechat", help="企业微信推送公众号结果")
    p_wcom_wc.add_argument("--webhook", default="")
    p_wcom_wc.add_argument("--outputs", default="outputs")
    p_wcom_wc.add_argument("--limit", type=int, default=10)

    p_wcom_xhs = sub.add_parser("wecom-xhs", help="企业微信推送小红书结果")
    p_wcom_xhs.add_argument("--webhook", default="")
    p_wcom_xhs.add_argument("--outputs", default="outputs")
    p_wcom_xhs.add_argument("--limit", type=int, default=10)

    p_wcom_all = sub.add_parser("wecom-all", help="企业微信推送公众号+小红书结果")
    p_wcom_all.add_argument("--webhook", default="")
    p_wcom_all.add_argument("--outputs", default="outputs")
    p_wcom_all.add_argument("--limit", type=int, default=10)

    args, _unknown = parser.parse_known_args()
    legacy_paths = _sync_unified_config(args.project_config, args.profile)
    config_entry = args.config if getattr(args, "config", "") else args.project_config
    rednotes_cfg = legacy_paths["rednotes"]
    if args.sync_config:
        _sync_unified_config(args.project_config, args.profile)

    if args.cmd == "wechat-crawl":
        a = ["-c", config_entry, "-o", args.output]
        if args.profile:
            a += ["--profile", args.profile]
        return _run_py(str(Path("scripts") / "wechat" / "wechat_crawler.py"), a)

    if args.cmd == "wechat-llm":
        a = ["-c", config_entry, "-i", args.input]
        if args.output:
            a += ["-o", args.output]
        if args.profile:
            a += ["--profile", args.profile]
        return _run_py(str(Path("scripts") / "wechat" / "llm_qwen.py"), a)

    if args.cmd == "wechat-feishu":
        if args.webhook:
            a = ["-i", args.input, "--webhook", args.webhook]
            return _run_py(str(Path("scripts") / "integrations" / "feishu_formatter.py"), a)
        return _run_py(
            str(Path("scripts") / "wechat" / "wechat_feishu_sender.py"),
            ["--stage2", args.input, "-c", config_entry, *(["--profile", args.profile] if args.profile else [])],
        )

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

    if args.cmd == "rednotes-crawl-all":
        if not args.legacy:
            a = ["--config", config_entry]
            if args.profile:
                a += ["--profile", args.profile]
            if args.outputs:
                a += ["--outputs", args.outputs]
            if args.no_download_images:
                a.append("--no-download-images")
            if args.no_llm:
                a.append("--no-llm")
            return _run_py(str(Path("scripts") / "xhs" / "xhs_pipeline.py"), a)

        legacy_rednotes_cfg = args.config if args.config else rednotes_cfg
        a = ["crawl-all", "-c", legacy_rednotes_cfg, "--storage-state", args.storage_state]
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
        return _run_py(str(Path("scripts") / "integrations" / "wecom_sender.py"), a)

    if args.cmd == "wecom-xhs":
        a = ["xhs", "--outputs", args.outputs, "--limit", str(int(args.limit))]
        if args.webhook:
            a += ["--webhook", args.webhook]
        return _run_py(str(Path("scripts") / "integrations" / "wecom_sender.py"), a)

    if args.cmd == "wecom-all":
        a = ["all", "--outputs", args.outputs, "--limit", str(int(args.limit))]
        if args.webhook:
            a += ["--webhook", args.webhook]
        return _run_py(str(Path("scripts") / "integrations" / "wecom_sender.py"), a)

    if args.cmd is not None:
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        return 2

    if not sys.stdin.isatty():
        parser.print_help()
        return 2

    return _run_interactive_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
