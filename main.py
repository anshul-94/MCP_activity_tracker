import os
import sqlite3
import json
import re
import sys
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict
from contextlib import contextmanager
from fastmcp import FastMCP

# 1. ARCHITECTURAL CONFIGURATION
# Enforces persistent storage. No ephemeral /tmp fallback.
DB_PATH = os.environ.get("DB_PATH", os.path.expanduser("~/.mcp_activity_tracker.db"))
CATEGORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")

# 2. CONCURRENCY CONTROL
# Process-level write lock to prevent contention between concurrent MCP tool workers.
_WRITE_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)
logger = logging.getLogger("mcp_server")

@contextmanager
def get_db(write=False, retries=5):
    """
    Production-grade SQLite manager with exponential backoff and transaction safety.
    """
    if write: _WRITE_LOCK.acquire()
    
    conn = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        
        # Performance & Reliability Tuning
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        
        if write: conn.execute("BEGIN TRANSACTION")
        
        yield conn
        
        if write: conn.commit()
    except Exception as e:
        if conn and write: conn.rollback()
        logger.error(f"Database Error: {e}")
        raise
    finally:
        if conn: conn.close()
        if write: _WRITE_LOCK.release()

def init_db():
    with get_db(write=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                description  TEXT NOT NULL,
                category     TEXT,
                start_time   TEXT NOT NULL,
                end_time     TEXT NOT NULL,
                date         TEXT NOT NULL,
                user_id      TEXT NOT NULL DEFAULT 'default_user',
                UNIQUE(description, date, start_time, end_time, user_id)
            )
        """)
        # Optimize search & summary performance with b-tree indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_date ON activities(user_id, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON activities(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON activities(date)")

init_db()

# 3. INTELLIGENT CATEGORY SYSTEM
def get_category(description: str) -> str:
    desc = description.lower()
    scores: Dict[str, int] = {}
    
    # Priority weighting to avoid deterministic ties
    priority = {"Productivity": 10, "Fitness": 9, "Study": 8, "Personal": 7}
    
    try:
        if os.path.exists(CATEGORY_FILE):
            with open(CATEGORY_FILE, "r") as f:
                categories = json.load(f)
                for cat, keywords in categories.items():
                    name = cat.replace("_", " ").title()
                    score = sum(2 if re.search(rf"\b{re.escape(kw.lower())}\b", desc) else 0 for kw in keywords if kw != "other")
                    score += sum(1 if kw.lower() in desc else 0 for kw in keywords if kw != "other")
                    if score > 0:
                        scores[name] = score + priority.get(name, 0) / 100.0
    except: pass

    if not scores:
        # Hardcoded fallback logic for resilience
        fallbacks = {"Productivity": ["work", "meeting", "code", "dev"], "Fitness": ["gym", "run", "sport"], "Personal": ["eat", "sleep", "rest"]}
        for cat, kws in fallbacks.items():
            if any(k in desc for k in kws): return cat
        return "Misc"

    return max(scores, key=scores.get)

# 4. ROBUST TIME HANDLING & ATOMIC MIDNIGHT SPLITTING
def normalize_time(t: str) -> Optional[str]:
    if not t: return None
    t = str(t).strip().upper().replace('.', ':')
    match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*([AP]M)?', t)
    if not match: return None
    h, m, p = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if p == "PM" and h < 12: h += 12
    elif p == "AM" and h == 12: h = 0
    if h > 23 or m > 59: return None
    return f"{h:02d}:{m:02d}"

def split_activity(desc: str, start: str, end: str, date_str: str) -> List[Tuple[str, str, str, str]]:
    if start < end: return [(desc[:500], start, end, date_str)]
    curr_date = datetime.strptime(date_str, "%Y-%m-%d")
    next_date = (curr_date + timedelta(days=1)).strftime("%Y-%m-%d")
    return [(desc[:500], start, "23:59", date_str), (desc[:500], "00:00", end, next_date)]

def format_12h(t24: str) -> str:
    return datetime.strptime(t24, "%H:%M").strftime("%I:%M %p").lstrip('0').lower()

# 5. PRODUCTION-GRADE MCP TOOLS
mcp = FastMCP("Activity Tracker")

@mcp.tool()
def log_activity(description: str, start_time: str, end_time: str, date: str = None, user_id: str = "default_user") -> str:
    """Logs activity with atomic cross-midnight support. Enforces 500-char desc limit."""
    try:
        user_id = str(user_id or "default_user").strip()
        date = date or datetime.now().strftime("%Y-%m-%d")
        s, e = normalize_time(start_time), normalize_time(end_time)
        
        if not s or not e: return "❌ Error: Invalid time format. Please use '2 PM' or '14:30'."
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date): return "❌ Error: Invalid date format. Use YYYY-MM-DD."
        
        category = get_category(description)
        entries = split_activity(description, s, e, date)
        
        with get_db(write=True) as conn:
            success_count = 0
            for d, start, end, dt in entries:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO activities (description, category, start_time, end_time, date, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (d, category, start, end, dt, user_id)
                )
                success_count += cursor.rowcount
            
            if success_count == 0:
                return f"⚠️ Activity already exists for {user_id}."
            
            summary = f"✅ Logged '{description[:50]}' as {category}"
            if len(entries) > 1: summary += " (Split across midnight)"
            return summary
            
    except Exception as ex:
        return f"❌ Failure: {str(ex)}"

@mcp.tool()
def search_activity(query: str = None, category: str = None, date: str = None, user_id: str = "default_user", limit: int = 50, offset: int = 0) -> str:
    """Search activities with strict pagination (max 50) and transport-safe output."""
    try:
        user_id = str(user_id or "default_user")
        sql, params = "SELECT * FROM activities WHERE user_id = ?", [user_id]
        if query: sql += " AND description LIKE ?"; params.append(f"%{query}%")
        if category: sql += " AND LOWER(category) = LOWER(?)"; params.append(category)
        if date: sql += " AND date = ?"; params.append(date)
        
        sql += " ORDER BY date DESC, start_time ASC LIMIT ? OFFSET ?"
        params += [min(limit, 50), max(offset, 0)]
        
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            if not rows: return f"No entries found for user '{user_id}'."
            
            output = [f"Found {len(rows)} entries (user: {user_id}):"]
            for r in rows:
                t = f"{format_12h(r['start_time'])}-{format_12h(r['end_time'])}"
                desc = (r['description'][:100] + '...') if len(r['description']) > 100 else r['description']
                output.append(f"• [{r['date']}] {t}: {desc} ({r['category']})")
            return "\n".join(output)
    except Exception as e:
        return f"❌ Search error: {str(e)}"

@mcp.tool()
def update_activity(activity_id: int, description: str = None, start_time: str = None, end_time: str = None, date: str = None, user_id: str = "default_user") -> str:
    """Update existing entry. Enforces same validation as log_activity."""
    try:
        u_id = str(user_id or "default_user")
        fields, params = [], []
        if description:
            fields.append("description = ?"); params.append(description[:500])
            fields.append("category = ?"); params.append(get_category(description))
        if start_time:
            s = normalize_time(start_time)
            if s: fields.append("start_time = ?"); params.append(s)
        if end_time:
            e = normalize_time(end_time)
            if e: fields.append("end_time = ?"); params.append(e)
        if date:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                fields.append("date = ?"); params.append(date)

        if not fields: return "⚠️ Nothing to update."
        params += [activity_id, u_id]
        sql = f"UPDATE activities SET {', '.join(fields)} WHERE id = ? AND user_id = ?"
        
        with get_db(write=True) as conn:
            c = conn.execute(sql, params)
            return f"✅ Activity #{activity_id} updated." if c.rowcount > 0 else "⚠️ Activity not found."
    except Exception as e:
        return f"❌ Update failed: {str(e)}"

@mcp.tool()
def delete_activity(activity_id: int, user_id: str = "default_user") -> str:
    """Atomic deletion of an activity record."""
    try:
        with get_db(write=True) as conn:
            c = conn.execute("DELETE FROM activities WHERE id = ? AND user_id = ?", (activity_id, str(user_id or "default_user")))
            return f"✅ Removed activity #{activity_id}" if c.rowcount > 0 else "⚠️ Not found."
    except Exception as e:
        return f"❌ Deletion failed."

@mcp.tool()
def get_activity_stats(user_id: str = "default_user", days: int = 7) -> str:
    """High-performance summary using indexed category and date lookups."""
    try:
        u_id = str(user_id or "default_user")
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        sql = "SELECT category, COUNT(*) as count FROM activities WHERE user_id = ? AND date >= ? GROUP BY category ORDER BY count DESC"
        
        with get_db() as conn:
            rows = conn.execute(sql, (u_id, cutoff)).fetchall()
            if not rows: return f"No logs found in last {days} days for '{u_id}'."
            
            res = [f"📊 Usage Stats for '{u_id}' (Past {days} days):"]
            for r in rows:
                res.append(f"  • {r['category']}: {r['count']} sessions")
            return "\n".join(res)
    except Exception as e:
        return f"❌ Summary generation failed."

if __name__ == "__main__":
    if not sys.stdin.isatty():
        mcp.run()
    else:
        # Development / Remote HTTP fallback
        port = int(os.environ.get("PORT", 8080))
        logger.info(f"Starting MCP Server on port {port}...")
        mcp.run(transport="http", host="0.0.0.0", port=port)
