from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        text2 = (text or "").lstrip()
        if text2.startswith("{") or text2.startswith("["):
            import json

            data = json.loads(text or "{}")
            return data if isinstance(data, dict) else {}
        raise RuntimeError(
            "Failed to parse YAML. Install PyYAML (pip install pyyaml) "
            f"or provide JSON content: {path}"
        )


def _dump_yaml(data: Dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    except Exception:
        import json

        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)  # type: ignore[index]
        else:
            out[key] = copy.deepcopy(val)
    return out


def _resolve_env_in_value(value: Any) -> Any:
    if isinstance(value, str):
        m = _ENV_PATTERN.fullmatch(value.strip())
        if m:
            return os.environ.get(m.group(1), "")
        return value
    if isinstance(value, dict):
        return {k: _resolve_env_in_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_in_value(v) for v in value]
    return value


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _secret(section: Dict[str, Any], key: str, env_key: str) -> str:
    direct = str(section.get(key) or "").strip()
    if direct:
        m = _ENV_PATTERN.fullmatch(direct)
        if m:
            return os.environ.get(m.group(1), "").strip()
        return direct
    env_name = str(section.get(env_key) or "").strip()
    if not env_name:
        return ""
    return os.environ.get(env_name, "").strip()


def _normalize_accounts(items: Iterable[Any], *, url_key: str) -> list[Dict[str, str]]:
    out: list[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get(url_key) or item.get("url") or "").strip()
        if not name or not url:
            continue
        out.append({"name": name, url_key: url})
    return out


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_project_config(config_path: str = "configs/config.yaml", profile: str = "") -> Tuple[Dict[str, Any], str]:
    """
    Load unified config with optional profile overlay.

    Precedence:
    1) base config.yaml
    2) config.<profile>.yaml (if exists)
    3) env var placeholders like ${FOO} within loaded values
    """
    base_path = Path(config_path)
    base = _load_yaml(base_path)
    selected_profile = (profile or str(base.get("app", {}).get("profile") or "")).strip()
    merged = base

    if selected_profile:
        profile_path = base_path.with_name(f"{base_path.stem}.{selected_profile}{base_path.suffix}")
        if profile_path.exists():
            merged = _deep_merge(base, _load_yaml(profile_path))

    resolved = _resolve_env_in_value(merged)
    if not isinstance(resolved, dict):
        resolved = {}
    return resolved, selected_profile


def export_rednotes_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    xhs = cfg.get("xhs") if isinstance(cfg.get("xhs"), dict) else {}
    qwen = xhs.get("qwen") if isinstance(xhs.get("qwen"), dict) else {}
    feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    bitable = cfg.get("bitable") if isinstance(cfg.get("bitable"), dict) else {}
    prompts = xhs.get("prompts") if isinstance(xhs.get("prompts"), dict) else {}

    accounts = _normalize_accounts(xhs.get("accounts") or [], url_key="url")
    tables_raw = bitable.get("tables") if isinstance(bitable.get("tables"), dict) else {}
    tables: Dict[str, Dict[str, str]] = {}
    for source, val in tables_raw.items():
        if not isinstance(val, dict):
            continue
        tables[str(source)] = {
            "name": str(val.get("name") or "").strip(),
            "table_id": str(val.get("table_id") or "").strip(),
        }

    out: Dict[str, Any] = {
        "since": str(xhs.get("since") or "").strip(),
        "maxcrawl": _coerce_int(xhs.get("maxcrawl"), 20),
        "interval_sec": _coerce_float(xhs.get("interval_sec"), 1.5),
        "accounts": accounts,
        "qwen": {
            "api_key": _secret(qwen, "api_key", "api_key_env"),
            "base_url": str(qwen.get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip(),
            "model": str(qwen.get("model") or "qwen3-vl-plus").strip(),
            "timeout_sec": _coerce_int(qwen.get("timeout_sec"), 120),
            "enable_thinking": _coerce_bool(qwen.get("enable_thinking"), True),
        },
        "feishu": {
            "app_id": _secret(feishu, "app_id", "app_id_env"),
            "app_secret": _secret(feishu, "app_secret", "app_secret_env"),
            "receive_id_type": str(feishu.get("receive_id_type") or "chat_id").strip() or "chat_id",
            "receive_id": _secret(feishu, "receive_id", "receive_id_env"),
        },
        "bitable": {
            "use_views": _coerce_bool(bitable.get("use_views"), True),
            "write_mode": str(bitable.get("write_mode") or "overwrite").strip(),
            "app_token": _secret(bitable, "app_token", "app_token_env"),
            "table_id": _secret(bitable, "table_id", "table_id_env"),
            "tables": tables,
        },
        "prompt_stage1_xhs": str(prompts.get("stage1_xhs") or "").rstrip(),
        "prompt_stage2_xhs": str(prompts.get("stage2_xhs") or "").rstrip(),
        "prompt_bitable_one_liner": str(prompts.get("bitable_one_liner") or "").rstrip(),
        "prompt_bitable_one_liner_analysis": str(prompts.get("bitable_one_liner_analysis") or "").rstrip(),
    }

    if tables.get("wechat", {}).get("table_id"):
        out["bitable"]["wechat_table_id"] = tables["wechat"]["table_id"]
    if tables.get("xhs", {}).get("table_id"):
        out["bitable"]["xhs_table_id"] = tables["xhs"]["table_id"]
    return out


def export_wechat_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    wechat = cfg.get("wechat") if isinstance(cfg.get("wechat"), dict) else {}
    accounts = _normalize_accounts(wechat.get("accounts") or [], url_key="fakeid")
    prompts = wechat.get("prompts") if isinstance(wechat.get("prompts"), dict) else {}
    return {
        "cookie": _secret(wechat, "cookie", "cookie_env"),
        "user_agent": str(wechat.get("user_agent") or "").strip(),
        "token": str(wechat.get("token") or "").strip(),
        "since": str(wechat.get("since") or "").strip(),
        "accounts": accounts,
        "prompt": str(prompts.get("stage1_wechat") or "").rstrip(),
        "prompt_stage2": str(prompts.get("stage2_wechat") or "").rstrip(),
    }


def export_cover_gen_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cover = cfg.get("cover") if isinstance(cfg.get("cover"), dict) else {}
    wanx = cover.get("wanx") if isinstance(cover.get("wanx"), dict) else {}
    prompt = cover.get("prompt") if isinstance(cover.get("prompt"), dict) else {}
    output = cover.get("output") if isinstance(cover.get("output"), dict) else {}
    return {
        "wanx": {
            "api_key": _secret(wanx, "api_key", "api_key_env"),
            "sync_base_url": str(
                wanx.get("sync_base_url")
                or "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
            ).strip(),
            "async_base_url": str(
                wanx.get("async_base_url")
                or "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation"
            ).strip(),
            "model": str(wanx.get("model") or "wan2.6-t2i").strip(),
            "size": str(wanx.get("size") or "1280*1280").strip(),
            "n": _coerce_int(wanx.get("n"), 1),
            "negative_prompt": str(wanx.get("negative_prompt") or "").strip(),
            "prompt_extend": _coerce_bool(wanx.get("prompt_extend"), True),
            "watermark": _coerce_bool(wanx.get("watermark"), False),
            "async_mode": _coerce_bool(wanx.get("async_mode"), True),
            "poll_interval_sec": _coerce_float(wanx.get("poll_interval_sec"), 5.0),
            "timeout_sec": _coerce_float(wanx.get("timeout_sec"), 300.0),
        },
        "prompt": {
            "template": str(prompt.get("template") or "").rstrip(),
        },
        "output": {
            "dir_name": str(output.get("dir_name") or "covers").strip(),
            "ext": str(output.get("ext") or ".png").strip(),
            "force": _coerce_bool(output.get("force"), False),
        },
    }


def sync_legacy_configs(
    config_path: str = "configs/config.yaml",
    *,
    profile: str = "",
    rednotes_out: str = "configs/legacy/rednotes.yaml",
    wechat_out: str = "configs/legacy/wechat.yaml",
    cover_out: str = "configs/legacy/cover_gen.yaml",
) -> Dict[str, Path]:
    cfg, _selected = load_project_config(config_path, profile=profile)
    out_paths = {
        "rednotes": Path(rednotes_out),
        "wechat": Path(wechat_out),
        "cover_gen": Path(cover_out),
    }

    rednotes_data = export_rednotes_config(cfg)
    wechat_data = export_wechat_config(cfg)
    cover_data = export_cover_gen_config(cfg)

    for key, data in (("rednotes", rednotes_data), ("wechat", wechat_data), ("cover_gen", cover_data)):
        path = out_paths[key]
        _ensure_dir(path)
        path.write_text(_dump_yaml(data), encoding="utf-8")

    return out_paths


def get_xhs_runtime_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    xhs = cfg.get("xhs") if isinstance(cfg.get("xhs"), dict) else {}
    tikhub = xhs.get("tikhub") if isinstance(xhs.get("tikhub"), dict) else {}
    playwright = xhs.get("playwright") if isinstance(xhs.get("playwright"), dict) else {}
    fallback = xhs.get("fallback") if isinstance(xhs.get("fallback"), dict) else {}

    return {
        "outputs_root": str((cfg.get("paths") or {}).get("outputs_root") or "outputs").strip() or "outputs",
        "accounts": _normalize_accounts(xhs.get("accounts") or [], url_key="url"),
        "since": str(xhs.get("since") or "").strip(),
        "maxcrawl": _coerce_int(xhs.get("maxcrawl"), 20),
        "interval_sec": _coerce_float(xhs.get("interval_sec"), 1.5),
        "provider_order": [str(v).strip().lower() for v in (xhs.get("provider_order") or ["tikhub", "playwright"]) if str(v).strip()],
        "fallback_enabled": _coerce_bool(fallback.get("enabled"), True),
        "fallback_retry_times": _coerce_int(fallback.get("retry_times"), 1),
        "tikhub_api_key": _secret(tikhub, "api_key", "api_key_env"),
        "tikhub_retries": _coerce_int(tikhub.get("retries"), 2),
        "tikhub_timeout_sec": _coerce_int(tikhub.get("timeout_sec"), 20),
        "playwright_storage_state": str(playwright.get("storage_state") or str(Path("crawler_rednotes") / "storage_state.json")).strip(),
        "playwright_headless": _coerce_bool(playwright.get("headless"), True),
        "playwright_interval_sec": _coerce_float(playwright.get("interval_sec"), 1.5),
    }
