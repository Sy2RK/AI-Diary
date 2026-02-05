import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import List, Dict

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value.strip(), DATETIME_FMT)


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_rows(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def filter_by_since(rows: List[Dict[str, str]], since_dt: datetime) -> List[Dict[str, str]]:
    filtered = []
    for row in rows:
        ct = row.get("create_time", "").strip()
        try:
            dt = parse_dt(ct)
        except Exception:
            continue
        if dt > since_dt:
            filtered.append(row)
    return filtered


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter CSV rows by create_time > since")
    parser.add_argument("-i", "--input", default="app_msg_list.csv", help="Input CSV file (default: app_msg_list.csv)")
    parser.add_argument("-o", "--output", default="filtered.csv", help="Output CSV file (default: filtered.csv)")
    parser.add_argument("--since", required=True, help="Time cutoff, format YYYY-MM-DD HH:MM:SS")
    args = parser.parse_args()

    since_dt = parse_dt(args.since)
    input_path = Path(args.input)
    output_path = Path(args.output)

    rows = load_rows(input_path)
    filtered = filter_by_since(rows, since_dt)
    save_rows(output_path, rows[0].keys() if rows else [], filtered)

    print(f"Total: {len(rows)}, kept: {len(filtered)}, saved to {output_path}")


if __name__ == "__main__":
    main()
