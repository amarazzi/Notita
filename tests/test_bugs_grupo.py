"""Los bugs que salieron de probar Notita a mano en el grupo.

Cada sección es uno de los casos reportados. Para los que dependen del LLM se
mockea la respuesta del modelo y se testea el parseo y la ejecución de la
intención estructurada, que es la parte que nos toca.

Las fechas de ejemplo usan el mismo "hoy" del reporte: sábado 26/9/2026.
"""
from datetime import date, datetime, timedelta

import pytest

from notita import config, db, handlers, heuristica, llm, reminders, views
from notita.dates import Recurrencia, hoy, proxima_ocurrencia, texto_recurrencia

from .conftest import CHAT

SABADO = date(2026, 9, 26)


# --------------------------------------------------------------------------
# Andamios
# --------------------------------------------------------------------------

def mensaje(texto, uid=111, update_id=None, tipo_chat="group", chat_id=CHAT):
    cuerpo = {"message": {"chat": {"id": chat_id, "type": tipo_chat},
                          "from": {"id": uid}, "text": texto, "message_id": 7}}
    if update_id is not None:
        cuerpo["update_id"] = update_id
    return cuerpo


def click(data, uid=111, message_id=10):
    return {"callback_query": {"id": "cb", "data": data, "from": {"id": uid},
                               "message": {"message_id": message_id, "chat": {"id": CHAT}}}}


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


def editados(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "editMessageText"]


def responde(monkeypatch, **campos):
    """Lo que devolvería Gemini, con la forma del schema."""
    base = {"intencion": "anotar", "categoria_filtro": "ninguna", "referencia": "",
            "objetivos": [], "es_tarea": False, "comentario": "", "items": [],
            "cambio_fecha_kind": "desconocida", "cambio_responsable": "ninguno",
            "cambio_texto": ""}
    base.update(campos)
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: base)
    return base


def item(texto, **campos):
    base = {"texto": texto, "tipo": "casa", "categoria": "otros", "responsable": "ninguno",
            "fecha_kind": "desconocida", "fecha_weekday": -1, "fecha_day": -1,
            "fecha_month": -1, "fecha_year": -1, "fecha_dias": -1, "fecha_hora": -1,
            "fecha_minuto": -1, "fecha_minutos": -1, "recur_kind": "ninguna",
            "recur_interval": 1, "recur_weekday": -1, "recur_monthday": -1,
            "necesita_aclaracion": False, "pregunta": ""}
    base.update(campos)
    return base


def compra(nombre):
    return db.crear_tarea(CHAT, nombre, tipo="compras", categoria="compras")


# ==========================================================================
# ALTA 1 · Acciones sobre varios ítems
# ==========================================================================

def test_borrar_dos_cosas_borra_las_dos(enviados, monkeypatch):
    """«borrá la yerba y el papel higiénico del super» borraba sólo la yerba."""
    compra("yerba")
    compra("papel higiénico")
    compra("leche")
    responde(monkeypatch, intencion="borrar", objetivos=["yerba", "papel higiénico"])

    handlers.handle_update(mensaje("borrá la yerba y el papel higiénico del super"))

    assert [r["texto"] for r in db.pendientes(CHAT, tipo="compras")] == ["leche"]
    assert len(textos(enviados)) == 1, "una sola respuesta, no una por tarea"
    salida = textos(enviados)[0]
    assert "Yerba" in salida and "Papel higiénico" in salida
    assert "Cuál de estas" not in salida


def test_completar_dos_cosas_tacha_las_dos(enviados, monkeypatch):
    """«ya compré la leche y la lavandina» tachaba sólo la leche, en silencio."""
    compra("leche")
    compra("lavandina")
    responde(monkeypatch, intencion="completar", objetivos=["leche", "lavandina"])

    handlers.handle_update(mensaje("ya compré la leche y la lavandina"))

    assert db.pendientes(CHAT, tipo="compras") == []
    assert len(textos(enviados)) == 1


def test_si_una_no_existe_lo_dice_y_hace_la_otra(enviados, monkeypatch):
    compra("leche")
    responde(monkeypatch, intencion="completar", objetivos=["leche", "bicicleta"])

    handlers.handle_update(mensaje("ya compré la leche y la bicicleta"))

    assert db.pendientes(CHAT, tipo="compras") == []
    salida = textos(enviados)[0]
    assert "Leche" in salida
    assert "No encontré «bicicleta»" in salida


def test_solo_pregunta_por_el_objetivo_ambiguo(enviados, monkeypatch):
    """Lo claro se hace; sólo lo realmente ambiguo se pregunta."""
    compra("leche")
    db.crear_tarea(CHAT, "limpiar la heladera", categoria="limpieza", due=hoy())
    db.crear_tarea(CHAT, "descongelar la heladera", categoria="limpieza", due=hoy())
    responde(monkeypatch, intencion="borrar", objetivos=["leche", "heladera"])

    handlers.handle_update(mensaje("borrá la leche y la de la heladera"))

    assert db.pendientes(CHAT, tipo="compras") == []          # la clara se hizo
    assert len(db.pendientes(CHAT, tipo="casa")) == 2         # la ambigua no se tocó
    salida = textos(enviados)
    assert "Leche" in salida[0]
    assert "¿Cuál de estas, por «heladera»?" in salida[1]


def test_dos_objetivos_no_se_llevan_la_misma_tarea(enviados, monkeypatch):
    """Dos referencias parecidas no pueden resolver a la misma fila."""
    compra("leche")
    compra("leche de almendras")
    responde(monkeypatch, intencion="borrar", objetivos=["leche", "leche de almendras"])

    handlers.handle_update(mensaje("borrá la leche y la leche de almendras"))

    assert db.pendientes(CHAT, tipo="compras") == []


def test_el_objetivo_unico_sigue_andando_con_referencia(enviados, monkeypatch):
    """Compatibilidad: el modelo puede mandar `referencia` en vez de `objetivos`."""
    db.crear_tarea(CHAT, "llamar al plomero", categoria="arreglos", due=hoy())
    responde(monkeypatch, intencion="borrar", referencia="plomero")

    handlers.handle_update(mensaje("borrá la del plomero"))
    assert db.pendientes(CHAT, tipo="casa") == []


@pytest.mark.parametrize("frase,esperados", [
    ("borrá la yerba y el papel higiénico del super", ["yerba", "papel higienico"]),
    ("ya compré la leche y la lavandina", ["leche", "lavandina"]),
    ("borrá la leche, la yerba y el pan", ["leche", "yerba", "pan"]),
])
def test_el_modo_local_tambien_separa_objetivos(frase, esperados):
    assert heuristica.interpretar(frase)["objetivos"] == esperados


# ==========================================================================
# ALTA 2 · Acciones masivas
# ==========================================================================

def test_vaciar_el_super_por_texto(enviados, monkeypatch):
    for cosa in ("leche", "yerba", "pan"):
        compra(cosa)
    responde(monkeypatch, intencion="vaciar_super")

    handlers.handle_update(mensaje("borrá todo lo del súper"))

    assert db.pendientes(CHAT, tipo="compras") == []
    assert "las 3" in textos(enviados)[0]


def test_ya_compramos_todo_es_lo_mismo_que_el_boton(enviados, monkeypatch):
    compra("leche")
    compra("yerba")
    responde(monkeypatch, intencion="vaciar_super")

    handlers.handle_update(mensaje("ya compramos todo"))

    assert db.pendientes(CHAT, tipo="compras") == []
    with db.conn() as c:
        estados = [r["estado"] for r in c.execute("SELECT estado FROM tasks")]
    assert estados == ["hecha", "hecha"], "compradas, no borradas"


def test_vaciar_el_super_vacio_no_explota(enviados, monkeypatch):
    responde(monkeypatch, intencion="vaciar_super")
    handlers.handle_update(mensaje("borrá todo lo del súper"))
    assert "vacía" in textos(enviados)[0]


def test_borrar_todas_las_tareas_pide_confirmacion(enviados, monkeypatch):
    db.crear_tarea(CHAT, "limpiar la heladera", due=hoy())
    db.crear_tarea(CHAT, "llamar al plomero", due=hoy())
    compra("leche")
    responde(monkeypatch, intencion="borrar_todo")

    handlers.handle_update(mensaje("borrá todas las tareas"))

    assert len(db.pendientes(CHAT, tipo="casa")) == 2, "todavía no se borró nada"
    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    assert "¿Borro <b>las 2</b>" in envio["text"]
    assert any("bt:si" in b["callback_data"]
               for fila in envio["reply_markup"]["inline_keyboard"] for b in fila)

    handlers.handle_update(click("bt:si"))
    assert db.pendientes(CHAT, tipo="casa") == []
    assert len(db.pendientes(CHAT, tipo="compras")) == 1, "el súper no se toca"


def test_si_dice_que_no_no_borra_nada(enviados, monkeypatch):
    db.crear_tarea(CHAT, "limpiar la heladera", due=hoy())
    responde(monkeypatch, intencion="borrar_todo")
    handlers.handle_update(mensaje("borrá todo"))

    handlers.handle_update(click("bt:no"))

    assert len(db.pendientes(CHAT, tipo="casa")) == 1
    assert "no toqué nada" in editados(enviados)[-1]


@pytest.mark.parametrize("frase,intencion", [
    ("borrá todo lo del súper", "vaciar_super"),
    ("vaciá el super", "vaciar_super"),
    ("ya compramos todo", "vaciar_super"),
    ("borrá todas las tareas", "borrar_todo"),
    ("borrá todo", "borrar_todo"),
])
def test_el_modo_local_entiende_lo_masivo(frase, intencion):
    assert heuristica.interpretar(frase)["intencion"] == intencion


# ==========================================================================
# ALTA 3 · Tarea con fecha pasada
# ==========================================================================

def test_sacar_la_basura_ayer_se_anota_vencida(enviados, monkeypatch):
    """Era un infinitivo con fecha pasada, y lo tomaba como «completar»."""
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("sacar la basura", categoria="limpieza", fecha_kind="ayer")])

    handlers.handle_update(mensaje("sacar la basura ayer"))

    tareas = db.pendientes(CHAT, tipo="casa")
    assert len(tareas) == 1
    assert tareas[0]["due_date"] == (hoy() - timedelta(days=1)).isoformat()
    assert "No encontré" not in " ".join(textos(enviados))


def test_el_modo_local_no_confunde_el_pasado_con_completar():
    assert heuristica.interpretar("sacar la basura ayer")["intencion"] == "anotar"
    assert heuristica.interpretar("saqué la basura")["intencion"] == "completar"


def test_una_vencida_de_ayer_se_recuerda(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy() - timedelta(days=1))
    assert reminders.correr_rutina_diaria(ref=hoy())["recordatorios"] == 1


# ==========================================================================
# ALTA 4 · La hora
# ==========================================================================

def test_guarda_y_muestra_la_hora(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("llevar a Milo al veterinario", categoria="mascotas",
                         responsable="barbu", fecha_kind="dia_semana",
                         fecha_weekday=3, fecha_hora=18, fecha_minuto=0)])

    handlers.handle_update(mensaje("Barbu tiene que llevar a Milo al veterinario "
                                   "el jueves a las 18"))

    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert tarea["due_hora"] == "18:00"
    assert "18:00" in textos(enviados)[0]
    assert "18:00" in views.render_todo(CHAT)


def test_la_hora_aparece_en_el_listado_agrupado(enviados):
    db.crear_tarea(CHAT, "llamar al médico", due=hoy(), hora="09:30")
    assert "09:30" in views.render_todo(CHAT)


def test_en_dos_horas_guarda_la_hora(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("llamar a la inmobiliaria", fecha_kind="en_minutos",
                         fecha_minutos=120)])

    handlers.handle_update(mensaje("llamar a la inmobiliaria en 2 horas"))

    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert tarea["due_hora"] is not None, "«en 2 horas» tiene que guardar la hora"


def test_una_tarea_con_hora_espera_su_hora(enviados, monkeypatch):
    """Si la rutina corre temprano, la de las 18:00 no se recuerda todavía."""
    monkeypatch.setattr(config, "CRON_MINUTOS", 15)   # cron frecuente
    db.crear_tarea(CHAT, "llamar al médico", due=SABADO, hora="18:00")
    manana = datetime(2026, 9, 26, 9, 0, tzinfo=config.TZ)

    assert reminders.correr_rutina_diaria(momento=manana)["recordatorios"] == 0

    tarde = datetime(2026, 9, 26, 18, 5, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=tarde)["recordatorios"] == 1
    assert "18:00" in textos(enviados)[-1]


def test_una_tarea_sin_hora_espera_la_pasada_principal(enviados, monkeypatch):
    monkeypatch.setattr(config, "CRON_MINUTOS", 15)
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO)
    temprano = datetime(2026, 9, 26, 9, 0, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=temprano)["recordatorios"] == 0

    noche = datetime(2026, 9, 26, 20, 0, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=noche)["recordatorios"] == 1


def test_una_tarea_de_las_22_igual_se_recuerda_en_la_pasada_de_las_20(enviados, monkeypatch):
    """Con una sola corrida diaria, la pasada principal es la red de seguridad."""
    monkeypatch.setattr(config, "CRON_MINUTOS", 15)
    db.crear_tarea(CHAT, "cerrar la ventana", due=SABADO, hora="22:00")
    noche = datetime(2026, 9, 26, 20, 0, tzinfo=config.TZ)

    assert reminders.correr_rutina_diaria(momento=noche)["recordatorios"] == 1
    assert "22:00" in textos(enviados)[-1]


def test_no_se_recuerda_dos_veces_el_mismo_dia(enviados, monkeypatch):
    monkeypatch.setattr(config, "CRON_MINUTOS", 15)
    db.crear_tarea(CHAT, "llamar al médico", due=SABADO, hora="18:00")
    tarde = datetime(2026, 9, 26, 18, 5, tzinfo=config.TZ)
    noche = datetime(2026, 9, 26, 20, 0, tzinfo=config.TZ)

    assert reminders.correr_rutina_diaria(momento=tarde)["recordatorios"] == 1
    assert reminders.correr_rutina_diaria(momento=noche)["recordatorios"] == 0


def test_una_vencida_se_recuerda_a_cualquier_hora(enviados):
    db.crear_tarea(CHAT, "pagar el ABL", due=SABADO - timedelta(days=3))
    temprano = datetime(2026, 9, 26, 8, 0, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=temprano)["recordatorios"] == 1


def test_un_recado_con_demora_no_sale_al_toque(enviados, monkeypatch):
    """«avisale en 1 minuto» se mandaba al instante, diciendo «ahora mismo»."""
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("fijate el horno", tipo="recado", responsable="axel",
                         fecha_kind="en_minutos", fecha_minutos=1)])

    handlers.handle_update(mensaje("avisale a Axel en 1 minuto que se fije el horno",
                                   uid=222))

    salida = textos(enviados)
    assert len(salida) == 1, "todavía no se entrega"
    assert "ahora mismo" not in salida[0]
    recado = db.pendientes(CHAT, tipo="recado")[0]
    assert recado["due_hora"] is not None


def test_el_recado_con_hora_se_entrega_a_esa_hora(enviados):
    db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                   due=SABADO, hora="16:31", created_by="barbu")
    antes = datetime(2026, 9, 26, 16, 30, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=antes)["recados"] == 0

    despues = datetime(2026, 9, 26, 16, 31, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=despues)["recados"] == 1
    assert "horno" in textos(enviados)[-1]


def test_el_recado_sin_hora_espera_la_pasada_principal(enviados, monkeypatch):
    monkeypatch.setattr(config, "CRON_MINUTOS", 15)
    db.crear_tarea(CHAT, "comprá pan", tipo="recado", responsable="axel",
                   due=SABADO, created_by="barbu")
    temprano = datetime(2026, 9, 26, 10, 0, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=temprano)["recados"] == 0

    noche = datetime(2026, 9, 26, 20, 0, tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=noche)["recados"] == 1


def test_el_modo_local_entiende_las_horas():
    data = heuristica.interpretar("llevar a Milo al veterinario el jueves a las 18")
    assert data["items"][0]["fecha_hora"] == 18
    assert data["items"][0]["fecha_kind"] == "dia_semana"

    data = heuristica.interpretar("llamar a la inmobiliaria en 2 horas")
    assert data["items"][0]["fecha_kind"] == "en_minutos"
    assert data["items"][0]["fecha_minutos"] == 120


# ==========================================================================
# ALTA 5 · Reprogramar, reasignar, renombrar
# ==========================================================================

def test_reprogramar_por_texto(enviados, monkeypatch):
    """Antes contestaba «todavía no sé pasar tareas de un día para el otro»."""
    tid = db.crear_tarea(CHAT, "limpiar el horno", categoria="limpieza", due=hoy())
    responde(monkeypatch, intencion="reprogramar", objetivos=["horno"],
             cambio_fecha_kind="dia_semana", cambio_fecha_weekday=6)

    handlers.handle_update(mensaje("pasá lo del horno para el domingo"))

    assert db.obtener(tid)["due_date"] != hoy().isoformat()
    assert date.fromisoformat(db.obtener(tid)["due_date"]).weekday() == 6
    assert "📅" in textos(enviados)[0]


def test_reprogramar_resetea_el_cansancio(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "limpiar el horno", due=hoy() - timedelta(days=5))
    db.actualizar(tid, recordada_veces=4, last_reminded_on=hoy().isoformat())
    responde(monkeypatch, intencion="reprogramar", objetivos=["horno"],
             cambio_fecha_kind="manana")

    handlers.handle_update(mensaje("pasá lo del horno para mañana"))

    fila = db.obtener(tid)
    assert fila["recordada_veces"] == 0 and fila["last_reminded_on"] is None


def test_reprogramar_con_hora(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "llamar a la inmobiliaria", due=hoy())
    responde(monkeypatch, intencion="reprogramar", objetivos=["inmobiliaria"],
             cambio_fecha_kind="hoy", cambio_fecha_hora=18, cambio_fecha_minuto=30)

    handlers.handle_update(mensaje("lo de la inmobiliaria a las 18:30"))
    assert db.obtener(tid)["due_hora"] == "18:30"


def test_reprogramar_sin_fecha_pregunta(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "limpiar el horno", due=hoy())
    responde(monkeypatch, intencion="reprogramar", objetivos=["horno"],
             cambio_fecha_kind="desconocida")

    handlers.handle_update(mensaje("pasá lo del horno"))

    assert "¿Para cuándo" in textos(enviados)[0]
    assert (db.get_pending(CHAT) or {}).get("task_id") == tid


def test_reasignar_por_texto(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "llevar a Milo al veterinario", responsable="barbu",
                         due=hoy())
    responde(monkeypatch, intencion="reasignar", objetivos=["veterinario"],
             cambio_responsable="axel")

    handlers.handle_update(mensaje("lo del veterinario lo hago yo"))

    assert db.obtener(tid)["responsable"] == "axel"
    assert "Axel" in textos(enviados)[0]


def test_reasignar_sin_nombre_usa_a_quien_escribe(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "regar las plantas", due=hoy())
    responde(monkeypatch, intencion="reasignar", objetivos=["regar"],
             cambio_responsable="ninguno")

    handlers.handle_update(mensaje("lo de regar lo hago yo", uid=222))
    assert db.obtener(tid)["responsable"] == "barbu"


def test_renombrar_por_texto(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    responde(monkeypatch, intencion="renombrar", objetivos=["regar"],
             cambio_texto="regar las plantas del balcón")

    handlers.handle_update(mensaje("cambiá 'regar' por 'regar las plantas del balcón'"))

    assert db.obtener(tid)["texto"] == "regar las plantas del balcón"
    assert "✏️" in textos(enviados)[0]


def test_editar_algo_ambiguo_pregunta_con_botones(enviados, monkeypatch):
    a = db.crear_tarea(CHAT, "limpiar la heladera", due=hoy())
    db.crear_tarea(CHAT, "descongelar la heladera", due=hoy())
    responde(monkeypatch, intencion="reprogramar", objetivos=["heladera"],
             cambio_fecha_kind="manana")

    handlers.handle_update(mensaje("pasá la de la heladera para mañana"))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    assert "¿Cuál de estas" in envio["text"]
    botones = [b for fila in envio["reply_markup"]["inline_keyboard"] for b in fila]
    assert all(b["callback_data"].startswith("xa:") for b in botones)

    # Al elegir, se aplica la acción que estaba pendiente.
    handlers.handle_update(click(f"xa:{a}"))
    assert db.obtener(a)["due_date"] == (hoy() + timedelta(days=1)).isoformat()


# ==========================================================================
# MEDIA 6 · Fechas que no existen
# ==========================================================================

def test_el_31_de_febrero_avisa_y_pregunta(enviados, monkeypatch):
    """Guardaba el 28/2 en silencio."""
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("pagar la expensa", categoria="pagos", fecha_kind="fecha_exacta",
                         fecha_day=31, fecha_month=2)])

    handlers.handle_update(mensaje("pagar la expensa el 31 de febrero"))

    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert tarea["due_date"] is None, "no se inventa una fecha"
    salida = " ".join(textos(enviados))
    assert "31 de febrero no existe" in salida
    assert (db.get_pending(CHAT) or {}).get("kind") == "fecha"


def test_una_fecha_que_si_existe_no_molesta(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("pagar la expensa", fecha_kind="fecha_exacta",
                         fecha_day=28, fecha_month=2)])
    handlers.handle_update(mensaje("pagar la expensa el 28 de febrero"))

    assert db.pendientes(CHAT, tipo="casa")[0]["due_date"] is not None
    assert "no existe" not in " ".join(textos(enviados))


def test_reprogramar_a_una_fecha_imposible_tambien_avisa(enviados, monkeypatch):
    db.crear_tarea(CHAT, "pagar la expensa", due=hoy())
    responde(monkeypatch, intencion="reprogramar", objetivos=["expensa"],
             cambio_fecha_kind="fecha_exacta", cambio_fecha_day=30,
             cambio_fecha_month=2)

    handlers.handle_update(mensaje("pasá la expensa al 30 de febrero"))
    assert "no existe" in " ".join(textos(enviados))


# ==========================================================================
# MEDIA 7 · Las semanas
# ==========================================================================

def test_el_jueves_que_viene_no_es_esta_semana(enviados):
    """Con hoy = sábado 26/9, el jueves 1/10 es la semana que viene."""
    db.crear_tarea(CHAT, "llamar al gas", due=date(2026, 10, 1))

    texto = views.render_todo(CHAT, ref=SABADO)

    assert "LA SEMANA QUE VIENE" in texto
    assert "ESTA SEMANA" not in texto


def test_el_domingo_si_es_esta_semana(enviados):
    db.crear_tarea(CHAT, "lavar las cortinas", due=date(2026, 9, 27))
    # El domingo 27 es "mañana" respecto del sábado 26: va en MAÑANA.
    assert "MAÑANA" in views.render_todo(CHAT, ref=SABADO)


def test_desde_el_lunes_la_semana_llega_hasta_el_domingo(enviados):
    lunes = date(2026, 9, 21)
    db.crear_tarea(CHAT, "regar", due=date(2026, 9, 27))       # domingo: esta semana
    db.crear_tarea(CHAT, "pagar el ABL", due=date(2026, 9, 29))  # martes: la que viene

    texto = views.render_todo(CHAT, ref=lunes)
    assert texto.index("ESTA SEMANA") < texto.index("LA SEMANA QUE VIENE")
    assert "Regar" in texto.split("LA SEMANA QUE VIENE")[0]


def test_lo_muy_lejano_va_en_mas_adelante(enviados):
    db.crear_tarea(CHAT, "renovar el dni", due=SABADO + timedelta(days=30))
    assert "MÁS ADELANTE" in views.render_todo(CHAT, ref=SABADO)


# ==========================================================================
# MEDIA 8 · Recurrencias con intervalo
# ==========================================================================

def test_cada_3_dias_se_guarda_con_intervalo(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("regar las plantas", fecha_kind="hoy", recur_kind="diaria",
                         recur_interval=3)])

    handlers.handle_update(mensaje("regar las plantas cada 3 días"))

    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert tarea["recur_kind"] == "diaria" and tarea["recur_interval"] == 3
    assert "cada 3 días" in textos(enviados)[0]
    assert "cada 3 días" in views.render_todo(CHAT)


def test_la_proxima_de_cada_3_dias_es_a_los_3_dias(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("regar las plantas", fecha_kind="hoy", recur_kind="diaria",
                         recur_interval=3)])
    handlers.handle_update(mensaje("regar las plantas cada 3 días"))
    tid = db.pendientes(CHAT, tipo="casa")[0]["id"]

    nueva = db.marcar_hecha(tid, "axel")

    assert nueva is not None
    assert date.fromisoformat(nueva["due_date"]) == hoy() + timedelta(days=3)


@pytest.mark.parametrize("rec,esperado", [
    (Recurrencia("diaria", interval=3), "cada 3 días"),
    (Recurrencia("diaria"), "todos los días"),
    (Recurrencia("semanal", weekday=1), "todos los martes"),
    (Recurrencia("semanal", interval=2), "cada 2 semanas"),
    (Recurrencia("mensual", monthday=10), "todos los 10"),
    (Recurrencia("mensual", interval=3), "cada 3 meses"),
    (Recurrencia("anual"), "todos los años"),
    (None, ""),
])
def test_como_se_cuenta_la_recurrencia(rec, esperado):
    assert texto_recurrencia(rec) == esperado


def test_el_modo_local_guarda_el_intervalo():
    data = heuristica.interpretar("regar las plantas cada 3 dias")
    assert data["items"][0]["recur_kind"] == "diaria"
    assert data["items"][0]["recur_interval"] == 3


def test_la_recurrencia_de_3_dias_no_se_confunde_con_semanal():
    rec = Recurrencia("diaria", interval=3)
    assert proxima_ocurrencia(rec, SABADO) == SABADO + timedelta(days=3)


# ==========================================================================
# MEDIA 9 · Nombres del súper
# ==========================================================================

def test_el_super_guarda_la_cosa_sin_el_verbo(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True, items=[
        item("comprar lavandina", tipo="compras", categoria="compras",
             fecha_kind="algun_dia"),
        item("comprar detergente para los platos", tipo="compras", categoria="compras",
             fecha_kind="algun_dia"),
    ])

    handlers.handle_update(mensaje("hay q comprar lavandina y detergente pa los platos"))

    assert [r["texto"] for r in db.pendientes(CHAT, tipo="compras")] == [
        "lavandina", "detergente para los platos"]


def test_el_super_se_muestra_con_mayuscula(enviados):
    compra("detergente para los platos")
    texto, teclado = views.render_super(CHAT)
    assert "Detergente para los platos" in texto
    assert teclado[0][0]["text"] == "🛒 Detergente para los platos"
    # Y en /todo, donde antes salían en minúscula.
    assert "Detergente para los platos" in views.render_todo(CHAT)


def test_falta_leche_y_comprar_lavandina_quedan_iguales(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True, items=[
        item("leche", tipo="compras", categoria="compras", fecha_kind="algun_dia"),
        item("comprar lavandina", tipo="compras", categoria="compras",
             fecha_kind="algun_dia"),
    ])
    handlers.handle_update(mensaje("falta leche y hay que comprar lavandina"))

    texto, _ = views.render_super(CHAT)
    assert "Leche" in texto and "Lavandina" in texto
    assert "Comprar" not in texto


# ==========================================================================
# MEDIA 10 · Chat privado
# ==========================================================================

def test_por_privado_contesta_algo(enviados):
    """Antes no decía nada y parecía roto."""
    handlers.handle_update(mensaje("/start", uid=111, tipo_chat="private", chat_id=555))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    assert envio["chat_id"] == 555
    assert "grupo de la casa" in envio["text"]


def test_por_privado_no_gasta_gemini(enviados, monkeypatch):
    monkeypatch.setattr(llm, "interpretar_mensaje",
                        lambda *a, **k: pytest.fail("no tiene que llamar al LLM"))
    handlers.handle_update(mensaje("hola", tipo_chat="private", chat_id=555))
    assert textos(enviados)


def test_en_un_grupo_ajeno_se_queda_callado(enviados):
    handlers.handle_update(mensaje("hola", tipo_chat="group", chat_id=-100999))
    assert enviados == []


def test_por_privado_no_anota_nada(enviados):
    handlers.handle_update(mensaje("hay que limpiar la heladera", tipo_chat="private",
                                   chat_id=555))
    assert db.pendientes(555) == []


# ==========================================================================
# BAJA 11 · Recado a uno mismo
# ==========================================================================

def test_un_recado_a_uno_mismo_es_un_recordatorio(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("llamar al gas", tipo="recado", responsable="axel",
                         fecha_kind="manana")])

    handlers.handle_update(mensaje("avisale a Axel mañana que llame al gas", uid=111))

    salida = textos(enviados)[0]
    assert "Te recuerdo" in salida
    assert "manda a decir" not in salida


def test_al_entregarlo_tampoco_dice_que_alguien_lo_manda(enviados):
    db.crear_tarea(CHAT, "llamar al gas", tipo="recado", responsable="axel",
                   due=hoy(), created_by="axel")
    reminders.correr_rutina_diaria(ref=hoy())

    # Sin `[-1]`: los domingos después de los recados va el resumen de la semana.
    entrega = [t for t in textos(enviados) if "recuerdo" in t.lower()][0]
    assert "te manda a decir" not in entrega


def test_un_recado_a_otro_sigue_igual(enviados):
    db.crear_tarea(CHAT, "comprá pan", tipo="recado", responsable="axel",
                   due=hoy(), created_by="barbu")
    reminders.correr_rutina_diaria(ref=hoy())
    assert any("te manda a decir" in t for t in textos(enviados))


# ==========================================================================
# BAJA 12 · «el finde»
# ==========================================================================

def test_el_finde_se_muestra_como_este_finde(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("lavar las cortinas", fecha_kind="fin_de_semana")])

    handlers.handle_update(mensaje("para el finde: lavar las cortinas"))

    salida = textos(enviados)[0]
    assert "finde" in salida
    # Sin perder el día concreto, que es lo que importa para el recordatorio.
    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert date.fromisoformat(tarea["due_date"]).weekday() in (5, 6)


def test_el_dia_exacto_no_se_pierde(enviados):
    from notita.dates import DateSpec, es_este_finde, resolve

    # Un miércoles, «el finde» es el sábado de esa misma semana.
    miercoles = date(2026, 9, 30)
    sabado = resolve(DateSpec("fin_de_semana"), miercoles)
    assert sabado == date(2026, 10, 3)
    assert es_este_finde(sabado, miercoles)
    assert views.texto_del_finde(sabado, miercoles) == "este finde · Sáb 3/10"


def test_si_cae_la_semana_que_viene_no_dice_este(enviados):
    from notita.dates import es_este_finde

    domingo = date(2026, 9, 27)
    sabado_que_viene = date(2026, 10, 3)
    assert not es_este_finde(sabado_que_viene, domingo)
    assert views.texto_del_finde(sabado_que_viene, domingo).startswith("el finde")
