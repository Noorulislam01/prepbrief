"""Read the RSS feeds and save new articles.  Run:  python -m pipeline.fetch_rss"""
import html
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import yaml

from db.connection import get_conn, reset_current_user, set_current_user

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "PrepBrief/0.1 (personal study tool)"
TAG_RE = re.compile(r"<[^>]+>")


def clean_summary(raw: str | None) -> str | None:
    if not raw:
        return None
    text = html.unescape(TAG_RE.sub(" ", raw))
    return re.sub(r"\s+", " ", text).strip() or None


def parse_date(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def fetch_all() -> None:
    config = yaml.safe_load((ROOT / "config" / "feeds.yaml").read_text())
    source = config["source"]
    feeds = []
    for feed in config["feeds"]:
        parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
        if parsed.bozo and not parsed.entries:
            print(f"[skip] {feed['name']}: could not read feed ({parsed.bozo_exception})")
            continue
        entries = []
        for entry in parsed.entries:
            url = (entry.get("link") or "").split("?")[0].strip()
            title = (entry.get("title") or "").strip()
            if url and title:
                entries.append((url, title, parse_date(entry), clean_summary(entry.get("summary"))))
        feeds.append((feed["name"], len(parsed.entries), entries))

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app_accounts ORDER BY id")
        user_ids = [row[0] for row in cur.fetchall()]
    if not user_ids:
        print("No PrepBrief accounts exist yet; RSS headlines will be imported after sign-up.")
        return

    total_new = 0
    for user_id in user_ids:
        context_token = set_current_user(user_id)
        try:
            with get_conn() as conn, conn.cursor() as cur:
                user_new = 0
                for section, item_count, entries in feeds:
                    new_in_feed = 0
                    for url, title, published_at, summary in entries:
                        cur.execute(
                            "INSERT INTO articles (url, title, source, section, published_at, summary) "
                            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id,url) DO NOTHING",
                            (url, title, source, section, published_at, summary),
                        )
                        new_in_feed += cur.rowcount
                    user_new += new_in_feed
                    print(f"[user {user_id}] [ok] {section}: {item_count} items, {new_in_feed} new")
                conn.commit()
            total_new += user_new
        finally:
            reset_current_user(context_token)

    print(f"Done. {total_new} user-specific article copies saved across {len(user_ids)} account(s).")


if __name__ == "__main__":
    fetch_all()
