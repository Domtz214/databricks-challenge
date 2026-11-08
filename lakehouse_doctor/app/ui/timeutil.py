from datetime import datetime, timezone


def ago(ts) -> str:
    """'12m ago' from an ISO string / datetime; '' if unparseable."""
    try:
        t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        s = (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return ""
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"
