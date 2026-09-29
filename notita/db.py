"""Capa de datos: SQLite, sin ORM."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import date

from . import config
from .dates import Recurrencia, ahora, de_iso, iso

log = logging.getLogger("notita.db")

# Después de esto, una pregunta sin contestar se da por perdida. Una aclaración vence
# rápido: si contestás cuatro horas después, no estás aclarando nada, estás escribiendo
# un mensaje nuevo (y mezclarlos hace que el bot reprocese el anterior).
PENDING_HORAS = 6
PENDING_MINUTOS_ACLARACION = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    texto         TEXT    NOT NULL,
    compra        INTEGER NOT NULL DEFAULT 0,           -- la etiqueta 🛒, nada más
    categoria     TEXT    NOT NULL DEFAULT 'otros',
    responsable   TEXT    NOT NULL DEFAULT 'ninguno',   -- axel | barbu | ambos | ninguno
    due_date      TEXT,                                 -- ISO o NULL ("algún día")
    due_hora      TEXT,                                 -- "HH:MM" si dijeron la hora
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
    mensaje_origen_id INTEGER                            -- de qué mensaje salió (deshacer)
);
CREATE INDEX IF NOT EXISTS idx_tasks_estado ON tasks (estado, due_date);

-- Mensajes que no se pudieron mandar (el proxy de PythonAnywhere falla cada tanto).
-- Se reintentan en la próxima oportunidad, así una confirmación no se pierde nunca.
CREATE TABLE IF NOT EXISTS salientes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    texto      TEXT    NOT NULL,
    teclado    TEXT,
    creado_en  TEXT    NOT NULL,
    intentos   INTEGER NOT NULL DEFAULT 1
);

-- Los update_id que ya procesamos, para no repetir si Telegram reenvía.
CREATE TABLE IF NOT EXISTS updates_vistos (
    update_id TEXT PRIMARY KEY,   -- update_id o callback_query.id
    visto_en  TEXT NOT NULL
);

-- Lo que Notita está esperando que le contesten, SÓLO como respuesta a un
-- force_reply (v1 se comía el mensaje siguiente cualquiera).
CREATE TABLE IF NOT EXISTS pending (
    chat_id    INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,   -- renombrar | fecha_item
    task_id    INTEGER,
    data       TEXT,
    created_at TEXT NOT NULL
);

-- El tablero fijado: un solo mensaje por chat, que se edita en el lugar.
CREATE TABLE IF NOT EXISTS tablero (
    chat_id    INTEGER PRIMARY KEY,
    message_id INTEGER,
    editado_en TEXT,
    sucio      INTEGER NOT NULL DEFAULT 0
);

-- Menús, secciones expandidas y propuestas: se borran solos.
CREATE TABLE IF NOT EXISTS mensajes_temporales (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    tipo       TEXT    NOT NULL,
    expira_at  TEXT    NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

-- Lo que un texto PROPUSO modificar. No se ejecuta hasta que alguien toca.
CREATE TABLE IF NOT EXISTS propuestas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    payload    TEXT    NOT NULL,
    expira_at  TEXT    NOT NULL
);

-- Qué ítems creó un mensaje, para poder deshacerlos.
CREATE TABLE IF NOT EXISTS deshacer (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    item_ids   TEXT    NOT NULL,
    accion     TEXT    NOT NULL DEFAULT 'crear',
    expira_at  TEXT    NOT NULL
);

-- El parte sale una sola vez por día, por más que el cron llame mil veces.
CREATE TABLE IF NOT EXISTS partes_enviados (
    fecha      TEXT PRIMARY KEY,
    enviado_en TEXT NOT NULL
);

-- Notita en pausa: hasta cuándo no manda el parte.
CREATE TABLE IF NOT EXISTS pausa (
    chat_id INTEGER PRIMARY KEY,
    hasta   TEXT NOT NULL
);

-- Banderitas sueltas (si ya se mandó la bienvenida de v2, etc).
CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT
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


# Columnas agregadas después de la primera versión. `CREATE TABLE IF NOT EXISTS` no
# las agrega a una base que ya existe, así que hay que pedirlas explícitamente.
COLUMNAS_AGREGADAS = {
    "tasks": {
        "due_hora": "TEXT",
        "mensaje_origen_id": "INTEGER",
        "compra": "INTEGER NOT NULL DEFAULT 0",
    },
}

# Columnas que ya no se usan. `tipo` era casa/compras/recado: ahora hay una sola
# clase de cosa y la etiqueta 🛒 es un booleano.
COLUMNAS_MUERTAS = {"tasks": ("recordada_veces", "last_reminded_on", "tipo")}


VERSION_ESQUEMA = 3      # 3: una sola clase de cosa, con la etiqueta 🛒


def init_db() -> None:
    # Antes de abrir la transacción: copiar el archivo con una escritura a medias
    # daría un respaldo inservible.
    _respaldar_si_hace_falta()
    with conn() as c:
        c.executescript(SCHEMA)
        for tabla, columnas in COLUMNAS_AGREGADAS.items():
            existentes = {f["name"] for f in c.execute(f"PRAGMA table_info({tabla})")}
            for nombre, tipo in columnas.items():
                if nombre not in existentes:
                    c.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
        # Los arreglos de FORMA van acá, fuera de la migración versionada: son
        # idempotentes y tienen que correr siempre. Cuando este estaba adentro, la
        # base que ya había migrado salía por el `return` de arriba y no lo recibía
        # nunca: en producción quedó con la tabla vieja y todos los botones muertos.
        _rehacer_updates_vistos(c)
        _tipo_a_compra(c)                 # las dos necesitan `tipo`…
        _migrar_a_v2(c)
        _limpiar_columnas_muertas(c)      # …así que recién acá se puede borrar


def _respaldar_si_hace_falta() -> None:
    """Copia la base antes de tocarle la forma. Nunca pisa un respaldo.

    Hace falta en dos casos, y los dos importan:

    1. Hay una migración de esquema por delante.
    2. Quedan columnas muertas por borrar. Puede pasar con el esquema al día: en
       producción el `DROP COLUMN` falló (un índice viejo lo bloqueaba) y la
       migración se anotó como terminada igual.

    La primera versión de esto sólo respaldaba si la base era de v1, así que la
    migración que borra una columna iba a correr sin red. Y el nombre era fijo
    (`.v1.bak`): se habría pisado la única copia de esos datos.
    """
    import shutil
    from pathlib import Path

    base = Path(config.DB_PATH)
    if not base.exists() or str(config.DB_PATH) == ":memory:":
        return
    try:
        desde = int(_esquema_guardado() or 1)
    except (ValueError, TypeError):
        desde = 1
    motivo = None
    if desde < VERSION_ESQUEMA:
        motivo = f"v{VERSION_ESQUEMA}"
    elif _hay_columnas_muertas():
        motivo = "limpiar"
    if not motivo:
        return
    try:
        respaldo = _nombre_de_respaldo(base, motivo)
        # `copy` y no `copy2`: copy2 conserva la fecha del original, así que un
        # `ls -la` mostraba cuándo se tocó la base por última vez y no cuándo se hizo
        # la copia. Eligiendo un respaldo apurado, eso confunde. Los permisos sí se
        # copian (copymode), que es lo que importa.
        shutil.copy(base, respaldo)
        log.info("Respaldo antes de tocar la base (%s): %s", motivo, respaldo)
    except OSError as e:
        log.error("No pude respaldar la base: %s", e)


def _hay_columnas_muertas() -> bool:
    try:
        with conn() as c:
            existentes = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    except sqlite3.Error:
        return False
    return bool(existentes & set(COLUMNAS_MUERTAS["tasks"]))


def _esquema_guardado() -> str | None:
    """La versión de esquema anotada, o None si la base es de v1 o está vacía."""
    try:
        with conn() as c:
            fila = c.execute(
                "SELECT valor FROM ajustes WHERE clave = 'esquema'").fetchone()
        return fila["valor"] if fila else None
    except sqlite3.Error:
        return None                     # ni siquiera existe `ajustes`


def _nombre_de_respaldo(base, motivo: str):
    """`notita.db.antes-de-v3.2026-09-29.bak`, y nunca uno que ya exista.

    Lleva fecha porque ya hubo un respaldo antes (`.v1.bak`) y pisarlo sería perder
    la única copia de esos datos.
    """
    fecha = ahora().date().isoformat()
    candidato = base.with_name(f"{base.name}.antes-de-{motivo}.{fecha}.bak")
    intento = 2
    while candidato.exists():
        candidato = base.with_name(
            f"{base.name}.antes-de-{motivo}.{fecha}-{intento}.bak")
        intento += 1
    return candidato


def _migrar_a_v2(c) -> None:
    """Lo que v2 necesita de una base de v1.

    - Los recados diferidos no existen más: se descartan (queda registrado cuántos
      eran, para poder decirlo en el mensaje de bienvenida).
    - Se borran las columnas del auto-posponer y del recordatorio por tarea.
    """
    ya = c.execute("SELECT valor FROM ajustes WHERE clave = 'esquema'").fetchone()
    if ya and ya["valor"] == str(VERSION_ESQUEMA):
        return

    columnas = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    recados = 0
    if "tipo" in columnas:
        recados = c.execute(
            "SELECT COUNT(*) n FROM tasks WHERE tipo = 'recado' AND estado = 'pendiente'"
        ).fetchone()["n"]
    if recados:
        c.execute("UPDATE tasks SET estado = 'borrada' "
                  "WHERE tipo = 'recado' AND estado = 'pendiente'")
        c.execute("INSERT OR REPLACE INTO ajustes (clave, valor) VALUES (?,?)",
                  ("recados_descartados", str(recados)))
        log.info("v2: descarté %d recado(s) diferido(s) pendiente(s)", recados)

    c.execute("INSERT OR REPLACE INTO ajustes (clave, valor) VALUES (?,?)",
              ("esquema", str(VERSION_ESQUEMA)))


def _limpiar_columnas_muertas(c) -> None:
    """Borra las columnas que ya no se usan. Corre siempre, no en la migración.

    Dos razones para que esté acá y no dentro de `_migrar_a_v2`:

    1. La migración sale temprano si la base ya está en la versión actual, así que una
       base que quedó a medias no se arreglaba nunca (ya nos pasó con `updates_vistos`).
    2. Un índice que menciona la columna **bloquea** el DROP:
       «error in index idx_tasks_estado after drop column: no such column: tipo».
       Y `CREATE INDEX IF NOT EXISTS` no redefine un índice que ya existe, así que el
       índice viejo sobrevive a un cambio de esquema. Hay que borrarlo a mano y
       dejar que el esquema lo vuelva a crear.
    """
    existentes = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    muertas = [col for col in COLUMNAS_MUERTAS["tasks"] if col in existentes]
    if not muertas:
        return

    indices = c.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='tasks'"
    ).fetchall()
    for indice in indices:
        sql = indice["sql"] or ""       # los de UNIQUE implícitos no tienen sql
        if any(col in sql for col in muertas):
            c.execute(f"DROP INDEX IF EXISTS {indice['name']}")
            log.info("Borré el índice %s: mencionaba una columna muerta",
                     indice["name"])

    for columna in muertas:
        try:
            c.execute(f"ALTER TABLE tasks DROP COLUMN {columna}")
            log.info("Borré la columna muerta %s", columna)
        except sqlite3.OperationalError as e:
            # SQLite < 3.35 no sabe borrar columnas. No es grave: quedan sin usar.
            log.info("No pude borrar %s (%s). Queda sin usar.", columna, e)

    # El esquema define los índices con IF NOT EXISTS: los que borré se recrean acá,
    # ya con la definición nueva.
    for sentencia in SCHEMA.split(";"):
        if "CREATE INDEX" in sentencia.upper():
            c.execute(sentencia)


def _tipo_a_compra(c) -> None:
    """Lo que estaba en la lista del súper pasa a tener la etiqueta 🛒.

    El texto no se toca: los ítems viejos ya dicen «Leche», «Yerba». Corre una sola
    vez, porque después alguien puede sacarle la etiqueta a mano y no queremos
    volver a pisarla.
    """
    columnas = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    if "tipo" not in columnas or "compra" not in columnas:
        return
    ya = c.execute("SELECT valor FROM ajustes WHERE clave = 'compras_migradas'").fetchone()
    if ya:
        return
    cur = c.execute("UPDATE tasks SET compra = 1 WHERE tipo = 'compras'")
    c.execute("INSERT OR REPLACE INTO ajustes (clave, valor) VALUES (?,?)",
              ("compras_migradas", str(cur.rowcount or 0)))
    if cur.rowcount:
        log.info("Etiqueté %d cosa(s) como compra", cur.rowcount)


def _rehacer_updates_vistos(c) -> None:
    """En v1 `update_id` era INTEGER; en v2 guarda también los `cb:<id>` de los toques.

    `CREATE TABLE IF NOT EXISTS` no cambia una tabla que ya existe, así que en una base
    de v1 la columna seguía siendo INTEGER, SQLite rechazaba el texto y TODOS los
    botones se descartaban en silencio. La tabla es sólo un caché para no procesar dos
    veces lo mismo: rehacerla no pierde nada.
    """
    tipos = {f["name"]: (f["type"] or "").upper()
             for f in c.execute("PRAGMA table_info(updates_vistos)")}
    if tipos.get("update_id") == "TEXT":
        return
    log.info("Rehaciendo updates_vistos: la columna era %s", tipos.get("update_id"))
    c.execute("DROP TABLE IF EXISTS updates_vistos")
    c.execute("""CREATE TABLE updates_vistos (
                     update_id TEXT PRIMARY KEY,
                     visto_en  TEXT NOT NULL
                 )""")


def ajuste(clave: str, valor: str | None = None) -> str | None:
    """Lee o escribe una banderita. Con `valor`, escribe y devuelve lo nuevo."""
    with conn() as c:
        if valor is not None:
            c.execute("INSERT OR REPLACE INTO ajustes (clave, valor) VALUES (?,?)",
                      (clave, valor))
            return valor
        fila = c.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
        return fila["valor"] if fila else None


# --------------------------------------------------------------------------
# Tareas
# --------------------------------------------------------------------------

def crear_tarea(
    chat_id: int,
    texto: str,
    compra: bool = False,
    categoria: str = "otros",
    responsable: str = "ninguno",
    due: date | None = None,
    recurrencia: Recurrencia | None = None,
    created_by: str = "ninguno",
    hora: str | None = None,
    mensaje_origen_id: int | None = None,
) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO tasks (chat_id, texto, compra, categoria, responsable, due_date,
                                  due_hora, mensaje_origen_id,
                                  recur_kind, recur_interval, recur_weekday, recur_monthday,
                                  created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chat_id, " ".join(texto.split()), 1 if compra else 0, categoria,
                responsable, iso(due),
                hora, mensaje_origen_id,
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
        compra=bool(row["compra"]),
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


def pendientes(chat_id: int, compra: bool | None = None,
               categoria: str | None = None):
    q = "SELECT * FROM tasks WHERE chat_id = ? AND estado = 'pendiente'"
    args: list = [chat_id]
    if compra is not None:
        q += " AND compra = ?"
        args.append(1 if compra else 0)
    if categoria:
        q += " AND categoria = ?"
        args.append(categoria)
    q += " ORDER BY (due_date IS NULL), due_date, id"
    with conn() as c:
        return c.execute(q, args).fetchall()







def puntaje(referencia: str, texto: str) -> float:
    """Qué tanto se parecen, de 0 a 1. Sirve para «borrá la de la heladera»."""
    from difflib import SequenceMatcher

    from .dates import normalizar

    a, b = normalizar(referencia), normalizar(texto)
    if not a or not b:
        return 0.0
    # Uno contenido en el otro, pero respetando los límites de palabra: sin esto,
    # «ya compré el pan» tachaba «comprar pantuflas» con puntaje perfecto.
    if _contiene_palabra(a, b) or _contiene_palabra(b, a):
        return 1.0
    palabras_a = {p for p in a.split() if len(p) > 2}
    palabras_b = {p for p in b.split() if len(p) > 2}
    solape = len(palabras_a & palabras_b) / len(palabras_a) if palabras_a else 0.0
    return max(solape, SequenceMatcher(None, a, b).ratio())


def _contiene_palabra(aguja: str, pajar: str) -> bool:
    import re

    return re.search(rf"\b{re.escape(aguja)}\b", pajar) is not None


def buscar(chat_id: int, referencia: str, minimo: float = 0.5) -> list[tuple[float, sqlite3.Row]]:
    """Tareas pendientes que se parezcan a `referencia`, de la más parecida a la menos.

    """
    candidatas = [(puntaje(referencia, r["texto"]), r) for r in pendientes(chat_id)]
    return sorted([c for c in candidatas if c[0] >= minimo], key=lambda c: -c[0])


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

# Después de tantos intentos fallidos, el mensaje se descarta: si no, una cola vieja
# se le vuelca encima al grupo días después.
INTENTOS_SALIENTE = 5


def encolar_saliente(chat_id: int, texto: str, teclado: list | None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO salientes (chat_id, texto, teclado, creado_en) VALUES (?,?,?,?)",
            (chat_id, texto, json.dumps(teclado) if teclado else None,
             ahora().isoformat(timespec="seconds")))
        return int(cur.lastrowid)


def salientes_pendientes(chat_id: int | None = None, limite: int = 5):
    """Los que quedaron sin mandar, del más viejo al más nuevo."""
    with conn() as c:
        if chat_id is None:
            return c.execute("SELECT * FROM salientes ORDER BY id LIMIT ?",
                             (limite,)).fetchall()
        return c.execute("SELECT * FROM salientes WHERE chat_id = ? ORDER BY id LIMIT ?",
                         (chat_id, limite)).fetchall()


def borrar_saliente(saliente_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM salientes WHERE id = ?", (saliente_id,))


def sumar_intento_saliente(saliente_id: int) -> int:
    """Suma un intento y devuelve cuántos van. Si ya son demasiados, lo borra."""
    with conn() as c:
        c.execute("UPDATE salientes SET intentos = intentos + 1 WHERE id = ?",
                  (saliente_id,))
        fila = c.execute("SELECT intentos FROM salientes WHERE id = ?",
                         (saliente_id,)).fetchone()
        intentos = fila["intentos"] if fila else INTENTOS_SALIENTE
        if intentos >= INTENTOS_SALIENTE:
            c.execute("DELETE FROM salientes WHERE id = ?", (saliente_id,))
    return intentos


MAX_UPDATES_VISTOS = 2000


def update_nuevo(clave: str | int | None) -> bool:
    """True si esto no se procesó antes. Lo registra de paso.

    La clave puede ser un update_id o un `cb:<callback_query.id>`: los callbacks
    necesitan la misma garantía (dos toques del mismo botón no pueden tener efecto
    doble). El INSERT es la guarda: si dos workers procesan lo mismo a la vez, la
    clave primaria hace que uno solo gane.
    """
    if not clave:
        return True          # sin id (tests, llamadas internas): siempre pasa
    with conn() as c:
        try:
            c.execute("INSERT INTO updates_vistos (update_id, visto_en) VALUES (?,?)",
                      (str(clave), ahora().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            # Sólo es repetido si la fila está de verdad. Un «datatype mismatch»
            # también llega por acá, y tomarlo por repetido hacía que se descartaran
            # TODOS los toques de botón sin decir nada.
            ya_estaba = c.execute("SELECT 1 FROM updates_vistos WHERE update_id = ?",
                                  (str(clave),)).fetchone()
            if ya_estaba:
                return False
            log.error("No pude registrar el update %s, lo proceso igual", clave)
            return True
        # No hace falta guardar la historia entera: se recortan los más viejos.
        c.execute("""DELETE FROM updates_vistos WHERE rowid NOT IN
                     (SELECT rowid FROM updates_vistos ORDER BY visto_en DESC LIMIT ?)""",
                  (MAX_UPDATES_VISTOS,))
    return True


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
    """La pregunta que Notita dejó abierta, si todavía tiene sentido contestarla."""
    from datetime import datetime, timedelta

    with conn() as c:
        row = c.execute("SELECT * FROM pending WHERE chat_id = ?", (chat_id,)).fetchone()
    if not row:
        return None
    # Una pregunta de hace días ya no es una pregunta: si sigue viva, se come el
    # primer mensaje que mencione una fecha.
    try:
        nacio = datetime.fromisoformat(row["created_at"])
    except (TypeError, ValueError):
        nacio = None
    vida = (timedelta(minutes=PENDING_MINUTOS_ACLARACION) if row["kind"] == "aclaracion"
            else timedelta(hours=PENDING_HORAS))
    if nacio and ahora() - nacio > vida:
        clear_pending(chat_id)
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


# --------------------------------------------------------------------------
# v2: tablero, mensajes temporales, propuestas, deshacer, parte y pausa
# --------------------------------------------------------------------------

def tablero_actual(chat_id: int) -> dict | None:
    with conn() as c:
        fila = c.execute("SELECT * FROM tablero WHERE chat_id = ?", (chat_id,)).fetchone()
    return dict(fila) if fila else None


def guardar_tablero(chat_id: int, message_id: int | None, sucio: bool = False) -> None:
    with conn() as c:
        c.execute("""INSERT INTO tablero (chat_id, message_id, editado_en, sucio)
                     VALUES (?,?,?,?)
                     ON CONFLICT(chat_id) DO UPDATE SET
                       message_id = excluded.message_id,
                       editado_en = excluded.editado_en,
                       sucio = excluded.sucio""",
                  (chat_id, message_id, ahora().isoformat(timespec="seconds"),
                   1 if sucio else 0))


def marcar_tablero_sucio(chat_id: int, sucio: bool = True) -> None:
    with conn() as c:
        c.execute("UPDATE tablero SET sucio = ? WHERE chat_id = ?",
                  (1 if sucio else 0, chat_id))


def anotar_temporal(chat_id: int, message_id: int, tipo: str, minutos: int) -> None:
    from datetime import timedelta

    with conn() as c:
        c.execute("""INSERT INTO mensajes_temporales (chat_id, message_id, tipo, expira_at)
                     VALUES (?,?,?,?)
                     ON CONFLICT(chat_id, message_id) DO UPDATE SET
                       tipo = excluded.tipo, expira_at = excluded.expira_at""",
                  (chat_id, message_id, tipo,
                   (ahora() + timedelta(minutes=minutos)).isoformat(timespec="seconds")))


def renovar_temporal(chat_id: int, message_id: int, minutos: int) -> None:
    """El mensaje de compras se usa mientras se compra: cada toque le da más vida."""
    from datetime import timedelta

    with conn() as c:
        c.execute("UPDATE mensajes_temporales SET expira_at = ? "
                  "WHERE chat_id = ? AND message_id = ?",
                  ((ahora() + timedelta(minutes=minutos)).isoformat(timespec="seconds"),
                   chat_id, message_id))


def temporales_vencidos(limite: int = 20):
    with conn() as c:
        return c.execute("SELECT * FROM mensajes_temporales WHERE expira_at <= ? LIMIT ?",
                         (ahora().isoformat(timespec="seconds"), limite)).fetchall()


def olvidar_temporal(chat_id: int, message_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM mensajes_temporales WHERE chat_id = ? AND message_id = ?",
                  (chat_id, message_id))


def guardar_propuesta(chat_id: int, payload: dict, minutos: int = 30) -> int:
    from datetime import timedelta

    with conn() as c:
        cur = c.execute("INSERT INTO propuestas (chat_id, payload, expira_at) VALUES (?,?,?)",
                        (chat_id, json.dumps(payload, ensure_ascii=False),
                         (ahora() + timedelta(minutes=minutos)).isoformat(timespec="seconds")))
        return int(cur.lastrowid)


def leer_propuesta(propuesta_id: int) -> dict | None:
    with conn() as c:
        fila = c.execute("SELECT * FROM propuestas WHERE id = ?", (propuesta_id,)).fetchone()
    if not fila or fila["expira_at"] <= ahora().isoformat(timespec="seconds"):
        return None
    return json.loads(fila["payload"])


def borrar_propuesta(propuesta_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM propuestas WHERE id = ?", (propuesta_id,))


def limpiar_propuestas() -> int:
    with conn() as c:
        cur = c.execute("DELETE FROM propuestas WHERE expira_at <= ?",
                        (ahora().isoformat(timespec="seconds"),))
        return cur.rowcount or 0


def guardar_deshacer(chat_id: int, item_ids: list[int], accion: str = "crear",
                     minutos: int = 10) -> int:
    from datetime import timedelta

    with conn() as c:
        cur = c.execute("INSERT INTO deshacer (chat_id, item_ids, accion, expira_at) "
                        "VALUES (?,?,?,?)",
                        (chat_id, json.dumps(item_ids), accion,
                         (ahora() + timedelta(minutes=minutos)).isoformat(timespec="seconds")))
        return int(cur.lastrowid)


def leer_deshacer(deshacer_id: int) -> dict | None:
    with conn() as c:
        fila = c.execute("SELECT * FROM deshacer WHERE id = ?", (deshacer_id,)).fetchone()
    if not fila or fila["expira_at"] <= ahora().isoformat(timespec="seconds"):
        return None
    return {"id": fila["id"], "chat_id": fila["chat_id"], "accion": fila["accion"],
            "item_ids": json.loads(fila["item_ids"])}


def ultimo_deshacer(chat_id: int) -> dict | None:
    """El deshacer más reciente que siga vivo. Lo ofrece el tablero.

    Un ✅ mal tocado no tenía vuelta atrás: la tarea desaparecía del tablero y había
    que anotarla de nuevo.
    """
    with conn() as c:
        fila = c.execute("SELECT * FROM deshacer WHERE chat_id = ? AND expira_at > ? "
                         "ORDER BY id DESC LIMIT 1",
                         (chat_id, ahora().isoformat(timespec="seconds"))).fetchone()
    if not fila:
        return None
    return {"id": fila["id"], "chat_id": fila["chat_id"], "accion": fila["accion"],
            "item_ids": json.loads(fila["item_ids"])}


def borrar_deshacer(deshacer_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM deshacer WHERE id = ?", (deshacer_id,))


def vencer_deshacer_de(item_ids: list[int]) -> list[int]:
    """Modificar un ítem vence el deshacer que lo incluía. Devuelve los que vencieron."""
    if not item_ids:
        return []
    vencidos = []
    with conn() as c:
        for fila in c.execute("SELECT id, item_ids FROM deshacer").fetchall():
            if set(json.loads(fila["item_ids"])) & set(item_ids):
                vencidos.append(fila["id"])
        for did in vencidos:
            c.execute("DELETE FROM deshacer WHERE id = ?", (did,))
        c.execute("DELETE FROM deshacer WHERE expira_at <= ?",
                  (ahora().isoformat(timespec="seconds"),))
    return vencidos


def parte_ya_enviado(fecha: date) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM partes_enviados WHERE fecha = ?",
                         (iso(fecha),)).fetchone() is not None


def anotar_parte(fecha: date) -> bool:
    """Marca el parte del día como enviado. False si ya estaba (otra corrida ganó)."""
    with conn() as c:
        try:
            c.execute("INSERT INTO partes_enviados (fecha, enviado_en) VALUES (?,?)",
                      (iso(fecha), ahora().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            return False
    return True


def ultimo_parte() -> date | None:
    with conn() as c:
        fila = c.execute("SELECT MAX(fecha) f FROM partes_enviados").fetchone()
    return de_iso(fila["f"]) if fila and fila["f"] else None


def pausar(chat_id: int, hasta: date) -> None:
    with conn() as c:
        c.execute("INSERT INTO pausa (chat_id, hasta) VALUES (?,?) "
                  "ON CONFLICT(chat_id) DO UPDATE SET hasta = excluded.hasta",
                  (chat_id, iso(hasta)))


def pausada_hasta(chat_id: int) -> date | None:
    with conn() as c:
        fila = c.execute("SELECT hasta FROM pausa WHERE chat_id = ?", (chat_id,)).fetchone()
    if not fila:
        return None
    hasta = de_iso(fila["hasta"])
    if hasta and hasta < ahora().date():
        with conn() as c:                     # se despausa sola
            c.execute("DELETE FROM pausa WHERE chat_id = ?", (chat_id,))
        return None
    return hasta


# Para COMPARAR dos cosas (nunca para guardarlas) se sacan los artículos y los
# arranques de relleno: «comprar la cómoda para la habitación» y «comprar cómoda para
# la habitación» son lo mismo, y «falta leche» es lo mismo que «comprar leche».
ARTICULOS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del",
             "de", "para"}
# Se aceptan las conjugaciones: «comprar», «compramos», «compré», «compra».
ARRANQUES = re.compile(
    r"^(?:hay que|tenemos que|tengo que|tenes que|hace falta|no te olvides de|"
    r"acordate de|compr\w*|trae\w*|consegu\w*|busca\w*|falta\w*|pedi\w*|"
    r"encarga\w*|repone\w*|se acabo\w*|se termino\w*)\s+")


def clave_duplicado(texto: str) -> str:
    """La forma canónica para comparar. Lo que se guarda es el texto original."""
    from .dates import normalizar

    plano = normalizar(texto)
    while True:
        recortado = ARRANQUES.sub("", plano)
        if recortado == plano:
            break
        plano = recortado
    return " ".join(p for p in plano.split() if p not in ARTICULOS)


def pendiente_igual(chat_id: int, texto: str):
    """Un pendiente que es LO MISMO. Para no anotar dos veces.

    A propósito exacto y no difuso: en v1 el fuzzy tachaba pantuflas cuando comprabas
    pan. Lo que se ignora son los artículos y los arranques de relleno.
    """
    objetivo = clave_duplicado(texto)
    if not objetivo:
        return None
    with conn() as c:
        filas = c.execute(
            "SELECT * FROM tasks WHERE chat_id = ? AND estado = 'pendiente'",
            (chat_id,)).fetchall()
    for fila in filas:
        if clave_duplicado(fila["texto"]) == objetivo:
            return fila
    return None


# Las tablas que guardan cosas por chat. Si Telegram convierte el grupo (pasa al
# hacer admin a alguien), el chat_id cambia y hay que mudar todo o el bot arranca
# vacío.
TABLAS_POR_CHAT = ("tasks", "propuestas", "deshacer", "pausa", "pending", "salientes")

# Estas guardan message_id, y los message_id son de UN chat: en el nuevo no existen.
# Se descartan y el tablero se publica de cero.
TABLAS_DE_MENSAJES = ("tablero", "mensajes_temporales")


def migrar_chat(viejo: int, nuevo: int) -> dict[str, int]:
    """Muda todo de un chat_id a otro. Devuelve cuántas filas movió por tabla."""
    movidas = {}
    with conn() as c:
        for tabla in TABLAS_DE_MENSAJES:
            c.execute(f"DELETE FROM {tabla} WHERE chat_id = ?", (viejo,))
        for tabla in TABLAS_POR_CHAT:
            try:
                cur = c.execute(f"UPDATE OR REPLACE {tabla} SET chat_id = ? WHERE chat_id = ?",
                                (nuevo, viejo))
            except sqlite3.Error as e:
                log.error("No pude mudar %s: %s", tabla, e)
                continue
            if cur.rowcount:
                movidas[tabla] = cur.rowcount
    log.info("Chat %s -> %s: %s", viejo, nuevo, movidas)
    return movidas
