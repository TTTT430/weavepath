from __future__ import annotations

import argparse
import json

from graph_core.database_lifecycle import restore_database_from_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a verified WeavePath migration backup")
    parser.add_argument("--database", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--confirm", required=True, help="Exact phrase: RESTORE <database filename>")
    args = parser.parse_args()
    result = restore_database_from_manifest(
        args.database, args.manifest, confirmation=args.confirm
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
