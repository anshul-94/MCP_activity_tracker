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

@mcp.tool()
def search_activity(
    start_time: str = None,
    end_time: str = None,
    date: str = None,
    keyword: str = None,
    category: str = None
):
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM activities WHERE 1=1"
            params = []

            if date:
                query += " AND date = ?"
                params.append(date)

            if start_time and end_time:
                query += " AND start_time <= ? AND end_time >= ?"
                params.extend([end_time, start_time])

            if keyword:
                query += " AND sub_activity LIKE ?"
                params.append(f"%{keyword}%")

            if category:
                query += " AND category = ?"
                params.append(category)

            cursor.execute(query, params)
            rows = cursor.fetchall()

        if not rows:
            return "No activity found"

        return [
            f"{r['date']} | {r['category']} ({r['sub_activity']}) {r['start_time']}–{r['end_time']}"
            for r in rows
        ]

    except Exception as e:
        logger.error(f"SEARCH ERROR: {e}")
        return f"❌ Search failed: {str(e)}"
    


@mcp.tool()
def delete_activity(
    date: str = None,
    start_time: str = None,
    end_time: str = None,
    keyword: str = None
):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            query = "DELETE FROM activities WHERE 1=1"
            params = []

            if date:
                query += " AND date = ?"
                params.append(date)

            if start_time and end_time:
                query += " AND start_time <= ? AND end_time >= ?"
                params.extend([end_time, start_time])

            if keyword:
                query += " AND sub_activity LIKE ?"
                params.append(f"%{keyword}%")

            cursor.execute(query, params)
            deleted = cursor.rowcount

        return f"✅ Deleted {deleted} activities"

    except Exception as e:
        logger.error(f"DELETE ERROR: {e}")
        return f"❌ Delete failed: {str(e)}"
    

@mcp.tool()
def update_activity(
    date: str,
    old_start_time: str,
    old_end_time: str,
    new_start_time: str = None,
    new_end_time: str = None,
    new_category: str = None,
    new_sub_activity: str = None
):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            updates = []
            params = []

            if new_start_time:
                updates.append("start_time = ?")
                params.append(new_start_time)

            if new_end_time:
                updates.append("end_time = ?")
                params.append(new_end_time)

            if new_category:
                updates.append("category = ?")
                params.append(new_category)

            if new_sub_activity:
                updates.append("sub_activity = ?")
                params.append(new_sub_activity)

            if not updates:
                return "⚠️ Nothing to update"

            query = f"""
            UPDATE activities
            SET {', '.join(updates)}
            WHERE date = ? AND start_time = ? AND end_time = ?
            """

            params.extend([date, old_start_time, old_end_time])

            cursor.execute(query, params)
            updated = cursor.rowcount

        return f"✅ Updated {updated} activities"

    except Exception as e:
        logger.error(f"UPDATE ERROR: {e}")
        return f"❌ Update failed: {str(e)}"
    
@mcp.tool()
def activity_summary(date: str = None):
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT category, COUNT(*) as count FROM activities WHERE 1=1"
            params = []

            if date:
                query += " AND date = ?"
                params.append(date)

            query += " GROUP BY category"

            cursor.execute(query, params)
            rows = cursor.fetchall()

        if not rows:
            return "No data available"

        return {r["category"]: r["count"] for r in rows}

    except Exception as e:
        logger.error(f"SUMMARY ERROR: {e}")
        return f"❌ Summary failed: {str(e)}"


# RUN SERVER
if __name__ == "__main__":mcp.run(transport="http",host="0.0.0.0",port=8000)
