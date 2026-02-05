import argparse
import base64
import json
import mimetypes
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 基础配置
QWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_API_KEY = "sk-fa546040e339425d9164bf43ae05501b"  
QWEN_MODEL = "qwen3-max"
QWEN_TEMPERATURE = 0.2
QWEN_TIMEOUT_SEC = 300
QWEN_ENABLE_THINKING = True  #开启思考

LLM_BATCH_SIZE = 10
LLM_LOG_PATH = "llm_calls.log"
OUTPUTS_DIR = "outputs"
WECHAT_OUTPUTS_SUBDIR = "wechat"


class QwenClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout_sec: int):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec

    def chat(self, messages: List[Dict[str, Any]]) -> str:
        import requests

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": QWEN_TEMPERATURE,
        }
        if QWEN_ENABLE_THINKING:
            payload["extra_body"] = {"enable_thinking": True}
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        message = data.get("choices", [{}])[0].get("message", {})
        return message.get("content", "") or ""


_qwen_client: Optional[QwenClient] = None


def get_qwen_client() -> QwenClient:
    global _qwen_client
    if _qwen_client is not None:
        return _qwen_client

    api_key = (QWEN_API_KEY or os.environ.get("QWEN_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("请在 llm_qwen.py 填写 QWEN_API_KEY 或设置环境变量 QWEN_API_KEY / DASHSCOPE_API_KEY")

    model = (os.environ.get("QWEN_MODEL", "") or QWEN_MODEL).strip()
    if not model:
        raise RuntimeError("请在 llm_qwen.py 填写 QWEN_MODEL 或设置环境变量 QWEN_MODEL")

    _qwen_client = QwenClient(
        api_key=api_key,
        base_url=QWEN_API_URL,
        model=model,
        timeout_sec=int(os.environ.get("QWEN_TIMEOUT_SEC", QWEN_TIMEOUT_SEC)),
    )
    return _qwen_client


def image_file_to_data_url(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    mime, _ = mimetypes.guess_type(p.name)
    mime = mime or "image/jpeg"
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_messages(system_prompt: str, user_text: str, image_paths: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Build OpenAI-compatible messages.

    Reserve space for future multimodal extension:
    - If image_paths provided, user message uses a content list with text + image_url entries.
    """
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    image_paths = image_paths or []
    if not image_paths:
        messages.append({"role": "user", "content": user_text})
        return messages

    content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
    for img_path in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_file_to_data_url(img_path)}})
    messages.append({"role": "user", "content": content})
    return messages


def strip_code_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.strip().strip("`").strip()
    return s


def log_event(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    Path(LLM_LOG_PATH).open("a", encoding="utf-8").write(f"[{ts}] {message}\n")


def call_qwen_api(prompt: str, content: str, image_paths: Optional[List[str]] = None) -> str:
    if not prompt:
        raise RuntimeError("系统提示词为空，请在 YAML 中设置 prompt")
    client = get_qwen_client()
    messages = build_messages(prompt, content, image_paths=image_paths)
    try:
        return client.chat(messages)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"LLM request failed: {exc}") from exc


def load_prompts_from_yaml(path: str) -> Dict[str, str]:
    import yaml

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        prompt = str(data.get("prompt") or "").strip()
        prompt2 = str(data.get("prompt_stage2") or data.get("prompt2") or "").strip()
        return {"prompt": prompt, "prompt2": prompt2}
    except Exception:
        return {"prompt": "", "prompt2": ""}


def parse_llm_json_objects(llm_result: str) -> List[Dict[str, Any]]:
    """
    Parse Qwen output to JSON objects.

    Expected:
    - JSONL (each line one JSON object)
    - or a single JSON object
    - or a JSON list of objects
    """
    text = strip_code_fences(llm_result)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed_items: List[Dict[str, Any]] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            parsed_items.append(obj)
    if parsed_items:
        return parsed_items

    try:
        obj2 = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(obj2, list):
        return [item for item in obj2 if isinstance(item, dict)]
    if isinstance(obj2, dict):
        return [obj2]
    return []


def load_existing_json_array(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def save_json_array(path: Path, items: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def json_array_to_jsonl_text(items: List[Dict[str, Any]]) -> str:
    return "\n".join(json.dumps(obj, ensure_ascii=False) for obj in items) + ("\n" if items else "")


def next_available_path(path: Path) -> Path:
    """
    If path exists, return a new path with -1/-2 suffix before extension.
    Example: 20260113.json -> 20260113-1.json -> 20260113-2.json ...
    """
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix or ""
    for i in range(1, 10_000):
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many existing files, cannot find available name for {path}")


def next_available_dir(path: Path) -> Path:
    """
    If directory exists, return a new directory with -1/-2 suffix.
    Example: outputs/20260113 -> outputs/20260113-1 -> outputs/20260113-2 ...
    """
    if not path.exists():
        return path
    parent = path.parent
    name = path.name
    for i in range(1, 10_000):
        candidate = parent / f"{name}-{i}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many existing folders, cannot find available name for {path}")


def ensure_run_dir(date_str: str) -> Path:
    base = Path(OUTPUTS_DIR) / WECHAT_OUTPUTS_SUBDIR / date_str
    run_dir = next_available_dir(base)
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def process_json_rows(
    json_path: str,
    output_path: str,
    prompt_override: str = "",
    image_paths: Optional[List[str]] = None,
) -> None:
    path = Path(json_path)
    if not path.exists() or path.stat().st_size == 0:
        print(f"No JSON content to process at {json_path}", file=sys.stderr)
        return

    base_prompt = prompt_override.strip()
    if not base_prompt:
        raise RuntimeError("LLM prompt is empty; 请在 YAML 中设置 prompt")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Input JSON must be a list of objects") from exc

    if isinstance(data, dict):
        rows: List[Dict[str, Any]] = [data]
    elif isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
    else:
        raise RuntimeError("Input JSON must be a list of objects")

    if not rows:
        print(f"No rows found in {json_path}", file=sys.stderr)
        return

    batch_size = max(1, int(LLM_BATCH_SIZE))
    total = len(rows)
    total_batches = (total + batch_size - 1) // batch_size
    out_path = Path(output_path)

    results = load_existing_json_array(out_path)
    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, total)
        batch = rows[start:end]

        item_lines: List[str] = []
        for idx, row in enumerate(batch, start=1):
            title = row.get("title", "")
            url = row.get("link", "") or row.get("url", "")
            content_text = row.get("content", "")
            item_lines.append(
                f"### item {idx}\n"
                f"标题: {title}\n"
                f"链接: {url}\n"
                f"正文:\n{content_text}\n"
            )

        user_content = (
            f"{base_prompt}\n\n"
            f"本次输入包含 {len(batch)} 篇文章，请按输入顺序输出 {len(batch)} 个 JSON 对象（可用 JSONL 或 JSON 数组）。"
            "每个对象字段且仅包含 title、url、summary、score、tag；"
            "score 为 0-10 数字（保留1位小数）；summary 与 tag 严格按系统提示词要求。\n\n"
            + "\n".join(item_lines)
        )

        try:
            llm_result = call_qwen_api(base_prompt, user_content, image_paths=image_paths)
            parsed_items = parse_llm_json_objects(llm_result)
            if not parsed_items:
                raise RuntimeError("LLM response is not valid JSON/JSONL")

            for idx, row in enumerate(batch[: len(parsed_items)]):
                parsed = parsed_items[idx]
                title = row.get("title", "")
                url = row.get("link", "") or row.get("url", "")
                obj = {
                    "title": parsed.get("title", title),
                    "url": parsed.get("url", url),
                    "summary": str(parsed.get("summary", "")).strip(),
                    "score": parsed.get("score", ""),
                    "tag": str(parsed.get("tag", "")).strip(),
                }
                results.append(obj)

            save_json_array(out_path, results)
            log_event(f"Qwen LLM success batch {batch_idx + 1}/{total_batches} ({len(batch)} rows)")
            print(f"LLM batch [{batch_idx + 1}/{total_batches}] done ({start + 1}-{end})")
        except Exception as exc:  # noqa: BLE001
            log_event(f"Qwen LLM failed batch {batch_idx + 1}/{total_batches}: {exc}")
            print(f"LLM batch [{batch_idx + 1}/{total_batches}] failed: {exc}", file=sys.stderr)


def filter_json_for_stage2(json_path: str, output_path: str, min_score: float = 6.0) -> bool:
    in_path = Path(json_path)
    rows = load_existing_json_array(in_path)
    if not rows:
        print(f"No JSON content to filter at {json_path}", file=sys.stderr)
        return False

    kept: List[Dict[str, Any]] = []
    for obj in rows:
        score_val = obj.get("score", 0)
        try:
            score_num = float(score_val)
        except (TypeError, ValueError):
            score_num = 0.0
        if score_num >= min_score:
            kept.append(obj)

    save_json_array(Path(output_path), kept)
    log_event(f"Filtered JSON for stage2: kept {len(kept)} items (min_score={min_score})")
    return bool(kept)


def process_json_stage2(json_path: str, output_path: str, prompt: str) -> None:
    rows = load_existing_json_array(Path(json_path))
    if not rows:
        print(f"No JSON content at {json_path}", file=sys.stderr)
        return
    content = json_array_to_jsonl_text(rows)
    try:
        llm_result = call_qwen_api(prompt, content)
        text = strip_code_fences(llm_result).strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise RuntimeError("Stage2 output must be a JSON array")
        Path(output_path).write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_event(f"Qwen LLM stage2 success for {json_path} -> {output_path}")
        print(f"LLM stage2 done, saved to {output_path}")
    except Exception as exc:  # noqa: BLE001
        log_event(f"Qwen LLM stage2 failed for {json_path}: {exc}")
        print(f"LLM stage2 failed: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Qwen (DashScope compatible) LLM on JSON input (paper.json)")
    parser.add_argument(
        "-i",
        "--input",
        "--csv",
        dest="input_path",
        default="paper.json",
        help="Input JSON for processing (default: paper.json)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="Output JSON filename (default: YYYYMMDD.json under outputs/wechat/YYYYMMDD[-N]/)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="wechat.yaml",
        help="Optional YAML config path to load prompt from (field: prompt)",
    )
    parser.add_argument(
        "--image",
        dest="image_paths",
        action="append",
        default=[],
        help="Optional image path (reserved for future multimodal extension; can be repeated)",
    )
    args = parser.parse_args()

    date_str = datetime.now().strftime("%Y%m%d")
    run_dir = ensure_run_dir(date_str)
    output_name = Path(args.output).name if args.output else f"{date_str}.json"
    out_path = run_dir / output_name
    prompts = load_prompts_from_yaml(args.config)
    prompt_override = prompts.get("prompt", "")
    if not prompt_override:
        print("Prompt is empty: please set `prompt` in your YAML config", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"Outputs folder: {run_dir}")
        prompt2 = prompts.get("prompt2", "")

        process_json_rows(args.input_path, str(out_path), prompt_override=prompt_override, image_paths=args.image_paths)
        print(f"LLM processing completed. Output saved to {out_path}")

        if prompt2:
            stage2_output = str(run_dir / f"{out_path.stem}_stage2.json")
            filtered_json = str(run_dir / f"{out_path.stem}_filtered.json")
            if filter_json_for_stage2(str(out_path), filtered_json, min_score=6.0):
                process_json_stage2(filtered_json, stage2_output, prompt2)
                # Best-effort: export unified web JSON (ReportDocument)
                try:
                    from web_export import export_report_documents

                    export_report_documents(Path("web_output") / "report_documents.json", wechat_stage2=Path(stage2_output))
                except Exception:
                    pass
            else:
                print("No JSON items after score filter; skip stage2.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"LLM processing failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
