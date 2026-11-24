"""PrepBrief JSON API. Start with: uvicorn app.main:app --reload"""
from datetime import date
import hashlib
import hmac
import html
import os
from pathlib import Path
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
import psycopg
from google import genai
from starlette.responses import JSONResponse
import trafilatura
import yaml

from db.connection import get_conn, reset_current_user, set_current_user
from pipeline.embeddings import MODEL as EMBEDDING_MODEL
from pipeline.embeddings import cosine_similarity, embed_document, embed_query

app = FastAPI(title="PrepBrief API", version="0.1.0")
SESSION_COOKIE = "prepbrief_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60
PUBLIC_PATHS = {"/", "/app.js", "/styles.css", "/auth/status", "/auth/signup", "/auth/signup/verify",
                "/auth/password/forgot", "/auth/password/reset", "/auth/login", "/auth/logout"}


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def password_digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)


def set_session_cookie(response: Response, token: str, request: Request) -> None:
    secure = request.url.scheme == "https" or os.environ.get("APP_COOKIE_SECURE", "").lower() == "true"
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True,
        secure=secure, samesite="lax", path="/",
    )


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/assets/"):
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return JSONResponse({"detail": "Please log in to continue."}, status_code=401)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM app_sessions WHERE token_hash=%s AND expires_at > now()",
                (token_digest(token),),
            )
            session = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return JSONResponse(
            {"detail": "Authentication tables are missing. Run `python -m db.init_db` once, then restart PrepBrief."},
            status_code=503,
        )
    if not session:
        return JSONResponse({"detail": "Your session expired. Please log in again."}, status_code=401)
    request.state.user_id = session[0]
    context_token = set_current_user(session[0])
    try:
        return await call_next(request)
    finally:
        reset_current_user(context_token)


def row_dict(cursor, row) -> dict:
    return {column.name: value for column, value in zip(cursor.description, row)}


def article_exists(cursor, article_id: int) -> bool:
    cursor.execute("SELECT 1 FROM articles WHERE id = %s", (article_id,))
    return cursor.fetchone() is not None


class SaveRequest(BaseModel):
    is_saved: bool = True


class SignupRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    username: str | None = Field(default=None, min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=1, max_length=256)


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class VerifySignupRequest(EmailCodeRequest):
    code: str = Field(pattern=r"^\d{8}$")


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    code: str = Field(pattern=r"^\d{8}$")
    new_password: str = Field(min_length=10, max_length=256)
    confirm_password: str = Field(min_length=10, max_length=256)


class RecoveryEmailVerifyRequest(EmailCodeRequest):
    code: str = Field(pattern=r"^\d{8}$")


def send_auth_code(email_address: str, code: str, purpose: str) -> None:
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")
    if not host or not sender:
        raise HTTPException(status_code=503, detail="Email is not configured. Set SMTP_HOST and SMTP_FROM in .env, then restart PrepBrief.")
    message = EmailMessage()
    message["Subject"] = f"PrepBrief {purpose} code"
    message["From"] = sender
    message["To"] = email_address
    message.set_content(f"Your PrepBrief verification code is {code}. It expires in 10 minutes. If you did not request it, ignore this email.")
    port = int(os.environ.get("SMTP_PORT", "587"))
    timeout = 15
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as smtp:
                _smtp_login(smtp)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                _smtp_login(smtp)
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail=f"Could not send email ({type(exc).__name__}). Check SMTP settings in .env.") from exc


def _smtp_login(smtp) -> None:
    username = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if username:
        smtp.login(username, password or "")


def auth_code_digest(purpose: str, username: str, code: str) -> bytes:
    key = os.environ.get("DATABASE_URL", "").encode("utf-8")
    value = f"{purpose}:{username.lower()}:{code}".encode("utf-8")
    return hmac.new(key, value, hashlib.sha256).digest()


def create_email_code(purpose: str, username: str, email_address: str, payload: dict | None = None) -> None:
    code = f"{secrets.randbelow(100_000_000):08d}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM auth_email_codes WHERE expires_at<=now()")
        cur.execute(
            "SELECT 1 FROM auth_email_codes WHERE purpose=%s AND username=%s AND created_at>now()-interval '60 seconds'",
            (purpose, username.lower()),
        )
        if cur.fetchone():
            raise HTTPException(status_code=429, detail="Please wait a minute before requesting another email code.")
        cur.execute(
            "INSERT INTO auth_email_codes (purpose,username,email,payload,code_hash,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,now()+interval '10 minutes') "
            "ON CONFLICT (purpose,username) DO UPDATE SET email=EXCLUDED.email,payload=EXCLUDED.payload,"
            "code_hash=EXCLUDED.code_hash,attempts=0,created_at=now(),expires_at=EXCLUDED.expires_at",
            (purpose, username.lower(), email_address.lower(), Jsonb(payload) if payload else None,
             auth_code_digest(purpose, username, code)),
        )
    try:
        send_auth_code(email_address, code, "verification" if purpose in ("signup", "recovery_email") else "password reset")
    except Exception:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM auth_email_codes WHERE purpose=%s AND username=%s", (purpose, username.lower()))
        raise


def consume_email_code(cur, purpose: str, username: str, email_address: str, code: str):
    cur.execute(
        "SELECT id,payload,code_hash,attempts FROM auth_email_codes "
        "WHERE purpose=%s AND username=%s AND email=%s AND expires_at>now() FOR UPDATE",
        (purpose, username.lower(), email_address.lower()),
    )
    record = cur.fetchone()
    if not record or record[3] >= 5 or not hmac.compare_digest(record[2], auth_code_digest(purpose, username, code)):
        if record:
            cur.execute("UPDATE auth_email_codes SET attempts=attempts+1 WHERE id=%s", (record[0],))
            cur.connection.commit()
        raise HTTPException(status_code=400, detail="That code is invalid or expired. Request a new code and try again.")
    cur.execute("DELETE FROM auth_email_codes WHERE id=%s", (record[0],))
    return record[1]


class NoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class ArticleTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300000)


def analyze_saved_article(article_id: int, reading_minutes: int) -> dict:
    result = {"reading_minutes": reading_minutes, "tagged": False, "tag_status": None,
              "tag_error": None, "embedded": False, "embedding_chunks": 0,
              "embedding_error": None}
    try:
        from pipeline.tag_article import tag_article
        result["tag_status"] = tag_article(article_id)
        result["tagged"] = True
    except Exception as exc:
        result["tag_error"] = type(exc).__name__
    try:
        from pipeline.index_articles import index_article
        result["embedding_chunks"] = index_article(article_id)
        result["embedded"] = True
    except Exception as exc:
        result["embedding_error"] = type(exc).__name__
    return result


def fetch_public_article_text(url: str) -> str:
    """Fetch only HTTPS pages on domains already configured as RSS sources."""
    feeds_path = Path(__file__).resolve().parent.parent / "config" / "feeds.yaml"
    feeds = yaml.safe_load(feeds_path.read_text(encoding="utf-8"))
    allowed_hosts = {
        (urlsplit(feed["url"]).hostname or "").lower().rstrip(".")
        for feed in feeds.get("feeds", [])
    }

    def validate_url(candidate: str) -> None:
        try:
            parsed = urlsplit(candidate)
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="The saved article URL is invalid.") from exc
        if (parsed.scheme != "https" or host not in allowed_hosts or
                parsed.username or parsed.password or port not in (None, 443)):
            raise HTTPException(
                status_code=422,
                detail="Automatic fetching is limited to HTTPS article pages from the configured RSS sources.",
            )

    current_url = url
    headers = {"User-Agent": "PrepBrief/0.1 (personal study tool)", "Accept": "text/html"}
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False, headers=headers) as client:
            for _ in range(6):
                validate_url(current_url)
                with client.stream("GET", current_url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(status_code=502, detail="The article source returned an invalid redirect.")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code in (401, 403, 404, 429):
                        raise HTTPException(
                            status_code=422,
                            detail="The source did not provide a public article page. It may require sign-in or show only a preview; paste text you can access instead.",
                        )
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                        raise HTTPException(status_code=422, detail="The article link did not return an HTML page.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 5 * 1024 * 1024:
                            raise HTTPException(status_code=413, detail="The article page is larger than PrepBrief's 5 MB fetch limit.")
                    extracted = trafilatura.extract(
                        bytes(body), url=current_url, include_comments=False,
                        include_tables=False, output_format="txt",
                    )
                    if not extracted:
                        raise HTTPException(
                            status_code=422,
                            detail="PrepBrief could not extract article text from this page. Paste text you can access instead.",
                        )
                    from pipeline.import_text import clean_text
                    text = clean_text(html.unescape(re.sub(r"<[^>]+>", " ", extracted)))
                    word_count = len(text.split())
                    if word_count < 150:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Only {word_count} words were available, likely a preview rather than the full article. Paste text you can access instead.",
                        )
                    return text
            raise HTTPException(status_code=422, detail="The article source redirected too many times.")
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"The article source returned HTTP {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach the article source ({type(exc).__name__}).") from exc


class HighlightRequest(BaseModel):
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class TagPair(BaseModel):
    subject: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=150)


class CorrectionRequest(BaseModel):
    is_relevant: bool
    must_read: bool
    tags: list[TagPair] = Field(max_length=20)
    note: str | None = Field(default=None, max_length=2000)


class ReferenceRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=12000)
    source_url: str | None = Field(default=None, max_length=2000)
    tags: list[TagPair] = Field(default_factory=list, max_length=20)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=8)


class GroundedAnswer(BaseModel):
    answer: str
    citations: list[int]


@app.get("/auth/status")
def auth_status(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM app_accounts)")
            accounts_exist = cur.fetchone()[0]
            session_user = None
            if token:
                cur.execute(
                    "SELECT a.id,a.username,a.email FROM app_sessions s JOIN app_accounts a ON a.id=s.user_id "
                    "WHERE s.token_hash=%s AND s.expires_at > now()",
                    (token_digest(token),),
                )
                session_user = cur.fetchone()
            result = {"account_exists": accounts_exist, "authenticated": session_user is not None,
                      "user_id": session_user[0] if session_user else None,
                      "username": session_user[1] if session_user else None,
                      "email": session_user[2] if session_user else None}
            return result
    except psycopg.errors.UndefinedTable as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication tables are missing. Run `python -m db.init_db` once, then restart PrepBrief.",
        ) from exc


@app.post("/auth/signup")
def signup(request: SignupRequest):
    email_address = request.email.strip().lower()
    username = request.username.strip().lower() if request.username else None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM app_accounts WHERE lower(email)=%s", (email_address,))
        email_taken = cur.fetchone()
        if username:
            cur.execute("SELECT 1 FROM app_accounts WHERE lower(username)=%s", (username,))
            username_taken = cur.fetchone()
        else:
            username_taken = None
        if email_taken or username_taken:
            raise HTTPException(status_code=409, detail="That email address or username is already in use.")
    # Keep the salt and hash together so the password itself is never stored in a pending signup.
    salt = secrets.token_bytes(16)
    payload = {"username": username, "password_salt": salt.hex(), "password_hash": password_digest(request.password, salt).hex()}
    create_email_code("signup", email_address, email_address, payload)
    return {"verification_required": True, "message": "Enter the verification code sent to your email."}


@app.post("/auth/signup/verify", status_code=201)
def verify_signup(request: VerifySignupRequest, http_request: Request, background_tasks: BackgroundTasks):
    email_address = request.email.strip().lower()
    token = secrets.token_urlsafe(32)
    with get_conn() as conn, conn.cursor() as cur:
        payload = consume_email_code(cur, "signup", email_address, email_address, request.code)
        if not payload:
            raise HTTPException(status_code=400, detail="Signup details expired. Please sign up again.")
        username = payload.get("username")
        cur.execute(
            "INSERT INTO app_accounts (username,email,email_verified_at,password_salt,password_hash) "
            "VALUES (%s,%s,now(),%s,%s) ON CONFLICT DO NOTHING RETURNING id",
            (username, email_address, bytes.fromhex(payload["password_salt"]), bytes.fromhex(payload["password_hash"])),
        )
        account = cur.fetchone()
        if account is None:
            raise HTTPException(status_code=409, detail="That username or email address is already in use.")
        cur.execute("INSERT INTO app_sessions (token_hash,user_id,expires_at) VALUES (%s,%s,now()+interval '30 days')",
                    (token_digest(token), account[0]))
    response = JSONResponse({"authenticated": True, "user_id": account[0], "username": username})
    set_session_cookie(response, token, http_request)
    from pipeline.fetch_rss import fetch_all
    background_tasks.add_task(fetch_all)
    return response


@app.post("/auth/password/forgot")
def forgot_password(request: EmailCodeRequest):
    email_address = request.email.strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM app_accounts WHERE lower(email)=%s AND email_verified_at IS NOT NULL", (email_address,))
        exists = cur.fetchone() is not None
    if exists:
        create_email_code("reset", email_address, email_address)
    return {"message": "If the account has a verified email, a reset code has been sent."}


@app.post("/auth/password/reset")
def reset_password(request: PasswordResetRequest):
    if request.new_password != request.confirm_password:
        raise HTTPException(status_code=422, detail="The new passwords do not match.")
    email_address = request.email.strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app_accounts WHERE lower(email)=%s AND email_verified_at IS NOT NULL", (email_address,))
        account = cur.fetchone()
        if not account:
            raise HTTPException(status_code=400, detail="That code is invalid or expired. Request a new code and try again.")
        user_id = account[0]
        consume_email_code(cur, "reset", email_address, email_address, request.code)
        salt = secrets.token_bytes(16)
        cur.execute("UPDATE app_accounts SET password_salt=%s,password_hash=%s WHERE id=%s",
                    (salt, password_digest(request.new_password, salt), user_id))
        cur.execute("DELETE FROM app_sessions WHERE user_id=%s", (user_id,))
    return {"message": "Password updated. Please sign in with your new password."}


@app.post("/auth/email/change/request")
def request_recovery_email(request: EmailCodeRequest, http_request: Request):
    email_address = request.email.strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app_accounts WHERE id=%s", (http_request.state.user_id,))
        account = cur.fetchone()
        cur.execute("SELECT 1 FROM app_accounts WHERE lower(email)=%s AND id<>%s", (email_address, http_request.state.user_id))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="That email address is already in use by another account.")
    create_email_code("recovery_email", str(http_request.state.user_id), email_address)
    return {"message": "Enter the verification code sent to the new email address."}


@app.post("/auth/email/change/verify")
def verify_recovery_email(request: RecoveryEmailVerifyRequest, http_request: Request):
    email_address = request.email.strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        consume_email_code(cur, "recovery_email", str(http_request.state.user_id), email_address, request.code)
        cur.execute("UPDATE app_accounts SET email=%s,email_verified_at=now() WHERE id=%s",
                    (email_address, http_request.state.user_id))
    return {"message": "Your account email is verified and updated."}


@app.post("/auth/login")
def login(request: LoginRequest, http_request: Request):
    email_address = request.email.strip().lower()
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,username,password_salt,password_hash,email FROM app_accounts WHERE lower(email)=%s",
                (email_address,),
            )
            account = cur.fetchone()
            if account:
                salt, expected_hash = account[2], account[3]
            else:
                salt, expected_hash = b"prepbrief-auth-dummy", bytes(64)
    except psycopg.errors.UndefinedTable as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication tables are missing. Run `python -m db.init_db` once, then restart PrepBrief.",
        ) from exc

    submitted_hash = password_digest(request.password, salt)
    if not account or not hmac.compare_digest(submitted_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")

    token = secrets.token_urlsafe(32)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM app_sessions WHERE expires_at <= now()")
        cur.execute(
            "INSERT INTO app_sessions (token_hash,user_id,expires_at) VALUES (%s,%s,now() + interval '30 days')",
            (token_digest(token), account[0]),
        )
    response = JSONResponse({"authenticated": True, "user_id": account[0], "username": account[1]})
    set_session_cookie(response, token, http_request)
    return response


@app.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM app_sessions WHERE token_hash=%s", (token_digest(token),))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return response


@app.get("/articles")
def list_articles(
    date_filter: date | None = Query(default=None, alias="date"),
    subject: str | None = None,
    topic: str | None = None,
    section: str | None = Query(default=None, max_length=80),
    source: str | None = None,
    must_read: bool | None = None,
    saved: bool | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    conditions, params = [], []
    if date_filter:
        conditions.append("a.published_at::date = %s")
        params.append(date_filter)
    if subject:
        conditions.append(
            "EXISTS (SELECT 1 FROM article_topics ax JOIN topics tx ON tx.id=ax.topic_id "
            "JOIN subjects sx ON sx.id=tx.subject_id WHERE ax.article_id=a.id AND sx.name=%s)"
        )
        params.append(subject)
    if topic:
        conditions.append(
            "EXISTS (SELECT 1 FROM article_topics ax JOIN topics tx ON tx.id=ax.topic_id "
            "WHERE ax.article_id=a.id AND tx.name=%s)"
        )
        params.append(topic)
    if section:
        if section.strip().lower() == "other":
            feeds_path = Path(__file__).resolve().parent.parent / "config" / "feeds.yaml"
            feed_config = yaml.safe_load(feeds_path.read_text(encoding="utf-8"))
            known_sections = [feed["name"] for feed in feed_config.get("feeds", [])]
            conditions.append("(a.section IS NULL OR a.section <> ALL(%s))")
            params.append(known_sections)
        else:
            conditions.append("LOWER(a.section)=LOWER(%s)")
            params.append(section.strip())
    if source:
        conditions.append("a.source = %s")
        params.append(source)
    if must_read is not None:
        conditions.append("a.must_read = %s")
        params.append(must_read)
    if saved is not None:
        conditions.append("COALESCE(rs.is_saved, FALSE) = %s")
        params.append(saved)
    if q:
        conditions.append("(a.title ILIKE %s OR a.summary ILIKE %s OR a.full_text ILIKE %s)")
        pattern = f"%{q}%"
        params.extend((pattern, pattern, pattern))

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT a.id, a.url, a.title, a.source, a.section, a.published_at, a.summary,
               a.reading_minutes, a.must_read, a.is_relevant, a.status, a.confidence,
               (a.full_text IS NOT NULL AND btrim(a.full_text) !~* '^https?://[^[:space:]]+$') AS has_text,
               COALESCE(rs.is_read, FALSE) AS is_read,
               COALESCE(rs.is_saved, FALSE) AS is_saved,
               COALESCE((SELECT array_agg(DISTINCT s.name ORDER BY s.name)
                         FROM article_topics ats JOIN topics t ON t.id=ats.topic_id
                         JOIN subjects s ON s.id=t.subject_id WHERE ats.article_id=a.id), ARRAY[]::text[]) AS subjects,
               COALESCE((SELECT array_agg(DISTINCT t.name ORDER BY t.name)
                         FROM article_topics atp JOIN topics t ON t.id=atp.topic_id
                         WHERE atp.article_id=a.id), ARRAY[]::text[]) AS topics
        FROM articles a LEFT JOIN read_status rs ON rs.article_id=a.id
        {where}
        ORDER BY a.published_at DESC NULLS LAST, a.id DESC
        LIMIT %s OFFSET %s
    """
    params.extend((limit, offset))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [row_dict(cur, row) for row in cur.fetchall()]


@app.get("/articles/{article_id}")
def get_article(article_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT a.*, COALESCE(rs.is_read, FALSE) AS is_read, "
            "COALESCE(rs.is_saved, FALSE) AS is_saved FROM articles a "
            "LEFT JOIN read_status rs ON rs.article_id=a.id WHERE a.id=%s",
            (article_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Article not found")
        article = row_dict(cur, row)

        cur.execute(
            "SELECT s.name AS subject, t.name AS topic FROM article_topics at "
            "JOIN topics t ON t.id=at.topic_id JOIN subjects s ON s.id=t.subject_id "
            "WHERE at.article_id=%s ORDER BY s.name, t.name", (article_id,),
        )
        tag_rows = [row_dict(cur, item) for item in cur.fetchall()]
        article["subjects"] = sorted({item["subject"] for item in tag_rows})
        article["topics"] = [{"subject": item["subject"], "name": item["topic"]} for item in tag_rows]

        cur.execute(
            "SELECT id, start_offset, end_offset, origin FROM highlights "
            "WHERE article_id=%s ORDER BY start_offset", (article_id,),
        )
        article["highlights"] = [row_dict(cur, item) for item in cur.fetchall()]
        for highlight in article["highlights"]:
            highlight["text"] = article["full_text"][highlight["start_offset"]:highlight["end_offset"]] if article["full_text"] else ""

        cur.execute("SELECT position, text FROM takeaways WHERE article_id=%s ORDER BY position", (article_id,))
        article["key_takeaways"] = [row_dict(cur, item) for item in cur.fetchall()]
        cur.execute(
            "SELECT n.id,n.text,n.created_at,EXISTS(SELECT 1 FROM note_embeddings e WHERE e.note_id=n.id) AS embedding_indexed "
            "FROM notes n WHERE n.article_id=%s ORDER BY n.created_at DESC,n.id DESC",
            (article_id,),
        )
        article["notes"] = [row_dict(cur, item) for item in cur.fetchall()]
        cur.execute(
            "SELECT id, original_analysis, corrected_analysis, note, created_at "
            "FROM corrections WHERE article_id=%s ORDER BY created_at DESC, id DESC",
            (article_id,),
        )
        article["corrections"] = [row_dict(cur, item) for item in cur.fetchall()]
        return article


@app.get("/articles/{article_id}/related")
def related_articles(article_id: int, limit: int = Query(default=5, ge=1, le=20)):
    with get_conn() as conn, conn.cursor() as cur:
        if not article_exists(cur, article_id):
            raise HTTPException(status_code=404, detail="Article not found")
        cur.execute(
            "SELECT embedding FROM article_embeddings WHERE article_id=%s AND embedding_model=%s",
            (article_id, EMBEDDING_MODEL),
        )
        source_vectors = [row[0] for row in cur.fetchall()]
        if not source_vectors:
            cur.execute(
                "SELECT a.id,a.title,a.source,a.section,a.published_at,a.summary,a.reading_minutes,a.must_read, "
                "COUNT(DISTINCT shared.topic_id) AS shared_topic_count,array_agg(DISTINCT t.name ORDER BY t.name) AS shared_topics "
                "FROM article_topics current_tag JOIN article_topics shared "
                "ON shared.topic_id=current_tag.topic_id AND shared.article_id<>current_tag.article_id "
                "JOIN topics t ON t.id=shared.topic_id JOIN articles a ON a.id=shared.article_id "
                "WHERE current_tag.article_id=%s GROUP BY a.id "
                "ORDER BY shared_topic_count DESC,a.published_at DESC NULLS LAST LIMIT %s",
                (article_id, limit),
            )
            return [row_dict(cur, row) for row in cur.fetchall()]

        cur.execute(
            "SELECT a.id,a.title,a.source,a.section,a.published_at,a.summary,a.reading_minutes,a.must_read,ae.embedding, "
            "(SELECT COUNT(DISTINCT shared.topic_id) FROM article_topics mine JOIN article_topics shared "
            "ON shared.topic_id=mine.topic_id WHERE mine.article_id=%s AND shared.article_id=a.id) AS shared_topic_count, "
            "COALESCE((SELECT array_agg(DISTINCT t.name ORDER BY t.name) FROM article_topics mine "
            "JOIN article_topics shared ON shared.topic_id=mine.topic_id JOIN topics t ON t.id=shared.topic_id "
            "WHERE mine.article_id=%s AND shared.article_id=a.id),ARRAY[]::text[]) AS shared_topics "
            "FROM articles a LEFT JOIN article_embeddings ae ON ae.article_id=a.id AND ae.embedding_model=%s "
            "WHERE a.id<>%s AND (ae.article_id IS NOT NULL OR EXISTS "
            "(SELECT 1 FROM article_topics mine JOIN article_topics shared ON shared.topic_id=mine.topic_id "
            "WHERE mine.article_id=%s AND shared.article_id=a.id))",
            (article_id, article_id, EMBEDDING_MODEL, article_id, article_id),
        )
        grouped = {}
        for row in cur.fetchall():
            item = row_dict(cur, row)
            vector = item.pop("embedding")
            current = grouped.setdefault(item["id"], {**item, "vectors": []})
            if vector:
                current["vectors"].append(vector)

    for item in grouped.values():
        item["similarity"] = max(
            (cosine_similarity(left, right) for left in source_vectors for right in item["vectors"]),
            default=0.0,
        )
    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            item["similarity"] + 0.02 * item["shared_topic_count"],
            item["published_at"].timestamp() if item["published_at"] else 0,
        ),
        reverse=True,
    )[:limit]
    for item in ranked:
        item.pop("vectors", None)
    return ranked


@app.get("/day/{day}/summary")
def day_summary(day: date):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS article_count, "
            "COUNT(*) FILTER (WHERE COALESCE(rs.is_read,FALSE)) AS read_count, "
            "COUNT(*) FILTER (WHERE NOT COALESCE(rs.is_read,FALSE)) AS unread_count, "
            "COALESCE(SUM(CASE WHEN NOT COALESCE(rs.is_read,FALSE) "
            "THEN COALESCE(a.reading_minutes,0) ELSE 0 END),0) AS time_left_minutes "
            "FROM articles a LEFT JOIN read_status rs ON rs.article_id=a.id "
            "WHERE a.published_at::date=%s", (day,),
        )
        return {"date": day, **row_dict(cur, cur.fetchone())}


@app.post("/articles/{article_id}/read")
def mark_read(article_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        if not article_exists(cur, article_id):
            raise HTTPException(status_code=404, detail="Article not found")
        cur.execute(
            "INSERT INTO read_status (article_id, is_read, read_at) VALUES (%s, TRUE, now()) "
            "ON CONFLICT (article_id) DO UPDATE SET is_read=TRUE, read_at=now() "
            "RETURNING article_id, is_read, is_saved, read_at", (article_id,),
        )
        return row_dict(cur, cur.fetchone())


@app.post("/articles/{article_id}/save")
def save_article(article_id: int, request: SaveRequest = SaveRequest()):
    with get_conn() as conn, conn.cursor() as cur:
        if not article_exists(cur, article_id):
            raise HTTPException(status_code=404, detail="Article not found")
        cur.execute(
            "INSERT INTO read_status (article_id, is_saved) VALUES (%s, %s) "
            "ON CONFLICT (article_id) DO UPDATE SET is_saved=EXCLUDED.is_saved "
            "RETURNING article_id, is_read, is_saved, read_at", (article_id, request.is_saved),
        )
        return row_dict(cur, cur.fetchone())


@app.post("/articles/{article_id}/text")
def save_article_text(article_id: int, request: ArticleTextRequest):
    from pipeline.import_text import save_text

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Article text cannot be blank")
    parsed_text = urlsplit(text)
    if parsed_text.scheme.lower() in ("http", "https") and parsed_text.netloc and not re.search(r"\s", text):
        raise HTTPException(
            status_code=422,
            detail="This looks like a link, not article text. Use Fetch text from source link above the paste box.",
        )
    try:
        minutes = save_text(article_id, text)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return analyze_saved_article(article_id, minutes)


@app.post("/articles/{article_id}/fetch-text")
def fetch_and_analyze_article_text(article_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT url FROM articles WHERE id=%s", (article_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Article not found")
    text = fetch_public_article_text(row[0])
    try:
        from pipeline.import_text import save_text
        minutes = save_text(article_id, text)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = analyze_saved_article(article_id, minutes)
    result["fetched_words"] = len(text.split())
    return result


@app.post("/articles/{article_id}/notes", status_code=201)
def add_note(article_id: int, request: NoteRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Note text cannot be blank")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT title FROM articles WHERE id=%s", (article_id,))
        article_row = cur.fetchone()
        if article_row is None:
            raise HTTPException(status_code=404, detail="Article not found")
        article_title = article_row[0]
        cur.execute(
            "INSERT INTO notes (article_id, text) VALUES (%s, %s) "
            "RETURNING id, article_id, text, created_at", (article_id, text),
        )
        note = row_dict(cur, cur.fetchone())

    try:
        embedding = embed_document(article_title, text)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO note_embeddings (note_id, embedding, embedding_model) "
                "VALUES (%s, %s, %s) ON CONFLICT (note_id) DO UPDATE "
                "SET embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model, updated_at=now()",
                (note["id"], Jsonb(embedding), EMBEDDING_MODEL),
            )
        note["embedding_indexed"] = True
    except Exception:
        note["embedding_indexed"] = False
    return note


@app.post("/articles/{article_id}/corrections", status_code=201)
def add_correction(article_id: int, request: CorrectionRequest):
    pairs = [(tag.subject.strip(), tag.topic.strip()) for tag in request.tags]
    if len(pairs) != len(set(pairs)):
        raise HTTPException(status_code=422, detail="Duplicate subject/topic pair")
    if not request.is_relevant and (pairs or request.must_read):
        raise HTTPException(status_code=422, detail="A non-relevant article cannot have tags or be marked must-read")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT is_relevant, must_read, confidence FROM articles WHERE id=%s", (article_id,)
        )
        article = cur.fetchone()
        if article is None:
            raise HTTPException(status_code=404, detail="Article not found")

        cur.execute(
            "SELECT s.name, t.name FROM subjects s JOIN topics t ON t.subject_id=s.id"
        )
        valid_tags = {(subject, topic) for subject, topic in cur.fetchall()}
        invalid_tags = sorted(set(pairs) - valid_tags)
        if invalid_tags:
            raise HTTPException(status_code=422, detail={"unknown_tags": invalid_tags})

        cur.execute(
            "SELECT s.name, t.name FROM article_topics at JOIN topics t ON t.id=at.topic_id "
            "JOIN subjects s ON s.id=t.subject_id WHERE at.article_id=%s ORDER BY s.name,t.name",
            (article_id,),
        )
        old_tags = [{"subject": subject, "topic": topic} for subject, topic in cur.fetchall()]
        original = {
            "is_relevant": article[0], "must_read": article[1],
            "confidence": article[2], "tags": old_tags,
        }
        corrected = {
            "is_relevant": request.is_relevant,
            "must_read": request.must_read,
            "tags": [{"subject": subject, "topic": topic} for subject, topic in pairs],
        }
        cur.execute(
            "INSERT INTO corrections (article_id, original_analysis, corrected_analysis, note) "
            "VALUES (%s, %s, %s, %s) RETURNING id, article_id, created_at",
            (article_id, Jsonb(original), Jsonb(corrected), request.note.strip() or None if request.note else None),
        )
        correction = row_dict(cur, cur.fetchone())

        cur.execute("DELETE FROM article_topics WHERE article_id=%s", (article_id,))
        for subject, topic in pairs:
            cur.execute(
                "INSERT INTO article_topics (article_id, topic_id) "
                "SELECT %s, t.id FROM topics t JOIN subjects s ON s.id=t.subject_id "
                "WHERE s.name=%s AND t.name=%s ON CONFLICT DO NOTHING",
                (article_id, subject, topic),
            )
        cur.execute(
            "UPDATE articles SET is_relevant=%s, must_read=%s, status='processed', "
            "processed_at=now() WHERE id=%s",
            (request.is_relevant, request.must_read, article_id),
        )
        correction["corrected_analysis"] = corrected
        correction["note"] = request.note.strip() if request.note and request.note.strip() else None
        return correction


@app.post("/articles/{article_id}/highlights", status_code=201)
def add_highlight(article_id: int, request: HighlightRequest):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT full_text FROM articles WHERE id=%s", (article_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Article not found")
        full_text = row[0] or ""
        if request.end_offset <= request.start_offset or request.end_offset > len(full_text):
            raise HTTPException(status_code=422, detail="Highlight offsets are outside the article text")
        cur.execute(
            "INSERT INTO highlights (article_id, start_offset, end_offset, origin) "
            "VALUES (%s, %s, %s, 'user') RETURNING id, start_offset, end_offset, origin",
            (article_id, request.start_offset, request.end_offset),
        )
        highlight = row_dict(cur, cur.fetchone())
        highlight["text"] = full_text[highlight["start_offset"]:highlight["end_offset"]]
        return highlight


def reference_tags_for(cursor, reference_id: int) -> list[dict]:
    cursor.execute(
        "SELECT s.name AS subject, t.name AS topic FROM reference_item_topics rt "
        "JOIN topics t ON t.id=rt.topic_id JOIN subjects s ON s.id=t.subject_id "
        "WHERE rt.reference_id=%s ORDER BY s.name,t.name", (reference_id,),
    )
    return [row_dict(cursor, row) for row in cursor.fetchall()]


def save_reference_embedding(reference_id: int, title: str, category: str, summary: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM reference_embeddings WHERE reference_id=%s", (reference_id,))
    try:
        embedding = embed_document(f"{category}: {title}", summary)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reference_embeddings (reference_id, embedding, embedding_model) "
                "VALUES (%s, %s, %s) ON CONFLICT (reference_id) DO UPDATE "
                "SET embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model, updated_at=now()",
                (reference_id, Jsonb(embedding), EMBEDDING_MODEL),
            )
        return True
    except Exception:
        return False


def validate_reference_tags(cursor, tags: list[TagPair]) -> list[tuple[str, str]]:
    pairs = [(tag.subject.strip(), tag.topic.strip()) for tag in tags]
    if len(pairs) != len(set(pairs)):
        raise HTTPException(status_code=422, detail="Duplicate subject/topic pair")
    cursor.execute("SELECT s.name,t.name FROM subjects s JOIN topics t ON t.subject_id=s.id")
    valid_pairs = set(cursor.fetchall())
    unknown = sorted(set(pairs) - valid_pairs)
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_tags": unknown})
    return pairs


@app.get("/references")
def list_references(q: str | None = Query(default=None, max_length=200)):
    conditions, params = [], []
    if q:
        conditions.append("(r.title ILIKE %s OR r.category ILIKE %s OR r.summary ILIKE %s)")
        pattern = f"%{q}%"
        params.extend((pattern, pattern, pattern))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT r.id,r.title,r.category,r.summary,r.source_url,r.created_at,r.updated_at, "
            "COALESCE((SELECT jsonb_agg(jsonb_build_object('subject',s.name,'topic',t.name) ORDER BY s.name,t.name) "
            "FROM reference_item_topics rt JOIN topics t ON t.id=rt.topic_id "
            "JOIN subjects s ON s.id=t.subject_id WHERE rt.reference_id=r.id),'[]'::jsonb) AS tags, "
            "EXISTS(SELECT 1 FROM reference_embeddings e WHERE e.reference_id=r.id) AS embedding_indexed "
            f"FROM reference_items r{where} ORDER BY r.category,r.title",
            params,
        )
        return [row_dict(cur, row) for row in cur.fetchall()]


@app.get("/references/{reference_id}")
def get_reference(reference_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id,title,category,summary,source_url,created_at,updated_at "
            "FROM reference_items WHERE id=%s", (reference_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Reference item not found")
        reference = row_dict(cur, row)
        reference["tags"] = reference_tags_for(cur, reference_id)
        cur.execute("SELECT 1 FROM reference_embeddings WHERE reference_id=%s", (reference_id,))
        reference["embedding_indexed"] = cur.fetchone() is not None
        return reference


@app.post("/references", status_code=201)
def create_reference(request: ReferenceRequest):
    title, category, summary = request.title.strip(), request.category.strip(), request.summary.strip()
    source_url = request.source_url.strip() or None if request.source_url else None
    if not title or not category or not summary:
        raise HTTPException(status_code=422, detail="Title, type, and summary cannot be blank")
    if source_url and urlsplit(source_url).scheme not in ("https", "http"):
        raise HTTPException(status_code=422, detail="Source link must use http or https")
    with get_conn() as conn, conn.cursor() as cur:
        pairs = validate_reference_tags(cur, request.tags)
        cur.execute(
            "INSERT INTO reference_items (title,category,summary,source_url) VALUES (%s,%s,%s,%s) "
            "RETURNING id,created_at,updated_at",
            (title, category, summary, source_url),
        )
        row = row_dict(cur, cur.fetchone())
        reference_id = row["id"]
        for subject, topic in pairs:
            cur.execute(
                "INSERT INTO reference_item_topics (reference_id,topic_id) "
                "SELECT %s,t.id FROM topics t JOIN subjects s ON s.id=t.subject_id "
                "WHERE s.name=%s AND t.name=%s",
                (reference_id, subject, topic),
            )
    indexed = save_reference_embedding(reference_id, title, category, summary)
    return {
        "id": reference_id, "title": title, "category": category, "summary": summary,
        "source_url": source_url, "tags": [{"subject": s, "topic": t} for s, t in pairs],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "embedding_indexed": indexed,
    }


@app.put("/references/{reference_id}")
def update_reference(reference_id: int, request: ReferenceRequest):
    title, category, summary = request.title.strip(), request.category.strip(), request.summary.strip()
    source_url = request.source_url.strip() or None if request.source_url else None
    if not title or not category or not summary:
        raise HTTPException(status_code=422, detail="Title, type, and summary cannot be blank")
    if source_url and urlsplit(source_url).scheme not in ("https", "http"):
        raise HTTPException(status_code=422, detail="Source link must use http or https")
    with get_conn() as conn, conn.cursor() as cur:
        pairs = validate_reference_tags(cur, request.tags)
        cur.execute(
            "UPDATE reference_items SET title=%s,category=%s,summary=%s,source_url=%s,updated_at=now() "
            "WHERE id=%s RETURNING id,updated_at",
            (title, category, summary, source_url, reference_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Reference item not found")
        cur.execute("DELETE FROM reference_item_topics WHERE reference_id=%s", (reference_id,))
        for subject, topic in pairs:
            cur.execute(
                "INSERT INTO reference_item_topics (reference_id,topic_id) "
                "SELECT %s,t.id FROM topics t JOIN subjects s ON s.id=t.subject_id "
                "WHERE s.name=%s AND t.name=%s",
                (reference_id, subject, topic),
            )
    indexed = save_reference_embedding(reference_id, title, category, summary)
    return {
        "id": reference_id, "title": title, "category": category, "summary": summary,
        "source_url": source_url, "tags": [{"subject": s, "topic": t} for s, t in pairs],
        "updated_at": row[1], "embedding_indexed": indexed,
    }


@app.delete("/references/{reference_id}")
def delete_reference(reference_id: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM reference_items WHERE id=%s RETURNING id", (reference_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Reference item not found")
    return {"deleted": True, "id": reference_id}


@app.post("/ask")
def ask_notes(request: AskRequest):
    try:
        query_vector = embed_query(request.question.strip())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not embed the question: {type(exc).__name__}") from exc

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 'article' AS source_type,a.id,a.title, "
            "substring(a.full_text FROM ae.start_offset+1 FOR ae.end_offset-ae.start_offset) AS content, "
            "a.url AS source_url,ae.embedding FROM article_embeddings ae "
            "JOIN articles a ON a.id=ae.article_id WHERE ae.embedding_model=%s "
            "UNION ALL "
            "SELECT 'note' AS source_type,n.id,a.title,n.text AS content,a.url AS source_url,ne.embedding "
            "FROM note_embeddings ne JOIN notes n ON n.id=ne.note_id "
            "JOIN articles a ON a.id=n.article_id WHERE ne.embedding_model=%s "
            "UNION ALL "
            "SELECT 'reference' AS source_type,r.id,r.title,r.summary AS content,r.source_url,re.embedding "
            "FROM reference_embeddings re JOIN reference_items r ON r.id=re.reference_id "
            "WHERE re.embedding_model=%s",
            (EMBEDDING_MODEL, EMBEDDING_MODEL, EMBEDDING_MODEL),
        )
        candidates = [row_dict(cur, row) for row in cur.fetchall()]

    if not candidates:
        raise HTTPException(status_code=409, detail="There are no indexed articles, notes, or reference entries yet. Index article text or add a note/reference first.")
    for item in candidates:
        item["score"] = cosine_similarity(query_vector, item["embedding"])
    selected = sorted(candidates, key=lambda item: item["score"], reverse=True)[:request.top_k]
    contexts = [
        f"[{number}] {item['source_type'].title()}: {item['title']}\n{item['content']}"
        for number, item in enumerate(selected, start=1)
    ]
    prompt = (
        "Answer the user's question using only the supplied article passages, personal notes, and reference summaries. "
        "The article passages come from full text the user added for personal study. Do not add facts not supported by context. "
        "If context is insufficient, say so clearly. Cite supporting snippets in the answer "
        "using bracketed numbers like [1]. Do not invent citations.\n\n"
        f"QUESTION:\n{request.question.strip()}\n\nCONTEXT:\n" + "\n\n".join(contexts)
    )
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not set in .env")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    try:
        response = genai.Client(api_key=api_key).interactions.create(
            model=model,
            input=prompt,
            response_format={
                "type": "text", "mime_type": "application/json",
                "schema": GroundedAnswer.model_json_schema(),
            },
        )
        result = GroundedAnswer.model_validate_json(response.output_text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini could not answer this question: {type(exc).__name__}") from exc

    sources = []
    for citation in sorted(set(result.citations)):
        if 1 <= citation <= len(selected):
            item = selected[citation - 1]
            sources.append({
                "number": citation, "type": item["source_type"], "id": item["id"],
                "title": item["title"], "url": item["source_url"],
                "similarity": round(item["score"], 3),
            })
    return {"answer": result.answer, "sources": sources, "embedding_model": EMBEDDING_MODEL}


@app.get("/taxonomy")
def get_taxonomy():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT s.name AS subject, t.name AS topic, t.description "
            "FROM subjects s LEFT JOIN topics t ON t.subject_id=s.id "
            "ORDER BY s.name, t.name"
        )
        taxonomy = {}
        for row in cur.fetchall():
            subject, topic, description = row
            taxonomy.setdefault(subject, []).append(
                {"name": topic, "description": description} if topic is not None else None
            )
        return [
            {"name": subject, "topics": [topic for topic in topics if topic is not None]}
            for subject, topics in taxonomy.items()
        ]


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
