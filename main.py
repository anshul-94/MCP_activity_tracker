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
DB_PATH = os.environ.get("DB_PATH", os.path.expanduser("~/.mcp_activity_tracker.db"))
CATEGORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "categories.json")
_WRITE_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)
logger = logging.getLogger("mcp_server")

@contextmanager
def get_db(write=False):
    if write: _WRITE_LOCK.acquire()
    conn = None
    try:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_date ON activities(user_id, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON activities(category)")

init_db()

# 2. UPDATED SEARCH LOGIC (Multi-year Month Detection)
def detect_month_pattern(q: str) -> Optional[str]:
    """Detects month name or shortcode and returns SQL LIKE pattern for multi-year search."""
    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    short_m = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    q_low = q.lower()
    
    idx = -1
    for i, m in enumerate(months):
        if m in q_low: idx = i + 1; break
    if idx == -1:
        for i, m in enumerate(short_m):
            if m in q_low: idx = i + 1; break
            
    if idx != -1:
        return f"%-{idx:02d}-%"
    return None

def normalize_date(d: str) -> Optional[str]:
    if not d: return datetime.now().strftime("%Y-%m-%d")
    d = str(d).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d): return d
    fmts = ["%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"]
    for fmt in fmts:
        try: return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except: pass
    return None

# 3. UTILITIES
def get_category(description: str) -> str:
    desc = description.lower()
    try:
        if os.path.exists(CATEGORY_FILE):
            with open(CATEGORY_FILE, "r") as f:
                cats = json.load(f)
                for cat, keywords in cats.items():
                    if any(re.search(rf"\b{re.escape(k.lower())}\b", desc) for k in keywords if k != "other"):
                        return cat.replace("_", " ").title()
    except: pass
    return "Misc"

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

# 4. MCP TOOLS
mcp = FastMCP("Activity Tracker")

@mcp.tool()
def log_activity(description: str, start_time: str, end_time: str, date: str = None, user_id: str = "default_user") -> str:
    try:
        u_id = str(user_id or "default_user").strip()
        norm_date = normalize_date(date)
        if not norm_date: return f"❌ Error: Invalid date format '{date}'"
        s, e = normalize_time(start_time), normalize_time(end_time)
        if not s or not e: return "❌ Error: Invalid time format."
        category = get_category(description)
        with get_db(write=True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO activities (description, category, start_time, end_time, date, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                (description[:500], category, s, e, norm_date, u_id)
            )
            return f"✅ Logged for {norm_date}"
    except Exception as ex:
        return f"❌ Failure: {str(ex)}"

@mcp.tool()
def search_activity(query: str = None, category: str = None, date: str = None, user_id: str = "default_user", limit: int = 50) -> str:
    """Improved search: Month queries (e.g. 'April') return results across ALL years."""
    try:
        u_id = str(user_id or "default_user").strip()
        sql, params = "SELECT * FROM activities WHERE user_id = ?", [u_id]
        
        # Check for month pattern in either 'date' or 'query' parameter
        pattern = None
        if date:
            norm_date = normalize_date(date)
            if norm_date: 
                sql += " AND date = ?"; params.append(norm_date)
            else:
                pattern = detect_month_pattern(date)
        elif query:
            pattern = detect_month_pattern(query)
            
        if pattern:
            # Multi-year month search using LIKE
            sql += " AND date LIKE ?"
            params.append(pattern)
            logger.info(f"Multi-year search pattern for user {u_id}: {pattern}")
        elif query:
            # Keyword search if no month detected
            sql += " AND description LIKE ?"
            params.append(f"%{query}%")
            
        if category: 
            sql += " AND LOWER(category) = LOWER(?)"
            params.append(category)
            
        sql += " ORDER BY date DESC, start_time ASC LIMIT ?"
        params.append(min(limit, 50))
        
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            if not rows: return "No results found."
            output = [f"Found {len(rows)} entries matching search:"]
            for r in rows:
                output.append(f"• [{r['date']}] {r['start_time']}-{r['end_time']}: {r['description']} ({r['category']})")
            return "\n".join(output)
    except Exception as e:
        return f"❌ Search error: {str(e)}"

@mcp.tool()
def activity_summary(user_id: str = "default_user", days: int = 7) -> str:
    try:
        u_id = str(user_id or "default_user").strip()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with get_db() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) as c FROM activities WHERE user_id = ? AND date >= ? GROUP BY category ORDER BY c DESC",
                (u_id, cutoff)
            ).fetchall()
            if not rows: return "No data."
            return "\n".join([f"• {r['category']}: {r['c']}" for r in rows])
    except Exception as e:
        return f"❌ Summary error."

if __name__ == "__main__":
    if not sys.stdin.isatty():
        mcp.run()
    else:
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
