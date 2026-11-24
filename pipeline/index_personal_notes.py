"""Backfill embeddings for existing personal notes and reference summaries."""
import argparse
import time

from psycopg.types.json import Jsonb

from db.connection import get_conn, user_context
from pipeline.embeddings import MODEL, embed_document


def pending_items(limit: int) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 'note' AS kind,n.id,a.title,n.text AS content "
            "FROM notes n JOIN articles a ON a.id=n.article_id "
            "LEFT JOIN note_embeddings e ON e.note_id=n.id "
            "WHERE e.note_id IS NULL OR e.embedding_model<>%s "
            "UNION ALL "
            "SELECT 'reference' AS kind,r.id,r.title,r.category || ': ' || r.summary AS content "
            "FROM reference_items r LEFT JOIN reference_embeddings e ON e.reference_id=r.id "
            "WHERE e.reference_id IS NULL OR e.embedding_model<>%s LIMIT %s",
            (MODEL, MODEL, limit),
        )
        return [
            {column.name: value for column, value in zip(cur.description, row)}
            for row in cur.fetchall()
        ]


def save_embedding(item: dict, vector: list[float]) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        if item["kind"] == "note":
            cur.execute(
                "INSERT INTO note_embeddings (note_id,embedding,embedding_model) VALUES (%s,%s,%s) "
                "ON CONFLICT (note_id) DO UPDATE SET embedding=EXCLUDED.embedding, "
                "embedding_model=EXCLUDED.embedding_model,updated_at=now()",
                (item["id"], Jsonb(vector), MODEL),
            )
        else:
            cur.execute(
                "INSERT INTO reference_embeddings (reference_id,embedding,embedding_model) VALUES (%s,%s,%s) "
                "ON CONFLICT (reference_id) DO UPDATE SET embedding=EXCLUDED.embedding, "
                "embedding_model=EXCLUDED.embedding_model,updated_at=now()",
                (item["id"], Jsonb(vector), MODEL),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Index unembedded personal notes and reference summaries.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum items to index this run")
    parser.add_argument("--username", help="Account to index (required when multiple accounts exist)")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be between 1 and 500")

    try:
        with user_context(args.username):
            items = pending_items(args.limit)
            if not items:
                print("All personal notes and references are already indexed.")
                return

            indexed = 0
            for item in items:
                try:
                    vector = embed_document(item["title"], item["content"])
                    save_embedding(item, vector)
                    indexed += 1
                    print(f"[ok] {item['kind']} {item['id']}: {item['title']}")
                    time.sleep(0.2)
                except Exception as exc:
                    print(f"[skip] {item['kind']} {item['id']}: {type(exc).__name__}")
            print(f"Indexed {indexed} of {len(items)} personal item(s).")
    except LookupError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
