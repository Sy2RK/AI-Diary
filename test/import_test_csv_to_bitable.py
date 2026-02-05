from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


sys.dont_write_bytecode = True


# Hardcoded for quick testing.
# WARNING: This is unsafe for real projects. Do NOT commit real secrets.
FEISHU_APP_ID = "cli_a9f8b4203eb8dcca"
FEISHU_APP_SECRET = "aViEyBopvecfzOnKICvb3elkWKTeBefO"
BITABLE_APP_TOKEN = "R4s5buhFJaGOjws7EpbcSZGVnhb"
BITABLE_TABLE_ID = "tbliu1ltx9RSUVmt"


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_cell(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s in ("--", "—", "N/A", "n/a", "NULL", "null"):
        return None
    return s


def _iter_csv_rows(path: Path) -> Tuple[List[str], List[Dict[str, Optional[str]]]]:
    """
    Returns (headers, rows) where rows are dicts with cleaned string values (or None).
    Tries common encodings for CN CSV exports.
    """
    encodings = ["utf-8-sig", "utf-8", "gb18030"]
    last_err: Optional[Exception] = None
    for enc in encodings:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                headers = [str(h or "").strip() for h in (reader.fieldnames or [])]
                headers = [h for h in headers if h]
                rows: List[Dict[str, Optional[str]]] = []
                for r in reader:
                    if not isinstance(r, dict):
                        continue
                    row: Dict[str, Optional[str]] = {}
                    for k, v in r.items():
                        kk = str(k or "").strip()
                        if not kk:
                            continue
                        row[kk] = _clean_cell(v)
                    if any(v is not None for v in row.values()):
                        rows.append(row)
                return headers, rows
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
    raise last_err or RuntimeError(f"Failed to read csv: {path}")


def _chunked(items: List[Any], size: int) -> Iterable[List[Any]]:
    size = max(1, int(size))
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _preflight_table_id(client, table_id: str) -> None:
    tid = str(table_id or "").strip()
    if not tid:
        raise RuntimeError("Empty table_id")

    tables = client.list_tables(page_size=50)
    table_ids: List[tuple[str, str]] = []
    for it in tables:
        t = str(it.get("table_id") or "").strip()
        n = str(it.get("name") or "").strip()
        if t:
            table_ids.append((t, n))

    if any(t == tid for t, _n in table_ids):
        return

    sample = "\n".join([f"- {t}  {n}".rstrip() for t, n in table_ids[:30]]) or "(no tables returned)"
    raise RuntimeError(
        "TableIdNotFound: table_id is not under this app_token.\n"
        f"- app_token={getattr(client, 'app_token', '')}\n"
        f"- table_id={tid}\n"
        "Available tables under this app_token:\n"
        f"{sample}\n"
        "Fix: set BITABLE_APP_TOKEN / BITABLE_TABLE_ID to the correct Base/Table."
    )


def main() -> int:
    """
    Standalone script (not integrated into project CLI):
    - Reads CSV files under ./test
    - Writes rows into Feishu Bitable table (IDs are hardcoded in this script)

    Optional env overrides:
    - CSV_DIR: default test (or "." if you run from inside test/)
    - CSV_GLOB: default *.csv
    - WRITE_MODE: append|overwrite (default append)
    - BATCH_SIZE: default 100
    """
    csv_dir = Path(os.getenv("CSV_DIR", "test"))
    csv_glob = os.getenv("CSV_GLOB", "*.csv")
    write_mode = (os.getenv("WRITE_MODE", "append") or "append").strip().lower()
    batch_size = int(os.getenv("BATCH_SIZE", "100") or "100")

    app_id = FEISHU_APP_ID.strip()
    app_secret = FEISHU_APP_SECRET.strip()
    app_token = BITABLE_APP_TOKEN.strip()
    table_id = BITABLE_TABLE_ID.strip()

    if not app_id or not app_secret:
        raise RuntimeError("Missing FEISHU_APP_ID / FEISHU_APP_SECRET")
    if not table_id:
        raise RuntimeError("Missing BITABLE_TABLE_ID")

    from feishu_utils import get_tenant_access_token
    from bitable_sync import BitableClient

    token = get_tenant_access_token(app_id, app_secret)
    client = BitableClient(token=token, app_token=app_token, table_id=table_id)
    _preflight_table_id(client, table_id)

    csv_files = sorted(csv_dir.glob(csv_glob), key=lambda p: p.stat().st_mtime, reverse=False)
    if not csv_files:
        raise FileNotFoundError(f"No CSV found under: {csv_dir} glob={csv_glob}")

    if write_mode in ("overwrite", "cover", "reset", "truncate", "clear"):
        deleted = client.clear_all_records()
        print(f"[bitable] cleared table_id={table_id} deleted={deleted}")

    total_rows = 0
    total_created = 0

    for csv_path in csv_files:
        headers, rows = _iter_csv_rows(csv_path)
        if not headers or not rows:
            print(f"[csv] skip empty file={csv_path}")
            continue

        required: Dict[str, Dict[str, Any]] = {h: {"type": 1} for h in headers}
        required["source_file"] = {"type": 1}
        required["ingest_time"] = {"type": 1}
        client.ensure_fields(required)

        records: List[Dict[str, Any]] = []
        for r in rows:
            fields: Dict[str, Any] = {k: v for k, v in r.items() if v is not None}
            fields["source_file"] = csv_path.name
            fields["ingest_time"] = _now_iso()
            records.append({"fields": fields})

        for chunk in _chunked(records, batch_size):
            client.batch_create(chunk)
            total_created += len(chunk)

        total_rows += len(rows)
        print(f"[csv] imported file={csv_path.name} rows={len(rows)}")

    print(f"[done] files={len(csv_files)} rows={total_rows} created={total_created} table_id={table_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
