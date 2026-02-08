# Scripts Layout

## Root Script Policy
- Repo root keeps only `main.py` as the primary entry script.
- Historical root scripts are archived under `archive/legacy_root_scripts/`.
- Daily use should run scripts from `scripts/` directly, or use `main.py`.

## Big Script Splits
- Bitable sync:
  - `scripts/integrations/bitable_sync.py` (orchestration and CLI)
  - `scripts/integrations/bitable_client.py` (Bitable API client and attachment/link mapping)
- TikHub fetch:
  - `scripts/xhs/rednotes_api_tikhub.py` (crawl orchestration, save logic, CLI)
  - `scripts/xhs/tikhub_client.py` (TikHub HTTP API requests)
