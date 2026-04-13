import os
import sqlite3
import json
from datetime import datetime
from fastmcp import FastMCP

# Initialize MCP Server
mcp = FastMCP("Activity Tracker")

# 1. SETUP PATHS
# Store DB in home folder so it never gets lost
DB_PATH = os.path.expanduser("~/.mcp_activities.db")
# Find categories.json in the same folder as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATEGORY_FILE = os.path.join(BASE_DIR, "categories.json")

# 2. DATABASE HELPERS
def get_db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            category TEXT,
            sub_activity TEXT,
            start_time TEXT,
            end_time TEXT,
            date TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# 3. UTILITY FUNCTIONS
def load_categories():
    if not os.path.exists(CATEGORY_FILE):
        return {}
    with open(CATEGORY_FILE, "r") as f:
        return json.load(f)

def find_category(activity_text):
    categories = load_categories()
    text = activity_text.lower()
    
    for category_name, keywords in categories.items():
        for word in keywords:
            if word in text:
                return category_name, word
    return "misc", "other"

def validate_time(time_str):
    # Enforce HH:MM format
    try:
        datetime.strptime(time_str, "%H:%M")
        return True
    except:
        return False

def validate_date(date_str):
    # Enforce YYYY-MM-DD format
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except:
        return False

# 4. TOOLS
@mcp.tool()
def log_activity(description: str, start_time: str, end_time: str, date: str = None):
    """
    Log a new activity.
    - start_time & end_time must be HH:MM (e.g. 14:30)
    - date must be YYYY-MM-DD (defaults to today)
    """
    # handle default date
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    # validation
    if not validate_time(start_time) or not validate_time(end_time):
        return "❌ Error: Use HH:MM format for times (e.g. 09:00, 15:30)"
    if not validate_date(date):
        return "❌ Error: Use YYYY-MM-DD format for date (e.g. 2024-12-31)"

    category, sub = find_category(description)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO activities (description, category, sub_activity, start_time, end_time, date) VALUES (?, ?, ?, ?, ?, ?)",
        (description, category, sub, start_time, end_time, date)
    )
    conn.commit()
    conn.close()
    
    return f"✅ Logged '{description}' under {category}"

@mcp.tool()
def list_activities(date: str = None, keyword: str = None):
    """
    List activities. You can filter by date or search by keyword.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM activities WHERE 1=1"
    params = []

    if date:
        query += " AND date = ?"
        params.append(date)
    if keyword:
        query += " AND (description LIKE ? OR sub_activity LIKE ?)"
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")

    query += " ORDER BY date DESC, start_time DESC"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "No activities found."

    output = "Activities found:\n"
    for r in rows:
        output += f"ID: {r['id']} | {r['date']} | {r['start_time']}-{r['end_time']} | {r['description']} [{r['category']}]\n"
    return output

@mcp.tool()
def delete_activity(activity_id: int):
    """Delete an activity by its ID number."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        return f"✅ Deleted activity #{activity_id}"
    return f"⚠️ Activity #{activity_id} not found"

@mcp.tool()
def get_summary(date: str = None):
    """Get count of activities by category for a date (or all time)."""
    conn = get_db()
    cursor = conn.cursor()
    
    query = "SELECT category, COUNT(*) FROM activities"
    params = []
    if date:
        query += " WHERE date = ?"
        params.append(date)
    query += " GROUP BY category"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "No data for summary."

    summary = "Category Summary:\n"
    for category, count in rows:
        summary += f"- {category}: {count}\n"
    return summary

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
