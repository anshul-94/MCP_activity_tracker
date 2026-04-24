import os, sqlite3, json, re, sys, logging, threading
from datetime import datetime, timedelta
from contextlib import contextmanager
from fastmcp import FastMCP

# CONFIG
DB_PATH = os.getenv("DB_PATH", os.path.expanduser("~/.mcp_activity_tracker.db"))
CATEGORY_FILE = os.path.join(os.path.dirname(__file__), "categories.json")
LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp")

# ---------------- DB ----------------
@contextmanager
def db(write=False):
    if write: LOCK.acquire()
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        if write: conn.execute("BEGIN")
        yield conn
        if write: conn.commit()
    except:
        if write: conn.rollback()
        raise
    finally:
        conn.close()
        if write: LOCK.release()

def init():
    with db(True) as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS activities(
            id INTEGER PRIMARY KEY,
            description TEXT,
            category TEXT,
            start_time TEXT,
            end_time TEXT,
            date TEXT,
            user_id TEXT,
            UNIQUE(description, date, start_time, end_time, user_id)
        )""")

init()

# ---------------- HELPERS ----------------
def norm_date(d):
    if not d: return datetime.now().strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d","%d %b %Y","%d %B %Y","%d/%m/%Y"):
        try: return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except: pass
    return None

def norm_time(t):
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*([AP]M)?', str(t).upper())
    if not m: return None
    h, mnt, ap = int(m[1]), int(m[2] or 0), m[3]
    if ap == "PM" and h < 12: h += 12
    if ap == "AM" and h == 12: h = 0
    return f"{h:02d}:{mnt:02d}" if h < 24 and mnt < 60 else None

def get_cat(desc):
    try:
        data = json.load(open(CATEGORY_FILE))
        for k,v in data.items():
            if any(re.search(rf"\b{x}\b", desc.lower()) for x in v):
                return k.title()
    except: pass
    return "Misc"

def month_pattern(q):
    months = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]
    q = (q or "").lower()
    for i,m in enumerate(months,1):
        if m in q: return f"%-{i:02d}-%"
    return None

# ---------------- MCP ----------------
mcp = FastMCP("Activity Tracker")

@mcp.tool()
def log_activity(description:str, start_time:str, end_time:str, date:str=None, user_id="default_user"):
    d, s, e = norm_date(date), norm_time(start_time), norm_time(end_time)
    if not d: return "❌ Invalid date"
    if not s or not e: return "❌ Invalid time"

    with db(True) as c:
        c.execute("""INSERT OR IGNORE INTO activities 
        VALUES(NULL,?,?,?,?,?,?)""",
        (description[:500], get_cat(description), s, e, d, user_id))
    return f"✅ Logged {d}"

@mcp.tool()
def search_activity(query=None, category=None, date=None, user_id="default_user", limit=50):
    sql, p = "SELECT * FROM activities WHERE user_id=?", [user_id]

    pattern = month_pattern(date or query)

    if date and norm_date(date):
        sql += " AND date=?"; p.append(norm_date(date))
    elif pattern:
        sql += " AND date LIKE ?"; p.append(pattern)
    elif query:
        sql += " AND description LIKE ?"; p.append(f"%{query}%")

    if category:
        sql += " AND LOWER(category)=LOWER(?)"; p.append(category)

    sql += " ORDER BY date DESC LIMIT ?"; p.append(min(limit,50))

    with db() as c:
        rows = c.execute(sql,p).fetchall()

    if not rows: return "No results"

    return "\n".join(
        [f"{r['date']} {r['start_time']}-{r['end_time']} {r['description']} ({r['category']})"
         for r in rows]
    )

@mcp.tool()
def activity_summary(user_id="default_user", days=7):
    cutoff = (datetime.now()-timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as c:
        rows = c.execute("""
        SELECT category, COUNT(*) c FROM activities
        WHERE user_id=? AND date>=?
        GROUP BY category ORDER BY c DESC
        """,(user_id, cutoff)).fetchall()

    return "\n".join([f"{r['category']}: {r['c']}" for r in rows]) or "No data"

# ---------------- RUN ----------------
if __name__ == "__main__":
    mcp.run() if not sys.stdin.isatty() else mcp.run(transport="http", host="0.0.0.0", port=int(os.getenv("PORT",8080)))
