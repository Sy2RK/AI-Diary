from typing import Any, Dict, List

from .qwen_client import get_client, parse_json_output


def build_prompt(title: str, content_text: str) -> str:
    return (
        "Summarize the note in Chinese. Limit summary to 100 chars. "
        "Return JSON with keys: summary (string), key_points (array of strings), tags (array of strings).\n\n"
        f"Title: {title}\nContent: {content_text}\n"
    )


def analyze_note(title: str, content_text: str) -> Dict[str, Any]:
    client = get_client()
    prompt = build_prompt(title or "", content_text or "")
    result = client.chat(prompt)
    parsed = parse_json_output(result["content"])
    return parsed


def analyze_note_multimodal(note_id: str, title: str, content_text: str, image_paths: List[str]) -> Dict[str, Any]:
    client = get_client()
    messages = [
        {
            "role": "system",
            "content": (
                "你是内容分析助手。输出严格 JSON: "
                '{"summary":"<不超过100字>","key_points":["要点1","要点2","要点3"],"tags":["标签1","标签2","标签3"]}'
            ),
        },
        {
            "role": "user",
            "content": _build_multimodal_content(title, content_text, image_paths),
        },
    ]
    result = client.chat_vl(messages, enable_thinking=True)
    parsed = parse_json_output(result["content"])
    return parsed


def _build_multimodal_content(title: str, content_text: str, image_paths: List[str]):
    from .qwen_client import image_file_to_data_url

    parts: List[Dict[str, Any]] = []
    txt_prefix = f"标题: {title}\n正文: {content_text}\n请总结，限制摘要100字内。"
    parts.append({"type": "text", "text": txt_prefix})
    # 限制图片数量，避免请求过大
    max_imgs = 6
    for idx, path in enumerate(image_paths[:max_imgs]):
        try:
            data_url = image_file_to_data_url(path)
        except Exception:
            continue
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


def get_note_image_paths(_note_id: str, _limit: int = 6) -> List[str]:
    raise NotImplementedError("Database-backed media storage was removed; pass image_paths explicitly instead.")
