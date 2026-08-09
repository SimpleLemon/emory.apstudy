#!/usr/bin/env python3
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from appwrite_client import COLLECTIONS
from appwrite_helpers import list_appwrite_rows_all as list_rows_all
from services.calendar_store import CALENDAR_TABLES, init_calendar_store, upsert_calendar_row
from services.row_utils import row_id as _row_id


def migrate_table(table_id):
    rows = list_rows_all(table_id)
    imported = 0
    skipped = 0
    for row in rows:
        row_id = _row_id(row)
        if not row_id:
            skipped += 1
            continue
        upsert_calendar_row(table_id, row_id=row_id, data=row)
        imported += 1
    return {
        "source": len(rows),
        "imported": imported,
        "skipped": skipped,
    }


def main():
    init_calendar_store()
    failures = []
    for key in CALENDAR_TABLES:
        table_id = COLLECTIONS.get(key, key)
        stats = migrate_table(table_id)
        print(
            f"{table_id}: source={stats['source']} "
            f"imported={stats['imported']} skipped={stats['skipped']}"
        )
        if stats["source"] != stats["imported"] + stats["skipped"]:
            failures.append(table_id)
    if failures:
        print(f"Import count mismatch for: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
