#!/usr/bin/env python3

"""User microservice: accounts, sessions and taste profiles.

The bottom half of this file is the HTTP API; everything above it is plain
SQLite code that can be used on its own:

    python users.py --init-only     # just create the database
    python users.py                 # create it and serve on :8000
"""

import argparse
import hashlib
import hmac
import os
import pathlib
import secrets
import sqlite3
import sys

from flask import Flask, g, make_response, request
from prometheus_flask_exporter import PrometheusMetrics

HERE = pathlib.Path(__file__).parent
SCHEMA = HERE / "user.sql"

DB_PATH = os.environ.get("USER_DB", str(HERE / "data" / "users.db"))
SESSION_TTL = "+7 days"

# scrypt cost parameters. n=2**14 is ~16 MB and ~50 ms per hash: slow enough
# to matter if the database leaks, fast enough not to wreck your latency
# numbers in the Evaluation section.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


# ------------------------------------------------------------------
# connection
# ------------------------------------------------------------------
def connect(path: str | pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")      # per-connection, easy to forget
    conn.execute("PRAGMA busy_timeout = 5000")    # wait instead of "database is locked"
    return conn


def init_db(path: str | pathlib.Path) -> None:
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.close()


# ------------------------------------------------------------------
# passwords
# ------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)   # constant time
    except (ValueError, AttributeError):
        return False


# A pre-computed hash used to burn the same CPU time when the account does not
# exist, so an attacker cannot tell "no such user" from "wrong password" by
# timing the response.
_DUMMY_HASH = hash_password("not-a-real-password")


class UserError(Exception):
    """Something the caller did wrong -- the API maps this to HTTP 400."""


# ------------------------------------------------------------------
# accounts and sessions
# ------------------------------------------------------------------
def register(conn, username: str, email: str, password: str) -> int:
    if len(password) < 8:
        raise UserError("password must be at least 8 characters")
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                (username.strip(), email.strip().lower(), hash_password(password)),
            )
    except sqlite3.IntegrityError as e:
        msg = str(e)
        if "users.username" in msg:
            raise UserError("username already taken") from e
        if "users.email" in msg:
            raise UserError("email already registered") from e
        raise UserError("username must be 3-32 characters and the email valid") from e
    return cur.lastrowid           # the profile row is created by a trigger


def login(conn, identifier: str, password: str) -> str:
    """Check a username-or-email plus password, and return a session token."""
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ? OR email = ?",
        (identifier.strip(), identifier.strip().lower()),
    ).fetchone()

    if row is None:
        verify_password(password, _DUMMY_HASH)     # equalise timing
        raise UserError("invalid credentials")
    if not verify_password(password, row["password_hash"]):
        raise UserError("invalid credentials")

    token = secrets.token_urlsafe(32)
    with conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at)"
            " VALUES (?, ?, datetime('now', ?))",
            (token, row["id"], SESSION_TTL),
        )
        conn.execute(
            "UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (row["id"],)
        )
    return token


def authenticate(conn, token: str) -> sqlite3.Row | None:
    """Resolve a session token to a user. Returns None if absent or expired."""
    return conn.execute(
        "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token = ? AND s.expires_at > datetime('now')",
        (token,),
    ).fetchone()


def logout(conn, token: str) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ------------------------------------------------------------------
# profiles
# ------------------------------------------------------------------
PROFILE_FIELDS = {"display_name", "bio", "is_public", "era_from", "era_to", "max_runtime"}


def _touch(conn, user_id: int) -> None:
    """Bump the revision the recommendation agent watches."""
    conn.execute(
        "UPDATE profiles SET revision = revision + 1, updated_at = datetime('now')"
        " WHERE user_id = ?", (user_id,)
    )


def update_profile(conn, user_id: int, **fields) -> None:
    bad = set(fields) - PROFILE_FIELDS
    if bad:
        raise UserError(f"unknown profile field(s): {', '.join(sorted(bad))}")
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)     # keys are whitelisted above
    with conn:
        conn.execute(f"UPDATE profiles SET {cols} WHERE user_id = ?",
                     (*fields.values(), user_id))
        _touch(conn, user_id)


def set_genres(conn, user_id: int, likes: list[str], avoids: list[str] = ()) -> None:
    """Replace the user's genre preferences wholesale."""
    rows = ([(user_id, g.strip(), 1.0) for g in likes if g.strip()]
            + [(user_id, g.strip(), -1.0) for g in avoids if g.strip()])
    with conn:
        conn.execute("DELETE FROM profile_genres WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO profile_genres (user_id, genre, weight)"
            " VALUES (?, ?, ?)", rows)
        _touch(conn, user_id)


def set_tags(conn, user_id: int, tags: list[str]) -> None:
    """Replace the user's tags wholesale. Tags outside 2-40 chars are dropped."""
    rows = [(user_id, t.strip()) for t in tags if 2 <= len(t.strip()) <= 40]
    with conn:
        conn.execute("DELETE FROM profile_tags WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO profile_tags (user_id, tag) VALUES (?, ?)", rows)
        _touch(conn, user_id)


def get_profile(conn, username: str, viewer_id: int | None = None) -> dict | None:
    """One profile. A private profile is only visible to its owner."""
    row = conn.execute(
        "SELECT * FROM v_profile WHERE username = ?", (username,)
    ).fetchone()
    if row is None or (not row["is_public"] and row["user_id"] != viewer_id):
        return None
    return dict(row)


def list_profiles(conn, query: str = "", limit: int = 50) -> list[dict]:
    """Public profiles for the discover page, optionally filtered by a search
    box that matches either a username or an exact tag."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM v_profile WHERE is_public = 1 AND ("
        "    ? = ''"
        "    OR username LIKE '%' || ? || '%'"
        "    OR user_id IN (SELECT user_id FROM profile_tags WHERE tag = ?))"
        " ORDER BY updated_at DESC LIMIT ?",
        (query, query, query, limit),
    )]


# ------------------------------------------------------------------
# HTTP API
# ------------------------------------------------------------------
app = Flask(__name__)

# Exposes request counts and latency histograms on /metrics for Prometheus.
PrometheusMetrics(app)

# Idempotent, so it is safe here: this runs whether we are started by gunicorn
# or by main() below, and means the container needs no separate init step.
init_db(DB_PATH)


@app.before_request
def open_connection():
    """One SQLite connection per request -- never shared between threads."""
    g.conn = connect(DB_PATH)


@app.teardown_request
def close_connection(exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


@app.errorhandler(UserError)
def handle_user_error(exc):
    return {"error": str(exc)}, 400


def current_user():
    """The user behind this request's session cookie, or None."""
    return authenticate(g.conn, request.cookies.get("session", ""))


def session_response(token, status=200):
    resp = make_response({"ok": True}, status)
    # httponly: JavaScript cannot read the token, so an XSS bug cannot steal it.
    resp.set_cookie("session", token, httponly=True, samesite="Lax",
                    max_age=7 * 24 * 3600)
    return resp


@app.post("/api/user/register")
def route_register():
    body = request.get_json(silent=True) or {}
    username = body.get("username", "")
    password = body.get("password", "")
    register(g.conn, username, body.get("email", ""), password)
    return session_response(login(g.conn, username, password), status=201)


@app.post("/api/user/login")
def route_login():
    body = request.get_json(silent=True) or {}
    try:
        token = login(g.conn, body.get("identifier", ""), body.get("password", ""))
    except UserError as exc:
        return {"error": str(exc)}, 401      # 401 rather than the generic 400
    return session_response(token)


@app.post("/api/user/logout")
def route_logout():
    logout(g.conn, request.cookies.get("session", ""))
    resp = make_response({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.get("/api/user/me")
def route_me():
    user = current_user()
    if user is None:
        return {"error": "not logged in"}, 401
    return get_profile(g.conn, user["username"], user["id"])


@app.put("/api/user/profile")
def route_update_profile():
    user = current_user()
    if user is None:
        return {"error": "not logged in"}, 401

    # Reject typos instead of quietly ignoring them: a teammate sending
    # "biography" should hear about it rather than get a 200 and no change.
    body = request.get_json(silent=True) or {}
    unknown = set(body) - PROFILE_FIELDS - {"likes", "avoids", "tags"}
    if unknown:
        raise UserError(f"unknown field(s): {', '.join(sorted(unknown))}")

    update_profile(g.conn, user["id"],
                   **{k: body[k] for k in PROFILE_FIELDS if k in body})
    if "likes" in body or "avoids" in body:
        set_genres(g.conn, user["id"], body.get("likes", []), body.get("avoids", []))
    if "tags" in body:
        set_tags(g.conn, user["id"], body["tags"])
    return get_profile(g.conn, user["username"], user["id"])


@app.get("/api/user/profiles")
def route_list_profiles():
    return list_profiles(g.conn, request.args.get("q", ""))


@app.get("/api/user/profiles/<username>")
def route_get_profile(username):
    user = current_user()
    profile = get_profile(g.conn, username, user["id"] if user else None)
    if profile is None:
        return {"error": "no such profile"}, 404
    return profile


# Internal service API: never routed through the frontend.
def internal_authorized():
    expected = os.environ.get("INTERNAL_API_TOKEN", "")
    supplied = request.headers.get("X-Internal-Token", "")
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


@app.get("/internal/profiles")
def internal_profiles():
    if not internal_authorized():
        return {"error": "unauthorized"}, 401
    try:
        after = int(request.args.get("after", "0"))
        limit = int(request.args.get("limit", "50"))
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError
    except ValueError:
        return {"error": "invalid pagination"}, 400
    # Include private profiles for personalised processing, without exposing
    # credentials, sessions, email addresses or profile text in the listing.
    rows = g.conn.execute(
        "SELECT user_id, revision FROM profiles WHERE user_id > ? ORDER BY user_id LIMIT ?",
        (after, limit),
    )
    return [dict(row) for row in rows]


@app.get("/internal/profiles/<int:user_id>")
def internal_profile(user_id):
    if not internal_authorized():
        return {"error": "unauthorized"}, 401
    row = g.conn.execute(
        "SELECT user_id, revision, likes_genres, avoids_genres, bio, tags, era_from, era_to "
        "FROM v_profile WHERE user_id = ?", (user_id,),
    ).fetchone()
    if row is None:
        return {"error": "no such profile"}, 404
    return dict(row)


@app.get("/healthz")
def route_health():
    return {"ok": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--init-only", action="store_true",
                    help="create the database and exit without serving")
    args = ap.parse_args()

    if args.init_only:
        print(f"schema applied: {DB_PATH}")
        return 0
    app.run(host="0.0.0.0", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
