from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

from .project_config import sync_legacy_configs


DEFAULT_PROJECT_CONFIG = str(Path("configs") / "config.yaml")
LEGACY_CONFIG_DIR = Path("configs") / "legacy"


def default_legacy_paths() -> Dict[str, str]:
    return {
        "rednotes": str(LEGACY_CONFIG_DIR / "rednotes.yaml"),
        "wechat": str(LEGACY_CONFIG_DIR / "wechat.yaml"),
        "cover_gen": str(LEGACY_CONFIG_DIR / "cover_gen.yaml"),
    }


_UNIFIED_MARKERS_RE = re.compile(r"(?m)^\s*(app|paths|wechat|xhs|cover)\s*:")


def _looks_like_unified_config(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    if _UNIFIED_MARKERS_RE.search(text):
        return True

    name = path.name.lower()
    return name.startswith("config") and path.suffix.lower() in (".yaml", ".yml")


def resolve_legacy_config_for_cli(config_path: str, *, kind: str, profile: str = "") -> str:
    """
    Resolve config entry for legacy consumers.

    - If `config_path` points to unified config (configs/config*.yaml), auto-sync to
      configs/legacy/*.yaml and return the corresponding legacy file.
    - If `config_path` already points to legacy file, return it as-is.
    """
    entry = str(config_path or DEFAULT_PROJECT_CONFIG).strip() or DEFAULT_PROJECT_CONFIG
    p = Path(entry)
    out = default_legacy_paths()
    if kind not in out:
        raise ValueError(f"Unknown legacy config kind: {kind}")

    if _looks_like_unified_config(p):
        try:
            sync_legacy_configs(
                str(p),
                profile=profile,
                rednotes_out=out["rednotes"],
                wechat_out=out["wechat"],
                cover_out=out["cover_gen"],
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to sync unified config `{p}` to legacy config: {exc}") from exc
        return out[kind]

    return entry
