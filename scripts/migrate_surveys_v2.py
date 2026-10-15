"""Stop the app, then run: python -m scripts.migrate_surveys_v2 [--dry-run]."""

import argparse
import json

from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.database.connection import make_client
from app.database.migrations import migrate_surveys_v2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline survey v1-to-v2 migration. Stop all app processes first."
    )
    parser.add_argument("--dry-run", action="store_true", help="read-only preflight")
    args = parser.parse_args()
    settings = Settings()
    try:
        with make_client(settings) as client:
            client.admin.command("ping")
            summary = migrate_surveys_v2(client[settings.mongo_db], dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"Migration refused: {exc}")
        return 1
    except PyMongoError as exc:
        # Connection errors may contain URI credentials; only expose the type.
        print(f"MongoDB migration failed ({type(exc).__name__}); original backups are retained.")
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
