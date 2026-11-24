"""Neon / Postgres connection helper."""
import os
from contextvars import ContextVar
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv

load_dotenv()
CURRENT_USER_ID: ContextVar[int | None] = ContextVar("prepbrief_current_user_id", default=None)


def set_current_user(user_id: int):
    """Set the authenticated owner for database connections in this request context."""
    return CURRENT_USER_ID.set(user_id)


def reset_current_user(token) -> None:
    CURRENT_USER_ID.reset(token)


@contextmanager
def user_context(username: str | None = None):
    """Apply an account context for command-line jobs; infer it only when unambiguous."""
    with get_conn() as conn, conn.cursor() as cur:
        if username:
            cur.execute("SELECT id FROM app_accounts WHERE lower(username)=lower(%s)", (username,))
        else:
            cur.execute("SELECT id FROM app_accounts ORDER BY id LIMIT 2")
        rows = cur.fetchall()
    if not rows:
        raise LookupError("No PrepBrief account exists yet. Sign up in the app first.")
    if len(rows) > 1:
        raise LookupError("More than one account exists. Supply --username so this command uses the right private data.")
    token = set_current_user(rows[0][0])
    try:
        yield rows[0][0]
    finally:
        reset_current_user(token)


def get_conn() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    conn = psycopg.connect(url)
    user_id = CURRENT_USER_ID.get()
    if user_id is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
    return conn
