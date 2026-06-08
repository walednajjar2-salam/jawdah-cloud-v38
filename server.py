#!/usr/bin/env python3
"""
Jawdah Cloud v38 Deploy Ready
A dependency-free Python + SQLite backend for the Jawdah real estate system.
Run: python server.py
Local: http://127.0.0.1:8765 | Cloud: use platform URL
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = Path(os.environ.get("JAWDAH_DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = Path(os.environ.get("JAWDAH_DB_PATH", str(DATA_DIR / "jawdah.sqlite3"))).resolve()
HOST = os.environ.get("JAWDAH_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("JAWDAH_PORT", "8765"))

ROLE_PERMISSIONS = {
    "admin": {"all"},
    "accountant": {"dashboard", "properties:read", "clients:read", "contracts:read", "invoices", "accounts", "reports", "backup:export"},
    "operations": {"dashboard", "properties", "clients", "contracts", "invoices:read", "maintenance", "reports:read"},
    "maintenance": {"dashboard", "properties:read", "maintenance", "reports:read"},
    "viewer": {"dashboard", "properties:read", "clients:read", "contracts:read", "invoices:read", "accounts:read", "maintenance:read", "reports:read", "backup:export"},
}

TABLES = {
    "properties": ["id", "name", "type", "status", "price", "location", "image", "last_update", "notes"],
    "clients": ["id", "name", "phone", "email", "national_id", "balance", "notes"],
    "contracts": ["id", "property_id", "client_id", "start_date", "end_date", "rent_amount", "status", "payment_cycle", "notes"],
    "invoices": ["id", "invoice_no", "contract_id", "client_id", "property_id", "issue_date", "due_date", "description", "amount", "paid_amount", "status"],
    "payments": ["id", "invoice_id", "client_id", "property_id", "contract_id", "payment_date", "amount", "method", "note"],
    "accounts": ["id", "entry_date", "type", "category", "description", "client_id", "property_id", "invoice_id", "amount"],
    "maintenance": ["id", "property_id", "title", "priority", "status", "request_date", "cost", "notes"],
    "users": ["id", "username", "name", "role", "active", "created_at", "last_login"],
    "audit_log": ["id", "created_at", "username", "action", "entity", "entity_id", "details"],
}

WRITE_ROLES = {"admin", "accountant", "operations", "maintenance"}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today() -> str:
    return date.today().isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def password_hash(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, salt, digest = encoded.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        candidate = password_hash(password, salt).split("$", 2)[2]
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS properties (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                location TEXT,
                image TEXT,
                last_update TEXT,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                national_id TEXT,
                balance REAL NOT NULL DEFAULT 0,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS contracts (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                rent_amount REAL NOT NULL,
                status TEXT NOT NULL,
                payment_cycle TEXT NOT NULL DEFAULT 'monthly',
                notes TEXT,
                FOREIGN KEY(property_id) REFERENCES properties(id),
                FOREIGN KEY(client_id) REFERENCES clients(id)
            );
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                invoice_no TEXT UNIQUE NOT NULL,
                contract_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                property_id TEXT NOT NULL,
                issue_date TEXT NOT NULL,
                due_date TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                paid_amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                FOREIGN KEY(contract_id) REFERENCES contracts(id),
                FOREIGN KEY(client_id) REFERENCES clients(id),
                FOREIGN KEY(property_id) REFERENCES properties(id)
            );
            CREATE TABLE IF NOT EXISTS payments (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                property_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                payment_date TEXT NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                note TEXT,
                FOREIGN KEY(invoice_id) REFERENCES invoices(id),
                FOREIGN KEY(client_id) REFERENCES clients(id),
                FOREIGN KEY(property_id) REFERENCES properties(id),
                FOREIGN KEY(contract_id) REFERENCES contracts(id)
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                entry_date TEXT NOT NULL,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                client_id TEXT,
                property_id TEXT,
                invoice_id TEXT,
                amount REAL NOT NULL,
                FOREIGN KEY(client_id) REFERENCES clients(id),
                FOREIGN KEY(property_id) REFERENCES properties(id),
                FOREIGN KEY(invoice_id) REFERENCES invoices(id)
            );
            CREATE TABLE IF NOT EXISTS maintenance (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                title TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                request_date TEXT NOT NULL,
                cost REAL NOT NULL DEFAULT 0,
                notes TEXT,
                FOREIGN KEY(property_id) REFERENCES properties(id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                username TEXT,
                action TEXT NOT NULL,
                entity TEXT NOT NULL,
                entity_id TEXT,
                details TEXT
            );
            """
        )
        seed_if_empty(db)


def insert(db: sqlite3.Connection, table: str, row: Dict[str, Any]) -> None:
    keys = list(row.keys())
    placeholders = ",".join(["?"] * len(keys))
    sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({placeholders})"
    db.execute(sql, [row[k] for k in keys])


def seed_if_empty(db: sqlite3.Connection) -> None:
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        defaults = [
            ("admin", "System Admin", "admin", "admin123"),
            ("accountant", "Accountant", "accountant", "1234"),
            ("operations", "Operations", "operations", "1234"),
            ("maintenance", "Maintenance", "maintenance", "1234"),
            ("viewer", "Viewer", "viewer", "1234"),
        ]
        for username, name, role, pwd in defaults:
            insert(db, "users", {
                "id": uid("USR"), "username": username, "name": name, "role": role,
                "active": 1, "password_hash": password_hash(pwd), "created_at": now_iso(), "last_login": None
            })
    if db.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 0:
        props = [
            {"id":"P-1001","name":"Jawdah Pearl Residence","type":"Apartment","status":"Rented","price":780,"location":"Muscat","image":"🏢","last_update":today(),"notes":"Premium building"},
            {"id":"P-1002","name":"Al Noor Villa","type":"Villa","status":"Vacant","price":1250,"location":"Barka","image":"🏠","last_update":today(),"notes":"Ready for rent"},
            {"id":"P-1003","name":"Hospitality Suite A","type":"Suite","status":"Maintenance","price":650,"location":"Seeb","image":"🏨","last_update":today(),"notes":"AC maintenance"},
        ]
        for p in props:
            insert(db, "properties", p)
        clients = [
            {"id":"C-1001","name":"Oman Hospitality LLC","phone":"96203068","email":"ops@example.com","national_id":"CR-001","balance":0,"notes":"Corporate client"},
            {"id":"C-1002","name":"Mohammed Al Balushi","phone":"92120205","email":"client@example.com","national_id":"ID-002","balance":0,"notes":"Individual client"},
        ]
        for c in clients:
            insert(db, "clients", c)
        contract = {"id":"CT-1001","property_id":"P-1001","client_id":"C-1001","start_date":today(),"end_date":(date.today()+timedelta(days=330)).isoformat(),"rent_amount":780,"status":"Active","payment_cycle":"monthly","notes":"Auto seeded contract"}
        insert(db, "contracts", contract)
        invoice = {"id":"INV-ID-1001","invoice_no":"INV-2026-0001","contract_id":"CT-1001","client_id":"C-1001","property_id":"P-1001","issue_date":today(),"due_date":(date.today()+timedelta(days=10)).isoformat(),"description":"Monthly rent","amount":780,"paid_amount":350,"status":"Partial"}
        insert(db, "invoices", invoice)
        insert(db, "accounts", {"id":"ACC-1001","entry_date":today(),"type":"income","category":"Rent","description":"Partial collection INV-2026-0001","client_id":"C-1001","property_id":"P-1001","invoice_id":"INV-ID-1001","amount":350})
        insert(db, "accounts", {"id":"ACC-1002","entry_date":today(),"type":"expense","category":"Maintenance","description":"AC service","client_id":None,"property_id":"P-1003","invoice_id":None,"amount":80})
        insert(db, "maintenance", {"id":"M-1001","property_id":"P-1003","title":"AC cooling issue","priority":"High","status":"Open","request_date":today(),"cost":80,"notes":"Technician assigned"})
        insert(db, "audit_log", {"id":uid("LOG"),"created_at":now_iso(),"username":"system","action":"seed","entity":"database","entity_id":None,"details":"Initial sample data created"})


def has_permission(user: Dict[str, Any], permission: str) -> bool:
    role = user.get("role")
    perms = ROLE_PERMISSIONS.get(role, set())
    if "all" in perms:
        return True
    if permission in perms:
        return True
    base = permission.split(":", 1)[0]
    if base in perms and not permission.endswith(":delete"):
        return True
    if permission.endswith(":read") and (base in perms or f"{base}:read" in perms):
        return True
    return False


def audit(db: sqlite3.Connection, user: Optional[Dict[str, Any]], action: str, entity: str, entity_id: Optional[str], details: str = "") -> None:
    insert(db, "audit_log", {"id": uid("LOG"), "created_at": now_iso(), "username": (user or {}).get("username"), "action": action, "entity": entity, "entity_id": entity_id, "details": details})


def next_invoice_no(db: sqlite3.Connection) -> str:
    year = date.today().year
    count = db.execute("SELECT COUNT(*) FROM invoices WHERE invoice_no LIKE ?", (f"INV-{year}-%",)).fetchone()[0]
    return f"INV-{year}-{count + 1:04d}"


class JawdahHandler(BaseHTTPRequestHandler):
    server_version = "JawdahCloud/37"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def current_user(self, db: sqlite3.Connection) -> Optional[Dict[str, Any]]:
        auth = self.headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        if not token:
            return None
        row = db.execute(
            """
            SELECT u.id,u.username,u.name,u.role,u.active,u.created_at,u.last_login,s.expires_at
            FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token=? AND u.active=1
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            db.execute("DELETE FROM sessions WHERE token=?", (token,))
            db.commit()
            return None
        return dict(row)

    def require_user(self, db: sqlite3.Connection, permission: Optional[str] = None) -> Optional[Dict[str, Any]]:
        user = self.current_user(db)
        if not user:
            self.send_json({"ok": False, "error": "Authentication required"}, 401)
            return None
        if permission and not has_permission(user, permission):
            self.send_json({"ok": False, "error": "Permission denied"}, 403)
            return None
        return user

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api("GET", parsed.path, parsed.query)
        else:
            self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        self.handle_api("POST", parsed.path, parsed.query)

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        self.handle_api("PUT", parsed.path, parsed.query)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        self.handle_api("DELETE", parsed.path, parsed.query)

    def serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        safe = Path(path.lstrip("/")).as_posix()
        full = (PUBLIC_DIR / safe).resolve()
        if not str(full).startswith(str(PUBLIC_DIR.resolve())) or not full.exists() or full.is_dir():
            self.send_error(404, "File not found")
            return
        raw = full.read_bytes()
        ctype = mimetypes.guess_type(str(full))[0] or "application/octet-stream"
        if full.suffix in {".html", ".css", ".js"}:
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def handle_api(self, method: str, path: str, query: str) -> None:
        try:
            with connect() as db:
                parts = [p for p in path.split("/") if p][1:]
                if not parts:
                    return self.send_json({"ok": True, "version": "v38-cloud-deploy-ready"})
                if parts[0] == "health" and method == "GET":
                    return self.send_json({"ok": True, "status": "healthy", "version": "v38-cloud-deploy-ready", "database": str(DB_PATH)})
                if parts[0] == "login" and method == "POST":
                    return self.api_login(db)
                if parts[0] == "logout" and method == "POST":
                    return self.api_logout(db)
                if parts[0] == "me" and method == "GET":
                    user = self.require_user(db)
                    return None if not user else self.send_json({"ok": True, "user": user, "permissions": sorted(ROLE_PERMISSIONS.get(user["role"], []))})
                if parts[0] == "dashboard" and method == "GET":
                    user = self.require_user(db, "dashboard")
                    return None if not user else self.api_dashboard(db)
                if parts[0] == "bootstrap" and method == "GET":
                    user = self.require_user(db, "dashboard")
                    return None if not user else self.api_bootstrap(db, user)
                if parts[0] == "invoice_from_contract" and method == "POST":
                    user = self.require_user(db, "invoices")
                    return None if not user else self.api_invoice_from_contract(db, user)
                if parts[0] == "pay_invoice" and method == "POST":
                    user = self.require_user(db, "invoices")
                    return None if not user else self.api_pay_invoice(db, user)
                if parts[0] == "backup" and method == "GET":
                    user = self.require_user(db, "backup:export")
                    return None if not user else self.api_backup(db)
                if parts[0] == "restore" and method == "POST":
                    user = self.require_user(db, "admin")
                    return None if not user else self.api_restore(db, user)
                if parts[0] == "export" and method == "GET" and len(parts) >= 2:
                    user = self.require_user(db, "backup:export")
                    return None if not user else self.api_export_csv(db, parts[1])
                if parts[0] in TABLES:
                    return self.api_crud(db, method, parts, query)
                self.send_json({"ok": False, "error": "Unknown endpoint"}, 404)
        except sqlite3.IntegrityError as exc:
            self.send_json({"ok": False, "error": "Database integrity error", "detail": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": "Server error", "detail": str(exc)}, 500)

    def api_login(self, db: sqlite3.Connection) -> None:
        data = self.read_json()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        row = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return self.send_json({"ok": False, "error": "Invalid username or password"}, 401)
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(hours=12)).isoformat()
        db.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)", (token, row["id"], now_iso(), expires))
        db.execute("UPDATE users SET last_login=? WHERE id=?", (now_iso(), row["id"]))
        audit(db, dict(row), "login", "users", row["id"], "User login")
        db.commit()
        user = dict(row)
        user.pop("password_hash", None)
        self.send_json({"ok": True, "token": token, "user": user})

    def api_logout(self, db: sqlite3.Connection) -> None:
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        user = self.current_user(db)
        if token:
            db.execute("DELETE FROM sessions WHERE token=?", (token,))
        if user:
            audit(db, user, "logout", "users", user["id"], "User logout")
        db.commit()
        self.send_json({"ok": True})

    def api_bootstrap(self, db: sqlite3.Connection, user: Dict[str, Any]) -> None:
        data = {}
        for table, cols in TABLES.items():
            if table == "users" and user["role"] != "admin":
                continue
            visible_cols = ",".join(cols)
            data[table] = rows_to_dicts(db.execute(f"SELECT {visible_cols} FROM {table} ORDER BY rowid DESC").fetchall())
        self.send_json({"ok": True, "data": data, "dashboard": build_dashboard(db), "user": user})

    def api_dashboard(self, db: sqlite3.Connection) -> None:
        self.send_json({"ok": True, "dashboard": build_dashboard(db)})

    def api_crud(self, db: sqlite3.Connection, method: str, parts: List[str], query: str) -> None:
        table = parts[0]
        item_id = parts[1] if len(parts) > 1 else None
        perm_base = table
        read_perm = f"{perm_base}:read"
        write_perm = perm_base
        delete_perm = f"{perm_base}:delete"
        if table == "users":
            write_perm = "admin"
            read_perm = "admin"
            delete_perm = "admin"
        user = self.require_user(db, read_perm if method == "GET" else (delete_perm if method == "DELETE" else write_perm))
        if not user:
            return
        visible_cols = TABLES[table]
        if method == "GET":
            cols = ",".join(visible_cols)
            if item_id:
                row = db.execute(f"SELECT {cols} FROM {table} WHERE id=?", (item_id,)).fetchone()
                return self.send_json({"ok": bool(row), "item": dict(row) if row else None})
            return self.send_json({"ok": True, "items": rows_to_dicts(db.execute(f"SELECT {cols} FROM {table} ORDER BY rowid DESC").fetchall())})
        if method in ("POST", "PUT"):
            data = self.read_json()
            if table == "users":
                return self.save_user(db, user, method, data, item_id)
            return self.save_generic(db, user, table, data, item_id, method)
        if method == "DELETE":
            if not item_id:
                return self.send_json({"ok": False, "error": "Missing id"}, 400)
            reason = protected_delete_reason(db, table, item_id)
            if reason:
                return self.send_json({"ok": False, "error": reason}, 400)
            db.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
            audit(db, user, "delete", table, item_id, "Deleted record")
            db.commit()
            return self.send_json({"ok": True})

    def save_user(self, db: sqlite3.Connection, user: Dict[str, Any], method: str, data: Dict[str, Any], item_id: Optional[str]) -> None:
        if method == "POST":
            required = ["username", "name", "role", "password"]
            missing = [k for k in required if not data.get(k)]
            if missing:
                return self.send_json({"ok": False, "error": f"Missing: {', '.join(missing)}"}, 400)
            row = {
                "id": data.get("id") or uid("USR"), "username": data["username"].strip(), "name": data["name"].strip(),
                "role": data["role"], "active": int(bool(data.get("active", True))), "password_hash": password_hash(str(data["password"])),
                "created_at": now_iso(), "last_login": None,
            }
            insert(db, "users", row)
            audit(db, user, "create", "users", row["id"], f"Created user {row['username']}")
            db.commit()
            row.pop("password_hash", None)
            return self.send_json({"ok": True, "item": row})
        if not item_id:
            return self.send_json({"ok": False, "error": "Missing id"}, 400)
        current = db.execute("SELECT * FROM users WHERE id=?", (item_id,)).fetchone()
        if not current:
            return self.send_json({"ok": False, "error": "User not found"}, 404)
        fields = {"username": data.get("username", current["username"]), "name": data.get("name", current["name"]), "role": data.get("role", current["role"]), "active": int(bool(data.get("active", current["active"]))) }
        db.execute("UPDATE users SET username=?,name=?,role=?,active=? WHERE id=?", (fields["username"], fields["name"], fields["role"], fields["active"], item_id))
        if data.get("password"):
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(str(data["password"])), item_id))
        audit(db, user, "update", "users", item_id, f"Updated user {fields['username']}")
        db.commit()
        return self.send_json({"ok": True})

    def save_generic(self, db: sqlite3.Connection, user: Dict[str, Any], table: str, data: Dict[str, Any], item_id: Optional[str], method: str) -> None:
        row_id = item_id or data.get("id") or uid(table[:3].upper())
        data["id"] = row_id
        if table == "contracts":
            if not data.get("property_id") or not data.get("client_id") or float(data.get("rent_amount") or 0) <= 0:
                return self.send_json({"ok": False, "error": "Contract requires property, client, and valid rent amount"}, 400)
            if not exists(db, "properties", data["property_id"]) or not exists(db, "clients", data["client_id"]):
                return self.send_json({"ok": False, "error": "Invalid property or client"}, 400)
        if table == "invoices":
            return self.send_json({"ok": False, "error": "Create invoices from a contract using the invoice action"}, 400)
        if table == "payments":
            return self.send_json({"ok": False, "error": "Create payments from an invoice using the payment action"}, 400)
        if table == "accounts" and float(data.get("amount") or 0) <= 0:
            return self.send_json({"ok": False, "error": "Account entry requires positive amount"}, 400)
        cols = [c for c in TABLES[table] if c in data]
        clean = {c: data.get(c) for c in cols}
        if method == "POST":
            insert(db, table, clean)
            audit(db, user, "create", table, row_id, "Created record")
            db.commit()
            return self.send_json({"ok": True, "item": clean})
        else:
            if not item_id:
                return self.send_json({"ok": False, "error": "Missing id"}, 400)
            update_cols = [c for c in cols if c != "id"]
            if not update_cols:
                return self.send_json({"ok": True})
            sql = f"UPDATE {table} SET {','.join([c+'=?' for c in update_cols])} WHERE id=?"
            db.execute(sql, [clean[c] for c in update_cols] + [item_id])
            audit(db, user, "update", table, item_id, "Updated record")
            db.commit()
            return self.send_json({"ok": True})

    def api_invoice_from_contract(self, db: sqlite3.Connection, user: Dict[str, Any]) -> None:
        data = self.read_json()
        contract_id = data.get("contract_id")
        contract = db.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if not contract:
            return self.send_json({"ok": False, "error": "Contract not found"}, 404)
        amount = float(data.get("amount") or contract["rent_amount"])
        invoice = {
            "id": uid("INV"),
            "invoice_no": next_invoice_no(db),
            "contract_id": contract["id"],
            "client_id": contract["client_id"],
            "property_id": contract["property_id"],
            "issue_date": data.get("issue_date") or today(),
            "due_date": data.get("due_date") or (date.today() + timedelta(days=7)).isoformat(),
            "description": data.get("description") or "Rent invoice",
            "amount": amount,
            "paid_amount": 0,
            "status": "Pending",
        }
        insert(db, "invoices", invoice)
        audit(db, user, "create", "invoices", invoice["id"], f"Invoice {invoice['invoice_no']} from contract {contract_id}")
        db.commit()
        self.send_json({"ok": True, "item": invoice})

    def api_pay_invoice(self, db: sqlite3.Connection, user: Dict[str, Any]) -> None:
        data = self.read_json()
        invoice_id = data.get("invoice_id")
        amount = float(data.get("amount") or 0)
        invoice = db.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not invoice:
            return self.send_json({"ok": False, "error": "Invoice not found"}, 404)
        if amount <= 0:
            return self.send_json({"ok": False, "error": "Payment amount must be positive"}, 400)
        remaining = float(invoice["amount"]) - float(invoice["paid_amount"])
        if amount > remaining + 0.001:
            return self.send_json({"ok": False, "error": "Payment exceeds remaining invoice balance"}, 400)
        new_paid = float(invoice["paid_amount"]) + amount
        status = "Paid" if new_paid >= float(invoice["amount"]) - 0.001 else "Partial"
        payment = {
            "id": uid("PAY"),
            "invoice_id": invoice["id"],
            "client_id": invoice["client_id"],
            "property_id": invoice["property_id"],
            "contract_id": invoice["contract_id"],
            "payment_date": data.get("payment_date") or today(),
            "amount": amount,
            "method": data.get("method") or "Cash",
            "note": data.get("note") or "Invoice payment",
        }
        account = {
            "id": uid("ACC"), "entry_date": payment["payment_date"], "type": "income", "category": "Collection",
            "description": f"Payment for {invoice['invoice_no']}", "client_id": invoice["client_id"], "property_id": invoice["property_id"],
            "invoice_id": invoice["id"], "amount": amount,
        }
        insert(db, "payments", payment)
        insert(db, "accounts", account)
        db.execute("UPDATE invoices SET paid_amount=?, status=? WHERE id=?", (new_paid, status, invoice["id"]))
        audit(db, user, "pay", "invoices", invoice["id"], f"Collected {amount} for {invoice['invoice_no']}")
        db.commit()
        self.send_json({"ok": True, "payment": payment, "status": status, "paid_amount": new_paid})

    def api_backup(self, db: sqlite3.Connection) -> None:
        payload = {"version": "v38-cloud-deploy-ready", "exported_at": now_iso(), "tables": {}}
        for table, cols in TABLES.items():
            payload["tables"][table] = rows_to_dicts(db.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall())
        self.send_json({"ok": True, "backup": payload})

    def api_restore(self, db: sqlite3.Connection, user: Dict[str, Any]) -> None:
        data = self.read_json()
        backup = data.get("backup") or data
        tables = backup.get("tables", {})
        mode = data.get("mode") or "merge"
        if mode == "replace":
            for table in ["audit_log","maintenance","accounts","payments","invoices","contracts","clients","properties"]:
                db.execute(f"DELETE FROM {table}")
        for table, items in tables.items():
            if table not in TABLES or table == "users":
                continue
            for item in items:
                cols = [c for c in TABLES[table] if c in item]
                if not cols or not item.get("id"):
                    continue
                values = [item[c] for c in cols]
                placeholders = ",".join(["?"] * len(cols))
                updates = ",".join([f"{c}=excluded.{c}" for c in cols if c != "id"])
                db.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}", values)
        audit(db, user, "restore", "database", None, f"Restore mode {mode}")
        db.commit()
        self.send_json({"ok": True})

    def api_export_csv(self, db: sqlite3.Connection, table: str) -> None:
        if table not in TABLES:
            return self.send_json({"ok": False, "error": "Unknown table"}, 404)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=TABLES[table])
        writer.writeheader()
        for row in rows_to_dicts(db.execute(f"SELECT {','.join(TABLES[table])} FROM {table}").fetchall()):
            writer.writerow(row)
        raw = output.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename=jawdah-{table}.csv")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def exists(db: sqlite3.Connection, table: str, row_id: str) -> bool:
    return db.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone() is not None


def protected_delete_reason(db: sqlite3.Connection, table: str, row_id: str) -> str:
    checks = {
        "properties": [("contracts", "property_id", "Property has contracts"), ("invoices", "property_id", "Property has invoices"), ("accounts", "property_id", "Property has accounts")],
        "clients": [("contracts", "client_id", "Client has contracts"), ("invoices", "client_id", "Client has invoices"), ("accounts", "client_id", "Client has accounts")],
        "contracts": [("invoices", "contract_id", "Contract has invoices")],
        "invoices": [("payments", "invoice_id", "Invoice has payments"), ("accounts", "invoice_id", "Invoice has accounts")],
    }
    for child, col, msg in checks.get(table, []):
        if db.execute(f"SELECT 1 FROM {child} WHERE {col}=? LIMIT 1", (row_id,)).fetchone():
            return msg
    return ""


def build_dashboard(db: sqlite3.Connection) -> Dict[str, Any]:
    prop_total = db.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    rented = db.execute("SELECT COUNT(*) FROM properties WHERE lower(status) LIKE '%rented%' OR lower(status) LIKE '%leased%'").fetchone()[0]
    vacant = db.execute("SELECT COUNT(*) FROM properties WHERE lower(status) LIKE '%vacant%'").fetchone()[0]
    maintenance_count = db.execute("SELECT COUNT(*) FROM maintenance WHERE lower(status) NOT IN ('closed','done','completed')").fetchone()[0]
    invoices = rows_to_dicts(db.execute("SELECT * FROM invoices").fetchall())
    accounts = rows_to_dicts(db.execute("SELECT * FROM accounts").fetchall())
    income = sum(float(a["amount"] or 0) for a in accounts if a["type"] == "income")
    expense = sum(float(a["amount"] or 0) for a in accounts if a["type"] == "expense")
    billed = sum(float(i["amount"] or 0) for i in invoices)
    paid = sum(float(i["paid_amount"] or 0) for i in invoices)
    overdue = sum(max(0, float(i["amount"] or 0) - float(i["paid_amount"] or 0)) for i in invoices if i["status"] != "Paid" and i["due_date"] < today())
    occupancy = round((rented / prop_total * 100), 1) if prop_total else 0
    months = []
    for m in range(5, -1, -1):
        d = date.today().replace(day=1) - timedelta(days=31*m)
        key = d.strftime("%Y-%m")
        month_income = sum(float(a["amount"] or 0) for a in accounts if a["type"] == "income" and str(a["entry_date"]).startswith(key))
        month_expense = sum(float(a["amount"] or 0) for a in accounts if a["type"] == "expense" and str(a["entry_date"]).startswith(key))
        months.append({"month": key, "income": month_income, "expense": month_expense})
    health = 100
    if overdue > 0: health -= 15
    if maintenance_count > 0: health -= min(20, maintenance_count * 5)
    if occupancy < 70: health -= 15
    health = max(0, min(100, health))
    decisions = []
    if overdue > 0:
        decisions.append({"level":"High","text":"Follow overdue invoices before closing the month"})
    if vacant > 0:
        decisions.append({"level":"Medium","text":"Market vacant properties to improve occupancy"})
    if maintenance_count > 0:
        decisions.append({"level":"Medium","text":"Close open maintenance requests to protect service quality"})
    if not decisions:
        decisions.append({"level":"Good","text":"Operations look stable today"})
    return {
        "kpis": {
            "properties": prop_total, "rented": rented, "vacant": vacant, "maintenance": maintenance_count,
            "income": income, "expense": expense, "net": income - expense, "billed": billed, "paid": paid,
            "overdue": overdue, "occupancy": occupancy, "health": health,
        },
        "series": months,
        "decisions": decisions,
    }


def main() -> None:
    init_db()
    print(f"Jawdah Cloud v38 is running on http://{HOST}:{PORT}")
    print(f"Database: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), JawdahHandler).serve_forever()


if __name__ == "__main__":
    main()
