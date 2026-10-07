import hashlib
import os
import random
import secrets
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# On Linux/Docker the default is /data; on Windows it defaults to a local
# "data" folder unless DATA_DIR is set (IIS service deployments set it explicitly).
DATA_DIR = os.environ.get("DATA_DIR") or ("data" if os.name == "nt" else "/data")
DB_PATH = os.path.join(DATA_DIR, "portal.db")
TZ = ZoneInfo(os.environ.get("TZ", "America/New_York"))
SESSION_MAX_AGE = 12 * 3600
PBKDF2_ITERATIONS = 200_000


def _secret() -> str:
    path = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(path):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    return open(path).read().strip()


serializer = URLSafeTimedSerializer(_secret())


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2${salt}${digest.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return secrets.compare_digest(candidate, digest)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'viewer'))
        );
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY,
            badge TEXT NOT NULL,
            name TEXT NOT NULL,
            checked_in_at TEXT NOT NULL,
            checked_out_at TEXT
        );
        CREATE TABLE IF NOT EXISTS personnel (
            id INTEGER PRIMARY KEY,
            badge TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            firm TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    # Migrate the users table if its CHECK constraint predates the new roles.
    users_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if users_sql and "police" not in (users_sql["sql"] or ""):
        conn.executescript(
            """
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'viewer', 'police', 'security'))
            );
            INSERT INTO users_new SELECT id, username, password_hash, role FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
            """
        )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(visits)")}
    if "personnel_id" not in cols:
        conn.execute("ALTER TABLE visits ADD COLUMN personnel_id INTEGER")
    if "firm" not in cols:
        conn.execute("ALTER TABLE visits ADD COLUMN firm TEXT")
    if "visitor_badge" not in cols:
        conn.execute("ALTER TABLE visits ADD COLUMN visitor_badge TEXT")
    if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", hash_password("admin123"), "admin"),
        )
        print("Seeded admin user (password: admin123) -- change it after first login.")
    if conn.execute("SELECT COUNT(*) AS n FROM personnel").fetchone()["n"] == 0:
        rng = random.Random(2026)
        firsts = ["Alice", "Bob", "Carla", "Derek", "Elena", "Frank", "Grace", "Hugo", "Irene", "Jack"]
        lasts = ["Anderson", "Brown", "Chen", "Diaz", "Evans", "Foster", "Garcia", "Hollis", "Novak", "Ortiz"]
        firms = ["Rockwell Automation", "Inductive Automation", "Siemens", "Bechtel", "Thales"]
        used = set()
        for _ in range(10):
            name = f"{rng.choice(firsts)} {rng.choice(lasts)}"
            if name in used:
                continue
            used.add(name)
            conn.execute(
                "INSERT INTO personnel (badge, name, firm) VALUES (?, ?, ?)",
                (str(1000 + rng.randint(1, 8999)), name, firms[len(used) % len(firms)]),
            )
        print(f"Seeded {len(used)} personnel across {len(firms)} firms.")
    conn.commit()
    conn.close()


init_db()

app = FastAPI(title="Secure Facility Portal")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def fmt(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")


def dwell_between(start: str, end: str = None) -> str:
    a = datetime.fromisoformat(start)
    b = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
    minutes = int((b - a).total_seconds() // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


templates.env.filters["fmt"] = fmt
templates.env.filters["dwell"] = dwell_between


def current_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def read_flash(request: Request):
    token = request.cookies.get("flash")
    if not token:
        return None
    try:
        return serializer.loads(token, max_age=600)
    except Exception:
        return None


def render(request: Request, template: str, **ctx):
    flash = read_flash(request)
    user = current_user(request)
    resp = templates.TemplateResponse(
        request,
        template,
        {
            "request": request,
            "user": user,
            "perms": PERMS.get(user["role"], set()) if user else set(),
            "flash": flash,
            **ctx,
        },
    )
    if flash:
        resp.delete_cookie("flash")
    return resp


def flash_redirect(msg: str, location: str = "/"):
    resp = RedirectResponse(location, status_code=303)
    resp.set_cookie("flash", serializer.dumps(msg), path="/", samesite="lax", max_age=600)
    return resp


def require_login(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return user


PERMS = {
    "admin": {"checkin", "roster_write", "users"},
    "police": {"checkin"},
    "security": {"roster_write"},
    "viewer": set(),
}


def require_perm(request: Request, perm: str):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if perm not in PERMS.get(user["role"], set()):
        return flash_redirect(f"Your role ({user['role']}) cannot perform that action.")
    return user


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    conn.close()
    if not row or not check_password(password, row["password_hash"]):
        return render(request, "login.html", flash="Invalid credentials.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "session",
        serializer.dumps({"uid": row["id"], "username": row["username"], "role": row["role"]}),
        path="/",
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, q: str = ""):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    q_clean = q.strip()
    if q_clean:
        # Strict substring match; escape LIKE wildcards so % and _ in the query are literal.
        like = "%" + q_clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "WHERE p.name LIKE ? ESCAPE '\\' OR p.badge LIKE ? ESCAPE '\\' OR p.firm LIKE ? ESCAPE '\\'"
        params = (like, like, like)
    else:
        where = ""
        params = ()
    rows = conn.execute(
        f"""
        SELECT p.*,
               (SELECT id FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL) AS open_visit,
               (SELECT checked_in_at FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL) AS open_since,
               (SELECT COUNT(*) FROM visits WHERE personnel_id = p.id) AS visits_count,
               (SELECT visitor_badge FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL) AS vb
        FROM personnel p
        {where}
        ORDER BY p.name
        """,
        params,
    ).fetchall()
    count = conn.execute("SELECT COUNT(*) AS n FROM visits WHERE checked_out_at IS NULL").fetchone()["n"]
    # "Inside now" is independent of the search filter: everyone currently inside,
    # newest check-in first (earliest at the bottom).
    inside = conn.execute(
        """
        SELECT p.*,
               (SELECT id FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL) AS open_visit,
               (SELECT checked_in_at FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL) AS open_since,
               (SELECT visitor_badge FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL) AS vb
        FROM personnel p
        WHERE EXISTS (SELECT 1 FROM visits WHERE personnel_id = p.id AND checked_out_at IS NULL)
        ORDER BY open_since DESC
        """,
    ).fetchall()
    conn.close()
    return render(request, "dashboard.html", user=user, rows=rows, inside=inside, q=q, count=count)


@app.post("/checkin")
def check_in(request: Request, personnel_id: int = Form(...), visitor_badge: str = Form("")):
    user = require_perm(request, "checkin")
    if isinstance(user, RedirectResponse):
        return user
    visitor_badge = visitor_badge.strip()
    if not visitor_badge:
        return flash_redirect("Visitor's Badge number is required.")
    conn = db()
    p = conn.execute("SELECT * FROM personnel WHERE id = ?", (personnel_id,)).fetchone()
    if not p or not p["active"]:
        conn.close()
        return flash_redirect("Personnel not found on the active roster.")
    open_visit = conn.execute(
        "SELECT id FROM visits WHERE personnel_id = ? AND checked_out_at IS NULL", (personnel_id,)
    ).fetchone()
    if open_visit:
        conn.close()
        return flash_redirect(f"{p['name']} is already inside -- check them out first.")
    conn.execute(
        "INSERT INTO visits (personnel_id, badge, name, firm, visitor_badge, checked_in_at) VALUES (?, ?, ?, ?, ?, ?)",
        (personnel_id, p["badge"], p["name"], p["firm"], visitor_badge, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return flash_redirect(f"Checked in: {p['name']} ({p['firm']}, ID {p['badge']}, VB {visitor_badge}).")


@app.post("/checkout/{visit_id}")
def check_out(request: Request, visit_id: int):
    user = require_perm(request, "checkin")
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    row = conn.execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
    if not row or row["checked_out_at"]:
        conn.close()
        return flash_redirect("Visit not found or already closed.")
    conn.execute(
        "UPDATE visits SET checked_out_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), visit_id),
    )
    conn.commit()
    conn.close()
    return flash_redirect(f"Checked out: {row['name']} (ID {row['badge']}, VB {row['visitor_badge'] or '-'}).")


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    rows = conn.execute("SELECT * FROM visits ORDER BY checked_in_at DESC LIMIT 200").fetchall()
    conn.close()
    return render(request, "history.html", user=user, rows=rows)


@app.post("/roster/{person_id}/delete")
def delete_personnel(request: Request, person_id: int):
    user = require_perm(request, "roster_write")
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    p = conn.execute("SELECT * FROM personnel WHERE id = ?", (person_id,)).fetchone()
    if not p:
        conn.close()
        return flash_redirect("Personnel not found.", "/")
    if conn.execute("SELECT COUNT(*) AS n FROM visits WHERE personnel_id = ?", (person_id,)).fetchone()["n"] > 0:
        conn.close()
        return flash_redirect(f"{p['name']} has check-in history -- deactivate instead of deleting.", "/")
    conn.execute("DELETE FROM personnel WHERE id = ?", (person_id,))
    conn.commit()
    conn.close()
    return flash_redirect(f"Deleted: {p['name']} (ID {p['badge']}).", "/")


@app.post("/roster")
def create_personnel(request: Request, name: str = Form(...), badge: str = Form(...), firm: str = Form(...)):
    user = require_perm(request, "roster_write")
    if isinstance(user, RedirectResponse):
        return user
    name, badge, firm = name.strip(), badge.strip(), firm.strip()
    if not (name and badge and firm):
        return flash_redirect("Name, ID, and firm are all required.", "/")
    conn = db()
    try:
        conn.execute(
            "INSERT INTO personnel (badge, name, firm) VALUES (?, ?, ?)", (badge, name, firm)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return flash_redirect(f"ID {badge} already exists on the roster.", "/")
    conn.close()
    return flash_redirect(f"Added {name} ({firm}, ID {badge}).", "/")


@app.get("/roster/{person_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, person_id: int):
    user = require_perm(request, "roster_write")
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    p = conn.execute("SELECT * FROM personnel WHERE id = ?", (person_id,)).fetchone()
    conn.close()
    if not p:
        return flash_redirect("Personnel not found.", "/")
    return render(request, "edit.html", user=user, p=p)


@app.post("/roster/{person_id}")
def edit_personnel(request: Request, person_id: int, name: str = Form(...), badge: str = Form(...), firm: str = Form(...)):
    user = require_perm(request, "roster_write")
    if isinstance(user, RedirectResponse):
        return user
    name, badge, firm = name.strip(), badge.strip(), firm.strip()
    if not (name and badge and firm):
        return flash_redirect("Name, badge, and firm are all required.", "/")
    conn = db()
    try:
        conn.execute(
            "UPDATE personnel SET name = ?, badge = ?, firm = ? WHERE id = ?", (name, badge, firm, person_id)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return flash_redirect(f"ID {badge} already belongs to another person.", "/")
    conn.close()
    return flash_redirect(f"Updated {name} ({firm}, ID {badge}).", "/")


@app.post("/roster/{person_id}/toggle")
def toggle_personnel(request: Request, person_id: int):
    user = require_perm(request, "roster_write")
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    p = conn.execute("SELECT * FROM personnel WHERE id = ?", (person_id,)).fetchone()
    if not p:
        conn.close()
        return flash_redirect("Personnel not found.", "/")
    if p["active"] and conn.execute(
        "SELECT id FROM visits WHERE personnel_id = ? AND checked_out_at IS NULL", (person_id,)
    ).fetchone():
        conn.close()
        return flash_redirect(f"{p['name']} is still inside -- check them out before deactivating.", "/")
    conn.execute("UPDATE personnel SET active = 1 - active WHERE id = ?", (person_id,))
    conn.commit()
    conn.close()
    return flash_redirect(f"{'Deactivated' if p['active'] else 'Reactivated'}: {p['name']}.", "/")


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = require_perm(request, "users")
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    conn.close()
    return render(request, "users.html", user=user, rows=rows)


@app.post("/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
):
    user = require_perm(request, "users")
    if isinstance(user, RedirectResponse):
        return user
    username = username.strip()
    if not username or not password:
        return flash_redirect("Username and password required.", "/users")
    if role not in PERMS:
        role = "viewer"
    conn = db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return flash_redirect(f"User '{username}' already exists.", "/users")
    conn.close()
    return flash_redirect(f"Created user '{username}' ({role}).", "/users")


@app.post("/users/{user_id}/password")
def change_password(request: Request, user_id: int, password: str = Form(...)):
    user = require_perm(request, "users")
    if isinstance(user, RedirectResponse):
        return user
    conn = db()
    row = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return flash_redirect("User not found.", "/users")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    conn.commit()
    conn.close()
    return flash_redirect(f"Password updated for '{row['username']}'.", "/users")
