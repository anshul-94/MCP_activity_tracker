import os
import sqlite3
import json
from datetime import datetime
from fastmcp import FastMCP

# Initialize MCP Server
mcp = FastMCP("Activity Tracker")

# 1. SETUP PATHS — Single absolute DB path, never changes
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tracker.db")
CATEGORY_FILE = os.path.join(BASE_DIR, "categories.json")

print(f"🚀 MCP Activity Tracker starting...")
print(f"📁 Database Path: {DB_PATH}")

# 2. DATABASE HELPERS
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent write handling
    return conn

def init_db():
    """
    Ensures the table exists with user_id and UNIQUE constraint.
    Safe to run on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    conn = get_db()
    cursor = conn.cursor()

    # Check if user_id column exists (handles old DBs gracefully)
    cursor.execute("PRAGMA table_info(activities)")
    cols = [row[1] for row in cursor.fetchall()]

    if not cols:
        # Fresh database — create with full schema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                category    TEXT,
                sub_activity TEXT,
                start_time  TEXT,
                end_time    TEXT,
                date        TEXT,
                user_id     TEXT NOT NULL DEFAULT 'default_user',
                UNIQUE(description, date, start_time, end_time, user_id)
            )
        """)
    elif "user_id" not in cols:
        # Old schema — add user_id column safely
        cursor.execute("ALTER TABLE activities ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default_user'")
        print("⚠️  Migrated: added user_id column to existing table.")

    conn.commit()
    conn.close()

init_db()

# 3. UTILITY FUNCTIONS
def load_categories():
    """Load categories from JSON config file."""
    if not os.path.exists(CATEGORY_FILE):
        return {}
    with open(CATEGORY_FILE, "r") as f:
        return json.load(f)

def find_category(activity_text: str) -> str:
    """
    Categorize an activity description using categories.json.
    Supports partial matches and prioritization.
    """
    import re
    text = activity_text.lower()
    
    # Priority Overrides (to handle collisions like "football with friends")
    # If key is matched, forces the value category
    priority_overrides = {
        "football": "Fitness",
        "cricket": "Fitness",
        "basketball": "Fitness",
        "gym": "Fitness",
        "workout": "Fitness",
        "movie": "Entertainment",
        "gaming": "Entertainment",
        "coding": "Productivity"
    }
    
    # 1. Check priority overrides first
    for kw, cat in priority_overrides.items():
        if re.search(r'\b' + re.escape(kw) + r'\b', text):
            return cat

    json_cats = load_categories()
    if not json_cats:
        return "Misc"
        
    # 2. Check exact word boundary matches from categories.json
    for cat, keywords in json_cats.items():
        for kw in keywords:
            if kw == "other": continue
            kw_norm = kw.replace("_", " ").lower()
            if re.search(r'\b' + re.escape(kw_norm) + r'\b', text):
                return cat.replace("_", " ").title()
                
    # 3. Check partial substring fallback
    for cat, keywords in json_cats.items():
        for kw in keywords:
            if kw == "other": continue
            kw_norm = kw.replace("_", " ").lower()
            if kw_norm in text:
                return cat.replace("_", " ").title()
    return "Misc"
def format_time_12h(time_24h: str) -> str:
    """Convert 13:00 → 1 PM"""
    try:
        dt = datetime.strptime(time_24h, "%H:%M")
        return dt.strftime("%-I %p")
    except Exception:
        return time_24h

def format_date_short(date_str: str) -> str:
    """Convert 2024-04-10 → Apr 10"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d")
    except Exception:
        return date_str

def normalize_time(time_str: str) -> str:
    """Converts natural time formats to HH:MM format."""
    if not time_str: return None
    time_str = time_str.strip().upper().replace('.', '')
    formats = ['%H:%M', '%I %p', '%I:%M %p', '%I%p', '%I:%M%p']
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt).strftime('%H:%M')
        except ValueError:
            continue
    return None

def validate_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except Exception:
        return False

# 4. MCP TOOLS — All scoped by user_id for full isolation
@mcp.tool()
def log_activity(
    description: str,
    start_time: str,
    end_time: str,
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """
    Log a new activity for a specific user.
    - start_time & end_time: HH:MM format (e.g. 14:30)
    - date: YYYY-MM-DD (defaults to today if omitted)
    - user_id: identifies the user. Defaults to 'default_user'.
    Duplicate entries (same description + date + times + user) are silently ignored.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    start_time = normalize_time(start_time)
    end_time = normalize_time(end_time)
    if not start_time or not end_time:
        return "❌ Error: Invalid time format. Examples: '14:30', '4 PM', '4:30 pm'"
    if not validate_date(date):
        return "❌ Error: Use YYYY-MM-DD format for date (e.g. 2026-04-18)"

    category = find_category(description)

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT OR IGNORE INTO activities
               (description, category, start_time, end_time, date, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (description, category, start_time, end_time, date, user_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return f"⚠️ Duplicate skipped: '{description}' already logged for {user_id} on {date} at {start_time}–{end_time}"
        return f"✅ Logged '{description}' under {category} for user '{user_id}'"
    finally:
        conn.close()


@mcp.tool()
def list_activities(
    date: str = None,
    keyword: str = None,
    all_time: bool = False,
    user_id: str = "default_user"
) -> str:
    """
    List activities for a specific user.
    - date: filter by YYYY-MM-DD. Defaults to today if no other filter set.
    - keyword: search activity descriptions.
    - all_time: set True to retrieve all historical records for this user.
    - user_id: only returns data for this user. Defaults to 'default_user'.
    """
    if not date and not keyword and not all_time:
        date = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM activities WHERE user_id = ?"
    params = [user_id]

    if not all_time and date:
        query += " AND date = ?"
        params.append(date)
    if keyword:
        query += " AND description LIKE ?"
        params.append(f"%{keyword}%")

    query += " ORDER BY date DESC, start_time ASC LIMIT 50"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        scope = f"today ({date})" if date and not all_time else "all time"
        return f"No activities found for user '{user_id}' [{scope}]."

    lines = []
    for r in rows:
        d = format_date_short(r["date"])
        t1 = format_time_12h(r["start_time"])
        t2 = format_time_12h(r["end_time"])
        lines.append(f"#{r['id']} | {d} | {t1}–{t2} | {r['description']} | {r['category']}")

    return "\n".join(lines)


@mcp.tool()
def search_activity(
    query: str = None,
    category: str = None,
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """
    Search activities for a specific user by keyword, category, and/or date.
    If no date is set, searches ALL historical records for this user.
    - user_id: only returns this user's data. Defaults to 'default_user'.
    """
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = "SELECT * FROM activities WHERE user_id = ?"
    params = [user_id]

    if date:
        sql += " AND date = ?"
        params.append(date)
    if query:
        sql += " AND description LIKE ?"
        params.append(f"%{query}%")
    if category:
        sql += " AND LOWER(category) = LOWER(?)"
        params.append(category)

    sql += " ORDER BY date DESC, start_time ASC LIMIT 50"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return f"No activities found for user '{user_id}'."

    lines = []
    for r in rows:
        d = format_date_short(r["date"])
        t1 = format_time_12h(r["start_time"])
        t2 = format_time_12h(r["end_time"])
        lines.append(f"#{r['id']} | {d} | {t1}–{t2} | {r['description']} | {r['category']}")

    return "\n".join(lines)


@mcp.tool()
def update_activity(
    activity_id: int,
    description: str = None,
    start_time: str = None,
    end_time: str = None,
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """
    Update fields of an existing activity. Only this user's records can be modified.
    Provide only the fields you want to change.
    - user_id: only updates records owned by this user. Defaults to 'default_user'.
    """
    updates = []
    params = []

    if description:
        updates.append("description = ?")
        params.append(description)
        updates.append("category = ?")
        params.append(find_category(description))
    if start_time:
        start_time = normalize_time(start_time)
        if not start_time:
            return "❌ Invalid start_time format. Examples: '14:30', '4 PM', '4:30 pm'"
        updates.append("start_time = ?")
        params.append(start_time)
    if end_time:
        end_time = normalize_time(end_time)
        if not end_time:
            return "❌ Invalid end_time format. Examples: '14:30', '4 PM', '4:30 pm'"
        updates.append("end_time = ?")
        params.append(end_time)
    if date:
        if not validate_date(date):
            return "❌ Invalid date format. Use YYYY-MM-DD (e.g. 2026-04-18)"
        updates.append("date = ?")
        params.append(date)

    if not updates:
        return "⚠️ No changes provided. Pass at least one field to update."

    # Scope the WHERE clause to both id AND user_id — prevents cross-user updates
    params += [activity_id, user_id]
    sql = f"UPDATE activities SET {', '.join(updates)} WHERE id = ? AND user_id = ?"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    updated = cursor.rowcount
    conn.commit()
    conn.close()

    if updated > 0:
        return f"✅ Updated activity #{activity_id} for user '{user_id}'"
    return f"⚠️ Activity #{activity_id} not found for user '{user_id}'. Check ID and user."


@mcp.tool()
def delete_activity(
    activity_id: int,
    user_id: str = "default_user"
) -> str:
    """
    Delete an activity by ID. Only deletes records owned by this user.
    - user_id: only deletes this user's records. Defaults to 'default_user'.
    """
    conn = get_db()
    cursor = conn.cursor()
    # WHERE on both id AND user_id — prevents cross-user deletion
    cursor.execute("DELETE FROM activities WHERE id = ? AND user_id = ?", (activity_id, user_id))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        return f"✅ Deleted activity #{activity_id} for user '{user_id}'"
    return f"⚠️ Activity #{activity_id} not found for user '{user_id}'. Check ID and user."


@mcp.tool()
def delete_by_keyword(
    keyword: str,
    user_id: str = "default_user"
) -> str:
    """
    Delete activities by matching a keyword. Only deletes records owned by this user.
    - keyword: search text to match activities.
    - user_id: only deletes this user's records. Defaults to 'default_user'.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM activities WHERE description LIKE ? AND user_id = ?", 
        (f"%{keyword}%", user_id)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        return f"✅ Deleted {deleted} activities matching '{keyword}' for user '{user_id}'"
    return f"⚠️ No activities found matching '{keyword}' for user '{user_id}'."


@mcp.tool()
def update_by_keyword(
    keyword: str,
    description: str = None,
    start_time: str = None,
    end_time: str = None,
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """
    Update fields of existing activities that match a keyword. Only this user's records can be modified.
    Provide only the fields you want to change.
    - keyword: search text to match activities.
    - user_id: only updates records owned by this user. Defaults to 'default_user'.
    """
    updates = []
    params = []

    if description:
        updates.append("description = ?")
        params.append(description)
        updates.append("category = ?")
        params.append(find_category(description))
    if start_time:
        start_time = normalize_time(start_time)
        if not start_time:
            return "❌ Invalid start_time format. Examples: '14:30', '4 PM', '4:30 pm'"
        updates.append("start_time = ?")
        params.append(start_time)
    if end_time:
        end_time = normalize_time(end_time)
        if not end_time:
            return "❌ Invalid end_time format. Examples: '14:30', '4 PM', '4:30 pm'"
        updates.append("end_time = ?")
        params.append(end_time)
    if date:
        if not validate_date(date):
            return "❌ Invalid date format. Use YYYY-MM-DD (e.g. 2026-04-18)"
        updates.append("date = ?")
        params.append(date)

    if not updates:
        return "⚠️ No changes provided. Pass at least one field to update."

    params += [f"%{keyword}%", user_id]
    sql = f"UPDATE activities SET {', '.join(updates)} WHERE description LIKE ? AND user_id = ?"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    updated = cursor.rowcount
    conn.commit()
    conn.close()

    if updated > 0:
        return f"✅ Updated {updated} activities matching '{keyword}' for user '{user_id}'"
    return f"⚠️ No activities found matching '{keyword}' for user '{user_id}'."


@mcp.tool()
def get_summary(
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """
    Get activity count grouped by category for a specific user.
    - date: filter to a specific day (YYYY-MM-DD). Omit for all-time summary.
    - user_id: only summarizes this user's data. Defaults to 'default_user'.
    """
    conn = get_db()
    cursor = conn.cursor()

    sql = "SELECT category, COUNT(*) FROM activities WHERE user_id = ?"
    params = [user_id]

    if date:
        sql += " AND date = ?"
        params.append(date)

    sql += " GROUP BY category ORDER BY COUNT(*) DESC"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        scope = format_date_short(date) if date else "All Time"
        return f"No activity data for user '{user_id}' [{scope}]."

    scope = format_date_short(date) if date else "All Time"
    summary = f"📊 Activity Summary for '{user_id}' ({scope}):\n"
    for category, count in rows:
        summary += f"  • {category}: {count} {'activity' if count == 1 else 'activities'}\n"
    return summary.rstrip()


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
