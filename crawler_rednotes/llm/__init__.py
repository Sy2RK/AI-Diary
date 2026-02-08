import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List


class QwenClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout_sec: int):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec

    def chat(self, prompt: str) -> Dict[str, Any]:
        import requests

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a content analyst. Output only JSON with keys: "
                        "summary, key_points, tags."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_sec)
        if not resp.ok:
            # Include response body for debugging (truncate to avoid flooding logs).
            body = (resp.text or "").strip()
            if len(body) > 1200:
                body = body[:1200] + "...(truncated)"
            raise RuntimeError(f"Qwen HTTP {resp.status_code} error: {body}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return {"content": content, "raw": data}

    def chat_vl(self, messages: List[Dict[str, Any]], enable_thinking: bool = False) -> Dict[str, Any]:
        """
        Send multimodal messages (text + images). messages follows OpenAI compatible content list.
        """
        import requests

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if enable_thinking:
            payload["extra_body"] = {"enable_thinking": True}
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_sec)
        if not resp.ok:
            body = (resp.text or "").strip()
            if len(body) > 1200:
                body = body[:1200] + "...(truncated)"
            raise RuntimeError(f"Qwen HTTP {resp.status_code} error: {body}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return {"content": content, "raw": data}


def parse_json_output(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
    return json.loads(text)


def get_client() -> QwenClient:
    api_key = (os.environ.get("QWEN_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")).strip()
    if not api_key:
        raise RuntimeError("Missing Qwen API key. Set env QWEN_API_KEY or DASHSCOPE_API_KEY")
    base_url = (os.environ.get("QWEN_API_URL", "") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    model = (os.environ.get("QWEN_MODEL", "") or "qwen3-max").strip()
    timeout_sec = int(os.environ.get("QWEN_TIMEOUT_SEC", "60"))
    return QwenClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_sec=timeout_sec,
    )


def image_file_to_data_url(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    mime, _ = mimetypes.guess_type(p.name)
    mime = mime or "image/jpeg"
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"
