"""Run from the project root: python -m scripts.init_db."""

from app.core.config import Settings
from app.database.connection import initialize_database, make_client


def main() -> None:
    settings = Settings()
    with make_client(settings) as client:
        client.admin.command("ping")
        initialize_database(client[settings.mongo_db])
        print(f"MongoDB initialized: {settings.mongo_db} (users, sessions, surveys)")


if __name__ == "__main__":
    main()
