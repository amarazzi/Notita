"""El bot reprocesaba el mensaje anterior junto con el nuevo.

Pasó en el grupo: «falta pan» → «Anotado: Pan», y después «hay que limpiar la
heladera el martes» → «… Ya estaban anotadas: Pan». Peor: «ya compramos todo»
después de «falta azúcar» se interpretaba como anotar y el súper no se vaciaba.

La causa era `contexto_previo`: cuando quedaba una pregunta de aclaración abierta, el
mensaje siguiente se le mandaba al LLM junto con el anterior, y el modelo tomaba el
anterior como contenido para anotar, no como contexto.
"""
from datetime import datetime, timedelta

import pytest

from notita import config, db, handlers, heuristica, llm, reminders
from notita.dates import hoy

from .conftest import CHAT
from .test_bugs_grupo import click, item, mensaje, textos


def llm_por_texto(monkeypatch, respuestas):
    """Un LLM falso que contesta según el texto que REALMENTE recibe.

    La clave del test: si le llega el mensaje anterior pegado, se nota.
    """
    recibidos = []

    def fake(texto, autor, ref=None, contexto_previo=None):
        recibidos.append({"texto": texto, "contexto": contexto_previo})
        for clave, respuesta in respuestas.items():
            if clave in texto:
                return dict(respuesta)
        return {"intencion": "charla", "es_tarea": False, "comentario": "", "items": []}

    monkeypatch.setattr(llm, "interpretar_mensaje", fake)
    return recibidos


ANOTAR_PAN = {"intencion": "anotar", "es_tarea": True,
              "items": [item("pan", tipo="compras", categoria="compras",
                             fecha_kind="algun_dia")]}
ANOTAR_HELADERA = {"intencion": "anotar", "es_tarea": True,
                   "items": [item("limpiar la heladera", categoria="limpieza",
                                  fecha_kind="dia_semana", fecha_weekday=1)]}
VACIAR = {"intencion": "vaciar_super", "es_tarea": False, "items": []}


# --------------------------------------------------------------------------
# Los dos casos del reporte
# --------------------------------------------------------------------------

def test_cada_respuesta_refleja_solo_su_mensaje(enviados, monkeypatch):
    llm_por_texto(monkeypatch, {"pan": ANOTAR_PAN, "heladera": ANOTAR_HELADERA})

    handlers.handle_update(mensaje("falta pan", update_id=1))
    handlers.handle_update(mensaje("hay que limpiar la heladera el martes", update_id=2))

    primera, segunda = textos(enviados)
    assert "Pan" in primera
    assert "Limpiar la heladera" in segunda
    assert "Pan" not in segunda, "la segunda respuesta no puede hablar del pan"
    assert "Ya estaban anotadas" not in segunda


def test_ya_compramos_todo_vacia_el_super_aunque_venga_despues(enviados, monkeypatch):
    llm_por_texto(monkeypatch, {"azúcar": {
        "intencion": "anotar", "es_tarea": True,
        "items": [item("azúcar", tipo="compras", categoria="compras",
                       fecha_kind="algun_dia")]},
        "compramos todo": VACIAR})

    handlers.handle_update(mensaje("falta azúcar", update_id=1))
    assert len(db.pendientes(CHAT, tipo="compras")) == 1

    handlers.handle_update(mensaje("ya compramos todo", update_id=2))

    assert db.pendientes(CHAT, tipo="compras") == [], "el súper tiene que quedar vacío"
    assert "ya estaba anotado" not in textos(enviados)[-1].lower()


# --------------------------------------------------------------------------
# Con una aclaración pendiente de verdad
# --------------------------------------------------------------------------

def preguntar_aclaracion(monkeypatch, enviados):
    """Deja abierta una pregunta de aclaración, como cuando el LLM no entiende."""
    def fake(texto, autor, ref=None, contexto_previo=None):
        return {"intencion": "anotar", "es_tarea": True,
                "items": [item("eso", necesita_aclaracion=True,
                               pregunta="¿Qué cosa de la heladera?")]}

    monkeypatch.setattr(llm, "interpretar_mensaje", fake)
    handlers.handle_update(mensaje("la heladera", update_id=99))
    assert (db.get_pending(CHAT) or {}).get("kind") == "aclaracion"
    enviados.clear()


def test_un_mensaje_que_se_entiende_solo_no_es_una_aclaracion(enviados, monkeypatch):
    preguntar_aclaracion(monkeypatch, enviados)
    recibidos = llm_por_texto(monkeypatch, {"compramos todo": VACIAR})
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

    handlers.handle_update(mensaje("ya compramos todo", update_id=100))

    assert recibidos[-1]["contexto"] is None, "no se le pega el mensaje anterior"
    assert db.pendientes(CHAT, tipo="compras") == []


def test_una_aclaracion_de_verdad_si_usa_el_contexto(enviados, monkeypatch):
    preguntar_aclaracion(monkeypatch, enviados)
    recibidos = llm_por_texto(monkeypatch, {"descongelar": {
        "intencion": "anotar", "es_tarea": True,
        "items": [item("descongelar la heladera", categoria="limpieza",
                       fecha_kind="hoy")]}})

    handlers.handle_update(mensaje("descongelar", update_id=101))

    assert recibidos[-1]["contexto"] == "la heladera", "para eso está el contexto"
    assert db.pendientes(CHAT, tipo="casa")[0]["texto"] == "descongelar la heladera"


@pytest.mark.parametrize("frase,completo", [
    ("ya compramos todo", True),
    ("hay que limpiar la heladera el martes", True),
    ("falta pan", True),
    ("borrá la del plomero", True),
    ("mostrame el super", True),
    ("la heladera", False),
    ("la de arriba", False),
    ("azul", False),
    ("el del kiosco", False),
])
def test_que_cuenta_como_pedido_completo(frase, completo):
    assert heuristica.parece_pedido_completo(frase) is completo


def test_la_aclaracion_caduca_rapido(enviados, monkeypatch):
    """Contestar cuatro horas después no es aclarar: es un mensaje nuevo."""
    preguntar_aclaracion(monkeypatch, enviados)

    with db.conn() as c:     # la envejecemos media hora
        viejo = "2020-01-01T10:00:00-03:00"
        c.execute("UPDATE pending SET created_at = ?", (viejo,))

    assert db.get_pending(CHAT) is None


def test_la_de_fecha_dura_mas_que_la_de_aclaracion():
    assert db.PENDING_MINUTOS_ACLARACION < db.PENDING_HORAS * 60


def test_el_prompt_aclara_que_el_contexto_no_se_anota(monkeypatch):
    partes = {}

    def espiar(texto, schema, sistema):
        partes["texto"] = texto
        return {"intencion": "charla", "es_tarea": False, "items": []}

    monkeypatch.setattr(llm, "_call", espiar)
    monkeypatch.setattr(llm.config, "GEMINI_API_KEY", "x")
    llm.interpretar_mensaje("descongelar", "axel", hoy(), contexto_previo="la heladera")

    assert "NO devuelvas items por lo del mensaje anterior" in partes["texto"]
    assert "la heladera" in partes["texto"]


# --------------------------------------------------------------------------
# El recado con hora: que se entregue, aunque la hora haya pasado
# --------------------------------------------------------------------------

def test_un_recado_cuya_hora_paso_se_entrega_en_la_corrida_siguiente(enviados):
    """«en 1 minuto» a las 22:06: si el cron corre a las 23:16, sale ahí."""
    db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                   due=hoy(), hora="22:07", created_by="barbu")

    tarde = datetime.combine(hoy(), datetime.min.time(),
                             tzinfo=config.TZ).replace(hour=23, minute=16)
    assert reminders.correr_rutina_diaria(momento=tarde)["recados"] == 1
    # Sin mirar el último: los domingos después va el resumen de la semana.
    assert any("horno" in t for t in textos(enviados))


def test_un_recado_de_ayer_con_hora_no_se_descarta(enviados):
    db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                   due=hoy() - timedelta(days=1), hora="22:07", created_by="barbu")

    assert reminders.correr_rutina_diaria(ref=hoy())["recados"] == 1


def test_un_recado_de_hoy_a_una_hora_futura_espera(enviados):
    db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                   due=hoy(), hora="23:50", created_by="barbu")

    temprano = datetime.combine(hoy(), datetime.min.time(),
                                tzinfo=config.TZ).replace(hour=21, minute=0)
    assert reminders.correr_rutina_diaria(momento=temprano)["recados"] == 0
    # Pero al otro día sale, sin depender de la hora.
    assert reminders.correr_rutina_diaria(ref=hoy() + timedelta(days=1))["recados"] == 1


def test_con_el_cron_cada_5_minutos_sale_a_la_hora(enviados, monkeypatch):
    monkeypatch.setattr(config, "CRON_MINUTOS", 5)
    db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                   due=hoy(), hora="22:10", created_by="barbu")

    base = datetime.combine(hoy(), datetime.min.time(), tzinfo=config.TZ)
    assert reminders.correr_rutina_diaria(momento=base.replace(hour=22, minute=5))["recados"] == 0
    assert reminders.correr_rutina_diaria(momento=base.replace(hour=22, minute=10))["recados"] == 1


# --------------------------------------------------------------------------
# Y el singular, que el reporte volvió a ver
# --------------------------------------------------------------------------

def test_una_sola_cosa_se_tacha_en_singular(enviados):
    db.crear_tarea(CHAT, "café", tipo="compras", categoria="compras")
    handlers.handle_update(click("ct"))

    editado = [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]
    assert editado.startswith("✅ Tachada:")


def test_y_varias_en_plural(enviados):
    for cosa in ("café", "leche"):
        db.crear_tarea(CHAT, cosa, tipo="compras", categoria="compras")
    handlers.handle_update(click("ct"))

    editado = [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]
    assert editado.startswith("✅ Tachadas las 2:")


def test_tambien_en_singular_por_texto(enviados, monkeypatch):
    db.crear_tarea(CHAT, "café", tipo="compras", categoria="compras")
    llm_por_texto(monkeypatch, {"compramos todo": VACIAR})

    handlers.handle_update(mensaje("ya compramos todo"))

    assert textos(enviados)[-1].startswith("✅ Tachada:")
