from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse


@dataclass
class WanxConfig:
    api_key: str = ""
    sync_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    async_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation"
    model: str = "wan2.6-t2i"
    size: str = "1280*1280"
    n: int = 1
    negative_prompt: str = ""
    prompt_extend: bool = True
    watermark: bool = False
    async_mode: bool = True
    poll_interval_sec: float = 5.0
    timeout_sec: float = 300.0


@dataclass
class OutputConfig:
    dir_name: str = "covers"
    ext: str = ".png"
    force: bool = False


@dataclass
class CoverGenConfig:
    wanx: WanxConfig
    prompt_template: str
    output: OutputConfig


def _coerce_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _read_yaml(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        # Very small fallback: accept JSON only
        data = json.loads(text)
        return data if isinstance(data, dict) else {}


def load_cover_gen_config(path: str = "cover_gen.yaml") -> CoverGenConfig:
    raw = _read_yaml(path)

    wanx_raw = raw.get("wanx") if isinstance(raw.get("wanx"), dict) else {}
    prompt_raw = raw.get("prompt") if isinstance(raw.get("prompt"), dict) else {}
    output_raw = raw.get("output") if isinstance(raw.get("output"), dict) else {}

    wanx = WanxConfig(
        api_key=str(wanx_raw.get("api_key") or "").strip(),
        sync_base_url=str(wanx_raw.get("sync_base_url") or WanxConfig.sync_base_url).strip(),
        async_base_url=str(wanx_raw.get("async_base_url") or WanxConfig.async_base_url).strip(),
        model=str(wanx_raw.get("model") or WanxConfig.model).strip(),
        size=str(wanx_raw.get("size") or WanxConfig.size).strip(),
        n=int(wanx_raw.get("n") or 1),
        negative_prompt=str(wanx_raw.get("negative_prompt") or "").strip(),
        prompt_extend=_coerce_bool(wanx_raw.get("prompt_extend", True)),
        watermark=_coerce_bool(wanx_raw.get("watermark", False)),
        async_mode=_coerce_bool(wanx_raw.get("async_mode", True)),
        poll_interval_sec=float(wanx_raw.get("poll_interval_sec") or 5.0),
        timeout_sec=float(wanx_raw.get("timeout_sec") or 300.0),
    )

    prompt_template = str(prompt_raw.get("template") or "").rstrip()
    if not prompt_template:
        raise RuntimeError("Missing prompt.template in cover_gen.yaml")

    output = OutputConfig(
        dir_name=str(output_raw.get("dir_name") or "covers").strip() or "covers",
        ext=str(output_raw.get("ext") or ".png").strip() or ".png",
        force=_coerce_bool(output_raw.get("force", False)),
    )

    return CoverGenConfig(wanx=wanx, prompt_template=prompt_template, output=output)


def _safe_filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = PurePosixPath(path).name
    return name or f"wan_{int(time.time())}.png"


def _extract_image_urls(payload: Any) -> list[str]:
    """
    Supports both formats:
    - output.choices[].message.content[] -> {type:"image", image:"..."}
    - output.results[] -> {url:"..."}
    """
    urls: list[str] = []
    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, dict):
        return urls

    choices = output.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image" and isinstance(item.get("image"), str):
                    urls.append(item["image"])

    results = output.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)

    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    return deduped


def _tasks_base_url_from_request_url(request_url: str) -> str:
    parsed = urlparse(request_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/tasks"


def _render_prompt(template: str, *, title: str, summary: str, source: str) -> str:
    return template.format(title=title, summary=summary, source=source)


def wanx_generate_one(
    cfg: CoverGenConfig,
    prompt: str,
    out_path: Path,
    *,
    seed: Optional[int] = None,
    verbose: bool = False,
) -> Path:
    import requests

    api_key = (cfg.wanx.api_key or os.environ.get("DASHSCOPE_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("Missing DashScope API key (set cover_gen.yaml wanx.api_key or env DASHSCOPE_API_KEY)")

    request_url = (cfg.wanx.async_base_url if cfg.wanx.async_mode else cfg.wanx.sync_base_url).strip()
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if cfg.wanx.async_mode:
        headers["X-DashScope-Async"] = "enable"

    body: Dict[str, Any] = {
        "model": cfg.wanx.model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ]
        },
        "parameters": {
            "negative_prompt": cfg.wanx.negative_prompt,
            "prompt_extend": bool(cfg.wanx.prompt_extend),
            "watermark": bool(cfg.wanx.watermark),
            "n": int(cfg.wanx.n),
            "size": cfg.wanx.size,
        },
    }
    if seed is not None:
        body["parameters"]["seed"] = int(seed)

    resp = requests.post(request_url, headers=headers, json=body, timeout=120)
    try:
        data = resp.json()
    except Exception:
        data = {"_raw_text": resp.text}
    if verbose:
        print("wanx http:", resp.status_code)
        print("wanx resp:", json.dumps(data, ensure_ascii=False, indent=2)[:20_000])
    if resp.status_code != 200:
        raise RuntimeError(f"wanx request failed http={resp.status_code} resp={data}")

    urls = _extract_image_urls(data)
    output = data.get("output") if isinstance(data, dict) else None
    task_id = output.get("task_id") if isinstance(output, dict) else None

    if (not urls) and cfg.wanx.async_mode and isinstance(task_id, str) and task_id:
        tasks_base = _tasks_base_url_from_request_url(request_url)
        deadline = time.time() + float(cfg.wanx.timeout_sec)
        while True:
            if time.time() >= deadline:
                raise TimeoutError(f"timeout while waiting task: {task_id}")

            poll_url = f"{tasks_base}/{task_id}"
            poll = requests.get(poll_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
            poll.raise_for_status()
            poll_data = poll.json()
            urls = _extract_image_urls(poll_data)
            if urls:
                break
            poll_output = poll_data.get("output") if isinstance(poll_data, dict) else None
            poll_status = poll_output.get("task_status") if isinstance(poll_output, dict) else None
            if poll_status in ("SUCCEEDED", "FAILED", "CANCELED"):
                raise RuntimeError(f"task finished but no image urls, status={poll_status}, task_id={task_id}")
            time.sleep(float(cfg.wanx.poll_interval_sec))

    if not urls:
        raise RuntimeError("wanx returned no image urls")

    url = urls[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out_path.write_bytes(r.content)
    return out_path


def _load_one_liners(config_path: str, items: List[Dict[str, Any]]) -> Dict[str, str]:
    from bitable_sync import _generate_one_liners_qwen

    return _generate_one_liners_qwen(config_path=config_path, items=items, model="qwen3-max")


def _xhs_run_root_from_arg(run_dir: str) -> Path:
    p = Path(run_dir)
    if not p.exists():
        raise FileNotFoundError(p)
    return p


def find_latest_rednotes_run(outputs_root: str = "outputs/rednotes") -> Optional[Path]:
    root = Path(outputs_root)
    if not root.exists():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0]


def find_latest_wechat_stage2(outputs_root: str = "outputs/wechat") -> Optional[Path]:
    root = Path(outputs_root)
    if not root.exists():
        return None
    candidates = list(root.rglob("*_stage2.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def generate_covers_xhs(
    run_dir: str,
    *,
    config_path: str,
    cover_cfg_path: str,
    force: bool = False,
    limit: int = 0,
    verbose: bool = False,
) -> int:
    from bitable_sync import load_xhs_records

    cfg = load_cover_gen_config(cover_cfg_path)
    run_root = _xhs_run_root_from_arg(run_dir)
    out_dir = run_root / cfg.output.dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    records = load_xhs_records(run_dir=str(run_root), config_path=config_path, score_threshold=6.0)
    if limit > 0:
        records = records[: int(limit)]

    items_for_llm = [{"key": r.unique_key, "title": r.title, "summary": (r.summary or "").strip()} for r in records]
    one_liners = _load_one_liners(config_path, items_for_llm)

    ok = 0
    for r in records:
        one = one_liners.get(r.unique_key, "").strip()
        if not one:
            continue
        out_path = out_dir / f"{r.unique_key}{cfg.output.ext}"
        if out_path.exists() and not (force or cfg.output.force):
            ok += 1
            continue
        prompt = _render_prompt(cfg.prompt_template, title=r.title, summary=one, source="xhs")
        wanx_generate_one(cfg, prompt, out_path, verbose=verbose)
        ok += 1
        print(f"[cover] xhs ok unique_key={r.unique_key} path={out_path}")
    return ok


def generate_covers_wechat(
    stage2_path: str,
    *,
    config_path: str,
    cover_cfg_path: str,
    force: bool = False,
    limit: int = 0,
    verbose: bool = False,
) -> int:
    from bitable_sync import load_wechat_records

    cfg = load_cover_gen_config(cover_cfg_path)
    p = Path(stage2_path)
    if not p.exists():
        raise FileNotFoundError(p)
    run_root = p.parent
    out_dir = run_root / cfg.output.dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    records = load_wechat_records(stage2_json_path=str(p), paper_json_path="paper.json", score_threshold=0.0)
    if limit > 0:
        records = records[: int(limit)]

    items_for_llm = [{"key": r.unique_key, "title": r.title, "summary": (r.summary or "").strip()} for r in records]
    one_liners = _load_one_liners(config_path, items_for_llm)

    ok = 0
    for r in records:
        one = one_liners.get(r.unique_key, "").strip()
        if not one:
            continue
        out_path = out_dir / f"{r.unique_key}{cfg.output.ext}"
        if out_path.exists() and not (force or cfg.output.force):
            ok += 1
            continue
        prompt = _render_prompt(cfg.prompt_template, title=r.title, summary=one, source="wechat")
        wanx_generate_one(cfg, prompt, out_path, verbose=verbose)
        ok += 1
        print(f"[cover] wechat ok unique_key={r.unique_key} path={out_path}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate per-item cover images via Wanx (DashScope HTTP).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_xhs = sub.add_parser("xhs", help="Generate covers for outputs/rednotes/<run>/analysis/stage1.json items")
    p_xhs.add_argument("--run-dir", default="", help="outputs/rednotes/<run> folder (default: latest)")
    p_xhs.add_argument("-c", "--config", default="rednotes.yaml", help="Config (for Qwen one-liner)")
    p_xhs.add_argument("--cover-config", default="cover_gen.yaml", help="cover_gen.yaml")
    p_xhs.add_argument("--force", action="store_true", help="Overwrite existing images")
    p_xhs.add_argument("--limit", type=int, default=0)
    p_xhs.add_argument("--verbose", action="store_true")

    p_wc = sub.add_parser("wechat", help="Generate covers for outputs/wechat/*_stage2.json items")
    p_wc.add_argument("--stage2", default="", help="outputs/wechat/<run>/*_stage2.json (default: latest)")
    p_wc.add_argument("-c", "--config", default="rednotes.yaml", help="Config (for Qwen one-liner)")
    p_wc.add_argument("--cover-config", default="cover_gen.yaml", help="cover_gen.yaml")
    p_wc.add_argument("--force", action="store_true", help="Overwrite existing images")
    p_wc.add_argument("--limit", type=int, default=0)
    p_wc.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.cmd == "xhs":
        run_dir = str(args.run_dir or "").strip()
        if not run_dir:
            latest = find_latest_rednotes_run()
            if not latest:
                raise RuntimeError("No rednotes run folder found under outputs/rednotes")
            run_dir = str(latest)
        n = generate_covers_xhs(
            run_dir=run_dir,
            config_path=args.config,
            cover_cfg_path=args.cover_config,
            force=bool(args.force),
            limit=int(args.limit or 0),
            verbose=bool(args.verbose),
        )
        print(f"[cover] xhs done n={n}")
        return 0

    if args.cmd == "wechat":
        stage2 = str(args.stage2 or "").strip()
        if not stage2:
            latest = find_latest_wechat_stage2()
            if not latest:
                raise RuntimeError("No WeChat *_stage2.json found under outputs/wechat")
            stage2 = str(latest)
        n = generate_covers_wechat(
            stage2_path=stage2,
            config_path=args.config,
            cover_cfg_path=args.cover_config,
            force=bool(args.force),
            limit=int(args.limit or 0),
            verbose=bool(args.verbose),
        )
        print(f"[cover] wechat done n={n}")
        return 0

    print("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
