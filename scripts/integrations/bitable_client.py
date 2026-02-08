from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

FEISHU_OPENAPI = "https://open.feishu.cn/open-apis"


def _to_attachment_value(file_tokens: List[str]) -> Optional[List[Dict[str, str]]]:
    tokens = [str(t).strip() for t in (file_tokens or []) if str(t).strip()]
    if not tokens:
        return None
    # Field type=attachment. Writing expects list of {"file_token": "..."}.
    return [{"file_token": t} for t in tokens]


def _to_link_value(url: str, text: str = "") -> Optional[Dict[str, str]]:
    u = str(url or "").strip()
    if not u:
        return None
    t = str(text or "").strip() or u
    # Bitable Link field expects an object.
    return {"link": u, "text": t}


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

    def create_field(self, field_name: str, field_type: int, property_obj: Optional[Dict[str, Any]] = None) -> None:
        import requests

        url = self._fields_url()
        payload: Dict[str, Any] = {"field_name": field_name, "type": int(field_type)}
        if property_obj is not None:
            payload["property"] = property_obj
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"bitable fields create http={resp.status_code} resp={data}")
        if isinstance(data, dict) and data.get("code") != 0:
            raise RuntimeError(f"bitable fields create failed: {data}")

    def update_field(self, field_id: str, field_name: str, field_type: int, property_obj: Optional[Dict[str, Any]] = None) -> None:
        import requests

        fid = str(field_id or "").strip()
        if not fid:
            return
        url = f"{self._fields_url()}/{fid}"
        payload: Dict[str, Any] = {"field_name": str(field_name or "").strip(), "type": int(field_type)}
        payload["property"] = property_obj if property_obj is not None else None
        resp = requests.put(url, headers=self._headers(), json=payload, timeout=self.timeout_sec)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"raw_text": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(f"bitable fields update http={resp.status_code} resp={data}")
        if isinstance(data, dict) and data.get("code") != 0:
            raise RuntimeError(f"bitable fields update failed: {data}")

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
        existing_by_name: Dict[str, Dict[str, Any]] = {}
        for it in existing:
            if not isinstance(it, dict):
                continue
            name = str(it.get("field_name") or "").strip()
            if name:
                existing_by_name[name] = it
        existing_names = set(existing_by_name.keys())

        # Create missing fields in a deterministic order
        for name in required.keys():
            desired_type = int(required[name].get("type") or 1)
            desired_prop = required[name].get("property")

            if name in existing_names:
                cur = existing_by_name.get(name) or {}
                cur_type = int(cur.get("type") or 0)
                cur_prop = cur.get("property")
                fid = str(cur.get("field_id") or "").strip()

                # Update selected fields when the type/property mismatches (best-effort).
                needs_update = False
                if cur_type and cur_type != desired_type:
                    needs_update = True
                if desired_type == 2 and isinstance(desired_prop, dict):
                    # for number fields, enforce formatter when possible
                    want_fmt = str(desired_prop.get("formatter") or "").strip()
                    cur_fmt = str((cur_prop or {}).get("formatter") or "").strip() if isinstance(cur_prop, dict) else ""
                    if want_fmt and want_fmt != cur_fmt:
                        needs_update = True

                if needs_update and fid:
                    try:
                        print(f"[bitable] ensure_field update name={name} type={cur_type}->{desired_type}")
                        self.update_field(fid, name, desired_type, property_obj=desired_prop if isinstance(desired_prop, dict) else None)
                        time.sleep(max(0.0, float(create_interval_sec)))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[bitable] ensure_field update skipped/failed name={name} err={exc}")
                continue

            print(f"[bitable] ensure_field create name={name} type={desired_type}")
            try:
                self.create_field(name, desired_type, property_obj=desired_prop if isinstance(desired_prop, dict) else None)
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
