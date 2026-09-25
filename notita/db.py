"""Capa de datos: SQLite, sin ORM."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date

from . import config
from .dates import Recurrencia, ahora, de_iso, iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    texto         TEXT    NOT NULL,
    tipo          TEXT    NOT NULL DEFAULT 'casa',      -- casa | compras
    categoria     TEXT    NOT NULL DEFAULT 'otros',
    responsable   TEXT    NOT NULL DEFAULT 'ninguno',   -- axel | barbu | ambos | ninguno
    due_date      TEXT,                                 -- ISO o NULL ("algún día")
    recur_kind    TEXT,                                 -- diaria | semanal | mensual | anual
    recur_interval INTEGER DEFAULT 1,
    recur_weekday INTEGER,
    recur_monthday INTEGER,
    estado        TEXT    NOT NULL DEFAULT 'pendiente', -- pendiente | hecha | borrada
    postpone_count INTEGER NOT NULL DEFAULT 0,
    created_by    TEXT    NOT NULL DEFAULT 'ninguno',
    created_at    TEXT    NOT NULL,
    completed_by  TEXT,
    completed_at  TEXT,
    last_reminded_on TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_estado ON tasks (estado, tipo, due_date);

-- Conversación pendiente por chat: qué está esperando Notita que le contesten.
CREATE TABLE IF NOT EXISTS pending (
    chat_id    INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,   -- fecha | aclaracion
    task_id    INTEGER,
    data       TEXT,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


# --------------------------------------------------------------------------
# Tareas
# --------------------------------------------------------------------------

def crear_tarea(
    chat_id: int,
    texto: str,
    tipo: str = "casa",
    categoria: str = "otros",
    responsable: str = "ninguno",
    due: date | None = None,
    recurrencia: Recurrencia | None = None,
    created_by: str = "ninguno",
) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO tasks (chat_id, texto, tipo, categoria, responsable, due_date,
                                  recur_kind, recur_interval, recur_weekday, recur_monthday,
                                  created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chat_id, texto.strip(), tipo, categoria, responsable, iso(due),
                recurrencia.kind if recurrencia else None,
                recurrencia.interval if recurrencia else 1,
                recurrencia.weekday if recurrencia else None,
                recurrencia.monthday if recurrencia else None,
                created_by, ahora().isoformat(timespec="seconds"),
            ),
        )
        return int(cur.lastrowid)


def obtener(task_id: int) -> sqlite3.Row | None:
    with conn() as c:
        return c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def actualizar(task_id: int, **campos) -> None:
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    with conn() as c:
        c.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*campos.values(), task_id))


def recurrencia_de(row: sqlite3.Row) -> Recurrencia | None:
    if not row["recur_kind"]:
        return None
    return Recurrencia(
        kind=row["recur_kind"],
        interval=row["recur_interval"] or 1,
        weekday=row["recur_weekday"],
        monthday=row["recur_monthday"],
    )


def marcar_hecha(task_id: int, por: str) -> sqlite3.Row | None:
    """Marca hecha y, si es recurrente, deja creada la próxima. Devuelve la nueva."""
    from .dates import hoy, proxima_ocurrencia

    row = obtener(task_id)
    if row is None or row["estado"] != "pendiente":
        return None
    actualizar(
        task_id,
        estado="hecha",
        completed_by=por,
        completed_at=ahora().isoformat(timespec="seconds"),
    )
    rec = recurrencia_de(row)
    if rec is None:
        return None
    base = de_iso(row["due_date"]) or hoy()
    if base < hoy():  # si venía atrasada, la próxima se calcula desde hoy
        base = hoy()
    siguiente = proxima_ocurrencia(rec, base)
    if siguiente is None:
        return None
    nuevo_id = crear_tarea(
        chat_id=row["chat_id"],
        texto=row["texto"],
        tipo=row["tipo"],
        categoria=row["categoria"],
        responsable=row["responsable"],
        due=siguiente,
        recurrencia=rec,
        created_by=row["created_by"],
    )
    return obtener(nuevo_id)


def borrar(task_id: int) -> None:
    actualizar(task_id, estado="borrada")


def posponer(task_id: int, nueva: date) -> int:
    row = obtener(task_id)
    veces = (row["postpone_count"] if row else 0) + 1
    actualizar(task_id, due_date=iso(nueva), postpone_count=veces, last_reminded_on=None)
    return veces


def pendientes(chat_id: int, tipo: str | None = None, categoria: str | None = None):
    q = "SELECT * FROM tasks WHERE chat_id = ? AND estado = 'pendiente'"
    args: list = [chat_id]
    if tipo:
        q += " AND tipo = ?"
        args.append(tipo)
    if categoria:
        q += " AND categoria = ?"
        args.append(categoria)
    q += " ORDER BY (due_date IS NULL), due_date, id"
    with conn() as c:
        return c.execute(q, args).fetchall()


def vencen_hasta(chat_id: int, limite: date):
    with conn() as c:
        return c.execute(
            """SELECT * FROM tasks
               WHERE chat_id = ? AND estado = 'pendiente' AND tipo = 'casa'
                 AND due_date IS NOT NULL AND due_date <= ?
               ORDER BY due_date, id""",
            (chat_id, iso(limite)),
        ).fetchall()


def vencen_entre(chat_id: int, desde: date, hasta: date):
    with conn() as c:
        return c.execute(
            """SELECT * FROM tasks
               WHERE chat_id = ? AND estado = 'pendiente' AND tipo = 'casa'
                 AND due_date BETWEEN ? AND ?
               ORDER BY due_date, id""",
            (chat_id, iso(desde), iso(hasta)),
        ).fetchall()


def sin_fecha(chat_id: int):
    with conn() as c:
        return c.execute(
            """SELECT * FROM tasks
               WHERE chat_id = ? AND estado = 'pendiente' AND tipo = 'casa'
                 AND due_date IS NULL
               ORDER BY id""",
            (chat_id,),
        ).fetchall()


# --------------------------------------------------------------------------
# Estado conversacional
# --------------------------------------------------------------------------

def set_pending(chat_id: int, kind: str, task_id: int | None = None, data: dict | None = None) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO pending (chat_id, kind, task_id, data, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 kind = excluded.kind, task_id = excluded.task_id,
                 data = excluded.data, created_at = excluded.created_at""",
            (chat_id, kind, task_id, json.dumps(data or {}, ensure_ascii=False),
             ahora().isoformat(timespec="seconds")),
        )


def get_pending(chat_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM pending WHERE chat_id = ?", (chat_id,)).fetchone()
    if not row:
        return None
    return {
        "kind": row["kind"],
        "task_id": row["task_id"],
        "data": json.loads(row["data"] or "{}"),
        "created_at": row["created_at"],
    }


def clear_pending(chat_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM pending WHERE chat_id = ?", (chat_id,))
