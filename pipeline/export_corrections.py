"""Export private correction examples as JSONL for prompt evaluation."""
import argparse
import json
from pathlib import Path

from db.connection import get_conn, user_context

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "data" / "corrections_test_set.jsonl"


def export_corrections(output: Path) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, c.created_at, c.original_analysis, c.corrected_analysis, c.note, "
            "a.id, a.title, a.url, a.full_text FROM corrections c "
            "JOIN articles a ON a.id=c.article_id ORDER BY c.id"
        )
        rows = cur.fetchall()

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        for row in rows:
            example = {
                "correction_id": row[0],
                "corrected_at": row[1].isoformat(),
                "article_id": row[5],
                "title": row[6],
                "url": row[7],
                "article_text": row[8],
                "original_analysis": row[2],
                "corrected_analysis": row[3],
                "note": row[4],
            }
            file.write(json.dumps(example, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export corrections to a local JSONL test set.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output path (default: data/corrections_test_set.jsonl)")
    parser.add_argument("--username", help="Account to export (required when multiple accounts exist)")
    args = parser.parse_args()
    try:
        with user_context(args.username):
            count = export_corrections(args.output)
    except LookupError as exc:
        parser.error(str(exc))
    print(f"Exported {count} correction example(s) to {args.output}.")


if __name__ == "__main__":
    main()
