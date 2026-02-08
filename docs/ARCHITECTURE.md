# Project Architecture

## Goals
- Single source of truth for runtime config.
- Reuse shared integrations (Feishu first).
- Keep Xiaohongshu dual-source strategy with clear priority:
  - Primary: TikHub API
  - Fallback: Playwright browser crawl

## Key Files
- `configs/config.yaml`: unified config.
- `crawler_common/project_config.py`: unified config loader + legacy export.
- `scripts/config/sync_legacy_configs.py`: generate `rednotes.yaml`, `wechat.yaml`, `cover_gen.yaml` from unified config.
- `scripts/config/migrate_to_unified_config.py`: one-time migration from legacy yaml files to unified config.
- `crawler_common/feishu_api.py`: shared Feishu API module.
- `scripts/xhs/xhs_pipeline.py`: unified XHS pipeline (TikHub primary, Playwright fallback).
- `scripts/`: grouped executable scripts by domain (`wechat`, `xhs`, `integrations`, `config`).

## Runtime Flow
1. Load unified config from `configs/config.yaml` (+ optional profile overlay).
2. Sync legacy configs when needed for existing scripts.
3. Crawl XHS accounts in order:
   - Try provider from `xhs.provider_order`.
   - If failed/no result, fall back to next provider.
4. Normalize note payload and save to `outputs/rednotes/<run>/notes.json`.
5. Optionally run stage1/stage2 LLM analysis on the same run folder.

## CLI Notes
- Unified XHS pipeline:
  - `python scripts/xhs/xhs_pipeline.py --config configs/config.yaml --profile dev`
- Legacy config sync only:
  - `python scripts/config/sync_legacy_configs.py --config configs/config.yaml --profile dev`
- Main CLI with new pipeline:
  - `python main.py rednotes-crawl-all --project-config configs/config.yaml --profile dev`
  - Use `--legacy` to run old `crawler_rednotes crawl-all`.

## Script Layout
- `scripts/wechat/`: WeChat crawling, LLM, Feishu send.
- `scripts/xhs/`: XHS pipeline, TikHub fetch, XHS LLM, XHS Feishu send.
- `scripts/integrations/`: Bitable sync/doctor and cross-platform senders.
- `scripts/config/`: unified config migration/sync utilities.
- Root keeps `main.py` only; historical root scripts are archived in `archive/legacy_root_scripts/`.
