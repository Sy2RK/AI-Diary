from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

DEFAULT_DASHSCOPE_API_KEY = "sk-fa546040e339425d9164bf43ae05501b"

# Wan2.6 HTTP sync endpoint (Beijing region)
DEFAULT_SYNC_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

# Wan2.6 HTTP async endpoint (Beijing region)
DEFAULT_ASYNC_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation"

DEFAULT_MODEL = "wan2.6-t2i"
DEFAULT_SIZE = "1280*1280"
DEFAULT_PROMPT = "根据以下文章的内容为主题生成一张封面图，要求准确展示内容主旨，少用文字，多用图例来表示：Piedoc网站可将文字一键转为图表与PPT，支持多种风格模板（科技、商务、卡通等）、自定义编辑及Nano精简模式，1分钟生成高质量汇报材料。"


def _safe_filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = PurePosixPath(path).name
    return name or f"wan_{int(time.time())}.png"


def _unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for i in range(2, 10_000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many existing files for: {target}")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="通义万相 Wan2.6 文生图（HTTP）最小示例")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="提示词")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型名")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="图片尺寸，例如 1280*1280")
    parser.add_argument("--n", type=int, default=2, help="生成张数（1~4）")
    parser.add_argument("--negative-prompt", default="", help="负向提示词")
    parser.add_argument(
        "--prompt-extend",
        action="store_true",
        default=True,
        help="启用提示词改写（默认开启）",
    )
    parser.add_argument(
        "--no-prompt-extend",
        action="store_false",
        dest="prompt_extend",
        help="禁用提示词改写",
    )
    parser.add_argument("--watermark", action="store_true", default=False, help="添加水印（默认不加）")
    parser.add_argument("--seed", type=int, default=None, help="随机种子（可选）")
    parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="异步调用（返回 task_id 后轮询 /api/v1/tasks/{task_id}）",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="不等待任务完成（如果返回结果为空则直接退出；通常用于调试）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印更多响应信息，便于排查结果为空的问题",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="自定义请求 URL；不传则 sync 用 multimodal-generation，async 用 image-generation",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0, help="异步轮询间隔秒数（默认 5）")
    parser.add_argument("--timeout", type=float, default=300.0, help="异步总超时秒数（默认 300）")
    parser.add_argument(
        "--out-dir",
        default="wan_out",
        help="输出目录（默认 ./wan_out）",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="DashScope API Key；不传则优先使用环境变量 DASHSCOPE_API_KEY，最后回退到脚本内置默认值",
    )
    args = parser.parse_args()

    api_key = (args.api_key or os.environ.get("DASHSCOPE_API_KEY", "") or DEFAULT_DASHSCOPE_API_KEY).strip()
    if not api_key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY (env/--api-key).")

    try:
        import sys
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Unexpected python runtime issue: {e}") from e

    try:
        import requests
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency: requests.\n"
            f"Current python: {sys.executable}\n"
            "Install into THIS interpreter:\n"
            f"  {sys.executable} -m pip install requests\n"
        ) from e

    request_url = (args.base_url or (DEFAULT_ASYNC_BASE_URL if args.async_mode else DEFAULT_SYNC_BASE_URL)).strip()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if args.async_mode:
        headers["X-DashScope-Async"] = "enable"

    body: dict[str, Any] = {
        "model": args.model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": args.prompt,
                        }
                    ],
                }
            ]
        },
        "parameters": {
            "negative_prompt": args.negative_prompt,
            "prompt_extend": bool(args.prompt_extend),
            "watermark": bool(args.watermark),
            "n": int(args.n),
            "size": args.size,
        },
    }
    if args.seed is not None:
        body["parameters"]["seed"] = int(args.seed)

    print("---- request, please wait a moment ----")
    resp = requests.post(request_url, headers=headers, json=body, timeout=120)
    try:
        data = resp.json()
    except Exception:
        data = {"_raw_text": resp.text}

    if args.verbose:
        print("http:", resp.status_code)
        print("response:", json.dumps(data, ensure_ascii=False, indent=2)[:20_000])

    if resp.status_code != 200:
        print("request Failed, http_status: %s" % resp.status_code)
        code = data.get("code") if isinstance(data, dict) else None
        message = data.get("message") if isinstance(data, dict) else None
        request_id = data.get("request_id") if isinstance(data, dict) else None
        if code or message or request_id:
            print("code: %s, message: %s, request_id: %s" % (code, message, request_id))
        return 2

    urls = _extract_image_urls(data)

    output = data.get("output") if isinstance(data, dict) else None
    task_id = output.get("task_id") if isinstance(output, dict) else None
    task_status = output.get("task_status") if isinstance(output, dict) else None
    if (not urls) and (not args.no_wait) and isinstance(task_id, str) and task_id:
        tasks_base = _tasks_base_url_from_request_url(request_url)
        deadline = time.time() + float(args.timeout)
        while True:
            if time.time() >= deadline:
                print(f"timeout while waiting task: {task_id}")
                return 3

            poll_url = f"{tasks_base}/{task_id}"
            poll = requests.get(poll_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
            poll.raise_for_status()
            poll_data = poll.json()
            if args.verbose:
                print("poll:", json.dumps(poll_data, ensure_ascii=False, indent=2)[:20_000])

            poll_output = poll_data.get("output") if isinstance(poll_data, dict) else None
            poll_status = poll_output.get("task_status") if isinstance(poll_output, dict) else None
            urls = _extract_image_urls(poll_data)
            if urls:
                data = poll_data
                break
            if poll_status in ("SUCCEEDED", "FAILED", "CANCELED"):
                print(f"task finished but no image urls, status={poll_status}, task_id={task_id}")
                return 3
            time.sleep(float(args.poll_interval))

    if (not urls) and args.verbose:
        print("no image urls found. (task_status=%s, task_id=%s)" % (task_status, task_id))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for url in urls:
        file_name = _safe_filename_from_url(url)
        target = _unique_path(out_dir / file_name)

        r = requests.get(url, timeout=120)
        r.raise_for_status()
        target.write_bytes(r.content)
        saved += 1
        print(f"saved: {target}")

    print(f"done: {saved} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
