from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass
class QwenConfig:
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3-max"
    timeout_sec: int = 120
    enable_thinking: bool = True


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    receive_id_type: str = "chat_id"
    receive_id: str = ""


@dataclass
class BitableConfig:
    app_token: str = ""
    table_id: str = ""
    tables: Dict[str, Dict[str, str]] | None = None  # {"wechat": {"name": "...", "table_id": "..."}, ...}


@dataclass
class RednotesConfig:
    since: str = ""
    maxcrawl: int = 50
    interval_sec: float = 1.5
    accounts: List[Dict[str, str]] | None = None  # [{"name": "...", "url": "..."}, ...]
    prompt_stage1_xhs: str = ""
    prompt_stage2_xhs: str = ""
    qwen: QwenConfig | None = None
    feishu: FeishuConfig | None = None
    bitable: BitableConfig | None = None


def _coerce_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def _strip_inline_comment(line: str) -> str:
    # naive, but good enough for our config file
    if "#" not in line:
        return line
    return line.split("#", 1)[0].rstrip()


def _unquote(value: str) -> str:
    v = (value or "").strip()
    if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
        return v[1:-1]
    return v


def _load_simple_yaml(text: str) -> dict:
    """
    YAML subset loader for rednotes.yaml when PyYAML isn't available.

    Supported keys:
    - since (string)
    - maxcrawl (int)
    - interval_sec (float)
    - accounts (list of {name,url})
    - qwen (mapping)
    - feishu (mapping)
    - bitable (mapping)
    - prompt_stage1_xhs / prompt_stage2_xhs (block scalar with |)
    """
    raw_lines = (text or "").splitlines()
    i = 0
    root: dict = {}

    def read_block_scalar(start_idx: int, base_indent: int) -> Tuple[str, int]:
        buf: List[str] = []
        j = start_idx
        while j < len(raw_lines):
            ln = raw_lines[j].rstrip("\n").rstrip("\r")
            if not ln.strip():
                buf.append("")
                j += 1
                continue
            indent = len(ln) - len(ln.lstrip(" "))
            if indent <= base_indent:
                break
            buf.append(ln[base_indent + 2 :])  # strip one more level
            j += 1
        return "\n".join(buf).rstrip() + "\n", j

    while i < len(raw_lines):
        line = raw_lines[i].rstrip()
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line = _strip_inline_comment(line)
        if not line.strip():
            continue

        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        value = rest.strip()

        if value == "|":
            block, i = read_block_scalar(i, base_indent=0)
            root[key] = block
            continue

        if value == "":
            # nested structures
            if key == "accounts":
                items: List[dict] = []
                # read list items
                while i < len(raw_lines):
                    ln = raw_lines[i].rstrip()
                    if not ln.strip():
                        i += 1
                        continue
                    if not ln.startswith("- ") and not ln.startswith("  - "):
                        break
                    # normalize list marker indentation
                    ln_strip = ln.lstrip()
                    if not ln_strip.startswith("- "):
                        break
                    item: dict = {}
                    # parse "- name: xxx"
                    after = ln_strip[2:].strip()
                    if after:
                        k2, _, v2 = after.partition(":")
                        if _:
                            item[k2.strip()] = _unquote(v2.strip())
                    i += 1
                    # parse following "  url: ..."
                    while i < len(raw_lines):
                        ln2 = raw_lines[i].rstrip()
                        if not ln2.strip():
                            i += 1
                            continue
                        if ln2.lstrip().startswith("- "):
                            break
                        if not ln2.startswith("  ") and not ln2.startswith("    "):
                            break
                        ln2 = _strip_inline_comment(ln2)
                        k3, _, v3 = ln2.strip().partition(":")
                        if _:
                            item[k3.strip()] = _unquote(v3.strip())
                        i += 1
                    items.append(item)
                root[key] = items
                continue

            if key in ("qwen", "feishu", "bitable"):
                m: dict = {}
                while i < len(raw_lines):
                    ln = raw_lines[i].rstrip()
                    if not ln.strip():
                        i += 1
                        continue
                    if not ln.startswith("  "):
                        break
                    # Only accept direct children (2-space indent). Deeper nested blocks like:
                    # bitable:
                    #   tables:
                    #     wechat:
                    #       table_id: ...
                    # must not overwrite top-level keys in this simple loader.
                    indent = len(ln) - len(ln.lstrip(" "))
                    if indent != 2:
                        i += 1
                        continue
                    ln = _strip_inline_comment(ln)
                    k2, _, v2 = ln.strip().partition(":")
                    if _:
                        m[k2.strip()] = _unquote(v2.strip())
                    i += 1
                root[key] = m
                continue

            root[key] = ""
            continue

        # scalars
        v = _unquote(value)
        if key == "maxcrawl":
            try:
                root[key] = int(v)
            except ValueError:
                root[key] = 50
        elif key in ("interval_sec", "interval"):
            try:
                root["interval_sec"] = float(v)
            except ValueError:
                root["interval_sec"] = 1.5
        else:
            root[key] = v

    return root


def _dump_simple_yaml(data: dict) -> str:
    def fmt_scalar(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return str(v)
        s = str(v or "")
        if s == "":
            return '""'
        if any(ch in s for ch in [":", "#", "{", "}", "[", "]"]) or s.strip() != s:
            return f"\"{s}\""
        return s

    out_lines: list[str] = []
    for key in ("since", "maxcrawl", "interval_sec"):
        if key in data:
            out_lines.append(f"{key}: {fmt_scalar(data[key])}")
    out_lines.append("")

    accounts = data.get("accounts") or []
    out_lines.append("accounts:")
    if isinstance(accounts, list) and accounts:
        for item in accounts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if not name or not url:
                continue
            out_lines.append(f"  - name: {fmt_scalar(name)}")
            out_lines.append(f"    url: {fmt_scalar(url)}")
    else:
        out_lines.append("  []")
    out_lines.append("")

    for key in ("prompt_stage1_xhs", "prompt_stage2_xhs"):
        prompt = data.get(key) or ""
        out_lines.append(f"{key}: |")
        for ln in str(prompt).rstrip("\n").splitlines():
            out_lines.append(f"  {ln}")
        out_lines.append("")

    qwen = data.get("qwen") or {}
    out_lines.append("qwen:")
    if isinstance(qwen, dict):
        for k in ("api_key", "base_url", "model", "timeout_sec", "enable_thinking"):
            if k in qwen:
                out_lines.append(f"  {k}: {fmt_scalar(qwen[k])}")
    out_lines.append("")

    feishu = data.get("feishu") or {}
    out_lines.append("feishu:")
    if isinstance(feishu, dict):
        for k in ("app_id", "app_secret", "receive_id_type", "receive_id"):
            if k in feishu:
                out_lines.append(f"  {k}: {fmt_scalar(feishu[k])}")
    out_lines.append("")

    bitable = data.get("bitable") or {}
    out_lines.append("bitable:")
    if isinstance(bitable, dict):
        for k in ("app_token", "table_id", "wechat_table_name", "wechat_table_id", "xhs_table_name", "xhs_table_id"):
            if k in bitable:
                out_lines.append(f"  {k}: {fmt_scalar(bitable[k])}")
    return "\n".join(out_lines).rstrip() + "\n"


def load_rednotes_config(path: str) -> Tuple[RednotesConfig, dict]:
    p = Path(path)
    if not p.exists():
        cfg = RednotesConfig(accounts={}, qwen=QwenConfig())
        return cfg, {}

    raw_text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        raw = yaml.safe_load(raw_text) or {}
    except Exception:
        raw = _load_simple_yaml(raw_text) or {}
    accounts_raw = raw.get("accounts") or []
    accounts: List[Dict[str, str]] = []
    if isinstance(accounts_raw, list):
        for item in accounts_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if name and url:
                accounts.append({"name": name, "url": url})
    elif isinstance(accounts_raw, dict):
        # backward compatible: {name: url}
        for k, v in accounts_raw.items():
            name = str(k).strip()
            url = str(v).strip()
            if name and url:
                accounts.append({"name": name, "url": url})

    qwen_raw = raw.get("qwen") or {}
    if not isinstance(qwen_raw, dict):
        qwen_raw = {}

    feishu_raw = raw.get("feishu") or {}
    if not isinstance(feishu_raw, dict):
        feishu_raw = {}

    bitable_raw = raw.get("bitable") or {}
    if not isinstance(bitable_raw, dict):
        bitable_raw = {}

    # Support nested tables mapping when PyYAML is available.
    tables_raw = bitable_raw.get("tables")
    tables: Dict[str, Dict[str, str]] = {}
    if isinstance(tables_raw, dict):
        for source, cfg2 in tables_raw.items():
            if not isinstance(cfg2, dict):
                continue
            name = str(cfg2.get("name") or "").strip()
            tid = str(cfg2.get("table_id") or "").strip()
            if name or tid:
                tables[str(source)] = {"name": name, "table_id": tid}
    else:
        # Flat fallback keys (for simple yaml subset)
        w_name = str(bitable_raw.get("wechat_table_name") or "").strip()
        w_id = str(bitable_raw.get("wechat_table_id") or "").strip()
        x_name = str(bitable_raw.get("xhs_table_name") or "").strip()
        x_id = str(bitable_raw.get("xhs_table_id") or "").strip()
        if w_name or w_id:
            tables["wechat"] = {"name": w_name, "table_id": w_id}
        if x_name or x_id:
            tables["xhs"] = {"name": x_name, "table_id": x_id}

    interval_raw = raw.get("interval_sec")
    if interval_raw is None:
        interval_raw = raw.get("interval")
    try:
        interval_sec = float(interval_raw) if interval_raw is not None and str(interval_raw).strip() != "" else 1.5
    except (TypeError, ValueError):
        interval_sec = 1.5

    cfg = RednotesConfig(
        since=str(raw.get("since") or "").strip(),
        maxcrawl=int(raw.get("maxcrawl") or 50),
        interval_sec=interval_sec,
        accounts=accounts,
        prompt_stage1_xhs=str(raw.get("prompt_stage1_xhs") or "").rstrip() + ("\n" if str(raw.get("prompt_stage1_xhs") or "").strip() else ""),
        prompt_stage2_xhs=str(raw.get("prompt_stage2_xhs") or "").rstrip() + ("\n" if str(raw.get("prompt_stage2_xhs") or "").strip() else ""),
        qwen=QwenConfig(
            api_key=str(qwen_raw.get("api_key") or "").strip(),
            base_url=str(qwen_raw.get("base_url") or QwenConfig.base_url).strip(),
            model=str(qwen_raw.get("model") or QwenConfig.model).strip(),
            timeout_sec=int(qwen_raw.get("timeout_sec") or 120),
            enable_thinking=_coerce_bool(qwen_raw.get("enable_thinking", True)),
        ),
        feishu=FeishuConfig(
            app_id=str(feishu_raw.get("app_id") or "").strip(),
            app_secret=str(feishu_raw.get("app_secret") or "").strip(),
            receive_id_type=str(feishu_raw.get("receive_id_type") or "chat_id").strip() or "chat_id",
            receive_id=str(feishu_raw.get("receive_id") or "").strip(),
        ),
        bitable=BitableConfig(
            app_token=str(bitable_raw.get("app_token") or "").strip(),
            table_id=str(bitable_raw.get("table_id") or "").strip(),
            tables=tables or None,
        ),
    )
    return cfg, raw


def save_rednotes_config(path: str, cfg: RednotesConfig) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "since": cfg.since,
        "maxcrawl": int(cfg.maxcrawl),
        "interval_sec": float(cfg.interval_sec),
        "accounts": cfg.accounts or [],
        "prompt_stage1_xhs": cfg.prompt_stage1_xhs or "",
        "prompt_stage2_xhs": cfg.prompt_stage2_xhs or "",
        "qwen": {
            "api_key": (cfg.qwen.api_key if cfg.qwen else ""),
            "base_url": (cfg.qwen.base_url if cfg.qwen else QwenConfig.base_url),
            "model": (cfg.qwen.model if cfg.qwen else QwenConfig.model),
            "timeout_sec": int(cfg.qwen.timeout_sec if cfg.qwen else 120),
            "enable_thinking": bool(cfg.qwen.enable_thinking if cfg.qwen else True),
        },
        "feishu": {
            "app_id": (cfg.feishu.app_id if cfg.feishu else ""),
            "app_secret": (cfg.feishu.app_secret if cfg.feishu else ""),
            "receive_id_type": (cfg.feishu.receive_id_type if cfg.feishu else "chat_id"),
            "receive_id": (cfg.feishu.receive_id if cfg.feishu else ""),
        },
        "bitable": {
            "app_token": (cfg.bitable.app_token if cfg.bitable else ""),
            "table_id": (cfg.bitable.table_id if cfg.bitable else ""),
            # Prefer nested tables mapping; also keep flat copies for basic loaders.
            "tables": (cfg.bitable.tables if cfg.bitable and cfg.bitable.tables else {}),
            "wechat_table_name": (cfg.bitable.tables.get("wechat", {}).get("name") if cfg.bitable and cfg.bitable.tables else ""),
            "wechat_table_id": (cfg.bitable.tables.get("wechat", {}).get("table_id") if cfg.bitable and cfg.bitable.tables else ""),
            "xhs_table_name": (cfg.bitable.tables.get("xhs", {}).get("name") if cfg.bitable and cfg.bitable.tables else ""),
            "xhs_table_id": (cfg.bitable.tables.get("xhs", {}).get("table_id") if cfg.bitable and cfg.bitable.tables else ""),
        },
    }
    try:
        import yaml  # type: ignore

        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception:
        p.write_text(_dump_simple_yaml(data), encoding="utf-8")
