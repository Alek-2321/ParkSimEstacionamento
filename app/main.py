import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pika
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", "/data/parksim.db")
RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_USER = os.getenv("RABBIT_USER", "parksim_app")
RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "ParkSim2026!")
RABBIT_VHOST = os.getenv("RABBIT_VHOST", "parksim")
QUEUE = "parksim.events"

app = FastAPI(title="ParkSIM", version="2.0.0", description="Controle local de estacionamento por RFID")


@contextmanager
def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_dt():
    return datetime.now().astimezone()


def now_iso():
    return now_dt().isoformat(timespec="seconds")


def money(value):
    return round(float(value) + 1e-9, 2)


def calc_bill(entry_at: str, fixed: float, variable: float, interval_seconds: int, at=None):
    start = datetime.fromisoformat(entry_at)
    end = at or now_dt()
    elapsed = max(0, int((end - start).total_seconds()))
    increments = elapsed // interval_seconds if interval_seconds > 0 else 0
    variable_total = money(increments * variable)

    return {
        "elapsed_seconds": elapsed,
        "elapsed_text": format_duration(elapsed),
        "variable_increments": increments,
        "fixed_amount": money(fixed),
        "variable_unit": money(variable),
        "variable_total": variable_total,
        "total": money(fixed + variable_total),
        "interval_seconds": interval_seconds,
    }


def format_duration(seconds: int):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def publish(event: str, payload: dict):
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASSWORD)

    params = pika.ConnectionParameters(
        host=RABBIT_HOST,
        port=5672,
        virtual_host=RABBIT_VHOST,
        credentials=credentials,
        heartbeat=30,
        blocked_connection_timeout=5,
        connection_attempts=2,
        retry_delay=1,
    )

    body = json.dumps(
        {"event": event, "payload": payload, "at": now_iso()},
        ensure_ascii=False
    )

    try:
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE, durable=True)

        channel.basic_publish(
            exchange="",
            routing_key=QUEUE,
            body=body.encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json"
            ),
            mandatory=True,
        )

        connection.close()
        return True

    except Exception as exc:
        print(f"[WARN] Não foi possível publicar no RabbitMQ: {exc}", flush=True)
        return False


def settings_dict(conn):
    rows = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key,value FROM settings")
    }

    return {
        "fixed_amount": float(rows.get("fixed_amount", "5.00")),
        "variable_amount": float(rows.get("variable_amount", "0.50")),
        "interval_seconds": int(rows.get("interval_seconds", "60")),
    }


def is_locked(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE status <> 'CLOSED'"
    ).fetchone()[0] > 0


def session_view(row):
    item = dict(row)

    if item["status"] != "CLOSED":
        bill_end = (
            datetime.fromisoformat(item["exit_requested_at"])
            if item.get("exit_requested_at")
            else now_dt()
        )

        bill = calc_bill(
            item["entry_at"],
            item["fixed_amount"],
            item["variable_amount"],
            item["interval_seconds"],
            at=bill_end,
        )

        item.update(bill)

    else:
        item["elapsed_seconds"] = item.get("duration_seconds") or 0
        item["elapsed_text"] = format_duration(item["elapsed_seconds"])
        item["fixed_amount"] = item.get("fixed_amount", 0)
        item["variable_total"] = item.get("variable_total", 0)
        item["total"] = item.get("total", 0)

    return item


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_uid TEXT NOT NULL,
            entry_at TEXT NOT NULL,
            exit_requested_at TEXT,
            paid_at TEXT,
            exit_at TEXT,
            fixed_amount REAL NOT NULL,
            variable_amount REAL NOT NULL,
            interval_seconds INTEGER NOT NULL,
            duration_seconds INTEGER,
            variable_increments INTEGER,
            variable_total REAL,
            total REAL,
            status TEXT NOT NULL CHECK(status IN ('PARKED','PAYMENT_PENDING','AUTHORIZED','CLOSED'))
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_uid_status
        ON sessions(card_uid,status);

        CREATE INDEX IF NOT EXISTS idx_sessions_status
        ON sessions(status);

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            paid_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );
        """)

        defaults = {
            "fixed_amount": os.getenv("INITIAL_FIXED_AMOUNT", "5.00"),
            "variable_amount": os.getenv("INITIAL_VARIABLE_AMOUNT", "0.50"),
            "interval_seconds": os.getenv("INITIAL_INTERVAL_SECONDS", "60"),
        }

        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (key, str(value))
            )


@app.on_event("startup")
def startup():
    init_db()


class CardRead(BaseModel):
    uid: str = Field(min_length=1, max_length=64)


class SettingsUpdate(BaseModel):
    fixed_amount: float = Field(ge=0)
    variable_amount: float = Field(ge=0)
    interval_seconds: int = Field(ge=1, le=86400)


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
def health():
    with db() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status <> 'CLOSED'"
        ).fetchone()[0]

    return {
        "status": "ok",
        "service": "parksim-api",
        "active_sessions": active
    }


@app.get("/api/state")
def state():
    with db() as conn:
        settings = settings_dict(conn)
        locked = is_locked(conn)

        active_rows = conn.execute(
            "SELECT * FROM sessions WHERE status <> 'CLOSED' ORDER BY entry_at ASC"
        ).fetchall()

        history_rows = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT 30"
        ).fetchall()

        pending = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status='PAYMENT_PENDING'"
        ).fetchone()[0]

        authorized = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status='AUTHORIZED'"
        ).fetchone()[0]

        paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments"
        ).fetchone()[0]

        active = [session_view(r) for r in active_rows]
        history = [session_view(r) for r in history_rows]

        return {
            "settings": settings,
            "settings_locked": locked,
            "active_vehicles": active,
            "active_count": len(active),
            "payment_pending": pending,
            "authorized_count": authorized,
            "revenue": money(paid),
            "history": history,
        }


@app.get("/api/history")
def history(limit: int = 200):
    limit = min(max(limit, 1), 1000)

    with db() as conn:
        settings = settings_dict(conn)

        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()

        return [session_view(r) for r in rows]


@app.post("/api/card/read")
def read_card(data: CardRead):
    uid = "".join(
        ch for ch in data.uid.upper().strip()
        if ch.isalnum()
    )

    if not uid:
        raise HTTPException(400, "UID do cartão inválido.")

    event = None

    with db() as conn:
        settings = settings_dict(conn)

        active = conn.execute(
            """
            SELECT * FROM sessions
            WHERE card_uid=? AND status <> 'CLOSED'
            ORDER BY id DESC LIMIT 1
            """,
            (uid,)
        ).fetchone()

        if active:
            if active["status"] == "PARKED":
                bill = calc_bill(
                    active["entry_at"],
                    active["fixed_amount"],
                    active["variable_amount"],
                    active["interval_seconds"]
                )

                conn.execute(
                    """
                    UPDATE sessions
                    SET exit_requested_at=?,
                        duration_seconds=?,
                        variable_increments=?,
                        variable_total=?,
                        total=?,
                        status='PAYMENT_PENDING'
                    WHERE id=?
                    """,
                    (
                        now_iso(),
                        bill["elapsed_seconds"],
                        bill["variable_increments"],
                        bill["variable_total"],
                        bill["total"],
                        active["id"]
                    )
                )

                result = {
                    "action": "payment_required",
                    "session_id": active["id"],
                    "card_uid": uid,
                    **bill,
                    "message": "Cartão reconhecido. Cobrança calculada; aguarde a confirmação do operador."
                }

                event = "exit_payment_requested"

            elif active["status"] == "PAYMENT_PENDING":
                result = {
                    "action": "payment_required",
                    "session_id": active["id"],
                    "card_uid": uid,
                    "total": active["total"],
                    "message": "Este cartão já está aguardando confirmação do pagamento."
                }

                event = "exit_payment_pending_repeat"

            else:
                conn.execute(
                    "UPDATE sessions SET status='CLOSED', exit_at=? WHERE id=?",
                    (now_iso(), active["id"])
                )

                result = {
                    "action": "exit_authorized",
                    "session_id": active["id"],
                    "card_uid": uid,
                    "message": "Pagamento confirmado. Saída liberada e passagem encerrada."
                }

                event = "exit_authorized"

        else:
            cur = conn.execute(
                """
                INSERT INTO sessions(
                    card_uid,
                    entry_at,
                    fixed_amount,
                    variable_amount,
                    interval_seconds,
                    status
                )
                VALUES(?,?,?,?,?,'PARKED')
                """,
                (
                    uid,
                    now_iso(),
                    settings["fixed_amount"],
                    settings["variable_amount"],
                    settings["interval_seconds"]
                )
            )

            result = {
                "action": "entry_registered",
                "session_id": cur.lastrowid,
                "card_uid": uid,
                "entry_at": now_iso(),
                "message": "Entrada registrada. O tempo e o valor estão sendo contabilizados."
            }

            event = "entry_registered"

    publish(event, {"uid": uid, **result})

    return result


@app.post("/api/sessions/{session_id}/pay")
def confirm_payment(session_id: int):
    with db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id=?",
            (session_id,)
        ).fetchone()

        if not session:
            raise HTTPException(404, "Passagem não encontrada.")

        if session["status"] == "CLOSED":
            raise HTTPException(409, "Esta passagem já foi encerrada.")

        if session["status"] == "PARKED":
            raise HTTPException(409, "O cartão ainda não solicitou a saída.")

        if session["status"] == "AUTHORIZED":
            return {
                "ok": True,
                "message": "Pagamento já confirmado.",
                "session_id": session_id
            }

        paid_at = now_iso()
        amount = money(session["total"] or 0)

        conn.execute(
            "INSERT INTO payments(session_id,amount,paid_at) VALUES(?,?,?)",
            (session_id, amount, paid_at)
        )

        conn.execute(
            "UPDATE sessions SET paid_at=?,status='AUTHORIZED' WHERE id=?",
            (paid_at, session_id)
        )

    publish(
        "payment_confirmed",
        {"session_id": session_id, "amount": amount}
    )

    return {
        "ok": True,
        "session_id": session_id,
        "amount": amount,
        "message": "Pagamento confirmado. Aproxime novamente o cartão RFID para liberar a saída."
    }


@app.put("/api/settings")
def update_settings(data: SettingsUpdate):
    with db() as conn:
        if is_locked(conn):
            raise HTTPException(
                409,
                "As tarifas ficam bloqueadas enquanto houver veículos ou passagens em andamento. Aguarde todos saírem."
            )

        values = {
            "fixed_amount": str(money(data.fixed_amount)),
            "variable_amount": str(money(data.variable_amount)),
            "interval_seconds": str(data.interval_seconds),
        }

        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO settings(key,value)
                VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value)
            )

    publish("tariff_settings_updated", values)

    return {
        "ok": True,
        "settings": {
            "fixed_amount": float(values["fixed_amount"]),
            "variable_amount": float(values["variable_amount"]),
            "interval_seconds": int(values["interval_seconds"])
        }
    }


@app.get("/api/events")
def events(limit: int = 100):
    limit = min(max(limit, 1), 500)

    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?",
                (limit,)
            )
        ]