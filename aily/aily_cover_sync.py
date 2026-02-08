from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AilyConfig:
    base_url: str
    app_id: str
    biz_user_id: str
    user_access_token: str
    poll_interval_sec: float
    timeout_sec: float


@dataclass
class OutputConfig:
    dir_name: str
    ext: str
    force: bool


@dataclass
class CoverSyncConfig:
    aily: AilyConfig
    prompt_template: str
    output: OutputConfig


def _repo_root() -> Path:
    # aily/aily_cover_sync.py -> repo root is parent of "aily"
    return Path(__file__).resolve().parent.parent


def _ensure_repo_root_on_path() -> None:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _resolve_path(p: str) -> str:
    s = str(p or "").strip()
    if not s:
        return s
    direct = Path(s)
    if direct.exists():
        return str(direct)
    candidate = _repo_root() / s
    return str(candidate)


def _coerce_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def _load_bitable_sync_module():
    try:
        from scripts.integrations import bitable_sync as mod  # type: ignore

        return mod
    except Exception:
        import bitable_sync as mod  # type: ignore

        return mod


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
        # Minimal fallback: accept JSON only
        data = json.loads(text)
        return data if isinstance(data, dict) else {}


def load_cover_sync_config(path: str = "aily_cover.yaml") -> CoverSyncConfig:
    raw = _read_yaml(_resolve_path(path))
    aily_raw = raw.get("aily") if isinstance(raw.get("aily"), dict) else {}
    prompt_raw = raw.get("prompt") if isinstance(raw.get("prompt"), dict) else {}
    output_raw = raw.get("output") if isinstance(raw.get("output"), dict) else {}

    user_access_token = str(aily_raw.get("user_access_token") or "").strip() or os.environ.get("AILY_USER_ACCESS_TOKEN", "").strip()

    aily = AilyConfig(
        base_url=str(aily_raw.get("base_url") or "https://open.feishu.cn/open-apis").strip(),
        app_id=str(aily_raw.get("app_id") or "").strip(),
        biz_user_id=str(aily_raw.get("biz_user_id") or "").strip() or os.environ.get("AILY_BIZ_USER_ID", "").strip(),
        user_access_token=user_access_token,
        poll_interval_sec=float(aily_raw.get("poll_interval_sec") or 2.0),
        timeout_sec=float(aily_raw.get("timeout_sec") or 180.0),
    )
    if not aily.app_id:
        raise RuntimeError("Missing aily.app_id in aily_cover.yaml")
    if not aily.biz_user_id:
        raise RuntimeError("Missing aily.biz_user_id in aily_cover.yaml (or env AILY_BIZ_USER_ID)")
    if not aily.user_access_token:
        raise RuntimeError("Missing aily.user_access_token in aily_cover.yaml (or env AILY_USER_ACCESS_TOKEN)")

    tpl = str(prompt_raw.get("template") or "").rstrip()
    if not tpl:
        raise RuntimeError("Missing prompt.template in aily_cover.yaml")

    output = OutputConfig(
        dir_name=str(output_raw.get("dir_name") or "covers").strip() or "covers",
        ext=str(output_raw.get("ext") or ".png").strip() or ".png",
        force=_coerce_bool(output_raw.get("force", False)),
    )
    return CoverSyncConfig(aily=aily, prompt_template=tpl, output=output)


class AilyClient:
    def __init__(self, cfg: AilyConfig, timeout_sec: float = 30.0):
        self.cfg = cfg
        self.timeout_sec = float(timeout_sec)

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cfg.user_access_token}",
            "X-Aily-BizUserID": self.cfg.biz_user_id,
        }

    def create_session(self) -> str:
        import requests

        url = f"{self.cfg.base_url}/aily/v1/sessions"
        resp = requests.post(url, headers=self._headers(), json={}, timeout=self.timeout_sec)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw_text": resp.text}
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"aily create_session failed http={resp.status_code} resp={data}")
        sid = str(((data.get("data") or {}).get("session") or {}).get("id") or "").strip()
        if not sid:
            raise RuntimeError(f"aily create_session missing session_id resp={data}")
        return sid

    def create_message(self, session_id: str, content: str, idempotent_id: str) -> str:
        import requests

        url = f"{self.cfg.base_url}/aily/v1/sessions/{session_id}/messages"
        body = {
            "idempotent_id": idempotent_id,
            "content_type": "MDX",
            "content": content,
        }
        resp = requests.post(url, headers=self._headers(), json=body, timeout=self.timeout_sec)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw_text": resp.text}
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"aily create_message failed http={resp.status_code} resp={data}")
        mid = str(((data.get("data") or {}).get("message") or {}).get("id") or "").strip()
        return mid

    def create_run(self, session_id: str) -> str:
        import requests

        url = f"{self.cfg.base_url}/aily/v1/sessions/{session_id}/runs"
        body = {"app_id": self.cfg.app_id}
        resp = requests.post(url, headers=self._headers(), json=body, timeout=self.timeout_sec)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw_text": resp.text}
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"aily create_run failed http={resp.status_code} resp={data}")
        rid = str(((data.get("data") or {}).get("run") or {}).get("id") or "").strip()
        if not rid:
            raise RuntimeError(f"aily create_run missing run_id resp={data}")
        return rid

    def get_run(self, session_id: str, run_id: str) -> Dict[str, Any]:
        import requests

        url = f"{self.cfg.base_url}/aily/v1/sessions/{session_id}/runs/{run_id}"
        resp = requests.get(url, headers=self._headers(), timeout=self.timeout_sec)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw_text": resp.text}
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"aily get_run failed http={resp.status_code} resp={data}")
        run = (data.get("data") or {}).get("run") or {}
        return run if isinstance(run, dict) else {}

    def list_messages(self, session_id: str, run_id: str) -> List[Dict[str, Any]]:
        import requests

        url = f"{self.cfg.base_url}/aily/v1/sessions/{session_id}/messages"
        params = {"run_id": run_id, "with_partial_message": "true"}
        resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_sec)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw_text": resp.text}
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"aily list_messages failed http={resp.status_code} resp={data}")
        msgs = (data.get("data") or {}).get("messages") or []
        return [m for m in msgs if isinstance(m, dict)]

    def get_file(self, file_id: str) -> Dict[str, Any]:
        import requests

        url = f"{self.cfg.base_url}/aily/v1/files/{file_id}"
        resp = requests.get(url, headers=self._headers(), timeout=self.timeout_sec)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"_raw_text": resp.text}
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"aily get_file failed http={resp.status_code} resp={data}")
        f = (data.get("data") or {}).get("file") or {}
        return f if isinstance(f, dict) else {}


_URL_RE = re.compile(r"https?://[^\\s)\\]}>\"']+", flags=re.I)
_IMG_EXT_RE = re.compile(r"\\.(png|jpg|jpeg|webp|gif)(\\?.*)?$", flags=re.I)


def _extract_image_urls_from_mdx(text: str) -> List[str]:
    s = str(text or "")
    urls = [m.group(0) for m in _URL_RE.finditer(s)]
    out: List[str] = []
    seen: set[str] = set()
    image_like = [u for u in urls if _IMG_EXT_RE.search(u)]
    candidates = image_like or urls
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_urls_from_any(payload: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    likely_url_keys = {"url", "download_url", "downloadurl", "file_url", "fileurl", "image", "image_url", "imageurl"}

    def _add(u: str) -> None:
        s = str(u or "").strip()
        if not s or not s.lower().startswith(("http://", "https://")):
            return
        if s in seen:
            return
        seen.add(s)
        out.append(s)

    def _walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, vv in v.items():
                if isinstance(vv, str) and str(k).strip().lower() in likely_url_keys:
                    _add(vv)
                _walk(vv)
            return
        if isinstance(v, list):
            for it in v:
                _walk(it)
            return
        if isinstance(v, str):
            for m in _URL_RE.finditer(v):
                _add(m.group(0))

    _walk(payload)
    return out


def _normalize_file_ids(value: Any) -> List[str]:
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, list):
        out: List[str] = []
        for it in value:
            s = str(it or "").strip()
            if s:
                out.append(s)
        return out
    return []


def _extract_image_urls_from_msg(msg: Dict[str, Any]) -> List[str]:
    content_raw = msg.get("content")
    if isinstance(content_raw, str):
        mdx = content_raw
    else:
        try:
            mdx = json.dumps(content_raw, ensure_ascii=False)
        except Exception:
            mdx = str(content_raw or "")
    urls = _extract_image_urls_from_mdx(mdx)
    if urls:
        return urls
    return _extract_urls_from_any(msg)


def _extract_image_urls_from_file_meta(meta: Dict[str, Any]) -> List[str]:
    urls = _extract_urls_from_any(meta)
    out: List[str] = []
    seen: set[str] = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _is_assistant_message(msg: Dict[str, Any]) -> bool:
    sender = msg.get("sender") or {}
    if isinstance(sender, dict):
        st = str(sender.get("sender_type") or "").strip().upper()
        if st == "ASSISTANT":
            return True
    # fallback: some payloads may put sender_type at top-level
    st2 = str(msg.get("sender_type") or "").strip().upper()
    return st2 == "ASSISTANT"


def aily_generate_cover_image_url(
    client: AilyClient,
    *,
    prompt: str,
    idempotent_id: str,
    verbose: bool = False,
) -> Tuple[str, str, str]:
    """
    Returns (session_id, run_id, image_url).
    """
    session_id = client.create_session()
    _ = client.create_message(session_id, content=prompt, idempotent_id=idempotent_id)
    run_id = client.create_run(session_id)

    deadline = time.time() + float(client.cfg.timeout_sec)
    while True:
        run = client.get_run(session_id, run_id)
        status = str(run.get("status") or "").strip().upper()
        if verbose:
            print(f"[aily] run status={status} session_id={session_id} run_id={run_id}")
        if status in ("COMPLETED", "SUCCEEDED"):
            break
        if status in ("FAILED", "CANCELLED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"aily run failed status={status} run={run}")
        if time.time() >= deadline:
            raise TimeoutError(f"aily run timeout session_id={session_id} run_id={run_id} status={status}")
        time.sleep(float(client.cfg.poll_interval_sec))

    messages = client.list_messages(session_id, run_id)
    # Prefer latest assistant message
    messages = [m for m in messages if _is_assistant_message(m)]
    messages.reverse()

    for msg in messages:
        urls = _extract_image_urls_from_msg(msg)
        if urls:
            return session_id, run_id, urls[0]
        # Fallback to file_ids if present
        file_ids = _normalize_file_ids(msg.get("file_ids") or msg.get("fileIds") or [])
        if file_ids:
            # We only have file meta from get_file; URL retrieval is not documented here.
            if verbose:
                print(f"[aily] message has file_ids but no image urls: {file_ids}")
            # If file meta contains a downloadable url, use it.
            for fid in file_ids:
                fid2 = str(fid or "").strip()
                if not fid2:
                    continue
                try:
                    meta = client.get_file(fid2)
                except Exception as exc:  # noqa: BLE001
                    if verbose:
                        print(f"[aily] get_file failed file_id={fid2}: {exc}")
                    continue
                meta_urls = _extract_image_urls_from_file_meta(meta)
                if meta_urls:
                    return session_id, run_id, meta_urls[0]

    raise RuntimeError("No image url found in Aily assistant messages")


def _download(url: str, out_path: Path) -> Path:
    import requests

    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    out_path.write_bytes(r.content)
    return out_path


def _render_prompt(template: str, *, title: str, one_liner: str, source: str) -> str:
    return template.format(title=title, one_liner=one_liner, source=source)


def _ensure_cover_field(
    *,
    config_path: str,
    table_id: str,
    app_token: str,
    tenant_token: str,
) -> None:
    bitable_sync = _load_bitable_sync_module()

    client = bitable_sync.BitableClient(token=tenant_token, app_token=app_token, table_id=table_id)
    client.ensure_fields(
        {
            bitable_sync.UNIQUE_FIELD_NAME: {"type": 1},
            bitable_sync.COVER_ATTACH_FIELD: {"type": 17},
        },
        create_interval_sec=0.6,
    )


def _upsert_cover_by_unique_key(
    *,
    table_id: str,
    app_token: str,
    tenant_token: str,
    unique_key: str,
    file_token: str,
) -> None:
    bitable_sync = _load_bitable_sync_module()

    client = bitable_sync.BitableClient(token=tenant_token, app_token=app_token, table_id=table_id)
    idx = client.build_unique_key_index()
    rid = idx.get(unique_key)
    if not rid:
        raise RuntimeError(f"RecordNotFound: unique_key={unique_key} table_id={table_id}")
    client.batch_update(
        [
            {
                "record_id": rid,
                "fields": {bitable_sync.COVER_ATTACH_FIELD: [{"file_token": file_token}]},
            }
        ]
    )


def _load_one_liners_for_records(config_path: str, records: List[Any]) -> Dict[str, str]:
    # Use the same one-liner generator as Bitable sync.
    bitable_sync = _load_bitable_sync_module()

    items = [{"key": r.unique_key, "title": r.title, "summary": (r.summary or "").strip()} for r in records]
    return bitable_sync._generate_one_liners_qwen(config_path=config_path, items=items, model="qwen3-max")


def _resolve_bitable_table(
    *,
    config_path: str,
    tenant_token: str,
    source: str,
) -> Tuple[str, str]:
    """
    Returns (app_token, table_id).
    """
    bitable_sync = _load_bitable_sync_module()

    _app_id, _app_secret, app_token, default_table_id, use_views, _write_mode = bitable_sync._load_bitable_config(config_path)
    if use_views:
        if not default_table_id:
            raise RuntimeError("Missing bitable.table_id for views mode")
        return app_token, default_table_id

    table_ids = bitable_sync._ensure_source_tables(config_path, token=tenant_token, app_token=app_token)
    tid = (table_ids.get(source) or "").strip()
    if not tid:
        raise RuntimeError(f"Missing table_id for source={source} (tables mode)")
    return app_token, tid


def sync_covers_xhs(
    *,
    run_dir: str,
    config_path: str,
    aily_cfg_path: str,
    force: bool,
    limit: int,
    verbose: bool,
) -> int:
    bitable_sync = _load_bitable_sync_module()

    cfg = load_cover_sync_config(aily_cfg_path)
    client = AilyClient(cfg.aily)

    resolved_run_dir = _resolve_path(run_dir)
    resolved_project_config = _resolve_path(config_path)
    records = bitable_sync.load_xhs_records(run_dir=resolved_run_dir, config_path=resolved_project_config, score_threshold=6.0)
    if limit > 0:
        records = records[: int(limit)]

    one_liners = _load_one_liners_for_records(resolved_project_config, records)

    # Tenant token + bitable routing
    app_id, app_secret, _app_token, _table_id, _use_views, _wm = bitable_sync._load_bitable_config(resolved_project_config)
    tenant_token = bitable_sync._get_tenant_access_token(app_id, app_secret)
    app_token, table_id = _resolve_bitable_table(config_path=resolved_project_config, tenant_token=tenant_token, source="xhs")
    _ensure_cover_field(config_path=resolved_project_config, table_id=table_id, app_token=app_token, tenant_token=tenant_token)

    run_root = Path(resolved_run_dir)
    out_dir = run_root / cfg.output.dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    for r in records:
        one = one_liners.get(r.unique_key, "").strip()
        if not one:
            continue
        out_path = out_dir / f"{r.unique_key}{cfg.output.ext}"
        if out_path.exists() and not (force or cfg.output.force):
            ok += 1
            continue

        prompt = _render_prompt(cfg.prompt_template, title=r.title, one_liner=one, source="xhs")
        _sid, _rid, image_url = aily_generate_cover_image_url(
            client,
            prompt=prompt,
            idempotent_id=f"cover_{r.unique_key}",
            verbose=verbose,
        )
        _download(image_url, out_path)

        # Upload to Feishu Drive (bitable_image) and write to attachment field
        bitable_client = bitable_sync.BitableClient(token=tenant_token, app_token=app_token, table_id=table_id)
        file_token = bitable_client.upload_media(str(out_path))
        _upsert_cover_by_unique_key(
            table_id=table_id,
            app_token=app_token,
            tenant_token=tenant_token,
            unique_key=r.unique_key,
            file_token=file_token,
        )
        ok += 1
        print(f"[aily_cover] xhs ok unique_key={r.unique_key} path={out_path}")
    return ok


def sync_covers_wechat(
    *,
    stage2_path: str,
    config_path: str,
    aily_cfg_path: str,
    force: bool,
    limit: int,
    verbose: bool,
) -> int:
    bitable_sync = _load_bitable_sync_module()

    cfg = load_cover_sync_config(aily_cfg_path)
    client = AilyClient(cfg.aily)

    resolved_stage2 = _resolve_path(stage2_path)
    resolved_project_config = _resolve_path(config_path)
    paper_json = str((_repo_root() / "paper.json"))
    records = bitable_sync.load_wechat_records(stage2_json_path=resolved_stage2, paper_json_path=paper_json, score_threshold=0.0)
    if limit > 0:
        records = records[: int(limit)]

    one_liners = _load_one_liners_for_records(resolved_project_config, records)

    app_id, app_secret, _app_token, _table_id, _use_views, _wm = bitable_sync._load_bitable_config(resolved_project_config)
    tenant_token = bitable_sync._get_tenant_access_token(app_id, app_secret)
    app_token, table_id = _resolve_bitable_table(config_path=resolved_project_config, tenant_token=tenant_token, source="wechat")
    _ensure_cover_field(config_path=resolved_project_config, table_id=table_id, app_token=app_token, tenant_token=tenant_token)

    p = Path(resolved_stage2)
    run_root = p.parent
    out_dir = run_root / cfg.output.dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    for r in records:
        one = one_liners.get(r.unique_key, "").strip()
        if not one:
            continue
        out_path = out_dir / f"{r.unique_key}{cfg.output.ext}"
        if out_path.exists() and not (force or cfg.output.force):
            ok += 1
            continue

        prompt = _render_prompt(cfg.prompt_template, title=r.title, one_liner=one, source="wechat")
        _sid, _rid, image_url = aily_generate_cover_image_url(
            client,
            prompt=prompt,
            idempotent_id=f"cover_{r.unique_key}",
            verbose=verbose,
        )
        _download(image_url, out_path)

        bitable_client = bitable_sync.BitableClient(token=tenant_token, app_token=app_token, table_id=table_id)
        file_token = bitable_client.upload_media(str(out_path))
        _upsert_cover_by_unique_key(
            table_id=table_id,
            app_token=app_token,
            tenant_token=tenant_token,
            unique_key=r.unique_key,
            file_token=file_token,
        )
        ok += 1
        print(f"[aily_cover] wechat ok unique_key={r.unique_key} path={out_path}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate cover via Aily(OpenAPI) and write to Bitable attachment field.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_xhs = sub.add_parser("xhs", help="XHS: generate cover for outputs/rednotes/<run> and write to Bitable")
    p_xhs.add_argument("--run-dir", required=True, help="outputs/rednotes/<run> folder")
    p_xhs.add_argument("-c", "--config", default="configs/config.yaml", help="配置入口（统一配置或 legacy rednotes.yaml）")
    p_xhs.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    p_xhs.add_argument("--aily-config", default="aily_cover.yaml")
    p_xhs.add_argument("--force", action="store_true")
    p_xhs.add_argument("--limit", type=int, default=0)
    p_xhs.add_argument("--verbose", action="store_true")

    p_wc = sub.add_parser("wechat", help="WeChat: generate cover for outputs/wechat/<run> stage2 and write to Bitable")
    p_wc.add_argument("--stage2", required=True, help="outputs/wechat/<run>/*_stage2.json")
    p_wc.add_argument("-c", "--config", default="configs/config.yaml", help="配置入口（统一配置或 legacy rednotes.yaml）")
    p_wc.add_argument("--profile", default="", help="配置环境(profile)，仅统一配置生效")
    p_wc.add_argument("--aily-config", default="aily_cover.yaml")
    p_wc.add_argument("--force", action="store_true")
    p_wc.add_argument("--limit", type=int, default=0)
    p_wc.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    try:
        _ensure_repo_root_on_path()
        from crawler_common.config_entry import resolve_legacy_config_for_cli

        resolved_config = resolve_legacy_config_for_cli(args.config, kind="rednotes", profile=getattr(args, "profile", ""))
        if args.cmd == "xhs":
            n = sync_covers_xhs(
                run_dir=args.run_dir,
                config_path=resolved_config,
                aily_cfg_path=args.aily_config,
                force=bool(args.force),
                limit=int(args.limit or 0),
                verbose=bool(args.verbose),
            )
            print(f"[aily_cover] xhs done n={n}")
            return 0
        if args.cmd == "wechat":
            n = sync_covers_wechat(
                stage2_path=args.stage2,
                config_path=resolved_config,
                aily_cfg_path=args.aily_config,
                force=bool(args.force),
                limit=int(args.limit or 0),
                verbose=bool(args.verbose),
            )
            print(f"[aily_cover] wechat done n={n}")
            return 0
        print("Unknown command")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[aily_cover] failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
