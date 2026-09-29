"""Una sola clase de cosa, con 🛒 como etiqueta.

La distinción compras/tarea era la fuente de casi todos los bugs: la cómoda mal
clasificada, el verbo borrado («Regalo para mamá»), duplicados que no se detectaban,
la fecha que se perdía. Ahora hay una sola clase de cosa y 🛒 es sólo una etiqueta
para filtrar: no cambia cómo se guarda ni cómo se nombra.
"""
from datetime import timedelta

import pytest

from notita import cb, db, handlers, menus, parte, tablero
from notita.dates import hoy

from .conftest import CHAT
from .test_v2_captura import click, item, mensaje, responde, textos

MANANA = hoy() + timedelta(days=1)


def secciones_del_tablero(ref=None):
    return tablero.repartir(db.pendientes(CHAT), ref or hoy())


def en_alguna_seccion(texto, ref=None) -> bool:
    return any(texto.lower() in r["texto"].lower()
               for rows in secciones_del_tablero(ref).values() for r in rows)


# --------------------------------------------------------------------------
# El texto se guarda como lo dijeron
# --------------------------------------------------------------------------

@pytest.mark.parametrize("titulo,compra", [
    ("Falta leche", True),
    ("Comprar leche", True),
    ("Comprar regalo para mamá", False),
    ("Llamar al plomero", False),
])
def test_el_texto_se_guarda_tal_cual(enviados, monkeypatch, titulo, compra):
    """Ya no se le saca el verbo a nada: era una regla que sólo valía para compras."""
    responde(monkeypatch, items=[item(titulo, compra=compra, fecha_kind="desconocida")])

    handlers.handle_update(mensaje(titulo.lower()))

    guardada = db.pendientes(CHAT)[0]
    assert guardada["texto"] == titulo
    assert bool(guardada["compra"]) is compra


def test_el_regalo_conserva_su_texto(enviados, monkeypatch):
    """Antes quedaba «Regalo para mamá»: le comíamos el verbo."""
    responde(monkeypatch, items=[item("Comprar regalo para mamá", compra=False,
                                      fecha_kind="dia_semana", fecha_weekday=6)])

    handlers.handle_update(mensaje("comprar regalo para mamá antes del domingo"))

    guardada = db.pendientes(CHAT)[0]
    assert guardada["texto"] == "Comprar regalo para mamá"
    assert guardada["due_date"] is not None


# --------------------------------------------------------------------------
# La etiqueta 🛒 sólo sirve para filtrar
# --------------------------------------------------------------------------

def test_una_compra_sin_fecha_no_se_lista_en_el_tablero(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Falta leche", compra=True,
                                      fecha_kind="desconocida")])

    handlers.handle_update(mensaje("falta leche"))

    assert not en_alguna_seccion("leche"), "no va en las secciones de días"
    texto, filas = tablero.render(CHAT)
    assert "🛒 <b>COMPRAS</b> · 1" in texto
    assert any("🛒 Compras · 1" == b["text"] for fila in filas for b in fila)
    # Pero en la lista de compras sí.
    enviados.clear()
    menus.abrir_compras(CHAT)
    assert "Falta leche" in textos(enviados)[0]


def test_una_compra_con_fecha_va_en_su_dia_con_el_carrito(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Falta carbón", compra=True,
                                      fecha_kind="manana")])

    handlers.handle_update(mensaje("para el asado de mañana falta carbón"))

    manana = secciones_del_tablero()["manana"]
    assert [r["texto"] for r in manana] == ["Falta carbón"]
    _, filas = tablero.render(CHAT)
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert any("🛒" in e and "Falta carbón" in e for e in etiquetas), etiquetas
    # Y también está en la lista de compras, con la fecha.
    enviados.clear()
    menus.abrir_compras(CHAT)
    lista = textos(enviados)[0]
    assert "Falta carbón" in lista and "mañana" in lista


def test_una_cosa_sin_fecha_y_sin_carrito_va_a_algun_dia(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Pintar el balcón", compra=False,
                                      fecha_kind="desconocida")])

    handlers.handle_update(mensaje("pintar el balcón"))

    assert [r["texto"] for r in secciones_del_tablero()["algun_dia"]] == ["Pintar el balcón"]


def test_ninguna_cosa_aparece_dos_veces(enviados):
    db.crear_tarea(CHAT, "Falta carbón", compra=True, due=MANANA)
    db.crear_tarea(CHAT, "Falta leche", compra=True)
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())
    db.crear_tarea(CHAT, "Pintar el balcón")

    secciones = secciones_del_tablero()
    vistos = [r["id"] for rows in secciones.values() for r in rows]
    _, filas = tablero.render(CHAT)
    tachar = [b["callback_data"] for fila in filas for b in fila
              if b["text"].startswith("✅")]

    assert len(vistos) == len(set(vistos))
    assert len(tachar) == len(set(tachar))
    assert len(vistos) == 3, "la compra sin fecha no va en las secciones"


def test_el_toggle_no_toca_ni_el_texto_ni_la_fecha(enviados):
    tid = db.crear_tarea(CHAT, "Comprar carbón", due=MANANA, hora="18:00")
    antes = dict(db.obtener(tid))

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="a"))
    despues = db.obtener(tid)
    assert despues["compra"] == 1
    assert despues["texto"] == antes["texto"]
    assert despues["due_date"] == antes["due_date"]
    assert despues["due_hora"] == antes["due_hora"]

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="b"))
    assert db.obtener(tid)["compra"] == 0
    assert db.obtener(tid)["due_date"] == antes["due_date"]


def test_el_menu_ofrece_marcar_o_sacar(enviados):
    tid = db.crear_tarea(CHAT, "Comprar carbón", due=MANANA)
    enviados.clear()
    handlers.handle_update(click(cb.armar("m", tid), cq_id="m1"))
    etiquetas = [b["text"] for e in enviados if e["metodo"] == "sendMessage"
                 for fila in (e.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in fila]
    assert "🛒 Marcar como compra" in etiquetas

    db.actualizar(tid, compra=1)
    enviados.clear()
    handlers.handle_update(click(cb.armar("m", tid), cq_id="m2"))
    etiquetas = [b["text"] for e in enviados if e["metodo"] == "sendMessage"
                 for fila in (e.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in fila]
    assert "Sacar de compras" in etiquetas


def test_la_lista_de_compras_trae_todo_lo_marcado(enviados):
    db.crear_tarea(CHAT, "Falta leche", compra=True)
    db.crear_tarea(CHAT, "Falta carbón", compra=True, due=MANANA)
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())
    enviados.clear()

    menus.abrir_compras(CHAT)

    lista = textos(enviados)[0]
    assert "Falta leche" in lista and "Falta carbón" in lista
    assert "Sacar la basura" not in lista
    etiquetas = [b["text"] for e in enviados if e["metodo"] == "sendMessage"
                 for fila in (e.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in fila]
    assert any("Compramos todo" in e for e in etiquetas)


def test_el_parte_incluye_las_compras_con_fecha_de_manana(enviados):
    db.crear_tarea(CHAT, "Falta carbón", compra=True, due=hoy() + timedelta(days=1))
    db.crear_tarea(CHAT, "Falta leche", compra=True)

    texto, _ = parte.render(CHAT, hoy())

    assert "Falta carbón" in texto, "va como cualquier cosa de mañana"
    assert "🛒 En compras hay <b>1</b> cosas" in texto, "y las sueltas se cuentan"


# --------------------------------------------------------------------------
# Duplicados: una sola regla
# --------------------------------------------------------------------------

@pytest.mark.parametrize("uno,otro,iguales", [
    ("Comprar la cómoda para la habitación", "Comprar cómoda para la habitación", True),
    ("Falta leche", "Comprar leche", True),
    ("Hay que llamar al plomero", "Llamar plomero", True),
    ("Sacar la basura", "Sacar las basuras", False),
    ("Regar las plantas", "Regar el balcón", False),
])
def test_los_duplicados_se_comparan_sin_relleno(enviados, uno, otro, iguales):
    db.crear_tarea(CHAT, uno, due=hoy())

    assert (db.pendiente_igual(CHAT, otro) is not None) is iguales


def test_la_comoda_no_se_anota_dos_veces(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Comprar la cómoda para la habitación",
                                      fecha_kind="manana")])
    handlers.handle_update(mensaje("comprar la cómoda para la habitación mañana",
                                   update_id=1))
    responde(monkeypatch, items=[item("Comprar cómoda para la habitación",
                                      fecha_kind="desconocida")])
    handlers.handle_update(mensaje("comprar cómoda para la habitación", update_id=2))

    assert len(db.pendientes(CHAT)) == 1
    assert "Ya estaba" in textos(enviados)[-1]


def test_si_lo_repetido_trae_fecha_se_la_pone_y_lo_dice(enviados, monkeypatch):
    """Decir «ya estaba» y nada más perdía la única información nueva del mensaje."""
    tid = db.crear_tarea(CHAT, "Leche", compra=True)
    responde(monkeypatch, items=[item("Compramos leche", compra=True,
                                      fecha_kind="manana")])

    handlers.handle_update(mensaje("mañana compramos leche"))

    assert len(db.pendientes(CHAT)) == 1
    assert db.obtener(tid)["due_date"] == MANANA.isoformat()
    respuesta = textos(enviados)[-1]
    assert "Ya estaba" in respuesta and "le puse" in respuesta and "mañana" in respuesta


def test_si_lo_repetido_no_trae_nada_nuevo_no_cambia_la_fecha(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "Leche", compra=True, due=MANANA)
    responde(monkeypatch, items=[item("Falta leche", compra=True,
                                      fecha_kind="desconocida")])

    handlers.handle_update(mensaje("falta leche"))

    assert db.obtener(tid)["due_date"] == MANANA.isoformat()


# --------------------------------------------------------------------------
# «tablero» sin barra, y el resto de las formas de pedirlo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto", ["tablero", "Tablero", "📋", "/tablero", "/todo"])
def test_todas_las_formas_de_pedir_el_tablero(enviados, monkeypatch, texto):
    """Regresión: «tablero» sin barra no mostraba nada.

    Eran dos cosas: «📋» caía en «ese comando no lo tengo», y «tablero» editaba el
    tablero de arriba en vez de publicarlo abajo.
    """
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())
    tablero.publicar(CHAT)          # deja `editado_en` recién ahora
    enviados.clear()

    handlers.handle_update(mensaje(texto, update_id=abs(hash(texto)) % 9999))

    publicados = [e for e in enviados if e["metodo"] == "sendMessage"
                  and "La casa" in e.get("text", "")]
    assert publicados, f"{texto!r} no publicó el tablero: {[e['metodo'] for e in enviados]}"


# --------------------------------------------------------------------------
# Migración
# --------------------------------------------------------------------------

def base_con_super(ruta, esquema="2"):
    """Una base como la de producción: v2, con `tipo` y con ítems de compras."""
    import sqlite3

    c = sqlite3.connect(ruta)
    c.executescript("""
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 texto TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'casa',
 categoria TEXT NOT NULL DEFAULT 'otros', responsable TEXT NOT NULL DEFAULT 'ninguno',
 due_date TEXT, due_hora TEXT, mensaje_origen_id INTEGER, recur_kind TEXT,
 recur_interval INTEGER DEFAULT 1, recur_weekday INTEGER, recur_monthday INTEGER,
 estado TEXT NOT NULL DEFAULT 'pendiente', postpone_count INTEGER NOT NULL DEFAULT 0,
 created_by TEXT NOT NULL DEFAULT 'ninguno', created_at TEXT NOT NULL,
 completed_by TEXT, completed_at TEXT);
CREATE TABLE ajustes (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE updates_vistos (update_id TEXT PRIMARY KEY, visto_en TEXT NOT NULL);
""")
    c.execute("INSERT INTO ajustes VALUES ('esquema', ?)", (esquema,))
    for texto, tipo, fecha in (("Leche", "compras", None), ("Yerba", "compras", None),
                               ("Sacar la basura", "casa", "2026-09-30")):
        c.execute("INSERT INTO tasks (chat_id, texto, tipo, due_date, created_at) "
                  "VALUES (?,?,?,?,?)",
                  (CHAT, texto, tipo, fecha, "2026-09-01T10:00:00-03:00"))
    c.commit()
    c.close()


def test_la_migracion_etiqueta_las_compras_sin_tocar_el_texto(tmp_path, monkeypatch):
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    filas = {r["texto"]: r for r in db.pendientes(CHAT)}
    assert set(filas) == {"Leche", "Yerba", "Sacar la basura"}, "no se toca el texto"
    assert filas["Leche"]["compra"] == 1
    assert filas["Yerba"]["compra"] == 1
    assert filas["Sacar la basura"]["compra"] == 0
    assert filas["Sacar la basura"]["due_date"] == "2026-09-30", "ni la fecha"
    respaldos = list(tmp_path.glob("prod.db.antes-de-v3.*.bak"))
    assert len(respaldos) == 1, f"backup antes de migrar: {list(tmp_path.iterdir())}"


def test_la_migracion_no_vuelve_a_pisar_la_etiqueta(tmp_path, monkeypatch):
    """Si alguien saca algo de compras, la migración no lo puede volver a marcar."""
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))
    db.init_db()
    leche = [r for r in db.pendientes(CHAT) if r["texto"] == "Leche"][0]
    db.actualizar(leche["id"], compra=0)

    db.init_db()      # como si el proceso se reiniciara

    assert db.obtener(leche["id"])["compra"] == 0


def test_la_columna_tipo_ya_no_esta(tmp_path, monkeypatch):
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    with db.conn() as c:
        columnas = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    assert "compra" in columnas
    assert "tipo" not in columnas, "con SQLite >= 3.35 se borra"


# --------------------------------------------------------------------------
# El respaldo: nunca se pisa, y siempre hay uno antes de migrar
# --------------------------------------------------------------------------

def test_el_respaldo_no_pisa_el_de_la_migracion_anterior(tmp_path, monkeypatch):
    """Ya existía un `notita.db.v1.bak`: pisarlo sería perder esos datos."""
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    viejo = tmp_path / "prod.db.v1.bak"
    viejo.write_bytes(b"el respaldo de la migracion anterior")
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    assert viejo.read_bytes() == b"el respaldo de la migracion anterior"
    assert list(tmp_path.glob("prod.db.antes-de-v3.*.bak")), "y el nuevo existe"


def test_el_respaldo_lleva_fecha_y_no_se_pisa_a_si_mismo(tmp_path, monkeypatch):
    from notita import config
    from notita.dates import ahora

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))
    hoy_iso = ahora().date().isoformat()
    (tmp_path / f"prod.db.antes-de-v3.{hoy_iso}.bak").write_bytes(b"uno de antes")

    db.init_db()

    assert (tmp_path / f"prod.db.antes-de-v3.{hoy_iso}.bak").read_bytes() == b"uno de antes"
    assert (tmp_path / f"prod.db.antes-de-v3.{hoy_iso}-2.bak").exists()


def test_una_base_ya_en_v2_tambien_se_respalda(tmp_path, monkeypatch):
    """El bug: sólo respaldaba si la base era de v1.

    La de producción ya estaba en v2, así que la migración que BORRA una columna
    habría corrido sin ninguna copia.
    """
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta, esquema="2")
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    assert list(tmp_path.glob("prod.db.antes-de-v3.*.bak"))


def test_sin_migracion_por_delante_no_respalda_de_gusto(tmp_path, monkeypatch):
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))
    db.init_db()
    for bak in tmp_path.glob("*.bak"):
        bak.unlink()

    db.init_db()      # segundo arranque, ya migrada

    assert list(tmp_path.glob("*.bak")) == [], "no acumula copias en cada reinicio"


# --------------------------------------------------------------------------
# SQLite vieja, sin DROP COLUMN
# --------------------------------------------------------------------------

class SinDropColumn:
    """Una conexión que finge ser SQLite < 3.35."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args):
        import sqlite3 as sq

        if "DROP COLUMN" in sql.upper():
            raise sq.OperationalError('near "DROP": syntax error')
        return self._real.execute(sql, *args)

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)


def test_si_sqlite_no_sabe_borrar_columnas_igual_funciona(tmp_path, monkeypatch):
    """PythonAnywhere podría tener SQLite < 3.35: la columna queda sin uso."""
    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))
    db.init_db()                       # crea las tablas nuevas y la columna compra

    # Se rebobina el esquema para que la migración corra de nuevo, ahora sin DROP.
    db.ajuste("esquema", "2")
    with db.conn() as real:
        db._migrar_a_v2(SinDropColumn(real))

    with db.conn() as c:
        columnas = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    assert "compra" in columnas
    assert db.ajuste("esquema") == str(db.VERSION_ESQUEMA), "la migración se completa igual"

    # Y lo que importa: seguir anotando y leyendo con la columna vieja presente.
    tid = db.crear_tarea(CHAT, "Papel higiénico", compra=True)
    assert db.obtener(tid)["compra"] == 1
    textos_compras = [r["texto"] for r in db.pendientes(CHAT, compra=True)]
    assert "Papel higiénico" in textos_compras
    assert "Sacar la basura" not in textos_compras


# --------------------------------------------------------------------------
# Lo que se borró no volvió
# --------------------------------------------------------------------------

def test_ya_no_existe_la_limpieza_de_verbos():
    from notita import dates

    assert not hasattr(dates, "limpiar_item_de_compras")
    assert not hasattr(dates, "limpiar_item_de_super")


def test_el_schema_del_llm_usa_compra_booleano():
    from notita import llm

    props = llm.schema_mensaje()["properties"]["items"]["items"]["properties"]
    assert props["compra"]["type"] == "boolean"
    assert "tipo" not in props
    assert "compra" in llm.schema_mensaje()["properties"]["items"]["items"]["required"]


def test_el_prompt_dice_que_ante_la_duda_no_es_compra():
    from notita import llm

    sistema = llm._sistema()
    assert "ANTE LA DUDA: false" in sistema
    assert "LAS PALABRAS DEL MENSAJE" in sistema


def test_una_compra_sin_fecha_no_dice_algun_dia(enviados, monkeypatch):
    """A una compra no le falta la fecha: no es que esté «para algún día»."""
    responde(monkeypatch, items=[item("Falta leche", compra=True,
                                      fecha_kind="desconocida")])

    handlers.handle_update(mensaje("falta leche"))

    confirmacion = textos(enviados)[0]
    assert "🛒 Falta leche" in confirmacion
    assert "algún día" not in confirmacion


def test_una_cosa_sin_fecha_y_sin_carrito_si_lo_dice(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Pintar el balcón", compra=False,
                                      fecha_kind="desconocida")])

    handlers.handle_update(mensaje("pintar el balcón"))

    assert "algún día" in textos(enviados)[0]


def test_la_confirmacion_mezcla_las_dos_cosas(enviados, monkeypatch):
    responde(monkeypatch, items=[
        item("Comprar leche", compra=True, fecha_kind="desconocida"),
        item("Llamar al plomero", compra=False, fecha_kind="manana")])

    handlers.handle_update(mensaje("comprar leche y llamar al plomero mañana"))

    confirmacion = textos(enviados)[0]
    assert "Anoté 2 cositas" in confirmacion
    assert "🛒 Comprar leche" in confirmacion
    assert "Llamar al plomero" in confirmacion and "mañana" in confirmacion


# --------------------------------------------------------------------------
# El índice viejo que bloqueaba el DROP COLUMN
# --------------------------------------------------------------------------

def base_v3_con_indice_viejo(ruta):
    """Como quedó producción: esquema=3, pero `tipo` y el índice viejo ahí.

    El `DROP COLUMN` había fallado con «error in index idx_tasks_estado after drop
    column: no such column: tipo», y la migración se anotó como terminada igual.
    """
    import sqlite3

    c = sqlite3.connect(ruta)
    c.executescript("""
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 texto TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'casa',
 compra INTEGER NOT NULL DEFAULT 0, categoria TEXT NOT NULL DEFAULT 'otros',
 responsable TEXT NOT NULL DEFAULT 'ninguno', due_date TEXT, due_hora TEXT,
 mensaje_origen_id INTEGER, recur_kind TEXT, recur_interval INTEGER DEFAULT 1,
 recur_weekday INTEGER, recur_monthday INTEGER,
 estado TEXT NOT NULL DEFAULT 'pendiente', postpone_count INTEGER NOT NULL DEFAULT 0,
 created_by TEXT NOT NULL DEFAULT 'ninguno', created_at TEXT NOT NULL,
 completed_by TEXT, completed_at TEXT);
CREATE INDEX idx_tasks_estado ON tasks (estado, tipo, due_date);
CREATE TABLE ajustes (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE updates_vistos (update_id TEXT PRIMARY KEY, visto_en TEXT NOT NULL);
""")
    c.execute("INSERT INTO ajustes VALUES ('esquema','3'),('compras_migradas','6')")
    c.execute("INSERT INTO tasks (chat_id, texto, tipo, compra, created_at) "
              "VALUES (?,?,?,?,?)", (CHAT, "Falta leche", "compras", 1,
                                     "2026-09-01T10:00:00-03:00"))
    c.commit()
    c.close()


def test_un_indice_viejo_no_puede_bloquear_la_limpieza(tmp_path, monkeypatch):
    """`CREATE INDEX IF NOT EXISTS` no redefine un índice que ya existe.

    Así que el índice de v1 sobrevivió al cambio de esquema y bloqueaba el DROP.
    """
    from notita import config

    ruta = tmp_path / "prod.db"
    base_v3_con_indice_viejo(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    with db.conn() as c:
        columnas = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
        indice = c.execute("SELECT sql FROM sqlite_master WHERE type='index' "
                           "AND name='idx_tasks_estado'").fetchone()
    assert "tipo" not in columnas, "la columna se borra igual"
    assert "tipo" not in (indice["sql"] or ""), "y el índice queda con la forma nueva"
    assert [(r["texto"], r["compra"]) for r in db.pendientes(CHAT)] == [("Falta leche", 1)]


def test_se_respalda_aunque_el_esquema_ya_este_al_dia(tmp_path, monkeypatch):
    """Borrar columnas es tocar la forma: va con copia, esquema al día o no."""
    from notita import config

    ruta = tmp_path / "prod.db"
    base_v3_con_indice_viejo(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    assert list(tmp_path.glob("prod.db.antes-de-limpiar.*.bak"))


def test_una_vez_limpia_no_respalda_mas(tmp_path, monkeypatch):
    from notita import config

    ruta = tmp_path / "prod.db"
    base_v3_con_indice_viejo(ruta)
    monkeypatch.setattr(config, "DB_PATH", str(ruta))
    db.init_db()
    for bak in tmp_path.glob("*.bak"):
        bak.unlink()

    db.init_db()

    assert list(tmp_path.glob("*.bak")) == []


def test_el_indice_nuevo_no_menciona_columnas_muertas():
    """Si el esquema vuelve a nombrar `tipo`, este test lo caza."""
    for sentencia in db.SCHEMA.split(";"):
        if "CREATE INDEX" in sentencia.upper():
            for muerta in db.COLUMNAS_MUERTAS["tasks"]:
                assert muerta not in sentencia, sentencia.strip()


def test_la_fecha_del_respaldo_es_cuando_se_hizo(tmp_path, monkeypatch):
    """`copy2` conservaba la fecha del original: `ls -la` mostraba la hora equivocada."""
    import os
    import time

    from notita import config

    ruta = tmp_path / "prod.db"
    base_con_super(ruta)
    hace_un_mes = time.time() - 30 * 24 * 3600
    os.utime(ruta, (hace_un_mes, hace_un_mes))
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    db.init_db()

    respaldo = next(iter(tmp_path.glob("prod.db.antes-de-v3.*.bak")))
    assert respaldo.stat().st_mtime > hace_un_mes + 3600, "tiene que ser de ahora"
    assert respaldo.stat().st_mode == ruta.stat().st_mode, "los permisos sí se copian"


def test_el_contador_de_compras_da_lo_mismo_que_la_lista(enviados):
    """Contaba sólo las que no tienen fecha, y /compras las muestra todas.

    Dos números para lo mismo: después no se cree ninguno.
    """
    db.crear_tarea(CHAT, "Falta carbón", compra=True, due=MANANA)
    db.crear_tarea(CHAT, "Falta leche", compra=True)
    db.crear_tarea(CHAT, "Falta yerba", compra=True)
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())

    texto, filas = tablero.render(CHAT)
    enviados.clear()
    menus.abrir_compras(CHAT)

    assert "🛒 <b>COMPRAS</b> · 3" in texto
    assert any(b["text"] == "🛒 Compras · 3" for fila in filas for b in fila)
    assert "🛒 <b>Compras</b> · 3" in textos(enviados)[0]
    # Y el carbón sigue apareciendo una sola vez en las secciones.
    assert [r["texto"] for r in secciones_del_tablero()["manana"]] == ["Falta carbón"]


def test_con_todas_las_compras_fechadas_el_boton_igual_aparece(enviados):
    """Antes desaparecía el acceso a /compras aunque hubiera cosas etiquetadas."""
    db.crear_tarea(CHAT, "Falta carbón", compra=True, due=MANANA)

    texto, filas = tablero.render(CHAT)

    assert "🛒 <b>COMPRAS</b> · 1" in texto
    assert any("🛒 Compras · 1" == b["text"] for fila in filas for b in fila)
