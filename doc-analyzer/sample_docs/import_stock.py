"""One-off helper used during the Harbor Warehouse Rollout to migrate legacy
stock records into the new inventory system."""
import csv
import sys


def load_legacy(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def transform(rows):
    for r in rows:
        yield {
            "sku": r["item_code"].strip().upper(),
            "description": r["name"].strip(),
            "on_hand": int(float(r["qty"] or 0)),
            "aisle": r.get("location", "unknown"),
        }


def main(argv):
    rows = load_legacy(argv[1])
    migrated = list(transform(rows))
    print(f"migrated {len(migrated)} stock records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
