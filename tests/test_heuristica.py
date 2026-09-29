"""Tests del modo sin LLM: todo se interpreta en Python, sin salir a internet."""
from datetime import date, timedelta

import pytest

from notita import config, db, handlers, heuristica, llm
from notita.dates import extraer_fecha, extraer_recurrencia, hoy, resolve

from .conftest import CHAT

MARTES = date(2026, 9, 29)


def un_item(texto: str) -> dict:
    data = heuristica.interpretar(texto)
    assert data["intencion"] == "crear", f"no lo tomó como tarea: {texto}"
    assert len(data["items"]) == 1, data["items"]
    return data["items"][0]


# --------------------------------------------------------------------------
# Extracción de fecha y recurrencia dentro de una frase
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frase,esperado,resto", [
    ("limpiar la heladera el lunes", date(2026, 10, 5), "limpiar la heladera"),
    ("regar las plantas mañana", date(2026, 9, 30), "regar las plantas"),
    ("sacar la basura hoy", MARTES, "sacar la basura"),
    ("pagar expensas el 3 de octubre", date(2026, 10, 3), "pagar expensas"),
    ("turno con el dentista el 15/10", date(2026, 10, 15), "turno con el dentista"),
    ("pintar el balcón esta semana", date(2026, 10, 4), "pintar el balcón"),
    ("llamar al plomero la semana que viene", date(2026, 10, 11), "llamar al plomero"),
    ("hacer el trámite el lunes de la semana que viene", date(2026, 10, 5), "hacer el trámite"),
    ("ordenar el placard en dos semanas", date(2026, 10, 13), "ordenar el placard"),
    ("hacer las compras el finde", date(2026, 10, 3), "hacer las compras"),
    ("colgar el cuadro algún día", None, "colgar el cuadro"),
    ("colgar el cuadro", None, "colgar el cuadro"),
])
def test_extraer_fecha_de_una_frase(frase, esperado, resto):
    spec, sobra = extraer_fecha(frase)
    assert resolve(spec, MARTES) == esperado
    assert sobra == resto


def test_extraer_fecha_conserva_las_tildes():
    # El texto de la tarea no se puede "aplanar": tiene que quedar como lo escribieron.
    _, resto = extraer_fecha("comprar una cómoda el jueves")
    assert resto == "comprar una cómoda"


@pytest.mark.parametrize("frase,kind,interval,weekday,monthday,resto", [
    ("cambiar las piedritas cada semana", "semanal", 1, None, None, "cambiar las piedritas"),
    ("pagar expensas todos los 10", "mensual", 1, None, 10, "pagar expensas"),
    ("sacar la basura todos los martes", "semanal", 1, 1, None, "sacar la basura"),
    ("regar las plantas todos los días", "diaria", 1, None, None, "regar las plantas"),
    ("revisar el auto cada tres meses", "mensual", 3, None, None, "revisar el auto"),
    ("limpiar los filtros cada dos semanas", "semanal", 2, None, None, "limpiar los filtros"),
])
def test_extraer_recurrencia(frase, kind, interval, weekday, monthday, resto):
    rec, sobra = extraer_recurrencia(frase)
    assert (rec.kind, rec.interval, rec.weekday, rec.monthday) == (kind, interval, weekday, monthday)
    assert sobra == resto


def test_sin_recurrencia_no_toca_el_texto():
    rec, sobra = extraer_recurrencia("limpiar la heladera")
    assert rec is None and sobra == "limpiar la heladera"


# --------------------------------------------------------------------------
# Clasificación
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frase", [
    "falta leche", "comprar yerba", "se acabó el detergente",
    "faltan servilletas", "traer pan",
])
def test_lo_que_va_a_compras(frase):
    item = un_item(frase)
    assert item["compra"] is True
    assert item["categoria"] == "compras"
    # Ya no se fuerza la fecha: una compra puede tener día, o ninguno.
    assert item["fecha_kind"] in ("desconocida", "algun_dia")


@pytest.mark.parametrize("frase", [
    "falta pagar la luz",          # «falta» pero es una acción: no es del súper
    "hay que llamar al plomero",
    "limpiar la heladera",
    "falta sacar la basura",
])
def test_lo_que_no_va_a_compras(frase):
    assert un_item(frase)["compra"] is False


@pytest.mark.parametrize("frase,categoria", [
    ("pagar las expensas", "pagos"),
    ("sacar turno para el dni", "tramites"),
    ("llevar el gato al veterinario", "mascotas"),
    ("arreglar la canilla", "arreglos"),
    ("limpiar el baño", "limpieza"),
    ("devolver el libro", "otros"),
])
def test_categorias_por_palabra_clave(frase, categoria):
    assert un_item(frase)["categoria"] == categoria


def test_responsable_por_nombre():
    item = un_item("Barbu tiene que llamar al veterinario el lunes")
    assert item["responsable"] == "barbu"
    assert item["titulo"] == "llamar al veterinario"  # sin el nombre ni la fecha


def test_responsable_ambos():
    item = un_item("los dos tenemos que ordenar el placard mañana")
    assert item["responsable"] == "ambos"
    assert item["titulo"] == "ordenar el placard"


def test_responsable_cuando_se_nombran_los_dos():
    assert un_item("Axel y Barbu tienen que ordenar el garage")["responsable"] == "ambos"


def test_sin_responsable():
    assert un_item("colgar el cuadro")["responsable"] == "ninguno"


# --------------------------------------------------------------------------
# Separación en varios items
# --------------------------------------------------------------------------

def test_separa_por_comas():
    data = heuristica.interpretar("hay que limpiar la heladera, llamar al plomero y comprar focos")
    assert [i["titulo"] for i in data["items"]] == [
        "limpiar la heladera", "llamar al plomero", "comprar focos"]
    # «focos» es ferretería: en v2 va a tareas, no al súper.
    assert [i["compra"] for i in data["items"]] == [False, False, False]


def test_una_lista_de_compras_contagia_el_tipo():
    # El verbo está sólo en el primer pedazo: «comprar yerba, pan y dulce de leche».
    data = heuristica.interpretar("comprar yerba, pan y dulce de leche")
    assert [i["titulo"] for i in data["items"]] == ["comprar yerba", "pan", "dulce de leche"]
    assert all(i["compra"] for i in data["items"])


def test_sin_comas_no_parte_la_frase():
    # Sin LLM no arriesgamos: «y» sin coma puede ser parte de la tarea.
    item = un_item("hablar con el plomero y el electricista")
    assert item["titulo"] == "hablar con el plomero y el electricista"


def test_enumeracion_larguisima_se_deja_entera():
    largo = "comprar a, b, c, d, e, f, g, h"
    assert len(heuristica.interpretar(largo)["items"]) == 1


# --------------------------------------------------------------------------
# Charla
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frase,responde", [
    ("hola", True), ("hola notita", True), ("buenas", True), ("gracias", True),
    ("jajaja", False), ("dale", False), ("ok", False), ("🤍", False), ("?", False),
])
def test_la_charla_no_se_anota(frase, responde):
    data = heuristica.interpretar(frase)
    assert data["intencion"] != "crear"
    assert bool(data["comentario"]) is responde


def test_una_sola_palabra_rara_igual_se_anota():
    # Ante la duda, preferimos anotar algo raro antes que perderlo.
    assert un_item("mudanza")["titulo"] == "mudanza"


# --------------------------------------------------------------------------
# Integración: el bot sin GEMINI_API_KEY
# --------------------------------------------------------------------------

def mensaje(texto):
    return {"message": {"chat": {"id": CHAT}, "from": {"id": 111}, "text": texto,
                        "message_id": 1}}


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


def test_sin_api_key_igual_anota(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm, "interpretar_mensaje",
                        lambda *a, **k: pytest.fail("no debería llamar a Gemini"))

    handlers.handle_update(mensaje("hay que limpiar la heladera el lunes, y falta leche"))

    casa = db.pendientes(CHAT, compra=False)
    compras = db.pendientes(CHAT, compra=True)
    # El texto se guarda como lo dijeron, también en modo local.
    assert [r["texto"] for r in casa] == ["limpiar la heladera"]
    assert [r["texto"] for r in compras] == ["falta leche"]
    assert casa[0]["categoria"] == "limpieza"
    assert date.fromisoformat(casa[0]["due_date"]).weekday() == 0
    # En modo local no se disculpa: es el modo normal.
    assert "a mano" not in textos(enviados)[0]


def test_sin_api_key_no_pregunta_fecha_si_ya_la_dijeron(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    handlers.handle_update(mensaje("regar las plantas mañana"))
    assert db.get_pending(CHAT) is None
    assert db.pendientes(CHAT)[0]["due_date"] == (hoy() + timedelta(days=1)).isoformat()


def test_sin_api_key_no_bloquea_si_falta_la_fecha(enviados, monkeypatch):
    """Principio 3 de v2: nunca bloquear. Se guarda sin fecha y se ofrece corregir."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")

    handlers.handle_update(mensaje("hay que llamar al plomero"))

    guardada = db.pendientes(CHAT, compra=False)[0]
    assert guardada["due_date"] is None
    assert db.get_pending(CHAT) is None, "no queda esperando nada"
    assert "Corregir" in str(enviados)


def test_recurrente_sin_llm(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    handlers.handle_update(mensaje("pagar expensas todos los 10"))
    tarea = db.pendientes(CHAT)[0]
    assert tarea["recur_kind"] == "mensual"
    assert tarea["recur_monthday"] == 10
    assert date.fromisoformat(tarea["due_date"]).day == 10


def test_si_gemini_falla_la_tarea_no_se_pierde(enviados, monkeypatch):
    # Antes esto contestaba «se me trabó la cabeza» y guardaba el mensaje crudo.
    monkeypatch.setattr(config, "GEMINI_API_KEY", "hay-key-pero-falla")
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: None)

    handlers.handle_update(mensaje("hay que limpiar la heladera el lunes"))

    tarea = db.pendientes(CHAT)[0]
    assert tarea["texto"] == "limpiar la heladera"
    assert tarea["categoria"] == "limpieza"
    # En v2 no se disculpa: la tarea quedó bien guardada y se puede corregir tocando.
    assert "Limpiar la heladera" in textos(enviados)[0]


def test_charla_sin_llm_no_crea_tareas(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    handlers.handle_update(mensaje("hola notita"))
    assert db.pendientes(CHAT) == []
    assert "Hola" in textos(enviados)[0]
