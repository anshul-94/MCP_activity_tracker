import os
import sqlite3
import json
import re
import sys
from datetime import datetime
from contextlib import contextmanager
from fastmcp import FastMCP

# Initialize MCP Server
mcp = FastMCP("Activity Tracker")

# 1. SETUP PATHS — Single absolute DB path, never changes
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tracker.db")
CATEGORY_FILE = os.path.join(BASE_DIR, "categories.json")

def log_err(msg):
    """Log to stderr to avoid interfering with MCP stdio transport."""
    print(f"[MCP-LOG] {msg}", file=sys.stderr)

log_err(f"🚀 MCP Activity Tracker starting...")
log_err(f"📁 Database Path: {DB_PATH}")

# 2. DATABASE HELPERS
@contextmanager
def get_db():
    """Context manager for SQLite database connections."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent write handling
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()
    except Exception as e:
        log_err(f"❌ Database connection error: {e}")
        raise

def init_db():
    """
    Ensures the table exists with user_id and UNIQUE constraint.
    """
    try:
        with get_db() as conn:
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
                log_err("⚠️  Migrated: added user_id column to existing table.")

            conn.commit()
    except Exception as e:
        log_err(f"❌ Failed to initialize database: {e}")
        # We don't exit here to allow the server to start, but tools will fail with error messages.

# Initialize DB at startup
try:
    init_db()
except Exception:
    pass

# 3. UTILITY FUNCTIONS
def load_categories():
    """Load categories from JSON config file."""
    try:
        if not os.path.exists(CATEGORY_FILE):
            return {}
        with open(CATEGORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        log_err(f"⚠️ Error loading categories: {e}")
        return {}

def find_category(activity_text: str) -> str:
    """
    Categorize an activity description using categories.json.
    Supports partial matches and prioritization.
    """
    if not activity_text: return "Misc"
    text = activity_text.lower()
    
    priority_overrides = {
        "football": "Fitness", "cricket": "Fitness", "basketball": "Fitness",
        "gym": "Fitness", "workout": "Fitness", "movie": "Entertainment",
        "gaming": "Entertainment", "coding": "Productivity"
    }
    
    for kw, cat in priority_overrides.items():
        if re.search(r'\b' + re.escape(kw) + r'\b', text):
            return cat

    json_cats = load_categories()
    if not json_cats:
        return "Misc"
        
    for cat, keywords in json_cats.items():
        for kw in keywords:
            if kw == "other": continue
            kw_norm = kw.replace("_", " ").lower()
            if re.search(r'\b' + re.escape(kw_norm) + r'\b', text):
                return cat.replace("_", " ").title()
                
    for cat, keywords in json_cats.items():
        for kw in keywords:
            if kw == "other": continue
            kw_norm = kw.replace("_", " ").lower()
            if kw_norm in text:
                return cat.replace("_", " ").title()
    return "Misc"

def format_time_12h(time_24h: str) -> str:
    """Convert 13:00 → 1 PM (Platform safe formatting)"""
    try:
        dt = datetime.strptime(time_24h, "%H:%M")
        # %I often has leading zero, we strip it for cleaner look
        return dt.strftime("%I %p").lstrip('0')
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
    time_str = str(time_str).strip().upper().replace('.', '')
    formats = ['%H:%M', '%I %p', '%I:%M %p', '%I%p', '%I:%M%p']
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt).strftime('%H:%M')
        except ValueError:
            continue
    return None

def validate_date(date_str: str) -> bool:
    if not date_str: return False
    try:
        datetime.strptime(str(date_str), "%Y-%m-%d")
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
    """Log a new activity for a specific user. Times in HH:MM format. Date in YYYY-MM-DD."""
    try:
        user_id = str(user_id or "default_user")
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        s_time = normalize_time(start_time)
        e_time = normalize_time(end_time)
        
        if not s_time or not e_time:
            return f"❌ Error: Invalid time format '{start_time}' or '{end_time}'."
        if not validate_date(date):
            return f"❌ Error: Invalid date '{date}'. Use YYYY-MM-DD."

        category = find_category(description)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR IGNORE INTO activities
                   (description, category, start_time, end_time, date, user_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (description, category, s_time, e_time, date, user_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                return f"⚠️ Duplicate skipped: '{description}' already logged for {user_id}."
            return f"✅ Logged '{description}' under {category} for user '{user_id}'"
    except Exception as e:
        return f"❌ System Error in log_activity: {str(e)}"

@mcp.tool()
def list_activities(
    date: str = None,
    keyword: str = None,
    all_time: bool = False,
    user_id: str = "default_user"
) -> str:
    """List activities for a user. Defaults to today's activities."""
    try:
        user_id = str(user_id or "default_user")
        if not date and not keyword and not all_time:
            date = datetime.now().strftime("%Y-%m-%d")

        with get_db() as conn:
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

            if not rows:
                return f"No activities found for user '{user_id}'."

            lines = ["#ID | Date | Time | Description | Category"]
            for r in rows:
                d = format_date_short(r["date"])
                t = f"{format_time_12h(r['start_time'])}–{format_time_12h(r['end_time'])}"
                lines.append(f"#{r['id']} | {d} | {t} | {r['description']} | {r['category']}")

            return "\n".join(lines)
    except Exception as e:
        return f"❌ System Error in list_activities: {str(e)}"

@mcp.tool()
def search_activity(
    query: str = None,
    category: str = None,
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """Search activities for a specific user by keyword, category, and/or date."""
    try:
        user_id = str(user_id or "default_user")
        with get_db() as conn:
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

            if not rows:
                return f"No activities found for user '{user_id}' matching filters."

            lines = ["#ID | Date | Time | Description | Category"]
            for r in rows:
                d = format_date_short(r["date"])
                t = f"{format_time_12h(r['start_time'])}–{format_time_12h(r['end_time'])}"
                lines.append(f"#{r['id']} | {d} | {t} | {r['description']} | {r['category']}")
            return "\n".join(lines)
    except Exception as e:
        return f"❌ System Error in search_activity: {str(e)}"

@mcp.tool()
def update_activity(
    activity_id: int,
    description: str = None,
    start_time: str = None,
    end_time: str = None,
    date: str = None,
    user_id: str = "default_user"
) -> str:
    """Update an existing activity by ID. Provide only the fields to change."""
    try:
        user_id = str(user_id or "default_user")
        updates = []
        params = []
        if description:
            updates.append("description = ?"); params.append(description)
            updates.append("category = ?"); params.append(find_category(description))
        if start_time:
            s = normalize_time(start_time)
            if not s: return f"❌ Invalid start_time '{start_time}'."
            updates.append("start_time = ?"); params.append(s)
        if end_time:
            e = normalize_time(end_time)
            if not e: return f"❌ Invalid end_time '{end_time}'."
            updates.append("end_time = ?"); params.append(e)
        if date:
            if not validate_date(date): return f"❌ Invalid date '{date}'."
            updates.append("date = ?"); params.append(date)

        if not updates: return "⚠️ No changes provided."
        params += [activity_id, user_id]
        sql = f"UPDATE activities SET {', '.join(updates)} WHERE id = ? AND user_id = ?"

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            updated = cursor.rowcount
            conn.commit()

        if updated > 0: return f"✅ Updated activity #{activity_id} for '{user_id}'"
        return f"⚠️ Activity #{activity_id} not found for '{user_id}'."
    except Exception as e:
        return f"❌ System Error: {str(e)}"

@mcp.tool()
def delete_activity(activity_id: int, user_id: str = "default_user") -> str:
    """Delete an activity by ID."""
    try:
        user_id = str(user_id or "default_user")
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM activities WHERE id = ? AND user_id = ?", (activity_id, user_id))
            deleted = cursor.rowcount
            conn.commit()
        if deleted > 0: return f"✅ Deleted activity #{activity_id}"
        return f"⚠️ Activity #{activity_id} not found."
    except Exception as e:
        return f"❌ System Error: {str(e)}"

@mcp.tool()
def get_summary(date: str = None, user_id: str = "default_user") -> str:
    """Get activity count grouped by category for a user."""
    try:
        user_id = str(user_id or "default_user")
        with get_db() as conn:
            cursor = conn.cursor()
            sql = "SELECT category, COUNT(*) as count FROM activities WHERE user_id = ?"
            params = [user_id]
            if date:
                sql += " AND date = ?"; params.append(date)
            sql += " GROUP BY category ORDER BY count DESC"
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            if not rows: return f"No data for user '{user_id}'."
            scope = format_date_short(date) if date else "All Time"
            res = [f"📊 Summary for '{user_id}' ({scope}):"]
            for r in rows:
                res.append(f"  • {r['category']}: {r['count']} {'activity' if r['count'] == 1 else 'activities'}")
            return "\n".join(res)
    except Exception as e:
        return f"❌ System Error: {str(e)}"

if __name__ == "__main__":
    # Robust startup logic to handle different environments
    try:
        # Detect if we should use HTTP (cloud/pre-flight) or STDIO (local)
        # Port 8081 is common for pre-flight requirements
        port_env = os.environ.get("PORT")
        if port_env:
            port = int(port_env)
            log_err(f"Starting HTTP server on port {port}...")
            mcp.run(transport="http", host="0.0.0.0", port=port)
        elif "--port" in sys.argv:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx+1])
            mcp.run(transport="http", host="0.0.0.0", port=port)
        else:
            # Default to 8081 if specified by pre-flight expectations, 
            # OR fallback to stdio if that fails.
            try:
                # If we are in a terminal and no PORT is set, stdio is usually better.
                if sys.stdin.isatty():
                    mcp.run()
                else:
                    # Non-interactive might be a pre-flight check expecting a port
                    mcp.run(transport="http", host="0.0.0.0", port=8081)
            except Exception as e:
                log_err(f"Failed to start preferred transport, attempting fallback: {e}")
                mcp.run()
    except Exception as e:
        log_err(f"CRITICAL STARTUP ERROR: {e}")
        sys.exit(1)
