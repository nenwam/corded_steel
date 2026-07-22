"""Data layer for the Corded Steel tracker.

Talks to a plain SQLite file locally and to Turso (libSQL over HTTP) when Turso
credentials are present in Streamlit secrets. Both drivers expose the sqlite3
DB-API, so everything below is written once against that surface.

Security notes:
  * Every statement is parameterised with `?` placeholders. No caller-supplied
    value is ever interpolated into SQL, so the app is not injectable.
  * Table and column names are hard-coded literals, never taken from input.
  * Free-text input (participant / exercise names) is validated against an
    allowlist before it is stored, so nothing script-shaped ever reaches the DB.
  * The password is stored as a salted PBKDF2-SHA256 digest, never in clear.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

DB_FILENAME = "cordedsteel.db"

# Names are used as dataframe column labels and metric captions. Keeping them to
# this allowlist means they can never carry markup, quotes or control characters.
NAME_RE = re.compile(r"^[A-Za-z0-9 ._'\-]{1,32}$")
NAME_MAX_LEN = 32

PBKDF2_ITERATIONS = 240_000

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS participants (
        id       INTEGER PRIMARY KEY,
        name     TEXT NOT NULL UNIQUE,
        position INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS exercises (
        id       INTEGER PRIMARY KEY,
        name     TEXT NOT NULL UNIQUE,
        unit     TEXT NOT NULL DEFAULT '',
        decimals INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
        participant_id INTEGER NOT NULL REFERENCES participants(id),
        exercise_id    INTEGER NOT NULL REFERENCES exercises(id),
        goal           REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (participant_id, exercise_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entries (
        participant_id INTEGER NOT NULL REFERENCES participants(id),
        exercise_id    INTEGER NOT NULL REFERENCES exercises(id),
        day            TEXT NOT NULL,
        value          REAL NOT NULL DEFAULT 0,
        updated_at     TEXT NOT NULL,
        PRIMARY KEY (participant_id, exercise_id, day)
    )
    """,
    "CREATE INDEX IF NOT EXISTS entries_by_day ON entries (day)",
)

# One connection is shared across Streamlit sessions, so serialise access to it.
_LOCK = threading.RLock()


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

def _turso_credentials():
    """Return (url, token) from Streamlit secrets or the environment, else None.

    Reading `st.secrets` raises when no secrets file exists, which is the normal
    case for local development, so treat any failure as "not configured".
    """
    # Escape hatch for local work once Turso is configured: without it, having
    # secrets.toml on disk means every local test run edits production.
    if os.environ.get("CORDED_STEEL_LOCAL"):
        return None

    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")

    if not url:
        try:
            import streamlit as st

            section = st.secrets.get("turso", {})
            url = section.get("url") or ""
            token = section.get("auth_token") or ""
        except Exception:
            return None

    return (url, token) if url else None


def local_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_FILENAME)


def connect_local():
    """Open the on-disk SQLite file, ignoring any Turso configuration."""
    conn = sqlite3.connect(local_path(), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect():
    """Open a connection to Turso when configured, otherwise the local file."""
    creds = _turso_credentials()
    if creds:
        import libsql  # imported lazily: only needed for the hosted deployment

        url, token = creds
        return libsql.connect(database=url, auth_token=token)

    return connect_local()


def backend_name() -> str:
    return "Turso" if _turso_credentials() else "local SQLite"


def query(conn, sql, params=()):
    with _LOCK:
        return conn.execute(sql, params).fetchall()


def execute(conn, sql, params=()):
    with _LOCK:
        conn.execute(sql, params)
        conn.commit()


def execute_batch(conn, statements):
    """Run several (sql, params) pairs and commit once."""
    with _LOCK:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()


def init_schema(conn):
    with _LOCK:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    """Return a `pbkdf2_sha256$iterations$salt$digest` string."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def check_password(stored: str, password: str) -> bool:
    """Constant-time verification of `password` against a stored digest."""
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, int(iterations)
    )
    return hmac.compare_digest(candidate, expected)


def verify_password(conn, password: str) -> bool:
    stored = get_meta(conn, "password_hash")
    return bool(stored) and check_password(stored, password)


def set_password(conn, password: str) -> None:
    set_meta(conn, "password_hash", hash_password(password))


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #

def get_meta(conn, key: str, default=None):
    rows = query(conn, "SELECT value FROM app_meta WHERE key = ?", (key,))
    return rows[0][0] if rows else default


def set_meta(conn, key: str, value: str) -> None:
    execute(
        conn,
        """
        INSERT INTO app_meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def challenge_window(conn):
    """Return the (start, end) dates of the challenge as `date` objects."""
    start = _parse_date(get_meta(conn, "start_date")) or date.today()
    end = _parse_date(get_meta(conn, "end_date")) or (start + timedelta(days=30))
    if end < start:
        end = start
    return start, end


def challenge_days(conn):
    start, end = challenge_window(conn)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Participants and exercises
# --------------------------------------------------------------------------- #

def clean_name(raw: str) -> str:
    """Normalise and validate a person / exercise name.

    Raises ValueError if the name is empty or contains anything outside the
    allowlist, so unvalidated text never reaches the database or the page.
    """
    name = " ".join(str(raw).split())
    if not name:
        raise ValueError("Give them a name.")
    if len(name) > NAME_MAX_LEN:
        # Rejected rather than truncated — quietly renaming someone is worse
        # than telling them the name is too long.
        raise ValueError(f"Keep names to {NAME_MAX_LEN} characters or fewer.")
    if not NAME_RE.match(name):
        raise ValueError(
            "Names may only contain letters, numbers, spaces and . _ ' -"
        )
    return name


def list_participants(conn):
    return [
        {"id": row[0], "name": row[1]}
        for row in query(
            conn, "SELECT id, name FROM participants ORDER BY position, id"
        )
    ]


def list_exercises(conn):
    return [
        {"id": row[0], "name": row[1], "unit": row[2], "decimals": int(row[3])}
        for row in query(
            conn,
            "SELECT id, name, unit, decimals FROM exercises ORDER BY position, id",
        )
    ]


def add_participant(conn, raw_name: str, goals_by_exercise=None):
    """Add a participant. Returns their new id."""
    name = clean_name(raw_name)
    existing = query(
        conn, "SELECT id FROM participants WHERE name = ? COLLATE NOCASE", (name,)
    )
    if existing:
        raise ValueError(f"{name} is already on the board.")

    next_position = query(
        conn, "SELECT COALESCE(MAX(position), 0) + 1 FROM participants"
    )[0][0]
    execute(
        conn,
        "INSERT INTO participants (name, position) VALUES (?, ?)",
        (name, next_position),
    )
    participant_id = query(
        conn, "SELECT id FROM participants WHERE name = ?", (name,)
    )[0][0]

    goals_by_exercise = goals_by_exercise or {}
    execute_batch(
        conn,
        [
            (
                """
                INSERT INTO goals (participant_id, exercise_id, goal)
                VALUES (?, ?, ?)
                ON CONFLICT(participant_id, exercise_id)
                DO UPDATE SET goal = excluded.goal
                """,
                (participant_id, ex["id"], float(goals_by_exercise.get(ex["id"], 0))),
            )
            for ex in list_exercises(conn)
        ],
    )
    return participant_id


def remove_participant(conn, participant_id: int) -> None:
    participant_id = int(participant_id)
    execute_batch(
        conn,
        [
            ("DELETE FROM entries WHERE participant_id = ?", (participant_id,)),
            ("DELETE FROM goals WHERE participant_id = ?", (participant_id,)),
            ("DELETE FROM participants WHERE id = ?", (participant_id,)),
        ],
    )


# --------------------------------------------------------------------------- #
# Goals and entries
# --------------------------------------------------------------------------- #

def get_goals(conn):
    """Return {(participant_id, exercise_id): goal}."""
    return {
        (row[0], row[1]): float(row[2])
        for row in query(conn, "SELECT participant_id, exercise_id, goal FROM goals")
    }


def set_goal(conn, participant_id: int, exercise_id: int, goal: float) -> None:
    execute(
        conn,
        """
        INSERT INTO goals (participant_id, exercise_id, goal) VALUES (?, ?, ?)
        ON CONFLICT(participant_id, exercise_id) DO UPDATE SET goal = excluded.goal
        """,
        (int(participant_id), int(exercise_id), float(goal)),
    )


def get_entries(conn):
    """Return {(participant_id, exercise_id, 'YYYY-MM-DD'): value}."""
    return {
        (row[0], row[1], row[2]): float(row[3])
        for row in query(
            conn, "SELECT participant_id, exercise_id, day, value FROM entries"
        )
    }


def set_entries(conn, cells) -> None:
    """Upsert an iterable of (participant_id, exercise_id, day, value) cells.

    Writing one cell at a time (rather than a whole row) keeps concurrent edits
    from clobbering each other: two people editing different cells both win.
    Values are clamped to a sane range so a stray keystroke cannot poison a total.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    statements = []
    for participant_id, exercise_id, day, value in cells:
        statements.append(
            (
                """
                INSERT INTO entries (participant_id, exercise_id, day, value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(participant_id, exercise_id, day)
                DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (
                    int(participant_id),
                    int(exercise_id),
                    _as_iso_day(day),
                    _clamp(value),
                    stamp,
                ),
            )
        )
    if statements:
        execute_batch(conn, statements)


def _as_iso_day(day) -> str:
    if isinstance(day, (date, datetime)):
        return day.strftime("%Y-%m-%d")
    parsed = _parse_date(day)
    if parsed is None:
        raise ValueError(f"Unrecognised date: {day!r}")
    return parsed.strftime("%Y-%m-%d")


def _clamp(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    return max(0.0, min(number, 1_000_000.0))
