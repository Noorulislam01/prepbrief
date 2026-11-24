"""Embed user-provided article text for semantic search and Ask my notes."""
import argparse
import time

from psycopg.types.json import Jsonb

from db.connection import get_conn, user_context
from pipeline.embeddings import MODEL, embed_document, split_article


def articles_with_text(limit: int) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.id,a.title,a.full_text FROM articles a WHERE a.full_text IS NOT NULL AND ("
            "NOT EXISTS (SELECT 1 FROM article_embeddings e WHERE e.article_id=a.id) OR "
            "EXISTS (SELECT 1 FROM article_embeddings e WHERE e.article_id=a.id AND e.embedding_model<>%s)) "
            "ORDER BY a.published_at DESC NULLS LAST,a.id DESC LIMIT %s",
            (MODEL, limit),
        )
        return [
            {column.name: value for column, value in zip(cur.description, row)}
            for row in cur.fetchall()
        ]


def indexed_chunks(article_id: int) -> list[tuple[int, str]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_index,embedding_model FROM article_embeddings "
            "WHERE article_id=%s ORDER BY chunk_index", (article_id,),
        )
        return cur.fetchall()


def save_chunks(article_id: int, chunks: list[tuple[int, int, int, list[float]]]) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO article_embeddings "
            "(article_id,chunk_index,start_offset,end_offset,embedding,embedding_model) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            [(article_id, index, start, end, Jsonb(vector), MODEL)
             for index, start, end, vector in chunks],
        )


def index_article(article_id: int) -> int:
    """Generate and atomically replace embeddings for one article's current text."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT title,full_text FROM articles WHERE id=%s", (article_id,))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"No article found with id {article_id}.")
    title, full_text = row
    if not full_text or not full_text.strip():
        raise ValueError("Article has no text to embed.")
    chunks = split_article(full_text)
    vectors = []
    for index, (start, end, content) in enumerate(chunks):
        vectors.append((index, start, end, embed_document(title, content)))
        time.sleep(0.2)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM article_embeddings WHERE article_id=%s", (article_id,))
        cur.executemany(
            "INSERT INTO article_embeddings "
            "(article_id,chunk_index,start_offset,end_offset,embedding,embedding_model) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            [(article_id, index, start, end, Jsonb(vector), MODEL)
             for index, start, end, vector in vectors],
        )
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index full text that you supplied for Gemini semantic search.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum articles to check this run")
    parser.add_argument("--username", help="Account to index (required when multiple accounts exist)")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 200:
        parser.error("--limit must be between 1 and 200")

    try:
        with user_context(args.username):
            articles = articles_with_text(args.limit)
            indexed = 0
            for article in articles:
                chunks = split_article(article["full_text"])
                prior = indexed_chunks(article["id"])
                if len(prior) == len(chunks) and all(model == MODEL for _, model in prior):
                    continue
                try:
                    index_article(article["id"])
                    indexed += 1
                    print(f"[ok] article {article['id']}: {len(chunks)} chunk(s), {article['title']}")
                except Exception as exc:
                    print(f"[skip] article {article['id']}: {type(exc).__name__}")
            print(f"Indexed {indexed} article(s) from the {len(articles)} checked.")
    except LookupError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
