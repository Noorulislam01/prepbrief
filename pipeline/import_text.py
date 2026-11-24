"""Attach user-provided article text to an RSS item.

Usage: python -m pipeline.import_text ARTICLE_ID path\to\article.txt
       type article.txt | python -m pipeline.import_text ARTICLE_ID -
"""
import argparse
import re
import sys
from pathlib import Path

from db.connection import get_conn, user_context

WORDS_PER_MINUTE = 200


def clean_text(raw: str) -> str:
    """Normalize whitespace while preserving paragraph breaks."""
    paragraphs = [re.sub(r"[\t\r\f\v ]+", " ", part).strip()
                  for part in re.split(r"\n\s*\n", raw)]
    return "\n\n".join(part for part in paragraphs if part)


def save_text(article_id: int, text: str) -> int:
    text = clean_text(text)
    if not text:
        raise ValueError("Article text is empty.")

    word_count = len(text.split())
    reading_minutes = max(1, (word_count + WORDS_PER_MINUTE - 1) // WORDS_PER_MINUTE)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE articles SET full_text = %s, reading_minutes = %s, "
            "status = 'text_ready', is_relevant = NULL, confidence = NULL, "
            "processed_at = NULL WHERE id = %s RETURNING id",
            (text, reading_minutes, article_id),
        )
        if cur.fetchone() is None:
            raise LookupError(f"No article found with id {article_id}.")
        # Any previous AI output was based on older text and must be regenerated.
        cur.execute("DELETE FROM article_topics WHERE article_id = %s", (article_id,))
        cur.execute("DELETE FROM article_embeddings WHERE article_id = %s", (article_id,))
        # Offsets no longer point to the same text after a replacement.
        cur.execute("DELETE FROM highlights WHERE article_id = %s", (article_id,))
        cur.execute("DELETE FROM takeaways WHERE article_id = %s", (article_id,))
    return reading_minutes


def main() -> None:
    parser = argparse.ArgumentParser(description="Save text you are entitled to access for an RSS article.")
    parser.add_argument("article_id", type=int, help="ID shown in the PrepBrief database")
    parser.add_argument("text_file", help="UTF-8 text file path, or '-' to read from stdin")
    parser.add_argument("--username", help="Account to import into (required when multiple accounts exist)")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.text_file == "-" else Path(args.text_file).read_text(encoding="utf-8")
    try:
        with user_context(args.username):
            minutes = save_text(args.article_id, raw)
    except (ValueError, LookupError) as exc:
        parser.error(str(exc))
    print(f"Saved text for article {args.article_id} ({minutes} min reading time).")


if __name__ == "__main__":
    main()
