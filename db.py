import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initializes the database schema."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def add_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    """Registers or updates a user in the database."""
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, joined_at, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    last_seen = excluded.last_seen
                """,
                (user_id, username, first_name, now, now),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to add/update user {user_id}: {e}")


def get_all_user_ids() -> list[int]:
    """Returns a list of all registered user IDs."""
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT user_id FROM users")
            return [row["user_id"] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch user IDs: {e}")
        return []


def get_users_count() -> int:
    """Returns total number of registered users."""
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM users")
            row = cursor.fetchone()
            return row["cnt"] if row else 0
    except Exception as e:
        logger.error(f"Failed to count users: {e}")
        return 0


def delete_user(user_id: int) -> None:
    """Removes a user from the database."""
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to delete user {user_id}: {e}")
