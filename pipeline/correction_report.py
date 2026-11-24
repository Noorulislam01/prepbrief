"""Summarize recurring classification changes to guide taxonomy edits."""
import argparse
from collections import Counter

from db.connection import get_conn, user_context


def label_tags(analysis: dict) -> tuple[str, ...]:
    return tuple(sorted(f"{tag['subject']} / {tag['topic']}" for tag in analysis.get("tags", [])))


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize this account's classification corrections.")
    parser.add_argument("--username", help="Account to report (required when multiple accounts exist)")
    args = parser.parse_args()
    try:
        with user_context(args.username):
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT original_analysis, corrected_analysis FROM corrections ORDER BY id")
                rows = cur.fetchall()
    except LookupError as exc:
        parser.error(str(exc))

    if not rows:
        print("No corrections recorded yet. Correct an article in the reader first.")
        return

    relevance_changes = 0
    tag_changes = Counter()
    for original, corrected in rows:
        if original.get("is_relevant") != corrected.get("is_relevant"):
            relevance_changes += 1
        before, after = label_tags(original), label_tags(corrected)
        if before != after:
            tag_changes[(", ".join(before) or "(none)", ", ".join(after) or "(none)")] += 1

    print(f"Correction examples: {len(rows)}")
    print(f"Relevance changed: {relevance_changes}")
    print("\nMost common tag changes:")
    if not tag_changes:
        print("  No tag changes recorded.")
    else:
        for (before, after), count in tag_changes.most_common(20):
            print(f"  {count:>3}  {before}  ->  {after}")
    print("\nUse repeated patterns to refine descriptions in config/taxonomy.yaml.")


if __name__ == "__main__":
    main()
