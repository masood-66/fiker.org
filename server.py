from __future__ import annotations

import os
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request, send_file

import auth
import forum_db

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def json_response(payload: Any, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def error(error_code: str, message: str, status: int):
    return json_response({"error": error_code, "message": message}, status)


def require_db(handler: Callable):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            forum_db.ensure_schema()
        except Exception:
            return error("database_unavailable", "The forum database is not ready. Please try again shortly.", 503)
        return handler(*args, **kwargs)
    return wrapped


def body_json() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def text_field(data: dict[str, Any], key: str, minimum: int, maximum: int) -> str | None:
    value = data.get(key)
    if not isinstance(value, str):
        return None
    value = " ".join(value.strip().split()) if key == "title" else value.strip()
    if len(value) < minimum or len(value) > maximum:
        return None
    return value


@app.get("/api/health")
def health():
    return json_response({"ok": True, "service": "fiker-forum"})


@app.get("/auth/login")
def auth_login():
    return auth.login_redirect()


@app.get("/auth/callback")
def auth_callback():
    return auth.callback(request.args.get("code"), request.args.get("state"))


@app.post("/auth/logout")
def auth_logout():
    response = json_response({"ok": True})
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
def auth_me():
    try:
        user = auth.current_user()
    except Exception:
        user = None
    return json_response({"authenticated": bool(user), "user": auth.public_user(user)})


@app.get("/api/forum/categories")
@require_db
def api_categories():
    return json_response({"categories": forum_db.categories()})


@app.get("/api/forum/threads")
@require_db
def api_threads():
    query = request.args.get("q", "").strip()[:80]
    category = request.args.get("category", "").strip()[:80]
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1
    return json_response({"threads": forum_db.list_public_threads(query, category, page)})


@app.get("/api/forum/threads/<int:thread_id>")
@require_db
def api_thread(thread_id: int):
    thread = forum_db.get_public_thread(thread_id)
    return json_response({"thread": thread} if thread else {"error": "not_found", "message": "That discussion is not available."}, 200 if thread else 404)


@app.post("/api/forum/threads")
@require_db
@auth.require_user
def api_create_thread(user):
    data = body_json()
    category = data.get("category") if isinstance(data.get("category"), str) else ""
    title = text_field(data, "title", 8, 180)
    body = text_field(data, "body", 20, 5000)
    if not title or not body or not category or len(category) > 80:
        return error("invalid_input", "Add a title, category and a discussion of at least 20 characters.", 400)
    try:
        created = forum_db.create_thread(int(user["id"]), category, title, body)
    except PermissionError as exc:
        return error("rate_limited" if str(exc) == "rate_limited" else "not_allowed", "Please wait before submitting another discussion.", 429)
    except ValueError:
        return error("invalid_category", "Choose a valid forum category.", 400)
    return json_response({"thread": created, "message": "Your discussion was submitted for FIKER moderation."}, 201)


@app.post("/api/forum/threads/<int:thread_id>/replies")
@require_db
@auth.require_user
def api_create_reply(user, thread_id: int):
    body = text_field(body_json(), "body", 2, 5000)
    if not body:
        return error("invalid_input", "Write a reply of at least 2 characters.", 400)
    try:
        created = forum_db.create_reply(int(user["id"]), thread_id, body)
    except LookupError:
        return error("not_found", "That discussion is not available.", 404)
    except PermissionError as exc:
        if str(exc) == "locked":
            return error("thread_locked", "This discussion is locked for new replies.", 409)
        return error("rate_limited", "Please wait before submitting another reply.", 429)
    return json_response({"reply": created, "message": "Your reply was submitted for moderation."}, 201)


@app.patch("/api/forum/threads/<int:thread_id>")
@require_db
@auth.require_user
def api_update_thread(user, thread_id: int):
    data = body_json()
    title = text_field(data, "title", 8, 180)
    body = text_field(data, "body", 20, 5000)
    if not title or not body:
        return error("invalid_input", "Add a valid title and discussion body.", 400)
    if not forum_db.update_thread(int(user["id"]), thread_id, title, body):
        return error("not_allowed", "You can only edit your own active discussion.", 403)
    return json_response({"ok": True, "message": "Your edit was sent back to moderation."})


@app.delete("/api/forum/threads/<int:thread_id>")
@require_db
@auth.require_user
def api_delete_thread(user, thread_id: int):
    if not forum_db.delete_thread(int(user["id"]), thread_id):
        return error("not_allowed", "You can only remove your own discussion.", 403)
    return json_response({"ok": True})


@app.patch("/api/forum/posts/<int:post_id>")
@require_db
@auth.require_user
def api_update_post(user, post_id: int):
    body = text_field(body_json(), "body", 2, 5000)
    if not body:
        return error("invalid_input", "Write a reply of at least 2 characters.", 400)
    if not forum_db.update_post(int(user["id"]), post_id, body):
        return error("not_allowed", "You can only edit your own active reply.", 403)
    return json_response({"ok": True, "message": "Your edit was sent back to moderation."})


@app.delete("/api/forum/posts/<int:post_id>")
@require_db
@auth.require_user
def api_delete_post(user, post_id: int):
    if not forum_db.delete_post(int(user["id"]), post_id):
        return error("not_allowed", "You can only remove your own reply.", 403)
    return json_response({"ok": True})


@app.post("/api/forum/posts/<int:post_id>/reports")
@require_db
@auth.require_user
def api_report_post(user, post_id: int):
    data = body_json()
    reason = text_field(data, "reason", 3, 80)
    details = text_field(data, "details", 0, 1000) or ""
    if not reason:
        return error("invalid_input", "Choose a reason for the report.", 400)
    try:
        report_id = forum_db.report(int(user["id"]), None, post_id, reason, details)
    except PermissionError:
        return error("rate_limited", "Please wait before sending another report.", 429)
    except ValueError:
        return error("not_found", "That post is not available.", 404)
    return json_response({"reportId": report_id, "message": "Thank you. FIKER staff will review this report."}, 201)


@app.post("/api/forum/threads/<int:thread_id>/reports")
@require_db
@auth.require_user
def api_report_thread(user, thread_id: int):
    data = body_json()
    reason = text_field(data, "reason", 3, 80)
    details = text_field(data, "details", 0, 1000) or ""
    if not reason:
        return error("invalid_input", "Choose a reason for the report.", 400)
    try:
        report_id = forum_db.report(int(user["id"]), thread_id, None, reason, details)
    except PermissionError:
        return error("rate_limited", "Please wait before sending another report.", 429)
    except ValueError:
        return error("not_found", "That discussion is not available.", 404)
    return json_response({"reportId": report_id, "message": "Thank you. FIKER staff will review this report."}, 201)


@app.get("/api/admin/forum/summary")
@require_db
@auth.require_moderator
def admin_summary(user):
    return json_response({"summary": forum_db.admin_summary(), "user": auth.public_user(user)})


@app.get("/api/admin/forum/queue")
@require_db
@auth.require_moderator
def admin_queue(user):
    return json_response({"items": forum_db.admin_queue()})


@app.get("/api/admin/forum/reports")
@require_db
@auth.require_moderator
def admin_reports(user):
    return json_response({"reports": forum_db.admin_reports()})


@app.post("/api/admin/forum/threads/<int:thread_id>/moderate")
@require_db
@auth.require_moderator
def admin_moderate_thread(user, thread_id: int):
    data = body_json()
    action = data.get("action") if isinstance(data.get("action"), str) else ""
    note = text_field(data, "note", 0, 500) or ""
    if not forum_db.moderate_thread(int(user["id"]), thread_id, action, note):
        return error("invalid_action", "That moderation action is not available.", 400)
    return json_response({"ok": True})


@app.post("/api/admin/forum/posts/<int:post_id>/moderate")
@require_db
@auth.require_moderator
def admin_moderate_post(user, post_id: int):
    data = body_json()
    action = data.get("action") if isinstance(data.get("action"), str) else ""
    note = text_field(data, "note", 0, 500) or ""
    if not forum_db.moderate_post(int(user["id"]), post_id, action, note):
        return error("invalid_action", "That moderation action is not available.", 400)
    return json_response({"ok": True})


@app.post("/api/admin/forum/reports/<int:report_id>/resolve")
@require_db
@auth.require_moderator
def admin_resolve_report(user, report_id: int):
    data = body_json()
    status = data.get("status") if isinstance(data.get("status"), str) else ""
    note = text_field(data, "note", 0, 500) or ""
    if not forum_db.resolve_report(int(user["id"]), report_id, status, note):
        return error("invalid_action", "Choose reviewed or dismissed for an open report.", 400)
    return json_response({"ok": True})


def _static_file(requested_path: str) -> Path | None:
    clean = requested_path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if not clean:
        candidate = ROOT / "index.html"
    elif clean.startswith("assets/"):
        candidate = ROOT / clean
    elif clean in {"manus-routes.json", "sitemap.xml", "robots.txt"}:
        candidate = PUBLIC / clean
    elif clean.startswith("admin/forum"):
        candidate = ROOT / "admin/forum/index.html"
    else:
        candidate = ROOT / clean
        if candidate.is_dir():
            candidate = candidate / "index.html"
    try:
        candidate = candidate.resolve()
        if ROOT not in candidate.parents and candidate != ROOT:
            return None
    except OSError:
        return None
    return candidate if candidate.is_file() else None


@app.get("/", defaults={"requested_path": ""})
@app.get("/<path:requested_path>")
def static_routes(requested_path: str):
    candidate = _static_file(requested_path)
    if not candidate:
        if requested_path.startswith("api/") or requested_path.startswith("auth/"):
            return error("not_found", "Endpoint not found.", 404)
        return error("not_found", "Page not found.", 404)
    return send_file(candidate)


@app.errorhandler(404)
def not_found(_error):
    return error("not_found", "Not found.", 404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")), debug=False)
