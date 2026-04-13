import os
import sqlite3
import json
import logging
from fastmcp import FastMCP

# Configure logging for production-readiness
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-activity-tracker")

mcp = FastMCP("Remote Activity Tracker MCP")

# 1. Ensure database path is absolute using os.path
# 2. Ensure database file is created in a writable directory (the script's directory)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "tracker.db")
CATEGORY_FILE = os.path.join(BASE_DIR, "categories.json")

# -----------------------------
# LOAD CATEGORIES
# -----------------------------
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

# -----------------------------
# DB HELPER: 3 & 4. Use safe connection and context manager
# -----------------------------
def get_db_connection():
    """Handles connection with check_same_thread=False as requested."""
    try:
        return sqlite3.connect(DB_NAME, check_same_thread=False)
    except sqlite3.Error as e:
        logger.error(f"Failed to connect to database: {e}")
        raise

# -----------------------------
# DB INIT: 6. Ensure database initializes correctly every time
# -----------------------------
def init_db():
    try:
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
            logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Critical error during DB initialization: {e}")

# Call init on module load
init_db()

# -----------------------------
# TOOL 1: LOG ACTIVITY
# -----------------------------
@mcp.tool()
def log_activity(activity: str, start_time: str, end_time: str, date: str):
    """Log a new activity with category normalization. (5. Error handling added)"""
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

        return f"Logged under {category} → {sub_activity}"
    except Exception as e:
        logger.error(f"Error logging activity: {e}")
        return f"Error: Database operation failed: {str(e)}"

# -----------------------------
# TOOL 2: SEARCH ACTIVITY
# -----------------------------
@mcp.tool()
def search_activity(start_time: str, end_time: str, date: str):
    """Search for activities within a time range on a specific date."""
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

            results = cursor.fetchall()

        if not results:
            return "No activity found"

        formatted = [
            f"{row['category']} ({row['sub_activity']}) from {row['start_time']} to {row['end_time']}"
            for row in results
        ]

        return formatted
    except Exception as e:
        logger.error(f"Error searching activity: {e}")
        return f"Error: Could not search activities: {str(e)}"

# -----------------------------
# TOOL 3: STATS
# -----------------------------
@mcp.tool()
def activity_summary(date: str):
    """Get a summary of activities grouped by category for a specific date."""
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

            results = cursor.fetchall()

        if not results:
            return "No data available"

        return {row['category']: row['count'] for row in results}
    except Exception as e:
        logger.error(f"Error getting activity summary: {e}")
        return f"Error: Could not retrieve summary: {str(e)}"

# RUN REMOTE SERVER
if __name__ == "__main__":
    # Ensure tool runs on all interfaces for remote accessibility if needed
    mcp.run(transport="http", host="0.0.0.0", port=8000)

