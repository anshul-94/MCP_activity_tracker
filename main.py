from fastmcp import FastMCP
import sqlite3
import json

mcp = FastMCP("Remote Activity Tracker MCP")

DB_NAME = "tracker.db"
CATEGORY_FILE = "categories.json"

# -----------------------------
# LOAD CATEGORIES
# -----------------------------
def load_categories():
    try:
        with open(CATEGORY_FILE, "r") as f:
            return json.load(f)
    except:
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
# DB INIT
# -----------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
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

init_db()

# -----------------------------
# TOOL 1: LOG ACTIVITY
# -----------------------------
@mcp.tool()
def log_activity(activity: str, start_time: str, end_time: str, date: str):
    category, sub_activity = normalize_activity(activity)

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO activities (category, sub_activity, start_time, end_time, date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (category, sub_activity, start_time, end_time, date)
        )

    return f"Logged under {category} → {sub_activity}"

# -----------------------------
# TOOL 2: SEARCH ACTIVITY
# -----------------------------
@mcp.tool()
def search_activity(start_time: str, end_time: str, date: str):
    with sqlite3.connect(DB_NAME) as conn:
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
        f"{cat} ({sub}) from {start} to {end}"
        for cat, sub, start, end in results
    ]

    return formatted

# -----------------------------
# TOOL 3 (BONUS 🔥): STATS
# -----------------------------
@mcp.tool()
def activity_summary(date: str):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        cursor.execute("""
        SELECT category, COUNT(*) 
        FROM activities
        WHERE date = ?
        GROUP BY category
        """, (date,))

        results = cursor.fetchall()

    if not results:
        return "No data available"

    return {cat: count for cat, count in results}

# RUN REMOTE SERVER
if __name__ == "__main__":
    mcp.run(transport="http",host="0.0.0.0",port=8000)
