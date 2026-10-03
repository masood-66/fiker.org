from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import redirect, request

import forum_db

SESSION_COOKIE = "webdev_app_session"
STATE_COOKIE = "fiker_oauth_state"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    return os.getenv("MANUS_JWT_SECRET", "development-only-session-secret").encode()


def _origin() -> str:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",")[0].strip()
    return f"{proto}://{host}"


def redirect_uri() -> str:
    return _origin() + "/auth/callback"


def _cookie_kwargs() -> dict[str, Any]:
    public_https = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip() == "https"
    return {"httponly": True, "secure": public_https, "samesite": "None" if public_https else "Lax", "path": "/"}


def login_redirect():
    portal = os.getenv("MANUS_OAUTH_PORTAL_URL", "").rstrip("/")
    project_id = os.getenv("MANUS_PROJECT_ID", "")
    if not portal or not project_id:
        return ({"error": "auth_not_configured", "message": "Manus OAuth is not available in this environment yet."}, 503)
    nonce = secrets.token_urlsafe(24)
    state_payload = {"redirectUri": redirect_uri(), "nonce": nonce}
    state = _b64(json.dumps(state_payload, separators=(",", ":")).encode())
    response = redirect(portal + "/app-auth?" + urlencode({"appId": project_id, "redirectUri": redirect_uri(), "state": state, "responseType": "code"}))
    response.set_cookie(STATE_COOKIE, state, max_age=600, **_cookie_kwargs())
    return response


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode())


def callback(code: str | None, returned_state: str | None):
    expected_state = request.cookies.get(STATE_COOKIE, "")
    if not code or not returned_state or not expected_state or not hmac.compare_digest(returned_state, expected_state):
        return ({"error": "invalid_oauth_state", "message": "The login attempt expired or could not be verified."}, 400)
    try:
        state = json.loads(_unb64(returned_state).decode())
        if state.get("redirectUri") != redirect_uri() or not state.get("nonce"):
            raise ValueError("state binding")
        api = os.getenv("MANUS_OAUTH_API_URL", "").rstrip("/")
        client_id = os.getenv("MANUS_PROJECT_ID", "")
        if not api or not client_id:
            raise ValueError("OAuth environment")
        exchange = _post_json(api + "/webdev.v1.WebDevAuthPublicService/ExchangeToken", {"clientId": client_id, "grantType": "authorization_code", "code": code, "redirectUri": redirect_uri()})
        info = _post_json(api + "/webdev.v1.WebDevAuthPublicService/GetUserInfo", {"accessToken": exchange["accessToken"]})
        user = forum_db.upsert_user(info)
    except Exception:
        return ({"error": "oauth_failed", "message": "FIKER could not complete the sign-in flow. Please try again."}, 502)
    session = _sign_session({"openId": info.get("openId"), "userId": int(user["id"]), "issuedAt": int(time.time())})
    response = redirect(request.args.get("next") if request.args.get("next", "").startswith("/") else "/youth/forum/")
    response.set_cookie(SESSION_COOKIE, session, max_age=60 * 60 * 24 * 30, **_cookie_kwargs())
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


def _sign_session(payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"s.{body}.{signature}"


def _read_session(token: str) -> dict[str, Any] | None:
    try:
        prefix, body, signature = token.split(".", 2)
        if prefix != "s" or not hmac.compare_digest(signature, _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())):
            return None
        payload = json.loads(_unb64(body).decode())
        if int(payload.get("issuedAt", 0)) < int(time.time()) - 60 * 60 * 24 * 30:
            return None
        return payload
    except Exception:
        return None


def _read_platform_jwt(token: str) -> dict[str, Any] | None:
    try:
        header, body, signature = token.split(".", 2)
        header_obj = json.loads(_unb64(header).decode())
        if header_obj.get("alg") != "HS256":
            return None
        expected = _b64(hmac.new(_secret(), f"{header}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_unb64(body).decode())
        if payload.get("exp") and int(payload["exp"]) < int(time.time()):
            return None
        project_id = os.getenv("MANUS_PROJECT_ID")
        if project_id and payload.get("appId") not in {None, project_id} and payload.get("app_id") not in {None, project_id}:
            return None
        return payload
    except Exception:
        return None


def current_user() -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    session = _read_session(token)
    open_id = session.get("openId") if session else None
    claims = None if session else _read_platform_jwt(token)
    if claims:
        open_id = claims.get("openId") or claims.get("open_id") or claims.get("sub")
    if not open_id:
        return None
    user = forum_db.get_user_by_open_id(str(open_id))
    if user is None and claims:
        user = forum_db.upsert_user({
            "openId": str(open_id),
            "email": claims.get("email", ""),
            "name": claims.get("name") or claims.get("displayName") or "FIKER member",
        })
    return user


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {"id": int(user["id"]), "name": user.get("display_name") or "FIKER member", "role": user.get("role", "member"), "email": user.get("email", "")}


def require_user(handler: Callable):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return ({"error": "login_required", "message": "Sign in with your Manus account to continue."}, 401)
        if user.get("status") != "active":
            return ({"error": "account_disabled", "message": "This account cannot participate in the forum."}, 403)
        return handler(user, *args, **kwargs)
    return wrapped


def require_moderator(handler: Callable):
    @wraps(handler)
    @require_user
    def wrapped(user, *args, **kwargs):
        if not forum_db.is_moderator(user):
            return ({"error": "moderator_required", "message": "FIKER staff moderation access is required."}, 403)
        return handler(user, *args, **kwargs)
    return wrapped
