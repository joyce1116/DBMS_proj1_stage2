from datetime import datetime, timezone


def mongo_utc_now() -> datetime:
    """Return UTC at BSON date precision for stable round-trip responses."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)
