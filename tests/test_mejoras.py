"""Las mejoras de uso diario que salieron de la revisión adversarial.

La idea de fondo: que el bot siga siendo querido al tercer mes de uso.
"""
from datetime import timedelta

import pytest

from notita import db, handlers, heuristica, llm, reminders, telegram, views
from notita.dates import hoy

from .conftest import CHAT


def mensaje(texto, uid=111, update_id=None):
    cuerpo = {"message": {"chat": {"id": CHAT}, "from": {"id": uid}, "text": texto,
                          "message_id": 7}}
    if update_id is not None:
        cuerpo["update_id"] = update_id
    return cuerpo


def click(data, uid=111, message_id=10):
    return {"callback_query": {"id": "cb", "data": data, "from": {"id": uid},
                               "message": {"message_id": message_id, "chat": {"id": CHAT}}}}


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


def fake_items(monkeypatch, *items):
    completos = []
    for it in items:
        base = {"texto": "algo", "tipo": "casa", "categoria": "otros",
                "responsable": "ninguno", "fecha_kind": "hoy", "recur_kind": "ninguna",
                "necesita_aclaracion": False}
        base.update(it)
        completos.append(base)
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "anotar", "es_tarea": True, "comentario": "", "items": completos})


# --------------------------------------------------------------------------
# Las vencidas de siempre, en un solo mensaje
# --------------------------------------------------------------------------

def vieja(texto, noches=3):
    tid = db.crear_tarea(CHAT, texto, due=hoy() - timedelta(days=noches + 1))
    db.actualizar(tid, recordada_veces=noches)
    return tid


def test_las_vencidas_cansadas_van_juntas(enviados):
    for t in ("ordenar el placard", "colgar el cuadro", "tirar las cajas"):
        vieja(t)

    res = reminders.correr_rutina_diaria(ref=hoy())

    assert res["agrupadas"] == 3
    assert res["recordatorios"] == 0        # ningún mensaje suelto
    # Los domingos va también el resumen, así que se busca el agrupado.
    agrupado = [s for s in textos(enviados) if "pregunto por estas" in s]
    assert len(agrupado) == 1, "tres tareas, un solo mensaje"
    assert "Hace varios días que pregunto por estas <b>3</b>" in agrupado[0]
    for t in ("Ordenar el placard", "Colgar el cuadro", "Tirar las cajas"):
        assert t in agrupado[0]


def test_las_nuevas_siguen_yendo_de_a_una(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    db.crear_tarea(CHAT, "pagar la luz", due=hoy())

    res = reminders.correr_rutina_diaria(ref=hoy())

    assert res["recordatorios"] == 2 and res["agrupadas"] == 0


def test_una_sola_cansada_no_se_agrupa(enviados):
    vieja("ordenar el placard")
    res = reminders.correr_rutina_diaria(ref=hoy())
    # Un mensaje agrupado de una sola tarea sería raro: va suelto, con el chiste.
    assert res["agrupadas"] == 0 and res["recordatorios"] == 1


def test_se_cuentan_las_noches(enviados):
    tid = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    for dia in range(3):
        reminders.correr_rutina_diaria(ref=hoy() + timedelta(days=dia))
    assert db.obtener(tid)["recordada_veces"] == 3


def test_patearlas_una_semana(enviados):
    ids = [vieja(t) for t in ("ordenar el placard", "colgar el cuadro")]
    reminders.correr_rutina_diaria(ref=hoy())

    handlers.handle_update(click("vg:s"))

    for tid in ids:
        fila = db.obtener(tid)
        assert fila["due_date"] == (hoy() + timedelta(days=7)).isoformat()
        assert fila["recordada_veces"] == 0     # vuelven a arrancar de cero
    assert "2 para" in [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]


def test_verlas_de_a_una(enviados):
    for t in ("ordenar el placard", "colgar el cuadro"):
        vieja(t)
    reminders.correr_rutina_diaria(ref=hoy())
    enviados.clear()

    handlers.handle_update(click("vg:u"))

    sueltos = [e for e in enviados if e["metodo"] == "sendMessage"]
    assert len(sueltos) == 2
    assert all(e["reply_markup"]["inline_keyboard"] for e in sueltos)


def test_si_ya_no_quedan_vencidas_el_boton_lo_dice(enviados):
    tid = vieja("ordenar el placard")
    reminders.correr_rutina_diaria(ref=hoy())
    db.marcar_hecha(tid, "axel")
    enviados.clear()

    handlers.handle_update(click("vg:s"))
    assert "Ya no queda" in [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]


# --------------------------------------------------------------------------
# El súper: visible y tachable de una
# --------------------------------------------------------------------------

def test_el_super_aparece_en_el_resumen_del_domingo(enviados):
    from datetime import date

    domingo = date(2026, 9, 27)
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")
    db.crear_tarea(CHAT, "yerba", tipo="compras", categoria="compras")

    resumen = views.render_resumen_semanal(CHAT, domingo)
    assert "2 en el súper" in resumen


def test_compramos_todo(enviados):
    for cosa in ("leche", "yerba", "pan"):
        db.crear_tarea(CHAT, cosa, tipo="compras", categoria="compras")

    handlers.handle_update(click("ct"))

    assert db.pendientes(CHAT, tipo="compras") == []
    assert "Tachadas las 3" in [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]


def test_el_boton_de_compramos_todo_aparece_solo_si_hay_varias(enviados):
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")
    _, teclado = views.render_super(CHAT)
    assert not any("todo" in b["text"] for fila in teclado for b in fila)

    db.crear_tarea(CHAT, "yerba", tipo="compras", categoria="compras")
    _, teclado = views.render_super(CHAT)
    assert any("todo" in b["text"] for fila in teclado for b in fila)


def test_compramos_todo_con_la_lista_vacia(enviados):
    handlers.handle_update(click("ct"))
    assert "vacía" in [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]


# --------------------------------------------------------------------------
# Listas largas: partir en vez de morir
# --------------------------------------------------------------------------

def test_un_mensaje_largo_se_parte(enviados, monkeypatch):
    largo = "\n".join(f"linea numero {i} con texto de relleno" for i in range(300))
    assert len(largo) > telegram.LARGO_MAXIMO

    telegram.enviar_largo(CHAT, largo)

    partes = [e["text"] for e in enviados if e["metodo"] == "sendMessage"]
    assert len(partes) > 1
    assert all(len(p) <= telegram.LARGO_MAXIMO + 40 for p in partes)   # + el pie (1/3)
    assert "(1/" in partes[0]


def test_un_mensaje_corto_no_se_parte(enviados):
    telegram.enviar_largo(CHAT, "hola")
    partes = [e["text"] for e in enviados if e["metodo"] == "sendMessage"]
    assert partes == ["hola"]


def test_el_teclado_va_en_el_ultimo_pedazo(enviados):
    largo = "\n".join(f"linea {i} de relleno para pasar el limite" for i in range(300))
    telegram.enviar_largo(CHAT, largo, [[{"text": "ok", "callback_data": "x"}]])
    envios = [e for e in enviados if e["metodo"] == "sendMessage"]
    assert "reply_markup" not in envios[0]
    assert "reply_markup" in envios[-1]


def test_el_todo_con_muchas_tareas_no_muere(enviados, monkeypatch):
    for i in range(120):
        db.crear_tarea(CHAT, f"tarea numero {i} con un texto largo para llenar", due=hoy())
    handlers.handle_update(mensaje("/todo"))
    partes = [e["text"] for e in enviados if e["metodo"] == "sendMessage"]
    assert len(partes) > 1, "tiene que partirse, no perderse"


# --------------------------------------------------------------------------
# Recados de hoy, al instante
# --------------------------------------------------------------------------

def test_un_recado_para_hoy_se_dice_en_el_momento(enviados, monkeypatch):
    fake_items(monkeypatch, {"texto": "llego en 10 minutos", "tipo": "recado",
                             "responsable": "axel", "fecha_kind": "hoy"})
    handlers.handle_update(mensaje("avisale a Axel que llego en 10 minutos", uid=222))

    salida = textos(enviados)
    assert "ahora mismo" in salida[0]
    assert "te manda a decir" in salida[1]
    assert db.pendientes(CHAT, tipo="recado") == []


def test_un_recado_para_manana_espera(enviados, monkeypatch):
    fake_items(monkeypatch, {"texto": "comprá pan", "tipo": "recado",
                             "responsable": "axel", "fecha_kind": "manana"})
    handlers.handle_update(mensaje("decile a Axel que mañana compre pan", uid=222))

    assert "mañana a las 20:00" in textos(enviados)[0]
    assert len(db.pendientes(CHAT, tipo="recado")) == 1
    assert len(textos(enviados)) == 1       # todavía no se entregó


def test_si_no_se_puede_entregar_queda_para_la_noche(enviados, monkeypatch):
    fake_items(monkeypatch, {"texto": "llego tarde", "tipo": "recado",
                             "responsable": "axel", "fecha_kind": "hoy"})
    llamadas = []

    def falla_el_recado(metodo, **payload):
        llamadas.append(payload.get("text", ""))
        if "te manda a decir" in payload.get("text", ""):
            return None
        return {"message_id": len(llamadas)}

    monkeypatch.setattr(telegram, "llamar", falla_el_recado)
    handlers.handle_update(mensaje("avisale a Axel que llego tarde", uid=222))

    assert len(db.pendientes(CHAT, tipo="recado")) == 1, "no se puede dar por entregado"


# --------------------------------------------------------------------------
# No anotar lo mismo dos veces
# --------------------------------------------------------------------------

def test_no_se_anota_dos_veces_lo_mismo(enviados, monkeypatch):
    fake_items(monkeypatch, {"texto": "pagar las expensas", "categoria": "pagos"})
    handlers.handle_update(mensaje("hay que pagar las expensas", update_id=1))
    handlers.handle_update(mensaje("hay que pagar las expensas", uid=222, update_id=2))

    assert len(db.pendientes(CHAT)) == 1
    assert "ya estaba anotado" in textos(enviados)[-1].lower()


def test_algo_parecido_pero_distinto_si_se_anota(enviados, monkeypatch):
    fake_items(monkeypatch, {"texto": "pagar las expensas"})
    handlers.handle_update(mensaje("pagar las expensas", update_id=1))
    fake_items(monkeypatch, {"texto": "pagar la luz"})
    handlers.handle_update(mensaje("pagar la luz", update_id=2))

    assert len(db.pendientes(CHAT)) == 2


def test_en_un_lote_se_anotan_las_nuevas_y_se_avisa_de_la_repetida(enviados, monkeypatch):
    db.crear_tarea(CHAT, "limpiar la heladera", categoria="limpieza", due=hoy())
    fake_items(monkeypatch,
               {"texto": "limpiar la heladera", "categoria": "limpieza"},
               {"texto": "llamar al plomero", "categoria": "arreglos"})

    handlers.handle_update(mensaje("limpiar la heladera y llamar al plomero"))

    assert len(db.pendientes(CHAT)) == 2
    salida = textos(enviados)[0]
    assert "Llamar al plomero" in salida
    assert "Ya estaban anotadas" in salida


def test_un_recado_repetido_si_se_manda(enviados, monkeypatch):
    """Dos veces «avisale que llego tarde» son dos avisos, no un duplicado."""
    fake_items(monkeypatch, {"texto": "llego tarde", "tipo": "recado",
                             "responsable": "axel", "fecha_kind": "manana"})
    handlers.handle_update(mensaje("decile a Axel que llego tarde", uid=222, update_id=1))
    handlers.handle_update(mensaje("decile a Axel que llego tarde", uid=222, update_id=2))
    assert len(db.pendientes(CHAT, tipo="recado")) == 2


# --------------------------------------------------------------------------
# Modo local: más verbos de completar
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frase,referencia", [
    ("ya lavé los platos", "platos"),
    ("ya cambié las piedritas", "piedritas"),
    ("ya barrí la cocina", "cocina"),
    ("ya colgué el cuadro", "cuadro"),
    ("ya regué las plantas", "plantas"),
    ("ya pagué la luz", "luz"),
])
def test_mas_verbos_de_completar_sin_llm(frase, referencia):
    data = heuristica.interpretar(frase)
    assert data["intencion"] == "completar", frase
    assert data["referencia"] == referencia


@pytest.mark.parametrize("frase", [
    "hecho", "ya está", "ya fue", "ya lo hice",
])
def test_sin_referencia_no_inventa(frase):
    data = heuristica.interpretar(frase)
    assert data["intencion"] == "completar"
    assert data["referencia"] == "", "sin referencia, Notita tiene que preguntar cuál"


@pytest.mark.parametrize("frase", [
    "ya que estamos hay que limpiar el baño",
    "ya casi termino de pintar",
])
def test_los_ya_que_no_son_completar(frase):
    assert heuristica.interpretar(frase)["intencion"] == "anotar", frase


# --------------------------------------------------------------------------
# Migración de la base
# --------------------------------------------------------------------------

def test_una_base_vieja_se_migra(tmp_path, monkeypatch):
    """Antes, agregar una columna rompía las bases que ya existían en producción."""
    import sqlite3

    vieja = tmp_path / "vieja.db"
    monkeypatch.setattr(db.config, "DB_PATH", str(vieja))
    with sqlite3.connect(vieja) as c:   # el esquema sin la columna nueva
        c.execute("""CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            texto TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'casa',
            categoria TEXT NOT NULL DEFAULT 'otros',
            responsable TEXT NOT NULL DEFAULT 'ninguno', due_date TEXT,
            recur_kind TEXT, recur_interval INTEGER DEFAULT 1, recur_weekday INTEGER,
            recur_monthday INTEGER, estado TEXT NOT NULL DEFAULT 'pendiente',
            postpone_count INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL DEFAULT 'ninguno', created_at TEXT NOT NULL,
            completed_by TEXT, completed_at TEXT, last_reminded_on TEXT)""")
        c.execute("INSERT INTO tasks (chat_id, texto, created_at) VALUES (?,?,?)",
                  (CHAT, "tarea de antes", "2026-01-01T10:00:00-03:00"))

    db.init_db()

    with db.conn() as c:
        columnas = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    assert "recordada_veces" in columnas
    assert db.pendientes(CHAT)[0]["texto"] == "tarea de antes"   # no se perdió nada
    assert db.obtener(1)["recordada_veces"] == 0
