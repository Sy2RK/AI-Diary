import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml
from volcenginesdkarkruntime import Ark

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler_common.config_entry import DEFAULT_PROJECT_CONFIG, resolve_legacy_config_for_cli

# 基础配置（可直接填写；api_key 建议用环境变量 ARK_API_KEY）
LLM_API_URL = "https://ark.cn-beijing.volces.com/api/v3"  # 可留空使用默认域名
LLM_API_KEY = "1cd0b5da-fcaa-4ebe-9bf5-2364f1ae81fc"  # 留空则使用环境变量 ARK_API_KEY
LLM_MODEL = "doubao-seed-1-6-251015"  # 模型/endpoint 名
LLM_PROMPT = ""  # prompt 仅从 YAML 读取
LLM_TEMPERATURE = 0.3
LLM_TOP_P = 0.3
LLM_MAX_TOKENS = 32768
LLM_BATCH_SIZE = 10
LLM_LOG_PATH = "llm_calls.log"
OUTPUTS_DIR = "outputs"
WECHAT_OUTPUTS_SUBDIR = "wechat"

_ark_client: Ark | None = None


def get_ark_client() -> Ark:
    global _ark_client
    if _ark_client is not None:
        return _ark_client
    api_key = (LLM_API_KEY or os.environ.get("ARK_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("请在 llm.py 填写 LLM_API_KEY 或设置环境变量 ARK_API_KEY")
    kwargs: Dict[str, str] = {"api_key": api_key}
    if LLM_API_URL:
        kwargs["base_url"] = LLM_API_URL.rstrip("/")
    _ark_client = Ark(**kwargs)
    return _ark_client


def call_volcengine_api(prompt: str, content: str) -> str:
    client = get_ark_client()
    model = LLM_MODEL.strip()
    if not model:
        raise RuntimeError("请在 llm.py 填写 LLM_MODEL")
    if not prompt:
        raise RuntimeError("系统提示词为空，请设置 LLM_PROMPT 或使用 --prompt/--prompt-pdf")

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            temperature=LLM_TEMPERATURE,
            top_p=LLM_TOP_P,
            max_tokens=LLM_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"LLM request failed: {exc}") from exc

    choices = getattr(completion, "choices", None) or []
    if not choices:
        raise RuntimeError("LLM response missing choices")
    message = choices[0].message if hasattr(choices[0], "message") else choices[0].get("message", {})
    content_resp = getattr(message, "content", None) or (message.get("content") if isinstance(message, dict) else None)
    if not content_resp:
        raise RuntimeError("LLM response missing message.content")
    return content_resp


def log_event(message: str) -> None:
    """追加写入简易日志，记录时间和消息。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    Path(LLM_LOG_PATH).open("a", encoding="utf-8").write(f"[{ts}] {message}\n")


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


def process_json_rows(json_path: str, output_path: str, prompt_override: str = "") -> None:
    """逐行调用 LLM，返回 JSON 对象并追加写入 JSONL（每行一个对象）。"""
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
        rows: List[Dict[str, str]] = [data]
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
    with out_path.open("a", encoding="utf-8") as f_out:
        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total)
            batch = rows[start:end]
            item_lines = []
            for idx, row in enumerate(batch, start=1):
                title = row.get("title", "")
                url = row.get("link", "") or row.get("url", "")
                content = row.get("content", "")
                item_lines.append(
                    f"### item {idx}\n"
                    f"标题: {title}\n"
                    f"链接: {url}\n"
                    f"正文:\n{content}\n"
                )
            user_content = (
                f"{base_prompt}\n\n"
                f"本次输入包含 {len(batch)} 篇文章，请按输入顺序输出 {len(batch)} 行 JSONL，"
                "每行一个对象，字段且仅包含 title、url、summary、score；"
                "summary 为150-200字中文总结（海外AI应用/游戏视角），score 为0-10数字；\n\n"
                + "\n".join(item_lines)
            )
            try:
                llm_result = call_volcengine_api(base_prompt, user_content)
                lines = [line for line in llm_result.splitlines() if line.strip()]
                parsed_items: List[Dict[str, Any]] = []
                for line in lines:
                    try:
                        parsed_items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                if not parsed_items:
                    try:
                        parsed_obj = json.loads(llm_result)
                        if isinstance(parsed_obj, list):
                            parsed_items = [item for item in parsed_obj if isinstance(item, dict)]
                        elif isinstance(parsed_obj, dict):
                            parsed_items = [parsed_obj]
                    except json.JSONDecodeError:
                        parsed_items = []

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
                    }
                    f_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
                log_event(f"LLM success batch {batch_idx + 1}/{total_batches} ({len(batch)} rows)")
                print(f"LLM batch [{batch_idx + 1}/{total_batches}] done ({start + 1}-{end})")
            except Exception as exc:  # noqa: BLE001
                log_event(f"LLM failed batch {batch_idx + 1}/{total_batches}: {exc}")
                print(
                    f"LLM batch [{batch_idx + 1}/{total_batches}] failed: {exc}",
                    file=sys.stderr,
                )


def load_prompts_from_yaml(path: str) -> Dict[str, str]:
    """从 YAML 配置读取 prompt 与 prompt_stage2 字段。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        prompt = str(data.get("prompt") or "").strip()
        prompt2 = str(data.get("prompt_stage2") or data.get("prompt2") or "").strip()
        return {"prompt": prompt, "prompt2": prompt2}
    except Exception:
        return {"prompt": "", "prompt2": ""}


def process_jsonl_stage2(jsonl_path: str, output_path: str, prompt: str) -> None:
    """使用第二个 prompt 对 JSONL 文件进行二次处理。"""
    content = Path(jsonl_path).read_text(encoding="utf-8")
    if not content.strip():
        print(f"No JSONL content at {jsonl_path}", file=sys.stderr)
        return
    try:
        llm_result = call_volcengine_api(prompt, content)
        text = llm_result.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise RuntimeError("Stage2 output must be a JSON array")
        Path(output_path).write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_event(f"LLM stage2 success for {jsonl_path} -> {output_path}")
        print(f"LLM stage2 done, saved to {output_path}")
    except Exception as exc:  # noqa: BLE001
        log_event(f"LLM stage2 failed for {jsonl_path}: {exc}")
        print(f"LLM stage2 failed: {exc}", file=sys.stderr)


def filter_jsonl_for_stage2(jsonl_path: str, output_path: str, min_score: float = 6.0) -> bool:
    """过滤 JSONL，移除 score < min_score 的行，输出过滤后的 JSONL。"""
    in_path = Path(jsonl_path)
    if not in_path.exists() or in_path.stat().st_size == 0:
        print(f"No JSONL content to filter at {jsonl_path}", file=sys.stderr)
        return False
    kept_lines: List[str] = []
    for line in in_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        score_val = obj.get("score", 0)
        try:
            score_num = float(score_val)
        except (TypeError, ValueError):
            score_num = 0.0
        if score_num >= min_score:
            kept_lines.append(json.dumps(obj, ensure_ascii=False))
    out_path = Path(output_path)
    out_path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
    log_event(f"Filtered JSONL for stage2: kept {len(kept_lines)} lines (min_score={min_score})")
    return bool(kept_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Volcengine LLM on JSON (whole file in one request)")
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
        help="Output JSONL filename (default: YYYYMMDD.jsonl under outputs/wechat/YYYYMMDD[-N]/)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=DEFAULT_PROJECT_CONFIG,
        help="配置入口（统一配置 configs/config.yaml 或 legacy wechat.yaml）",
    )
    parser.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    args = parser.parse_args()

    date_str = datetime.now().strftime("%Y%m%d")
    run_dir = ensure_run_dir(date_str)
    output_name = Path(args.output).name if args.output else f"{date_str}.jsonl"
    output_path = str(run_dir / output_name)

    try:
        cfg_path = resolve_legacy_config_for_cli(args.config, kind="wechat", profile=args.profile)
        prompts = load_prompts_from_yaml(cfg_path)
        prompt_override = prompts.get("prompt", "")
        if not prompt_override:
            print("Prompt is empty: please set `prompt` in your YAML config", file=sys.stderr)
            sys.exit(1)
        print(f"Outputs folder: {run_dir}")
        jsonl_path = Path(output_path)
        prompt2 = prompts.get("prompt2", "")
        if jsonl_path.exists():
            print(f"Found existing JSONL: {output_path}, skipping step1.")
            log_event(f"Skip step1, reuse JSONL {output_path}")
        else:
            process_json_rows(args.input_path, output_path, prompt_override=prompt_override)
            print(f"LLM processing completed. Output appended to {output_path}")

        if prompt2:
            stage2_output = str(jsonl_path.with_name(f"{jsonl_path.stem}_stage2.json"))
            filtered_jsonl = str(jsonl_path.with_name(f"{jsonl_path.stem}_filtered.jsonl"))
            if filter_jsonl_for_stage2(output_path, filtered_jsonl, min_score=6.0):
                process_jsonl_stage2(filtered_jsonl, stage2_output, prompt2)
            else:
                print("No JSONL rows after score filter; skip stage2.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"LLM processing failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
