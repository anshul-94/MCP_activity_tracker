import os
import sqlite3
import json
import logging
from fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-activity-tracker")

mcp = FastMCP("Remote Activity Tracker MCP")

# ✅ FIX 1: Always use writable temp directory (VERY IMPORTANT)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "tracker.db")

# 🔥 fallback (Claude safe)
if not os.access(BASE_DIR, os.W_OK):
    DB_NAME = os.path.join("/tmp", "tracker.db")

CATEGORY_FILE = os.path.join(BASE_DIR, "categories.json")

# LOAD CATEGORIES
def load_categories():
    try:
        if os.path.exists(CATEGORY_FILE):
            with open(CATEGORY_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading categories: {e}")
    return {}

def normalize_activity(user_input: str):
    categories = load_categories()
    user_input = user_input.lower()

    for category, subacts in categories.items():
        for sub in subacts:
            if sub in user_input:
                return category, sub

    return "misc", "other"

# DB CONNECTION (SAFE)
def get_db_connection():
    return sqlite3.connect(
        DB_NAME,
        check_same_thread=False,
        timeout=10
    )

# DB INIT (FORCE CREATE)
def init_db():
    try:
        os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                sub_activity TEXT,
                start_time TEXT,
                end_time TEXT,
                date TEXT
            )
            """)
            conn.commit()

        logger.info(f"DB initialized at {DB_NAME}")

    except Exception as e:
        logger.error(f"DB INIT ERROR: {e}")

init_db()

# TOOL 1: LOG ACTIVITY
@mcp.tool()
def log_activity(activity: str, start_time: str, end_time: str, date: str):
    try:
        category, sub_activity = normalize_activity(activity)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO activities (category, sub_activity, start_time, end_time, date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (category, sub_activity, start_time, end_time, date)
            )
            conn.commit()

        return f"✅ Logged: {category} ({sub_activity})"

    except Exception as e:
        logger.error(f"LOG ERROR: {e}")
        return f"❌ Failed to log activity: {str(e)}"

# TOOL 2: SEARCH ACTIVITY
@mcp.tool()
def search_activity(start_time: str, end_time: str, date: str):
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
            SELECT category, sub_activity, start_time, end_time
            FROM activities
            WHERE date = ?
            AND start_time <= ?
            AND end_time >= ?
            """, (date, end_time, start_time))

            rows = cursor.fetchall()

        if not rows:
            return "No activity found"

        return [
            f"{r['category']} ({r['sub_activity']}) {r['start_time']}–{r['end_time']}"
            for r in rows
        ]

    except Exception as e:
        logger.error(f"SEARCH ERROR: {e}")
        return f"❌ Search failed: {str(e)}"

# TOOL 3: SUMMARY
@mcp.tool()
def activity_summary(date: str):
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
            SELECT category, COUNT(*) as count
            FROM activities
            WHERE date = ?
            GROUP BY category
            """, (date,))

            rows = cursor.fetchall()

        if not rows:
            return "No data available"

        return {r["category"]: r["count"] for r in rows}

    except Exception as e:
        logger.error(f"SUMMARY ERROR: {e}")
        return f"❌ Summary failed: {str(e)}"

# RUN SERVER
if __name__ == "__main__":mcp.run(transport="http",host="0.0.0.0",port=8000)
