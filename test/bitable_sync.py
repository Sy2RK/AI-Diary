from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

FEISHU_OPENAPI = "https://open.feishu.cn/open-apis"
UNIQUE_FIELD_NAME = "唯一键"
ATTACH_IMAGES_FIELD = "图片(附件)"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _split_tags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: List[str] = []
        for it in value:
            s = str(it or "").strip()
            if s:
                out.append(s)
        return out
    s = str(value or "").strip()
    if not s:
        return []
    s = s.replace("，", ",").replace("、", ",").replace("|", ",").replace(";", ",").replace("；", ",")
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]


def _safe_text(value: Any, max_len: int = 8000) -> str:
    s = str(value or "")
    if len(s) <= max_len:
        return s
    return s[: max_len - 20].rstrip() + "\n...[truncated]..."


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _parse_wechat_source_id(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    biz = re.search(r"(?:\\?|&)__biz=([^&]+)", u)
    mid = re.search(r"(?:\\?|&)mid=(\\d+)", u)
    idx = re.search(r"(?:\\?|&)idx=(\\d+)", u)
    if biz and mid and idx:
        return f"{biz.group(1)}_{mid.group(1)}_{idx.group(1)}"
    return _sha1(u)


def _extract_unique_key_field(fields: Dict[str, Any]) -> str:
    v = fields.get(UNIQUE_FIELD_NAME) or fields.get("unique_key")
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list) and v:
        return str(v[0]).strip()
    if v is None:
        return ""
    return str(v).strip()

def _coerce_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y", "on")

def _coerce_write_mode(val: Any) -> str:
    """
    Normalize write mode:
    - overwrite: clear table before writing
    - edit: append/upsert into existing table
    """
    s = str(val or "").strip().lower()
    if s in ("overwrite", "cover", "reset", "truncate", "clear"):
        return "overwrite"
    if s in ("edit", "append", "upsert", "merge", "update"):
        return "edit"
    return ""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_latest_path(glob_root: Path, pattern: str) -> Optional[Path]:
    if not glob_root.exists():
        return None
    candidates = list(glob_root.rglob(pattern))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

def _extract_json(text: str) -> Any:
    s = (text or "").strip()
    if "```" in s:
        # strip fenced block
        s = s.replace("```json", "```").replace("```JSON", "```")
        parts = s.split("```")
        # take the largest chunk
        s = max((p.strip() for p in parts if p.strip()), key=len, default=s)
    # try direct parse
    try:
        return json.loads(s)
    except Exception:
        pass
    # fallback: find first [...] or {...}
    m = re.search(r"(\\{.*\\}|\\[.*\\])", s, flags=re.S)
    if m:
        return json.loads(m.group(1))
    raise RuntimeError("LLM output is not valid JSON")


def _load_one_liner_prompt(config_path: str) -> str:
    from crawler_rednotes.config_rednotes import load_rednotes_config

    _cfg, raw = load_rednotes_config(config_path)
    prompt = str(raw.get("prompt_bitable_one_liner") or "").strip()
    if not prompt:
        raise RuntimeError("Missing prompt_bitable_one_liner in rednotes.yaml")
    return prompt


def _load_qwen_env_from_rednotes(config_path: str) -> Dict[str, Any]:
    from crawler_rednotes.config_rednotes import load_rednotes_config

    cfg, _raw = load_rednotes_config(config_path)
    if not cfg.qwen or not cfg.qwen.api_key:
        raise RuntimeError("Missing qwen.api_key in rednotes.yaml")
    return {
        "api_key": cfg.qwen.api_key,
        "base_url": cfg.qwen.base_url,
        "timeout_sec": int(cfg.qwen.timeout_sec or 120),
        "enable_thinking": bool(cfg.qwen.enable_thinking),
    }


def _generate_one_liners_qwen(
    config_path: str,
    items: List[Dict[str, Any]],
    model: str = "qwen3-max",
) -> Dict[str, str]:
    """
    Batch generate one-liners.
    items: [{"key": "<unique>", "title": "...", "summary": "..."}]
    returns: {key: one_liner}
    """
    from crawler_rednotes.llm.qwen_client import QwenClient

    prompt_tpl = _load_one_liner_prompt(config_path)
    q = _load_qwen_env_from_rednotes(config_path)
    client = QwenClient(
        api_key=q["api_key"],
        base_url=q["base_url"],
        model=model,
        timeout_sec=q["timeout_sec"],
    )

    user_payload = json.dumps(items, ensure_ascii=False)
    messages = [
        {"role": "system", "content": prompt_tpl},
        {"role": "user", "content": user_payload},
    ]
    resp = client.chat_vl(messages, enable_thinking=q["enable_thinking"])
    data = _extract_json(str(resp.get("content") or ""))
    if not isinstance(data, list):
        raise RuntimeError("One-liner response must be a JSON array")
    out: Dict[str, str] = {}
    for it in data:
        if not isinstance(it, dict):
            continue
        k = str(it.get("key") or "").strip()
        s = str(it.get("one_liner") or "").strip()
        if k and s:
            out[k] = s.replace("\n", " ").strip()
    return out


@dataclass
class UnifiedRecord:
    source: str
    source_id: str
    unique_key: str
    title: str
    author: str
    url: str
    publish_time: str
    summary: str
    tags: List[str]
    raw_content: str
    score: Optional[float]
    cover_url: str
    image_urls: List[str]
    ingest_time: str


def _record_to_bitable_fields(rec: UnifiedRecord) -> Dict[str, Any]:
    # Keep minimal readable fields; internal unique key is required for upsert.
    return {
        UNIQUE_FIELD_NAME: rec.unique_key or None,
        "编号": None,  # filled later
        "标题": rec.title or None,
        "一句话总结": None,  # filled later
        # Attachment field (for XHS). Filled later (cover + all images).
        ATTACH_IMAGES_FIELD: None,
    }

_VIEW_FIELD_SPECS: Dict[str, Dict[str, Any]] = {
    UNIQUE_FIELD_NAME: {"type": 1},
    "编号": {"type": 1},
    "标题": {"type": 1},
    "一句话总结": {"type": 1},
    # Bitable attachment field type is 17
    ATTACH_IMAGES_FIELD: {"type": 17},
}

_WECHAT_FIELD_SPECS: Dict[str, Dict[str, Any]] = {
    UNIQUE_FIELD_NAME: {"type": 1},
    "编号": {"type": 1},
    "标题": {"type": 1},
    "一句话总结": {"type": 1},
    # Keep schema aligned; WeChat leaves attachments empty
    ATTACH_IMAGES_FIELD: {"type": 17},
}


_XHS_FIELD_SPECS: Dict[str, Dict[str, Any]] = {
    UNIQUE_FIELD_NAME: {"type": 1},
    "编号": {"type": 1},
    "标题": {"type": 1},
    "一句话总结": {"type": 1},
    ATTACH_IMAGES_FIELD: {"type": 17},
}

def _to_attachment_value(file_tokens: List[str]) -> Optional[List[Dict[str, str]]]:
    tokens = [str(t).strip() for t in (file_tokens or []) if str(t).strip()]
    if not tokens:
        return None
    # Field type=attachment. Writing expects list of {"file_token": "..."}.
    return [{"file_token": t} for t in tokens]


class BitableClient:
    def __init__(self, token: str, app_token: str, table_id: str, timeout_sec: int = 15):
        self.token = token
        self.app_token = app_token
        self.table_id = table_id
        self.timeout_sec = timeout_sec

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _base_records_url(self) -> str:
        return f"{FEISHU_OPENAPI}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records"

    def _tables_url(self) -> str:
        return f"{FEISHU_OPENAPI}/bitable/v1/apps/{self.app_token}/tables"

    def _fields_url(self) -> str:
        return f"{FEISHU_OPENAPI}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields"

    def list_tables(self, page_size: int = 50) -> List[Dict[str, Any]]:
        import requests

        url = self._tables_url()
        resp = requests.get(url, headers=self._headers(), params={"page_size": int(page_size)}, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"bitable tables list http={resp.status_code} resp={data}")
        if isinstance(data, dict) and data.get("code") != 0:
            raise RuntimeError(f"bitable tables list failed: {data}")
        items = (data.get("data") or {}).get("items") or []
        return [it for it in items if isinstance(it, dict)]

    def create_table(self, name: str) -> str:
        import requests

        url = self._tables_url()
        name = (name or "").strip() or "table"
        # Try a few payload shapes for compatibility with different API versions.
        candidates = [
            {"table": {"name": name}},
            {"name": name},
            {"table_name": name},
        ]
        last_err: Optional[Exception] = None
        for payload in candidates:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_sec)
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {"raw_text": resp.text}
            if resp.status_code >= 400:
                last_err = RuntimeError(f"bitable tables create http={resp.status_code} resp={data}")
                continue
            if isinstance(data, dict) and data.get("code") != 0:
                last_err = RuntimeError(f"bitable tables create failed: {data}")
                continue
            tid = str(((data.get("data") or {}).get("table") or {}).get("table_id") or "").strip()
            if not tid:
                tid = str((data.get("data") or {}).get("table_id") or "").strip()
            if tid:
                return tid
            last_err = RuntimeError(f"bitable tables create missing table_id resp={data}")
        raise last_err or RuntimeError("bitable tables create failed")

    def get_or_create_table_id(self, table_name: str) -> str:
        name = (table_name or "").strip()
        for it in self.list_tables():
            if str(it.get("name") or "").strip() == name:
                tid = str(it.get("table_id") or "").strip()
                if tid:
                    return tid
        return self.create_table(name)

    def list_fields(self, page_size: int = 200) -> List[Dict[str, Any]]:
        import requests

        url = self._fields_url()
        items: List[Dict[str, Any]] = []
        page_token = ""
        page_size = max(1, min(int(page_size), 200))
        while True:
            params: Dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_sec)
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {"raw_text": resp.text}
            if resp.status_code >= 400:
                raise RuntimeError(f"bitable fields list http={resp.status_code} resp={data}")
            if isinstance(data, dict) and data.get("code") != 0:
                raise RuntimeError(f"bitable fields list failed: {data}")
            payload = data.get("data") or {}
            batch = payload.get("items") or []
            if isinstance(batch, list):
                items.extend([it for it in batch if isinstance(it, dict)])
            if not payload.get("has_more"):
                break
            page_token = str(payload.get("page_token") or payload.get("next_page_token") or "").strip()
            if not page_token:
                break
        return items

    def delete_field(self, field_id: str) -> None:
        import requests

        fid = str(field_id or "").strip()
        if not fid:
            return
        url = f"{self._fields_url()}/{fid}"
        resp = requests.delete(url, headers=self._headers(), timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"bitable fields delete http={resp.status_code} resp={data}")
        if isinstance(data, dict) and data.get("code") != 0:
            raise RuntimeError(f"bitable fields delete failed: {data}")

    def list_records(self, page_size: int = 500) -> List[Dict[str, Any]]:
        import requests

        url = self._base_records_url()
        items: List[Dict[str, Any]] = []
        page_token = ""
        page_size = max(1, min(int(page_size), 500))
        while True:
            params: Dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_sec)
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {"raw_text": resp.text}
            if resp.status_code >= 400:
                raise RuntimeError(f"bitable records list http={resp.status_code} resp={data}")
            if isinstance(data, dict) and data.get("code") != 0:
                raise RuntimeError(f"bitable records list failed: {data}")
            payload = data.get("data") or {}
            batch = payload.get("items") or []
            if isinstance(batch, list):
                items.extend([it for it in batch if isinstance(it, dict)])
            if not payload.get("has_more"):
                break
            page_token = str(payload.get("page_token") or payload.get("next_page_token") or "").strip()
            if not page_token:
                break
        return items

    def batch_delete(self, record_ids: List[str]) -> None:
        import requests

        ids = [str(rid).strip() for rid in (record_ids or []) if str(rid).strip()]
        if not ids:
            return
        url = f"{self._base_records_url()}/batch_delete"
        # API expects an array of record_id strings (not objects).
        # Ref error: "Invalid parameter type in json: Records. Invalid parameter value: {'record_id':'...'}"
        payload = {"records": ids}
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"bitable batch_delete http={resp.status_code} resp={data}")
        if isinstance(data, dict) and data.get("code") != 0:
            raise RuntimeError(f"bitable batch_delete failed: {data}")

    def clear_all_records(self, page_size: int = 500, chunk_size: int = 100, pace_sec: float = 0.25) -> int:
        """
        Delete all records in the current table. Returns number of deleted records (best effort).
        """
        deleted = 0
        items = self.list_records(page_size=page_size)
        record_ids = []
        for it in items:
            rid = str(it.get("record_id") or it.get("id") or "").strip()
            if rid:
                record_ids.append(rid)
        for i in range(0, len(record_ids), chunk_size):
            chunk = record_ids[i : i + chunk_size]
            self.batch_delete(chunk)
            deleted += len(chunk)
            if pace_sec:
                time.sleep(float(pace_sec))
        return deleted

    def upload_media(self, local_path: str, file_name: str = "", parent_type: str = "bitable_image") -> str:
        """
        Upload a local file to Feishu and return file_token for Bitable attachment fields.

        Uses drive/v1/medias/upload_all. For Bitable, use parent_type=bitable_image and parent_node=Base app_token.
        """
        import mimetypes
        import os
        import requests

        path = str(local_path or "").strip()
        if not path:
            raise ValueError("empty_path")
        if not os.path.exists(path):
            raise FileNotFoundError(path)

        url = f"{FEISHU_OPENAPI}/drive/v1/medias/upload_all"
        size = os.path.getsize(path)
        name = (file_name or "").strip() or os.path.basename(path)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"

        with open(path, "rb") as f:
            data = {
                "file_name": name,
                "parent_type": parent_type,
                "parent_node": self.app_token,
                "size": str(int(size)),
            }
            files = {"file": (name, f, ctype)}
            resp = requests.post(url, headers={"Authorization": f"Bearer {self.token}"}, data=data, files=files, timeout=self.timeout_sec)
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"drive media upload http={resp.status_code} resp={payload}")
        if isinstance(payload, dict) and payload.get("code") != 0:
            raise RuntimeError(f"drive media upload failed: {payload}")
        ft = str(((payload.get("data") or {}).get("file_token")) or "").strip()
        if not ft:
            raise RuntimeError(f"drive media upload missing file_token resp={payload}")
        return ft

    def create_field(self, field_name: str, field_type: int) -> None:
        import requests

        url = self._fields_url()
        payload: Dict[str, Any] = {"field_name": field_name, "type": int(field_type)}
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"bitable fields create http={resp.status_code} resp={data}")
        if isinstance(data, dict) and data.get("code") != 0:
            raise RuntimeError(f"bitable fields create failed: {data}")

    def ensure_fields(
        self,
        required: Dict[str, Dict[str, Any]],
        create_interval_sec: float = 3.2,
    ) -> None:
        """
        Ensure required fields exist in the table.

        Notes:
        - Feishu has request rate limits for field creation; we pace requests.
        - We avoid complex field types; most fields are created as plain text.
        """
        existing = self.list_fields()
        existing_names = {str(it.get("field_name") or "").strip() for it in existing if isinstance(it, dict)}

        # Create missing fields in a deterministic order
        for name in required.keys():
            if name in existing_names:
                continue

            desired_type = int(required[name].get("type") or 1)
            print(f"[bitable] ensure_field create name={name} type={desired_type}")

            # Try create; if score number type fails, fallback to text.
            try:
                self.create_field(name, desired_type)
            except Exception as exc:  # noqa: BLE001
                if name == "score" and desired_type != 1:
                    print(f"[bitable] ensure_field retry as text name={name} err={exc}")
                    self.create_field(name, 1)
                else:
                    raise

            existing_names.add(name)
            time.sleep(max(0.0, float(create_interval_sec)))

    def list_all_records(self, page_size: int = 500) -> List[Dict[str, Any]]:
        import requests

        url = self._base_records_url()
        items: List[Dict[str, Any]] = []
        page_token = ""
        page_size = max(1, min(int(page_size), 500))
        while True:
            params: Dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_sec)
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {"raw_text": resp.text}
            if resp.status_code >= 400:
                raise RuntimeError(f"bitable list http={resp.status_code} resp={data}")
            if data.get("code") != 0:
                raise RuntimeError(f"bitable list failed: {data}")
            payload = data.get("data") or {}
            batch = payload.get("items") or []
            if isinstance(batch, list):
                items.extend([it for it in batch if isinstance(it, dict)])
            if not payload.get("has_more"):
                break
            page_token = str(payload.get("page_token") or payload.get("next_page_token") or "").strip()
            if not page_token:
                break
        return items

    def build_unique_key_index(self) -> Dict[str, str]:
        index: Dict[str, str] = {}
        for it in self.list_all_records():
            rid = str(it.get("record_id") or "").strip()
            fields = it.get("fields") or {}
            if not rid or not isinstance(fields, dict):
                continue
            uk = _extract_unique_key_field(fields)
            if uk:
                index[uk] = rid
        return index

    def batch_create(self, records: List[Dict[str, Any]]) -> None:
        import requests

        if not records:
            return
        url = f"{self._base_records_url()}/batch_create"
        resp = requests.post(url, headers=self._headers(), json={"records": records}, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            if isinstance(data, dict) and data.get("code") == 91403:
                raise RuntimeError(
                    "bitable batch_create forbidden (91403). "
                    "Usually means the app has no write permission to this Base/Table: "
                    "1) ensure Bitable record write scopes are granted & approved, "
                    "2) ensure the Base is shared to the app with edit permission."
                )
            raise RuntimeError(f"bitable batch_create http={resp.status_code} resp={data}")
        if data.get("code") != 0:
            raise RuntimeError(f"bitable batch_create failed: {data}")

    def batch_update(self, records: List[Dict[str, Any]]) -> None:
        import requests

        if not records:
            return
        url = f"{self._base_records_url()}/batch_update"
        resp = requests.post(url, headers=self._headers(), json={"records": records}, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            if isinstance(data, dict) and data.get("code") == 91403:
                raise RuntimeError(
                    "bitable batch_update forbidden (91403). "
                    "Usually means the app has no write permission to this Base/Table: "
                    "1) ensure Bitable record write scopes are granted & approved, "
                    "2) ensure the Base is shared to the app with edit permission."
                )
            raise RuntimeError(f"bitable batch_update http={resp.status_code} resp={data}")
        if data.get("code") != 0:
            raise RuntimeError(f"bitable batch_update failed: {data}")

    def upsert(
        self,
        unified_records: List[UnifiedRecord],
        chunk_size: int = 100,
        allowed_field_names: Optional[set[str]] = None,
    ) -> Tuple[int, int]:
        if not unified_records:
            return 0, 0

        unique_index = self.build_unique_key_index()
        to_create: List[Tuple[UnifiedRecord, Dict[str, Any]]] = []
        to_update: List[Tuple[UnifiedRecord, Dict[str, Any]]] = []

        for rec in unified_records:
            fields = _record_to_bitable_fields(rec)
            if allowed_field_names is not None:
                fields = {k: v for k, v in fields.items() if k in allowed_field_names and v is not None}
            rid = unique_index.get(rec.unique_key)
            if rid:
                print(f"[bitable] upsert plan source={rec.source} unique_key={rec.unique_key} action=update")
                to_update.append((rec, {"record_id": rid, "fields": fields}))
            else:
                print(f"[bitable] upsert plan source={rec.source} unique_key={rec.unique_key} action=create")
                to_create.append((rec, {"fields": fields}))

        created = 0
        updated = 0

        for i in range(0, len(to_create), chunk_size):
            chunk = to_create[i : i + chunk_size]
            self.batch_create([payload for _rec, payload in chunk])
            for rec, _payload in chunk:
                print(f"[bitable] upsert ok source={rec.source} unique_key={rec.unique_key} action=create")
            created += len(chunk)

        for i in range(0, len(to_update), chunk_size):
            chunk = to_update[i : i + chunk_size]
            self.batch_update([payload for _rec, payload in chunk])
            for rec, _payload in chunk:
                print(f"[bitable] upsert ok source={rec.source} unique_key={rec.unique_key} action=update")
            updated += len(chunk)

        return created, updated


def _load_bitable_config(config_path: str) -> Tuple[str, str, str, str, bool, str]:
    # returns: app_id, app_secret, app_token, table_id, use_views, write_mode
    from crawler_rednotes.config_rednotes import load_rednotes_config

    cfg, raw = load_rednotes_config(config_path)
    if not cfg.feishu or not cfg.feishu.app_id or not cfg.feishu.app_secret:
        raise RuntimeError("Missing feishu.app_id/app_secret in rednotes.yaml (required for Bitable OpenAPI)")
    if not cfg.bitable or not cfg.bitable.app_token:
        raise RuntimeError("Missing bitable.app_token in rednotes.yaml")
    bitable_raw = raw.get("bitable") if isinstance(raw, dict) else {}
    if not isinstance(bitable_raw, dict):
        bitable_raw = {}
    use_views = _coerce_bool(bitable_raw.get("use_views"))
    write_mode = _coerce_write_mode(bitable_raw.get("write_mode")) or ("overwrite" if _coerce_bool(bitable_raw.get("overwrite")) else "")
    if not write_mode:
        write_mode = "overwrite"
    return cfg.feishu.app_id, cfg.feishu.app_secret, cfg.bitable.app_token, cfg.bitable.table_id, use_views, write_mode


def _get_tenant_access_token(app_id: str, app_secret: str) -> str:
    # Reuse existing Tools implementation when available.
    try:
        from Tools.feishu_utils import get_tenant_access_token

        return get_tenant_access_token(app_id, app_secret)
    except Exception:
        import requests

        url = f"{FEISHU_OPENAPI}/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"get_token failed: {data}")
        return data["tenant_access_token"]


def load_xhs_records(run_dir: str = "", config_path: str = "rednotes.yaml", score_threshold: float = 6.0) -> List[UnifiedRecord]:
    _ = config_path  # reserved for future use
    root = Path("outputs") / "rednotes"
    resolved = Path(run_dir) if run_dir else _find_latest_path(root, "analysis/stage1.json")
    if not resolved:
        raise FileNotFoundError("No XHS stage1.json found under outputs/rednotes/. Run option 8 first.")
    if resolved.is_file():
        run_root = resolved.parent.parent
    else:
        run_root = resolved

    stage1_path = run_root / "analysis" / "stage1.json"
    stage2_path = run_root / "analysis" / "stage2.json"
    notes_path = run_root / "notes.json"

    stage1 = _read_json(stage1_path)
    if not isinstance(stage1, list):
        raise RuntimeError(f"stage1.json must be a JSON array: {stage1_path}")

    stage2_analysis_by_title: Dict[str, str] = {}
    if stage2_path.exists():
        try:
            stage2 = _read_json(stage2_path) or {}
            if isinstance(stage2, dict):
                for key in ("top_items", "other_items"):
                    items = stage2.get(key) or []
                    if isinstance(items, list):
                        for it in items:
                            if not isinstance(it, dict):
                                continue
                            title = str(it.get("title") or "").strip()
                            analysis = str(it.get("stage2_analysis") or "").strip()
                            if title and analysis:
                                stage2_analysis_by_title[title] = analysis
        except Exception:
            pass

    raw_notes_by_id: Dict[str, Dict[str, Any]] = {}
    saved_paths_by_id: Dict[str, Dict[str, Any]] = {}
    if notes_path.exists():
        notes = _read_json(notes_path) or {}
        raw_notes = notes.get("raw_notes") or []
        if isinstance(raw_notes, list):
            for n in raw_notes:
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("note_id") or "").strip()
                if nid:
                    raw_notes_by_id[nid] = n
        items = notes.get("items") or []
        if isinstance(items, list):
            for it2 in items:
                if not isinstance(it2, dict):
                    continue
                nid = str(it2.get("note_id") or "").strip()
                if not nid:
                    continue
                saved_paths_by_id[nid] = {
                    "cover_path": str(it2.get("cover_path") or "").strip(),
                    "image_paths": it2.get("image_paths") or [],
                }

    out: List[UnifiedRecord] = []
    for it in stage1:
        if not isinstance(it, dict):
            continue
        note_id = str(it.get("note_id") or it.get("id") or "").strip()
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or it.get("source_url") or "").strip()
        score_val = it.get("score")
        try:
            score = float(score_val) if score_val is not None and str(score_val).strip() != "" else None
        except (TypeError, ValueError):
            score = None
        if score is None or score < score_threshold:
            continue

        raw_note = raw_notes_by_id.get(note_id) or {}
        author = str(raw_note.get("account_name") or "").strip()
        publish_time = str(raw_note.get("publish_time") or "").strip()

        summary = str(it.get("summary") or "").strip()
        extra = stage2_analysis_by_title.get(title, "")
        if extra:
            summary = (summary + "\n\n二次分析: " + extra).strip()

        cover_url = str(raw_note.get("cover_url") or "").strip()
        cover_path = str(saved_paths_by_id.get(note_id, {}).get("cover_path") or it.get("cover_path") or "").strip()
        cover = cover_url or cover_path

        image_urls: List[str] = []
        local_imgs = saved_paths_by_id.get(note_id, {}).get("image_paths")
        if isinstance(local_imgs, list):
            image_urls = [str(x).strip() for x in local_imgs if str(x).strip()]
        if not image_urls:
            if isinstance(raw_note.get("image_urls"), list):
                image_urls = [str(x).strip() for x in raw_note.get("image_urls") if str(x).strip()]
            if not image_urls and isinstance(raw_note.get("images"), list):
                image_urls = [str(x).strip() for x in raw_note.get("images") if str(x).strip()]

        text_path = str(it.get("text_path") or "").strip()
        raw_content = ""
        if text_path:
            p = Path(text_path)
            if p.exists():
                txt = p.read_text(encoding="utf-8")
                if "[AI分析]" in txt:
                    txt = txt.split("[AI分析]", 1)[0].rstrip()
                raw_content = _safe_text(txt, max_len=8000)

        tags = _split_tags(it.get("tag"))

        unique_key = note_id or _sha1(url)
        out.append(
            UnifiedRecord(
                source="xhs",
                source_id=note_id or _sha1(url),
                unique_key=unique_key,
                title=title,
                author=author,
                url=url,
                publish_time=publish_time,
                summary=summary,
                tags=tags,
                raw_content=raw_content,
                score=score,
                cover_url=cover,
                image_urls=image_urls,
                ingest_time=_now_iso(),
            )
        )

    return out


def _infer_run_date_from_records(records: List[UnifiedRecord]) -> str:
    # Prefer crawl/run date embedded in local paths like outputs/rednotes/YYYYMMDD[-N]/...
    for r in records:
        for s in [r.cover_url, *(r.image_urls or [])]:
            m = re.search(r"outputs[\\\\/]+rednotes[\\\\/]+(\\d{8})", str(s or ""), flags=re.I)
            if m:
                return m.group(1)

    # fallback from publish_time, else today
    for r in records:
        pt = (r.publish_time or "").strip()
        if len(pt) >= 10 and pt[4] == "-" and pt[7] == "-":
            return pt[:10].replace("-", "")
    return datetime.now().strftime("%Y%m%d")


def load_wechat_records(
    stage2_json_path: str = "",
    paper_json_path: str = "paper.json",
    score_threshold: float = 0.0,
) -> List[UnifiedRecord]:
    if stage2_json_path:
        stage2_path = Path(stage2_json_path)
    else:
        wechat_root = Path("outputs") / "wechat"
        stage2_path = _find_latest_path(wechat_root, "*_stage2.json") if wechat_root.exists() else None
    if not stage2_path or not stage2_path.exists():
        raise FileNotFoundError("No WeChat *_stage2.json found under outputs/wechat/. Run WeChat LLM stage2 first.")

    stage2 = _read_json(stage2_path)
    if not isinstance(stage2, list):
        raise RuntimeError(f"wechat stage2 must be a JSON array: {stage2_path}")

    paper_index: Dict[str, Dict[str, Any]] = {}
    p = Path(paper_json_path)
    if p.exists():
        try:
            paper = _read_json(p)
            if isinstance(paper, list):
                for it in paper:
                    if not isinstance(it, dict):
                        continue
                    link = str(it.get("link") or "").strip()
                    if link:
                        paper_index[link] = it
        except Exception:
            pass

    out: List[UnifiedRecord] = []
    for it in stage2:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or it.get("link") or "").strip()
        title = str(it.get("title") or "").strip()
        if not url or not title:
            continue

        score_val = it.get("score")
        try:
            score = float(score_val) if score_val is not None and str(score_val).strip() != "" else None
        except (TypeError, ValueError):
            score = None
        if score is not None and score < score_threshold:
            continue

        pid = _parse_wechat_source_id(url)
        unique_key = pid or _sha1(url)

        paper_item = paper_index.get(url) or {}
        author = str(paper_item.get("account_name") or "").strip()
        publish_time = str(paper_item.get("create_time") or "").strip()
        raw_content = _safe_text(paper_item.get("content") or "", max_len=8000)

        tags = _split_tags(it.get("tag"))
        summary = str(it.get("summary") or "").strip()

        out.append(
            UnifiedRecord(
                source="wechat",
                source_id=pid or _sha1(url),
                unique_key=unique_key,
                title=title,
                author=author,
                url=url,
                publish_time=publish_time,
                summary=summary,
                tags=tags,
                raw_content=raw_content,
                score=score,
                cover_url="",
                image_urls=[],
                ingest_time=_now_iso(),
            )
        )

    return out


def sync_to_bitable(
    records: List[UnifiedRecord],
    config_path: str = "rednotes.yaml",
    ensure_fields: bool = True,
    ensure_fields_interval_sec: float = 3.2,
    write_mode_override: str = "",
) -> Tuple[int, int]:
    app_id, app_secret, app_token, default_table_id, use_views, write_mode_cfg = _load_bitable_config(config_path)
    token = _get_tenant_access_token(app_id, app_secret)
    write_mode = _coerce_write_mode(write_mode_override) or write_mode_cfg

    table_ids: Dict[str, str] = {}
    if not use_views:
        # Auto create per-source tables (wechat/xhs) when configured.
        table_ids = _ensure_source_tables(config_path, token=token, app_token=app_token)
        if table_ids:
            print(f"[bitable] tables resolved: {table_ids}")

    # Route to table by source. Fallback to default_table_id when missing.
    source = (records[0].source if records else "").strip()
    table_id = default_table_id if use_views else ((table_ids.get(source) if source else "") or default_table_id)
    if not table_id:
        if use_views:
            raise RuntimeError("Missing target table_id (configure bitable.table_id for views mode)")
        raise RuntimeError("Missing target table_id (configure bitable.tables or bitable.table_id)")

    client = BitableClient(token=token, app_token=app_token, table_id=table_id)
    if ensure_fields:
        specs = _VIEW_FIELD_SPECS if use_views else (_WECHAT_FIELD_SPECS if source == "wechat" else _XHS_FIELD_SPECS)
        client.ensure_fields(specs, create_interval_sec=float(ensure_fields_interval_sec))
    specs = _VIEW_FIELD_SPECS if use_views else (_WECHAT_FIELD_SPECS if source == "wechat" else _XHS_FIELD_SPECS)

    if write_mode == "overwrite":
        try:
            deleted = client.clear_all_records()
            print(f"[bitable] cleared table_id={table_id} deleted={deleted}")
        except Exception as exc:  # noqa: BLE001
            # Do not block normal sync; user can re-run after fixing permissions.
            print(f"[bitable] clear skipped/failed: {exc}")

    # Upload XHS local images as Bitable attachments (scheme A).
    upload_cache: Dict[str, str] = {}

    def upload_if_local(path: str) -> str:
        p = str(path or "").strip()
        if not p:
            return ""
        # Only upload local existing files; URLs are ignored here.
        try:
            fp = Path(p)
            if fp.exists() and fp.is_file():
                key = str(fp.resolve()).lower()
                if key in upload_cache:
                    return upload_cache[key]
                ft = client.upload_media(str(fp))
                upload_cache[key] = ft
                return ft
        except Exception:
            return ""
        return ""

    # build one-liners in batch (for stage2-filtered results)
    try:
        items_for_llm = [
            {"key": r.unique_key, "title": r.title, "summary": (r.summary or "").strip()}
            for r in records
            if (r.unique_key or "").strip() and (r.title or "").strip()
        ]
        one_liners = _generate_one_liners_qwen(config_path, items_for_llm, model="qwen3-max")
    except Exception as exc:  # noqa: BLE001
        print(f"[bitable] one_liner skipped/failed: {exc}")
        one_liners = {}

    # fill 编号 / 一句话总结; 编号 uses date + score + rank (by score desc)
    date_str = _infer_run_date_from_records(records)
    sorted_records = sorted(records, key=lambda r: (-(r.score or 0.0), r.title))
    index_map: Dict[str, str] = {}
    for rank, r in enumerate(sorted_records, start=1):
        index_map[r.unique_key] = f"{date_str}-{rank:02d}"

    # create per-record field dicts
    allowed = set(specs.keys())
    created = 0
    updated = 0

    # Use client.upsert but pass augmented fields through temporary wrapper records.
    enriched: List[UnifiedRecord] = []
    for r in records:
        enriched.append(r)

    # Monkey patch: store computed fields in a side map used by filtered_fields below.
    def filtered_fields(r: UnifiedRecord) -> Dict[str, Any]:
        f = _record_to_bitable_fields(r)
        f["编号"] = index_map.get(r.unique_key)
        ol = one_liners.get(r.unique_key, "").strip()
        if ol:
            f["一句话总结"] = ol

        # Attachments: only for XHS; store cover and all images as attachments.
        if r.source == "xhs":
            file_tokens: List[str] = []
            cover_ft = upload_if_local(r.cover_url)
            if cover_ft:
                file_tokens.append(cover_ft)
            for ip in (r.image_urls or []):
                ft = upload_if_local(ip)
                if ft:
                    file_tokens.append(ft)
            att = _to_attachment_value(file_tokens)
            if att is not None:
                f[ATTACH_IMAGES_FIELD] = att

        f = {k: v for k, v in f.items() if k in allowed and v is not None}
        return f

    # Re-implement upsert loop so we can inject filtered_fields.
    unique_index = {} if write_mode == "overwrite" else client.build_unique_key_index()
    to_create: List[Dict[str, Any]] = []
    to_update: List[Dict[str, Any]] = []
    for r in enriched:
        fields = filtered_fields(r)
        rid = unique_index.get(r.unique_key)
        if rid:
            to_update.append({"record_id": rid, "fields": fields})
        else:
            to_create.append({"fields": fields})

    for i in range(0, len(to_create), 100):
        chunk = to_create[i : i + 100]
        client.batch_create(chunk)
        created += len(chunk)
    for i in range(0, len(to_update), 100):
        chunk = to_update[i : i + 100]
        client.batch_update(chunk)
        updated += len(chunk)
    return created, updated


def _ensure_source_tables(config_path: str, token: str, app_token: str) -> Dict[str, str]:
    """
    Ensure per-source tables exist and persist table_id back to rednotes.yaml.

    Uses `rednotes.yaml: bitable.tables` (nested mapping) when available.
    """
    from crawler_rednotes.config_rednotes import load_rednotes_config
    from crawler_rednotes.yaml_patch import set_scalar

    cfg, _raw = load_rednotes_config(config_path)
    if not cfg.bitable:
        raise RuntimeError("Missing bitable config in rednotes.yaml")

    tables_cfg = cfg.bitable.tables or {}
    tables_cfg.setdefault("wechat", {"name": "wechat", "table_id": ""})
    tables_cfg.setdefault("xhs", {"name": "xhs", "table_id": ""})

    resolved: Dict[str, str] = {}
    # We only need one client for listing/creating tables.
    app_client = BitableClient(token=token, app_token=app_token, table_id="DUMMY")
    changed = False

    for source, meta in tables_cfg.items():
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("name") or source).strip() or source
        tid = str(meta.get("table_id") or "").strip()
        if not tid:
            print(f"[bitable] ensure_table create source={source} name={name}")
            tid = app_client.get_or_create_table_id(name)
            meta["table_id"] = tid
            # Patch only the relevant key path, preserving comments/formatting.
            set_scalar(config_path, ["bitable", "tables", str(source), "table_id"], tid)
            set_scalar(config_path, ["bitable", f"{source}_table_id"], tid)
            changed = True
        resolved[str(source)] = tid
    # Do not rewrite the whole YAML; we already patched scalar fields above.
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync XHS/WeChat content into Feishu Bitable (upsert by unique_key).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize Bitable schema (create missing fields only, no records)")
    p_init.add_argument("-c", "--config", default="rednotes.yaml")
    p_init.add_argument("--ensure-fields-interval", type=float, default=3.2, help="Seconds between create-field requests")

    p_xhs = sub.add_parser("xhs", help="Sync Rednotes (XHS) from outputs/rednotes/<run>/analysis/stage1.json")
    p_xhs.add_argument("-c", "--config", default="rednotes.yaml")
    p_xhs.add_argument("--run-dir", default="", help="Run dir under outputs/rednotes/YYYYMMDD[-N]/ (default: latest)")
    p_xhs.add_argument("--min-score", type=float, default=6.0)
    p_xhs.add_argument("--note-id", default="", help="Only sync a specific note_id")
    p_xhs.add_argument("--no-ensure-fields", action="store_true", help="Do not auto-create missing fields")
    p_xhs.add_argument("--ensure-fields-interval", type=float, default=3.2, help="Seconds between create-field requests")

    p_wc = sub.add_parser("wechat", help="Sync WeChat from outputs/wechat/*_stage2.json + paper.json")
    p_wc.add_argument("-c", "--config", default="rednotes.yaml")
    p_wc.add_argument("--stage2", default="", help="WeChat *_stage2.json (default: latest)")
    p_wc.add_argument("--paper", default="paper.json")
    p_wc.add_argument("--min-score", type=float, default=0.0)
    p_wc.add_argument("--url", default="", help="Only sync a specific article url")
    p_wc.add_argument("--no-ensure-fields", action="store_true", help="Do not auto-create missing fields")
    p_wc.add_argument("--ensure-fields-interval", type=float, default=3.2, help="Seconds between create-field requests")

    p_all = sub.add_parser("all", help="Sync both sources (best effort)")
    p_all.add_argument("-c", "--config", default="rednotes.yaml")
    p_all.add_argument("--xhs-run-dir", default="")
    p_all.add_argument("--wechat-stage2", default="")
    p_all.add_argument("--paper", default="paper.json")
    p_all.add_argument("--xhs-min-score", type=float, default=6.0)
    p_all.add_argument("--wechat-min-score", type=float, default=0.0)
    p_all.add_argument("--no-ensure-fields", action="store_true", help="Do not auto-create missing fields")
    p_all.add_argument("--ensure-fields-interval", type=float, default=3.2, help="Seconds between create-field requests")

    args = parser.parse_args()

    try:
        if args.cmd == "init":
            app_id, app_secret, app_token, default_table_id, use_views, _write_mode = _load_bitable_config(args.config)
            token = _get_tenant_access_token(app_id, app_secret)

            # Optional cleanup: remove deprecated fields by name if they exist.
            # User request: remove cover/all-images text columns and any source(single-select) column.
            remove_names = {"封面", "全部图片内容", "来源", "单选", "封面(附件)"}

            if use_views:
                if not default_table_id:
                    raise RuntimeError("Missing bitable.table_id for views mode")
                client = BitableClient(token=token, app_token=app_token, table_id=default_table_id)
                try:
                    existing = client.list_fields()
                    for it in existing:
                        name = str(it.get("field_name") or "").strip()
                        fid = str(it.get("field_id") or "").strip()
                        if name in remove_names and fid:
                            client.delete_field(fid)
                            print(f"[bitable] init removed field: {name}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[bitable] init cleanup skipped/failed: {exc}")
                client.ensure_fields(_VIEW_FIELD_SPECS, create_interval_sec=float(args.ensure_fields_interval))
                print("[bitable] init ok (views mode)")
                return 0

            # tables mode: ensure per-source tables exist and ensure fields on each
            table_ids = _ensure_source_tables(args.config, token=token, app_token=app_token)
            for source, tid in table_ids.items():
                if not tid:
                    continue
                client = BitableClient(token=token, app_token=app_token, table_id=tid)
                try:
                    existing = client.list_fields()
                    for it in existing:
                        name = str(it.get("field_name") or "").strip()
                        fid = str(it.get("field_id") or "").strip()
                        if name in remove_names and fid:
                            client.delete_field(fid)
                            print(f"[bitable] init removed field: {name} table={source}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[bitable] init cleanup skipped/failed: {exc}")
                specs = _WECHAT_FIELD_SPECS if source == "wechat" else _XHS_FIELD_SPECS
                client.ensure_fields(specs, create_interval_sec=float(args.ensure_fields_interval))
            print("[bitable] init ok (tables mode)")
            return 0

        if args.cmd == "xhs":
            records = load_xhs_records(run_dir=args.run_dir, config_path=args.config, score_threshold=args.min_score)
            if args.note_id:
                records = [r for r in records if r.source_id == args.note_id]
            created, updated = sync_to_bitable(
                records,
                config_path=args.config,
                ensure_fields=not args.no_ensure_fields,
                ensure_fields_interval_sec=float(args.ensure_fields_interval),
            )
            print(f"[bitable] xhs created={created} updated={updated} total={len(records)}")
            return 0

        if args.cmd == "wechat":
            records = load_wechat_records(stage2_json_path=args.stage2, paper_json_path=args.paper, score_threshold=args.min_score)
            if args.url:
                records = [r for r in records if r.url == args.url]
            created, updated = sync_to_bitable(
                records,
                config_path=args.config,
                ensure_fields=not args.no_ensure_fields,
                ensure_fields_interval_sec=float(args.ensure_fields_interval),
            )
            print(f"[bitable] wechat created={created} updated={updated} total={len(records)}")
            return 0

        if args.cmd == "all":
            total_created = 0
            total_updated = 0
            any_success = False
            # If using single-table views mode + overwrite, clear only once (before the first successful sync),
            # otherwise the second sync would wipe the first batch.
            _app_id, _app_secret, _app_token, _table_id, use_views, write_mode = _load_bitable_config(args.config)
            cleared_once = False

            try:
                xhs = load_xhs_records(run_dir=args.xhs_run_dir, config_path=args.config, score_threshold=args.xhs_min_score)
                c, u = sync_to_bitable(
                    xhs,
                    config_path=args.config,
                    ensure_fields=not args.no_ensure_fields,
                    ensure_fields_interval_sec=float(args.ensure_fields_interval),
                    write_mode_override=("overwrite" if (write_mode == "overwrite" and not cleared_once) else "edit"),
                )
                total_created += c
                total_updated += u
                any_success = True
                if use_views and write_mode == "overwrite":
                    cleared_once = True
                print(f"[bitable] xhs created={c} updated={u} total={len(xhs)}")
            except Exception as exc:  # noqa: BLE001
                print(f"[bitable] xhs failed: {exc}")

            try:
                wc = load_wechat_records(stage2_json_path=args.wechat_stage2, paper_json_path=args.paper, score_threshold=args.wechat_min_score)
                c, u = sync_to_bitable(
                    wc,
                    config_path=args.config,
                    ensure_fields=not args.no_ensure_fields,
                    ensure_fields_interval_sec=float(args.ensure_fields_interval),
                    write_mode_override=("overwrite" if (write_mode == "overwrite" and not cleared_once) else "edit"),
                )
                total_created += c
                total_updated += u
                any_success = True
                if use_views and write_mode == "overwrite":
                    cleared_once = True
                print(f"[bitable] wechat created={c} updated={u} total={len(wc)}")
            except Exception as exc:  # noqa: BLE001
                print(f"[bitable] wechat failed: {exc}")

            print(f"[bitable] all done created={total_created} updated={total_updated}")
            return 0 if any_success else 1

        print("Unknown command")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[bitable] sync failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
