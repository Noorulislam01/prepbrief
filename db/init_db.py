"""Create tables and load the taxonomy.  Run:  python -m db.init_db"""
from pathlib import Path

import yaml

from db.connection import get_conn

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    schema = (ROOT / "db" / "schema.sql").read_text()
    taxonomy = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text())

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(schema)

        for subject in taxonomy["subjects"]:
            cur.execute(
                "INSERT INTO subjects (name) VALUES (%s) "
                "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (subject["name"],),
            )
            subject_id = cur.fetchone()[0]
            for topic in subject["topics"]:
                cur.execute(
                    "INSERT INTO topics (subject_id, name, description) VALUES (%s, %s, %s) "
                    "ON CONFLICT (subject_id, name) DO UPDATE SET description = EXCLUDED.description",
                    (subject_id, topic["name"], topic.get("description")),
                )
        conn.commit()

    print("Database ready: tables created and taxonomy loaded.")


if __name__ == "__main__":
    main()
