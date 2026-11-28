"""SQLite database, Master Admin authentication, channel state, and AI Assistant persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("HAPPY_TOONZZ_DB", str(DATA_DIR / "happy_toonzz.db")))

SESSION_COOKIE_NAME = "ht_session"
SESSION_TTL_HOURS = 24 * 7  # 7 days
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_env_file() -> None:
    """Load KEY=VALUE pairs from .env if present and unset in process env."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()


def get_conn() -> sqlite3.Connection:
    """Open a SQLite connection with Row factory and foreign keys enabled."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def hash_password(password: str) -> str:
    """Hash a password with a per-user random salt using scrypt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored scrypt hash."""
    try:
        scheme, n_s, r_s, p_s, salt_hex, digest_hex = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=32,
        )
        return hmac.compare_digest(derived, bytes.fromhex(digest_hex))
    except Exception:
        return False


def _hash_token(raw_token: str) -> str:
    secret = os.environ.get("SESSION_SECRET", "happy-toonzz-dev-secret").encode("utf-8")
    return hmac.new(secret, raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def init_db() -> str | None:
    """Create tables if missing and seed initial channel, looks, settings, and master admin."""
    generated_pw: str | None = None
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channel_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_name TEXT NOT NULL,
                tagline TEXT NOT NULL,
                location TEXT NOT NULL,
                bio TEXT NOT NULL,
                youtube_handle TEXT NOT NULL,
                youtube_url TEXT NOT NULL,
                youtube_subs TEXT NOT NULL,
                youtube_videos TEXT NOT NULL,
                instagram_handle TEXT NOT NULL,
                instagram_url TEXT NOT NULL,
                instagram_followers TEXT NOT NULL,
                facebook_handle TEXT NOT NULL,
                facebook_url TEXT NOT NULL,
                facebook_followers TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS looks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                subtitle TEXT NOT NULL,
                category TEXT NOT NULL,
                badge TEXT NOT NULL,
                image_path TEXT NOT NULL,
                description TEXT NOT NULL,
                swatches_json TEXT NOT NULL DEFAULT '["#f58ec0","#78cbf2","#f9cc66"]',
                is_default INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 100
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT '',
                media_url TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_tasks (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS homepage_content (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                draft_html TEXT NOT NULL DEFAULT '',
                draft_css TEXT NOT NULL DEFAULT '',
                published_html TEXT NOT NULL DEFAULT '',
                published_css TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS homepage_content_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_html TEXT NOT NULL,
                content_css TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL CHECK (action IN ('draft', 'published')),
                source TEXT NOT NULL CHECK (source IN ('ai', 'admin', 'restore')),
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )

        homepage_content_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(homepage_content)").fetchall()
        }
        if "draft_css" not in homepage_content_columns:
            conn.execute("ALTER TABLE homepage_content ADD COLUMN draft_css TEXT NOT NULL DEFAULT ''")
        if "published_css" not in homepage_content_columns:
            conn.execute("ALTER TABLE homepage_content ADD COLUMN published_css TEXT NOT NULL DEFAULT ''")

        homepage_version_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(homepage_content_versions)").fetchall()
        }
        if "content_css" not in homepage_version_columns:
            conn.execute(
                "ALTER TABLE homepage_content_versions ADD COLUMN content_css TEXT NOT NULL DEFAULT ''"
            )

        conn.execute(
            """
            UPDATE ai_tasks
            SET status = 'failed',
                error = 'The server restarted before this task finished.',
                updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            (_utc_now().isoformat(),),
        )

        # Clean up any non-admin rows since auth is Master Admin-only
        conn.execute("DELETE FROM users WHERE role != 'admin'")

        now = _utc_now().isoformat()

        conn.execute(
            "INSERT OR IGNORE INTO homepage_content (id, updated_at) VALUES (1, ?)",
            (now,),
        )

        row = conn.execute("SELECT id FROM channel_profile WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO channel_profile (
                    id, channel_name, tagline, location, bio,
                    youtube_handle, youtube_url, youtube_subs, youtube_videos,
                    instagram_handle, instagram_url, instagram_followers,
                    facebook_handle, facebook_url, facebook_followers, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "Happy Toonzz",
                    "Our Little Happy Friend • Small Character ... Big Happiness!",
                    "Chennai, India",
                    (
                        "Cute 3D/AI-animated kids' stories, Tamil & English kindness shorts, "
                        "bedtime moral tales, baby learning songs, and musical adventures "
                        "built to spread positivity and smiles."
                    ),
                    "@HappyToonzz-g7f",
                    "https://www.youtube.com/@HappyToonzz-g7f",
                    "161",
                    "192+",
                    "@happy_toonzz",
                    "https://www.instagram.com/happy_toonzz/",
                    "28,000",
                    "Happy Toonzz",
                    "",
                    "2,800",
                    now,
                ),
            )

        look_count = conn.execute("SELECT COUNT(*) FROM looks").fetchone()[0]
        if look_count == 0:
            default_looks = [
                (
                    "superhero",
                    "Superhero Look",
                    "Coral cape, golden star tunic & sky clouds",
                    "adventure",
                    "Adventure",
                    "images/look-superhero.jpg",
                    (
                        "Coral-red and gold cape, pastel-blue star tunic, golden star belt "
                        "buckle, and domino mask framing the signature purple eyes."
                    ),
                    json.dumps(["#fb8281", "#78cbf2", "#f9cc66"]),
                    1,
                    1,
                ),
                (
                    "astronaut",
                    "Space Astronaut",
                    "Cat-ear bubble helmet & pastel cosmos",
                    "adventure",
                    "Sci-Fi",
                    "images/look-astronaut.jpg",
                    (
                        "Puffy white-and-pastel-blue space suit with planet patches, clear "
                        "cat-ear bubble helmet, and pastel nebula stars behind."
                    ),
                    json.dumps(["#ffffff", "#a882f6", "#f58ec0"]),
                    1,
                    2,
                ),
                (
                    "winter",
                    "Cozy Winter",
                    "Knit pom-pom beanie, scarf & snowflakes",
                    "cozy",
                    "Cozy",
                    "images/look-winter.jpg",
                    (
                        "Chunky knitted pink-and-cream pom-pom beanie, sky-blue striped scarf, "
                        "knit vest, and mittens under glowing snowflakes."
                    ),
                    json.dumps(["#f58ec0", "#78cbf2", "#fff3cf"]),
                    1,
                    3,
                ),
                (
                    "chef",
                    "Pastry Chef",
                    "White chef toque, strawberry apron & cupcake",
                    "cozy",
                    "Culinary",
                    "images/look-chef.jpg",
                    (
                        "Pleated white chef hat and pastel polka-dot strawberry apron, holding "
                        "a wooden whisk next to a blue-frosted sprinkle cupcake."
                    ),
                    json.dumps(["#ffffff", "#f58ec0", "#fb8281"]),
                    1,
                    4,
                ),
            ]
            conn.executemany(
                """
                INSERT INTO looks (
                    slug, title, subtitle, category, badge,
                    image_path, description, swatches_json, is_default, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                default_looks,
            )

        admin_exists = conn.execute(
            "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
        if not admin_exists:
            admin_user = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
            admin_email = (
                os.environ.get("ADMIN_EMAIL", "admin@happytoonzz.local").strip()
                or "admin@happytoonzz.local"
            )
            admin_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
            if not admin_pw:
                admin_pw = secrets.token_urlsafe(10)
                generated_pw = admin_pw
            conn.execute(
                """
                INSERT INTO users (username, email, password_hash, role, created_at)
                VALUES (?, ?, ?, 'admin', ?)
                """,
                (admin_user, admin_email, hash_password(admin_pw), now),
            )

        # Seed Gemini API key from env if present and not yet configured in DB
        env_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if env_key:
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('gemini_api_key', ?)",
                (env_key,),
            )

        youtube_env_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        if youtube_env_key:
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('youtube_api_key', ?)",
                (youtube_env_key,),
            )

        conn.commit()
    return generated_pw


def create_session(user_id: int) -> str:
    """Create a persistent session token in SQLite and return the raw cookie token."""
    raw_token = secrets.token_urlsafe(32)
    now = _utc_now()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions (token_hash, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (_hash_token(raw_token), user_id, expires.isoformat(), now.isoformat()),
        )
        conn.commit()
    return raw_token


def get_user_by_session(raw_token: str | None) -> dict[str, Any] | None:
    """Look up a valid non-expired admin session token."""
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.email, u.role, s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND u.role = 'admin'
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        try:
            exp = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            return None
        if exp < _utc_now():
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "role": row["role"],
        }


def delete_session(raw_token: str | None) -> None:
    """Revoke a session token from SQLite."""
    if not raw_token:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(raw_token),))
        conn.commit()


def authenticate_admin(identifier: str, password: str) -> dict[str, Any] | None:
    """Authenticate master admin by username or email and password."""
    clean = identifier.strip()
    if not clean or not password:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE (username = ? OR email = ?) AND role = 'admin' LIMIT 1",
            (clean, clean),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "role": row["role"],
        }


def update_admin_credentials(
    user_id: int,
    new_username: str,
    new_password: str | None = None,
) -> str | None:
    """Update master admin username and optionally password. Returns error string or None."""
    uname = new_username.strip()
    if len(uname) < 3 or len(uname) > 32:
        return "Username must be 3 to 32 characters."
    if not all(ch.isalnum() or ch in "._-" for ch in uname):
        return "Username may only contain letters, numbers, dots, underscores, or hyphens."
    if new_password and len(new_password) < 6:
        return "New password must be at least 6 characters."

    try:
        with get_conn() as conn:
            if new_password:
                conn.execute(
                    "UPDATE users SET username = ?, password_hash = ? WHERE id = ? AND role = 'admin'",
                    (uname, hash_password(new_password), user_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET username = ? WHERE id = ? AND role = 'admin'",
                    (uname, user_id),
                )
            conn.commit()
        return None
    except sqlite3.IntegrityError:
        return "That username is already in use."


def get_setting(key: str, default: str = "") -> str:
    """Retrieve an app setting from SQLite, falling back to environment."""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if row and row["value"]:
            return row["value"]
    return default


def set_setting(key: str, value: str) -> None:
    """Persist an app setting in SQLite."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value.strip()),
        )
        conn.commit()


def get_gemini_api_key() -> str:
    """Prefer the deployment environment key, then use the saved app setting."""
    return os.environ.get("GEMINI_API_KEY", "").strip() or get_setting("gemini_api_key")


def get_youtube_api_key() -> str:
    """Prefer the deployment environment key, then use the saved app setting."""
    return os.environ.get("YOUTUBE_API_KEY", "").strip() or get_setting("youtube_api_key")


def get_channel_profile() -> dict[str, Any]:
    """Fetch current channel profile & social metrics."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM channel_profile WHERE id = 1").fetchone()
        return dict(row) if row else {}


def get_homepage_content() -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT draft_html, draft_css, published_html, published_css, updated_at
            FROM homepage_content
            WHERE id = 1
            """
        ).fetchone()
        return dict(row) if row else {
            "draft_html": "",
            "draft_css": "",
            "published_html": "",
            "published_css": "",
            "updated_at": "",
        }


def save_homepage_content_draft(
    content_html: str,
    source: str,
    summary: str,
    content_css: str | None = None,
) -> None:
    if source not in {"ai", "admin", "restore"}:
        raise ValueError("Unsupported homepage draft source.")
    now = _utc_now().isoformat()
    with get_conn() as conn:
        current_css = conn.execute(
            "SELECT draft_css FROM homepage_content WHERE id = 1"
        ).fetchone()["draft_css"]
        draft_css = current_css if content_css is None else content_css
        conn.execute(
            "UPDATE homepage_content SET draft_html = ?, draft_css = ?, updated_at = ? WHERE id = 1",
            (content_html, draft_css, now),
        )
        conn.execute(
            """
            INSERT INTO homepage_content_versions
                (content_html, content_css, action, source, summary, created_at)
            VALUES (?, ?, 'draft', ?, ?, ?)
            """,
            (content_html, draft_css, source, summary.strip()[:200], now),
        )
        conn.commit()


def publish_homepage_content(content_html: str, content_css: str | None = None) -> None:
    if not content_html.strip():
        raise ValueError("There is no homepage draft to publish.")
    now = _utc_now().isoformat()
    with get_conn() as conn:
        current_css = conn.execute(
            "SELECT draft_css FROM homepage_content WHERE id = 1"
        ).fetchone()["draft_css"]
        published_css = current_css if content_css is None else content_css
        conn.execute(
            """
            UPDATE homepage_content
            SET draft_html = ?, published_html = ?, draft_css = ?, published_css = ?, updated_at = ?
            WHERE id = 1
            """,
            (content_html, content_html, published_css, published_css, now),
        )
        conn.execute(
            """
            INSERT INTO homepage_content_versions
                (content_html, content_css, action, source, summary, created_at)
            VALUES (?, ?, 'published', 'admin', 'Published reviewed draft', ?)
            """,
            (content_html, published_css, now),
        )
        conn.commit()


def restore_homepage_content_version(version_id: int) -> bool:
    now = _utc_now().isoformat()
    with get_conn() as conn:
        version = conn.execute(
            "SELECT content_html, content_css FROM homepage_content_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if not version:
            return False
        content_html = version["content_html"]
        content_css = version["content_css"]
        conn.execute(
            "UPDATE homepage_content SET draft_html = ?, draft_css = ?, updated_at = ? WHERE id = 1",
            (content_html, content_css, now),
        )
        conn.execute(
            """
            INSERT INTO homepage_content_versions
                (content_html, content_css, action, source, summary, created_at)
            VALUES (?, ?, 'draft', 'restore', ?, ?)
            """,
            (content_html, content_css, f"Restored version {version_id} as a draft", now),
        )
        conn.commit()
        return True


def list_homepage_content_versions(limit: int = 30) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(limit, 100))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, action, source, summary, created_at
            FROM homepage_content_versions
            ORDER BY id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_channel_profile(fields: dict[str, str]) -> None:
    """Update editable channel metadata and follower stats."""
    allowed = {
        "channel_name",
        "tagline",
        "location",
        "bio",
        "youtube_handle",
        "youtube_url",
        "youtube_subs",
        "youtube_videos",
        "instagram_handle",
        "instagram_url",
        "instagram_followers",
        "facebook_handle",
        "facebook_url",
        "facebook_followers",
    }
    updates = {k: v.strip() for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = _utc_now().isoformat()
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values())
    with get_conn() as conn:
        conn.execute(f"UPDATE channel_profile SET {cols} WHERE id = 1", vals)
        conn.commit()


def get_looks() -> list[dict[str, Any]]:
    """Fetch all character looks ordered by sort_order."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM looks ORDER BY sort_order ASC, id ASC"
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["swatches"] = json.loads(item["swatches_json"])
            except Exception:
                item["swatches"] = ["#f58ec0", "#78cbf2", "#f9cc66"]
            result.append(item)
        return result


def add_look(
    title: str,
    subtitle: str,
    category: str,
    badge: str,
    image_path: str,
    description: str,
) -> int:
    """Insert a newly uploaded/generated character look and return its ID."""
    base_slug = "".join(c.lower() if c.isalnum() else "-" for c in title).strip("-") or "look"
    slug = f"{base_slug}-{secrets.token_hex(3)}"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO looks (slug, title, subtitle, category, badge, image_path, description, is_default, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 200)
            """,
            (slug, title.strip(), subtitle.strip(), category.strip(), badge.strip(), image_path, description.strip()),
        )
        conn.commit()
        return int(cur.lastrowid)


def delete_look(look_id: int) -> str | None:
    """Delete a non-default look and return its image_path if removable."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT image_path, is_default FROM looks WHERE id = ?", (look_id,)
        ).fetchone()
        if not row or row["is_default"]:
            return None
        conn.execute("DELETE FROM looks WHERE id = ?", (look_id,))
        conn.commit()
        return row["image_path"]


def add_message(name: str, email: str, message: str) -> None:
    """Save a fan/collaboration message."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (name, email, message, created_at) VALUES (?, ?, ?, ?)",
            (name.strip()[:80], email.strip()[:120], message.strip()[:1000], _utc_now().isoformat()),
        )
        conn.commit()


def list_messages() -> list[dict[str, Any]]:
    """Return latest fan/collab messages."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]


def get_admin_counts() -> dict[str, int]:
    """Return exact gallery and inbox totals for the admin overview."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM looks) AS look_count,
                (SELECT COUNT(*) FROM messages) AS message_count
            """
        ).fetchone()
        return {
            "look_count": int(row["look_count"]),
            "message_count": int(row["message_count"]),
        }


def delete_message(message_id: int) -> None:
    """Delete a message by ID."""
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.commit()


def add_ai_message(
    user_id: int,
    role: str,
    text: str,
    media_type: str = "",
    media_url: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    """Store an AI assistant chat message in SQLite."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_messages (user_id, role, text, media_type, media_url, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                role,
                text,
                media_type,
                media_url,
                json.dumps(meta or {}),
                _utc_now().isoformat(),
            ),
        )
        conn.commit()


def list_ai_messages(user_id: int, limit: int = 40) -> list[dict[str, Any]]:
    """Return recent AI assistant chat turns for user_id in chronological order."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ai_messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        items = []
        for r in reversed(rows):
            d = dict(r)
            try:
                d["meta"] = json.loads(d["meta_json"])
            except Exception:
                d["meta"] = {}
            items.append(d)
        return items


def clear_ai_messages(user_id: int) -> None:
    """Clear AI chat history for user_id."""
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_messages WHERE user_id = ?", (user_id,))
        conn.commit()


def create_ai_task(user_id: int, prompt: str) -> str | None:
    """Create one background task per admin; return None if another is active."""
    task_id = secrets.token_urlsafe(18)
    now = _utc_now().isoformat()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """
            SELECT id FROM ai_tasks
            WHERE user_id = ? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if active:
            return None
        conn.execute(
            """
            INSERT INTO ai_tasks (id, user_id, prompt, status, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (task_id, user_id, prompt, now, now),
        )
        conn.commit()
    return task_id


def update_ai_task(
    task_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if status not in {"running", "completed", "failed"}:
        raise ValueError("Invalid AI task status.")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ai_tasks
            SET status = ?, result_json = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(result or {}),
                error,
                _utc_now().isoformat(),
                task_id,
            ),
        )
        conn.commit()


def _ai_task_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    task = dict(row)
    try:
        task["result"] = json.loads(task.pop("result_json"))
    except (TypeError, json.JSONDecodeError):
        task["result"] = {}
    return task


def get_ai_task(user_id: int, task_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ai_tasks WHERE user_id = ? AND id = ?",
            (user_id, task_id),
        ).fetchone()
    return _ai_task_from_row(row)


def get_active_ai_task(user_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM ai_tasks
            WHERE user_id = ? AND status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return _ai_task_from_row(row)
