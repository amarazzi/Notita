"""Tests de entender pedidos hablando normal, sin comandos.

Antes, «mostrame las tareas de limpieza» contestaba «no puedo mostrarte tareas,
solo anoto», que además de inútil era falso.
"""
from datetime import timedelta

import pytest

from notita import db, handlers, heuristica, llm
from notita.dates import hoy

from .conftest import CHAT


def mensaje(texto, user_id=111):
    return {"message": {"chat": {"id": CHAT}, "from": {"id": user_id}, "text": texto,
                        "message_id": 1}}


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


def fake_intencion(monkeypatch, intencion, referencia="", categoria="ninguna"):
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": intencion, "referencia": referencia, "categoria_filtro": categoria,
        "es_tarea": False, "comentario": "", "items": []})


@pytest.fixture
def casa_con_tareas():
    db.crear_tarea(CHAT, "limpiar la heladera", categoria="limpieza", due=hoy())
    db.crear_tarea(CHAT, "llamar al plomero", categoria="arreglos", due=hoy() + timedelta(days=2))
    db.crear_tarea(CHAT, "pintar el balcón", categoria="arreglos")  # algún día
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")


# --------------------------------------------------------------------------
# Ver
# --------------------------------------------------------------------------

def test_ver_pendientes(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "ver_pendientes")
    handlers.handle_update(mensaje("qué hay que hacer?"))

    salida = textos(enviados)[0]
    assert "Limpiar la heladera" in salida and "Llamar al plomero" in salida


def test_ver_pendientes_de_una_categoria(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "ver_pendientes", categoria="limpieza")
    handlers.handle_update(mensaje("mostrame las tareas de limpieza"))

    salida = textos(enviados)[0]
    assert "Limpiar la heladera" in salida
    assert "Llamar al plomero" not in salida


def test_una_categoria_que_no_existe_se_ignora(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "ver_pendientes", categoria="jardinería")
    handlers.handle_update(mensaje("mostrame las de jardinería"))
    # No revienta: muestra todo.
    assert "Llamar al plomero" in textos(enviados)[0]


def test_ver_super(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "ver_super")
    handlers.handle_update(mensaje("mostrame la lista del super"))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    assert "leche" in envio["text"]
    assert envio["reply_markup"]["inline_keyboard"]  # con los botones para tachar


def test_ver_algun_dia(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "ver_algun_dia")
    handlers.handle_update(mensaje("qué teníamos para algún día?"))
    assert "Pintar el balcón" in textos(enviados)[0]


def test_ver_ayuda(enviados, monkeypatch):
    fake_intencion(monkeypatch, "ver_ayuda")
    handlers.handle_update(mensaje("cómo funcionás?"))
    assert "soy <b>Notita</b>" in textos(enviados)[0]


def test_ver_no_anota_nada(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "ver_pendientes")
    antes = len(db.pendientes(CHAT))
    handlers.handle_update(mensaje("qué hay que hacer?"))
    assert len(db.pendientes(CHAT)) == antes


# --------------------------------------------------------------------------
# Completar y borrar hablando
# --------------------------------------------------------------------------

def test_completar_por_texto(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "completar", referencia="heladera")
    handlers.handle_update(mensaje("ya limpié la heladera"))

    hechas = [r["texto"] for r in db.pendientes(CHAT)]
    assert "limpiar la heladera" not in hechas
    assert "✅" in textos(enviados)[0]


def test_completar_una_recurrente_crea_la_proxima(enviados, monkeypatch):
    from notita.dates import Recurrencia

    db.crear_tarea(CHAT, "sacar la basura", due=hoy(), recurrencia=Recurrencia("semanal"))
    fake_intencion(monkeypatch, "completar", referencia="basura")
    handlers.handle_update(mensaje("ya saqué la basura"))

    assert "La próxima" in textos(enviados)[0]
    assert db.pendientes(CHAT)[0]["due_date"] == (hoy() + timedelta(days=7)).isoformat()


def test_borrar_por_texto(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "borrar", referencia="plomero")
    handlers.handle_update(mensaje("borrá la del plomero"))

    assert "llamar al plomero" not in [r["texto"] for r in db.pendientes(CHAT)]
    assert "🗑️" in textos(enviados)[0]


def test_si_no_encuentra_nada_lo_dice(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "borrar", referencia="la bicicleta")
    handlers.handle_update(mensaje("borrá la de la bicicleta"))

    assert "No encontré nada parecido" in textos(enviados)[0]
    assert len(db.pendientes(CHAT)) == 4  # no tocó nada


def test_si_hay_varias_parecidas_pregunta(enviados, monkeypatch):
    db.crear_tarea(CHAT, "limpiar la heladera", due=hoy())
    db.crear_tarea(CHAT, "descongelar la heladera", due=hoy())
    fake_intencion(monkeypatch, "borrar", referencia="heladera")

    handlers.handle_update(mensaje("borrá la de la heladera"))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    assert "¿Cuál de estas?" in envio["text"]
    botones = [b[0]["text"] for b in envio["reply_markup"]["inline_keyboard"]]
    assert sorted(botones) == ["descongelar la heladera", "limpiar la heladera"]
    assert len(db.pendientes(CHAT)) == 2  # todavía no borró nada


def test_los_botones_de_la_desambiguacion_funcionan(enviados, monkeypatch):
    db.crear_tarea(CHAT, "limpiar la heladera", due=hoy())
    tid = db.crear_tarea(CHAT, "descongelar la heladera", due=hoy())
    fake_intencion(monkeypatch, "borrar", referencia="heladera")
    handlers.handle_update(mensaje("borrá la de la heladera"))

    handlers.handle_update({"callback_query": {
        "id": "x", "data": f"b:{tid}", "from": {"id": 111},
        "message": {"message_id": 10, "chat": {"id": CHAT}}}})

    assert [r["texto"] for r in db.pendientes(CHAT)] == ["limpiar la heladera"]


def test_sin_referencia_pide_precision(enviados, monkeypatch, casa_con_tareas):
    fake_intencion(monkeypatch, "completar", referencia="")
    handlers.handle_update(mensaje("ya está"))
    assert "¿Cuál de todas?" in textos(enviados)[0]


def test_no_se_completan_los_recados(enviados, monkeypatch):
    db.crear_tarea(CHAT, "te amo", tipo="recado", responsable="axel", due=hoy())
    fake_intencion(monkeypatch, "completar", referencia="te amo")
    handlers.handle_update(mensaje("ya está eso del te amo"))

    assert db.pendientes(CHAT, tipo="recado")  # sigue ahí, se entrega igual
    assert "No encontré nada" in textos(enviados)[0]


# --------------------------------------------------------------------------
# Puntaje de la búsqueda
# --------------------------------------------------------------------------

@pytest.mark.parametrize("referencia,texto,minimo", [
    ("heladera", "limpiar la heladera", 0.9),
    ("la de la heladera", "limpiar la heladera", 0.5),
    ("plomero", "llamar al plomero", 0.9),
    ("limpié la heladera", "limpiar la heladera", 0.5),
    ("balcon", "pintar el balcón", 0.9),          # sin tilde igual matchea
])
def test_puntaje_reconoce(referencia, texto, minimo):
    assert db.puntaje(referencia, texto) >= minimo


@pytest.mark.parametrize("referencia,texto", [
    ("bicicleta", "limpiar la heladera"),
    ("plomero", "comprar leche"),
    ("", "limpiar la heladera"),
])
def test_puntaje_descarta(referencia, texto):
    assert db.puntaje(referencia, texto) < 0.5


# --------------------------------------------------------------------------
# Modo local
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frase,intencion", [
    ("mostrame la lista del super", "ver_super"),
    ("qué falta comprar", "ver_super"),
    ("super", "ver_super"),
    ("qué hay que hacer", "ver_pendientes"),
    ("qué tenemos pendiente", "ver_pendientes"),
    ("mostrame las tareas", "ver_pendientes"),
    ("cómo funciona?", "ver_ayuda"),
    ("qué sabés hacer?", "ver_ayuda"),
])
def test_intenciones_sin_llm(frase, intencion):
    assert heuristica.interpretar(frase)["intencion"] == intencion


@pytest.mark.parametrize("frase,referencia", [
    ("ya limpié la heladera", "heladera"),
    ("borrá la del plomero", "plomero"),
    ("olvidate de la heladera", "heladera"),
    # Mal escrito, que es como se escribe desde el celular.
    ("elimna la del plomero", "plomero"),
    ("borrar lo de la heladera", "heladera"),
    ("sacala del plomero", "plomero"),
])
def test_completar_y_borrar_sin_llm(frase, referencia):
    data = heuristica.interpretar(frase)
    assert data["intencion"] in ("completar", "borrar")
    assert data["referencia"] == referencia


def test_sin_llm_igual_anota_lo_que_no_es_un_pedido():
    data = heuristica.interpretar("hay que limpiar la heladera")
    assert data["intencion"] == "anotar"
    assert data["items"][0]["texto"] == "limpiar la heladera"


def test_sin_llm_funciona_el_circuito_completo(enviados, monkeypatch):
    monkeypatch.setattr(__import__("notita").config, "GEMINI_API_KEY", "")
    db.crear_tarea(CHAT, "limpiar la heladera", due=hoy())

    handlers.handle_update(mensaje("ya limpié la heladera"))
    assert db.pendientes(CHAT) == []

    handlers.handle_update(mensaje("mostrame la lista del super"))
    assert "super" in textos(enviados)[-1].lower()
