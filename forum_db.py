from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # Local source checks can run before dependencies are installed.
    pymysql = None
    DictCursor = None

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _kind() -> str:
    return "sqlite" if _database_url().startswith("sqlite://") else "mysql"


def _sqlite_path() -> str:
    url = _database_url()
    if not url:
        return ":memory:"
    return unquote(url.removeprefix("sqlite://")) or ":memory:"


def _connect():
    if _kind() == "sqlite":
        conn = sqlite3.connect(_sqlite_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    if pymysql is None:
        raise RuntimeError("PyMySQL is not installed")
    parsed = urlparse(_database_url())
    if not parsed.hostname or not parsed.path:
        raise RuntimeError("DATABASE_URL must be a MySQL connection URL")
    query = parse_qs(parsed.query)
    kwargs: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": unquote(parsed.path.lstrip("/")),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if os.getenv("DB_SSL_DISABLED", "").lower() not in {"1", "true", "yes"}:
        kwargs["ssl"] = {"ca": "/etc/ssl/certs/ca-certificates.crt"}
    return pymysql.connect(**kwargs)


@contextmanager
def connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sql(sql: str) -> str:
    return sql.replace("%s", "?") if _kind() == "sqlite" else sql


def _execute(conn, sql: str, args: Iterable[Any] = ()):
    cursor = conn.cursor()
    cursor.execute(_sql(sql), tuple(args))
    return cursor


def _rows(cursor) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _one(cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _now_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def serialize_thread(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "body": row["body"],
        "category": row.get("category_name") or row.get("category_slug"),
        "categorySlug": row.get("category_slug"),
        "author": row.get("author_name") or "FIKER community member",
        "authorId": int(row["author_id"]) if row.get("author_id") is not None else None,
        "status": row.get("status"),
        "pinned": bool(row.get("pinned")),
        "locked": bool(row.get("locked")),
        "replyCount": int(row.get("reply_count") or 0),
        "createdAt": _now_value(row.get("created_at")),
        "updatedAt": _now_value(row.get("updated_at")),
    }


def serialize_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "threadId": int(row["thread_id"]),
        "body": row["body"],
        "author": row.get("author_name") or "FIKER community member",
        "authorId": int(row["author_id"]) if row.get("author_id") is not None else None,
        "status": row.get("status"),
        "createdAt": _now_value(row.get("created_at")),
        "updatedAt": _now_value(row.get("updated_at")),
    }


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with connection() as conn:
            if _kind() == "sqlite":
                ddl = [
                    "CREATE TABLE IF NOT EXISTS forum_schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
                    "CREATE TABLE IF NOT EXISTS forum_users (id INTEGER PRIMARY KEY AUTOINCREMENT, open_id TEXT NOT NULL UNIQUE, email TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
                    "CREATE TABLE IF NOT EXISTS forum_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
                    "CREATE TABLE IF NOT EXISTS forum_threads (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL, author_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', pinned INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, moderated_by INTEGER, moderated_at TEXT, moderation_note TEXT, FOREIGN KEY(category_id) REFERENCES forum_categories(id), FOREIGN KEY(author_id) REFERENCES forum_users(id))",
                    "CREATE TABLE IF NOT EXISTS forum_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER NOT NULL, author_id INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, moderated_by INTEGER, moderated_at TEXT, moderation_note TEXT, FOREIGN KEY(thread_id) REFERENCES forum_threads(id), FOREIGN KEY(author_id) REFERENCES forum_users(id))",
                    "CREATE TABLE IF NOT EXISTS forum_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL, thread_id INTEGER, post_id INTEGER, reason TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_by INTEGER, resolved_at TEXT, resolution_note TEXT, FOREIGN KEY(reporter_id) REFERENCES forum_users(id))",
                ]
            else:
                ddl = [
                    "CREATE TABLE IF NOT EXISTS forum_schema_version (version INT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)",
                    "CREATE TABLE IF NOT EXISTS forum_users (id BIGINT PRIMARY KEY AUTO_INCREMENT, open_id VARCHAR(255) NOT NULL UNIQUE, email VARCHAR(255) NOT NULL DEFAULT '', display_name VARCHAR(120) NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'member', status VARCHAR(20) NOT NULL DEFAULT 'active', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX idx_forum_users_role (role)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
                    "CREATE TABLE IF NOT EXISTS forum_categories (id BIGINT PRIMARY KEY AUTO_INCREMENT, slug VARCHAR(80) NOT NULL UNIQUE, name VARCHAR(120) NOT NULL, description VARCHAR(255) NOT NULL DEFAULT '', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
                    "CREATE TABLE IF NOT EXISTS forum_threads (id BIGINT PRIMARY KEY AUTO_INCREMENT, category_id BIGINT NOT NULL, author_id BIGINT NOT NULL, title VARCHAR(180) NOT NULL, body TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', pinned BOOLEAN NOT NULL DEFAULT FALSE, locked BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, moderated_by BIGINT NULL, moderated_at TIMESTAMP NULL, moderation_note VARCHAR(500) NULL, INDEX idx_forum_threads_status_updated (status, pinned, updated_at), FOREIGN KEY(category_id) REFERENCES forum_categories(id), FOREIGN KEY(author_id) REFERENCES forum_users(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
                    "CREATE TABLE IF NOT EXISTS forum_posts (id BIGINT PRIMARY KEY AUTO_INCREMENT, thread_id BIGINT NOT NULL, author_id BIGINT NOT NULL, body TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, moderated_by BIGINT NULL, moderated_at TIMESTAMP NULL, moderation_note VARCHAR(500) NULL, INDEX idx_forum_posts_thread_status (thread_id, status, created_at), FOREIGN KEY(thread_id) REFERENCES forum_threads(id), FOREIGN KEY(author_id) REFERENCES forum_users(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
                    "CREATE TABLE IF NOT EXISTS forum_reports (id BIGINT PRIMARY KEY AUTO_INCREMENT, reporter_id BIGINT NOT NULL, thread_id BIGINT NULL, post_id BIGINT NULL, reason VARCHAR(80) NOT NULL, details VARCHAR(1000) NOT NULL DEFAULT '', status VARCHAR(20) NOT NULL DEFAULT 'open', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_by BIGINT NULL, resolved_at TIMESTAMP NULL, resolution_note VARCHAR(500) NULL, INDEX idx_forum_reports_status_created (status, created_at), FOREIGN KEY(reporter_id) REFERENCES forum_users(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
                ]
            for statement in ddl:
                _execute(conn, statement)
            categories = [
                ("education", "Education", "Learning pathways, literacy and school/community engagement."),
                ("climate", "Climate", "Climate learning and practical environmental action."),
                ("community", "Community", "Safer communities, participation and local ideas."),
                ("child-protection", "Child protection", "Rights, safety and trusted support. Do not share identifying details."),
                ("youth", "Youth development", "Career awareness, mentoring and youth participation."),
            ]
            for slug, name, description in categories:
                if _kind() == "sqlite":
                    _execute(conn, "INSERT OR IGNORE INTO forum_categories (slug,name,description) VALUES (%s,%s,%s)", (slug, name, description))
                else:
                    _execute(conn, "INSERT IGNORE INTO forum_categories (slug,name,description) VALUES (%s,%s,%s)", (slug, name, description))
            version = _one(_execute(conn, "SELECT version FROM forum_schema_version ORDER BY version DESC LIMIT 1"))
            if not version:
                _execute(conn, "INSERT INTO forum_schema_version (version) VALUES (%s)", (1,))
                seed_user_id = _seed_user(conn)
                _seed_topics(conn, seed_user_id)
        _SCHEMA_READY = True


def _seed_user(conn) -> int:
    existing = _one(_execute(conn, "SELECT id FROM forum_users WHERE open_id=%s", ("seed:fiker",)))
    if existing:
        return int(existing["id"])
    if _kind() == "sqlite":
        cur = _execute(conn, "INSERT INTO forum_users (open_id,email,display_name,role) VALUES (%s,%s,%s,%s)", ("seed:fiker", "", "FIKER community", "moderator"))
    else:
        cur = _execute(conn, "INSERT INTO forum_users (open_id,email,display_name,role) VALUES (%s,%s,%s,%s)", ("seed:fiker", "", "FIKER community", "moderator"))
    return int(cur.lastrowid)


def _seed_topics(conn, user_id: int) -> None:
    if _one(_execute(conn, "SELECT id FROM forum_threads LIMIT 1")):
        return
    for title, body, category in [
        ("How do I find a safe learning opportunity?", "Share verified opportunities, learning pathways or questions for the community. Please do not include private contact details.", "education"),
        ("Share a climate idea for your school", "What small, practical action could help a school or community learn about climate and care for the environment?", "climate"),
        ("What makes a community feel safe?", "A calm prompt for ideas about listening, belonging, child rights and community care.", "community"),
    ]:
        cat = _one(_execute(conn, "SELECT id FROM forum_categories WHERE slug=%s", (category,)))
        _execute(conn, "INSERT INTO forum_threads (category_id,author_id,title,body,status) VALUES (%s,%s,%s,%s,%s)", (cat["id"], user_id, title, body, "approved"))


def upsert_user(identity: dict[str, Any]) -> dict[str, Any]:
    ensure_schema()
    open_id = str(identity.get("openId") or identity.get("open_id") or "").strip()
    if not open_id:
        raise ValueError("Authenticated identity did not include openId")
    email = str(identity.get("email") or "").strip()[:255]
    name = str(identity.get("name") or identity.get("displayName") or email or "FIKER member").strip()[:120]
    admin_emails = {x.strip().lower() for x in os.getenv("FORUM_ADMIN_EMAILS", "").split(",") if x.strip()}
    admin_open_ids = {x.strip() for x in os.getenv("FORUM_ADMIN_OPEN_IDS", "").split(",") if x.strip()}
    configured_moderator = email.lower() in admin_emails or open_id in admin_open_ids
    with connection() as conn:
        existing = _one(_execute(conn, "SELECT * FROM forum_users WHERE open_id=%s", (open_id,)))
        if existing:
            if configured_moderator:
                _execute(conn, "UPDATE forum_users SET email=%s,display_name=%s,role='moderator',updated_at=CURRENT_TIMESTAMP WHERE open_id=%s", (email, name, open_id))
            else:
                _execute(conn, "UPDATE forum_users SET email=%s,display_name=%s,updated_at=CURRENT_TIMESTAMP WHERE open_id=%s", (email, name, open_id))
        else:
            _execute(conn, "INSERT INTO forum_users (open_id,email,display_name,role) VALUES (%s,%s,%s,%s)", (open_id, email, name, "moderator" if configured_moderator else "member"))
        return _one(_execute(conn, "SELECT * FROM forum_users WHERE open_id=%s", (open_id,))) or {}


def get_user_by_open_id(open_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with connection() as conn:
        return _one(_execute(conn, "SELECT * FROM forum_users WHERE open_id=%s", (open_id,)))


def categories() -> list[dict[str, Any]]:
    ensure_schema()
    with connection() as conn:
        return _rows(_execute(conn, "SELECT slug,name,description FROM forum_categories ORDER BY name"))


def _thread_select(where: str = "", args: Iterable[Any] = (), limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
    sql = """SELECT t.*, c.slug AS category_slug, c.name AS category_name, u.display_name AS author_name,
        (SELECT COUNT(*) FROM forum_posts p WHERE p.thread_id=t.id AND p.status='approved') AS reply_count
        FROM forum_threads t JOIN forum_categories c ON c.id=t.category_id JOIN forum_users u ON u.id=t.author_id"""
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY t.pinned DESC, t.updated_at DESC"
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        args = tuple(args) + (limit, offset)
    with connection() as conn:
        return _rows(_execute(conn, sql, args))


def list_public_threads(query: str = "", category: str = "", page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    where = ["t.status='approved'"]
    args: list[Any] = []
    if query:
        where.append("(LOWER(t.title) LIKE %s OR LOWER(t.body) LIKE %s)")
        pattern = f"%{query.lower()}%"
        args.extend([pattern, pattern])
    if category:
        where.append("c.slug=%s")
        args.append(category)
    rows = _thread_select(" AND ".join(where), args, min(max(limit, 1), 50), max(page - 1, 0) * min(max(limit, 1), 50))
    return [serialize_thread(row) for row in rows]


def get_public_thread(thread_id: int) -> dict[str, Any] | None:
    rows = _thread_select("t.id=%s AND t.status='approved'", (thread_id,), 1, 0)
    if not rows:
        return None
    thread = serialize_thread(rows[0])
    with connection() as conn:
        posts = _rows(_execute(conn, "SELECT p.*,u.display_name AS author_name FROM forum_posts p JOIN forum_users u ON u.id=p.author_id WHERE p.thread_id=%s AND p.status='approved' ORDER BY p.created_at ASC", (thread_id,)))
    thread["replies"] = [serialize_post(row) for row in posts]
    return thread


def _category_id(conn, slug: str) -> int | None:
    row = _one(_execute(conn, "SELECT id FROM forum_categories WHERE slug=%s", (slug,)))
    return int(row["id"]) if row else None


def rate_limited(conn, user_id: int, kind: str, max_count: int = 5) -> bool:
    if kind == "thread":
        table = "forum_threads"
    elif kind == "reply":
        table = "forum_posts"
    else:
        table = "forum_reports"
    if _kind() == "sqlite":
        sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {'author_id' if kind != 'report' else 'reporter_id'}=%s AND created_at >= datetime('now','-10 minutes')"
    else:
        sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {'author_id' if kind != 'report' else 'reporter_id'}=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)"
    row = _one(_execute(conn, sql, (user_id,)))
    return int(row["n"] or 0) >= max_count


def create_thread(user_id: int, category_slug: str, title: str, body: str) -> dict[str, Any]:
    ensure_schema()
    with connection() as conn:
        if rate_limited(conn, user_id, "thread"):
            raise PermissionError("rate_limited")
        category_id = _category_id(conn, category_slug)
        if not category_id:
            raise ValueError("category")
        cur = _execute(conn, "INSERT INTO forum_threads (category_id,author_id,title,body,status) VALUES (%s,%s,%s,%s,'pending')", (category_id, user_id, title, body))
        return {"id": int(cur.lastrowid), "status": "pending"}


def create_reply(user_id: int, thread_id: int, body: str) -> dict[str, Any]:
    ensure_schema()
    with connection() as conn:
        if rate_limited(conn, user_id, "reply"):
            raise PermissionError("rate_limited")
        thread = _one(_execute(conn, "SELECT id,status,locked FROM forum_threads WHERE id=%s", (thread_id,)))
        if not thread or thread["status"] != "approved":
            raise LookupError("thread")
        if bool(thread["locked"]):
            raise PermissionError("locked")
        cur = _execute(conn, "INSERT INTO forum_posts (thread_id,author_id,body,status) VALUES (%s,%s,%s,'pending')", (thread_id, user_id, body))
        return {"id": int(cur.lastrowid), "status": "pending"}


def update_thread(user_id: int, thread_id: int, title: str, body: str) -> bool:
    ensure_schema()
    with connection() as conn:
        row = _one(_execute(conn, "SELECT author_id,status FROM forum_threads WHERE id=%s", (thread_id,)))
        if not row or int(row["author_id"]) != user_id or row["status"] in {"deleted", "hidden"}:
            return False
        _execute(conn, "UPDATE forum_threads SET title=%s,body=%s,status='pending',moderated_by=NULL,moderated_at=NULL,moderation_note=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=%s", (title, body, thread_id))
        return True


def update_post(user_id: int, post_id: int, body: str) -> bool:
    ensure_schema()
    with connection() as conn:
        row = _one(_execute(conn, "SELECT author_id,status FROM forum_posts WHERE id=%s", (post_id,)))
        if not row or int(row["author_id"]) != user_id or row["status"] in {"deleted", "hidden"}:
            return False
        _execute(conn, "UPDATE forum_posts SET body=%s,status='pending',moderated_by=NULL,moderated_at=NULL,moderation_note=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=%s", (body, post_id))
        return True


def delete_thread(user_id: int, thread_id: int) -> bool:
    ensure_schema()
    with connection() as conn:
        row = _one(_execute(conn, "SELECT author_id FROM forum_threads WHERE id=%s", (thread_id,)))
        if not row or int(row["author_id"]) != user_id:
            return False
        _execute(conn, "UPDATE forum_threads SET status='deleted',updated_at=CURRENT_TIMESTAMP WHERE id=%s", (thread_id,))
        _execute(conn, "UPDATE forum_posts SET status='deleted',updated_at=CURRENT_TIMESTAMP WHERE thread_id=%s", (thread_id,))
        return True


def delete_post(user_id: int, post_id: int) -> bool:
    ensure_schema()
    with connection() as conn:
        row = _one(_execute(conn, "SELECT author_id FROM forum_posts WHERE id=%s", (post_id,)))
        if not row or int(row["author_id"]) != user_id:
            return False
        _execute(conn, "UPDATE forum_posts SET status='deleted',updated_at=CURRENT_TIMESTAMP WHERE id=%s", (post_id,))
        return True


def report(user_id: int, thread_id: int | None, post_id: int | None, reason: str, details: str) -> int:
    ensure_schema()
    with connection() as conn:
        if rate_limited(conn, user_id, "report", 8):
            raise PermissionError("rate_limited")
        if not thread_id and not post_id:
            raise ValueError("target")
        cur = _execute(conn, "INSERT INTO forum_reports (reporter_id,thread_id,post_id,reason,details) VALUES (%s,%s,%s,%s,%s)", (user_id, thread_id, post_id, reason, details))
        return int(cur.lastrowid)


def is_moderator(user: dict[str, Any]) -> bool:
    return user.get("role") in {"moderator", "admin"}


def admin_summary() -> dict[str, int]:
    ensure_schema()
    with connection() as conn:
        pending_threads = _one(_execute(conn, "SELECT COUNT(*) AS n FROM forum_threads WHERE status='pending'"))["n"]
        pending_posts = _one(_execute(conn, "SELECT COUNT(*) AS n FROM forum_posts WHERE status='pending'"))["n"]
        open_reports = _one(_execute(conn, "SELECT COUNT(*) AS n FROM forum_reports WHERE status='open'"))["n"]
        return {"pendingThreads": int(pending_threads), "pendingReplies": int(pending_posts), "openReports": int(open_reports)}


def admin_queue() -> list[dict[str, Any]]:
    ensure_schema()
    with connection() as conn:
        threads = _rows(_execute(conn, "SELECT t.id,t.title,t.body,t.status,t.created_at,u.display_name AS author_name,c.name AS category_name,'thread' AS item_type FROM forum_threads t JOIN forum_users u ON u.id=t.author_id JOIN forum_categories c ON c.id=t.category_id WHERE t.status='pending' ORDER BY t.created_at ASC LIMIT 100"))
        posts = _rows(_execute(conn, "SELECT p.id,p.thread_id,p.body,p.status,p.created_at,u.display_name AS author_name,t.title AS thread_title,'reply' AS item_type FROM forum_posts p JOIN forum_users u ON u.id=p.author_id JOIN forum_threads t ON t.id=p.thread_id WHERE p.status='pending' ORDER BY p.created_at ASC LIMIT 100"))
    return [{"id": int(x["id"]), "type": "thread", "title": x["title"], "body": x["body"], "status": x["status"], "author": x["author_name"], "category": x["category_name"], "createdAt": _now_value(x["created_at"])} for x in threads] + [{"id": int(x["id"]), "type": "reply", "threadId": int(x["thread_id"]), "title": x["thread_title"], "body": x["body"], "status": x["status"], "author": x["author_name"], "createdAt": _now_value(x["created_at"])} for x in posts]


def moderate_thread(moderator_id: int, thread_id: int, action: str, note: str = "") -> bool:
    ensure_schema()
    with connection() as conn:
        row = _one(_execute(conn, "SELECT id FROM forum_threads WHERE id=%s", (thread_id,)))
        if not row:
            return False
        if action == "approve":
            sql = "UPDATE forum_threads SET status='approved',moderated_by=%s,moderated_at=CURRENT_TIMESTAMP,moderation_note=%s WHERE id=%s"
            args = (moderator_id, note, thread_id)
        elif action in {"hide", "delete"}:
            sql = "UPDATE forum_threads SET status=%s,moderated_by=%s,moderated_at=CURRENT_TIMESTAMP,moderation_note=%s WHERE id=%s"
            args = ("hidden" if action == "hide" else "deleted", moderator_id, note, thread_id)
        elif action == "lock":
            sql = "UPDATE forum_threads SET locked=TRUE,moderated_by=%s,moderated_at=CURRENT_TIMESTAMP,moderation_note=%s WHERE id=%s"
            args = (moderator_id, note, thread_id)
        elif action == "unlock":
            sql = "UPDATE forum_threads SET locked=FALSE,moderated_by=%s,moderated_at=CURRENT_TIMESTAMP,moderation_note=%s WHERE id=%s"
            args = (moderator_id, note, thread_id)
        elif action == "pin":
            sql = "UPDATE forum_threads SET pinned=NOT pinned,moderated_by=%s,moderated_at=CURRENT_TIMESTAMP,moderation_note=%s WHERE id=%s"
            args = (moderator_id, note, thread_id)
        else:
            return False
        _execute(conn, sql, args)
        return True


def moderate_post(moderator_id: int, post_id: int, action: str, note: str = "") -> bool:
    ensure_schema()
    with connection() as conn:
        row = _one(_execute(conn, "SELECT id FROM forum_posts WHERE id=%s", (post_id,)))
        if not row:
            return False
        status = "approved" if action == "approve" else "hidden" if action == "hide" else "deleted" if action == "delete" else None
        if not status:
            return False
        _execute(conn, "UPDATE forum_posts SET status=%s,moderated_by=%s,moderated_at=CURRENT_TIMESTAMP,moderation_note=%s WHERE id=%s", (status, moderator_id, note, post_id))
        return True


def admin_reports() -> list[dict[str, Any]]:
    ensure_schema()
    with connection() as conn:
        rows = _rows(_execute(conn, "SELECT r.*,u.display_name AS reporter_name,t.title AS thread_title,p.body AS post_body FROM forum_reports r JOIN forum_users u ON u.id=r.reporter_id LEFT JOIN forum_threads t ON t.id=r.thread_id LEFT JOIN forum_posts p ON p.id=r.post_id WHERE r.status='open' ORDER BY r.created_at ASC LIMIT 100"))
    return [{"id": int(x["id"]), "reason": x["reason"], "details": x["details"], "status": x["status"], "reporter": x["reporter_name"], "threadTitle": x.get("thread_title"), "postBody": x.get("post_body"), "createdAt": _now_value(x["created_at"])} for x in rows]


def resolve_report(moderator_id: int, report_id: int, status: str, note: str = "") -> bool:
    if status not in {"reviewed", "dismissed"}:
        return False
    ensure_schema()
    with connection() as conn:
        cur = _execute(conn, "UPDATE forum_reports SET status=%s,resolved_by=%s,resolved_at=CURRENT_TIMESTAMP,resolution_note=%s WHERE id=%s AND status='open'", (status, moderator_id, note, report_id))
        return cur.rowcount > 0
