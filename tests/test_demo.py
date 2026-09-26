"""La conversación de demostración, que actúa el código real (`docs/demo/guion.py`).

Es también un test de integración lindo: un día entero de la casa de punta a punta,
sin mocks salvo lo que contestaría Gemini. Si mañana cambia un texto o un flujo,
falla acá y no queda una demo que muestra algo que el bot ya no hace.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

from notita import config, llm, telegram

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "docs" / "demo"))


@pytest.fixture
def eventos(monkeypatch):
    import guion

    # guion.py pisa estas cosas a mano; las registramos para que se restauren.
    monkeypatch.setattr(telegram, "llamar", telegram.llamar)
    monkeypatch.setattr(llm, "interpretar_mensaje", llm.interpretar_mensaje)
    for atributo in ("DB_PATH", "ALLOWED_CHAT_ID", "GEMINI_API_KEY", "CONTEXTO_CASA"):
        monkeypatch.setattr(config, atributo, getattr(config, atributo))
    for nombre in guion.MODULOS_CON_HOY:
        modulo = sys.modules.get(nombre)
        if modulo is not None and hasattr(modulo, "hoy"):
            monkeypatch.setattr(modulo, "hoy", modulo.hoy)

    guion.fijar_hoy(date(2026, 10, 7))  # un miércoles, como el GIF
    original = config.PERSONAS_CASA
    yield guion.actuar()
    config.definir_personas(original)


def textos(eventos) -> str:
    partes = []
    for e in eventos:
        if e.tipo == "burbuja":
            partes.append(e.burbuja.html)
        elif e.tipo == "edicion":
            partes.append(e.texto)
    return "\n".join(partes)


def test_la_conversacion_del_gif_sigue_funcionando(eventos):
    todo = textos(eventos)
    # Carga en lote: tres cosas de un mensaje, cada una en su lugar.
    assert "Anoté 3 cositas" in todo
    assert "Comprar comida para Milo · <i>al súper</i>" in todo
    assert "Limpiar la heladera · <i>el sábado 10/10</i>" in todo   # «el finde»
    # Pregunta la fecha de lo que no la tenía, y la aplica al tocar el botón.
    assert "¿Para cuándo «Llamar al plomero»?" in todo
    assert "Llamar al plomero · <i>hoy</i>" in todo
    # Recado: se guarda y después se entrega mencionando a la persona.
    assert "💌 A Axel · <i>ahora mismo</i>" in todo   # los de hoy no esperan
    assert "Barbu te manda a decir" in todo
    # Y el recordatorio de las 20:00 que se marca hecho.
    assert "✅ <s>Llamar al plomero</s> — Axel" in todo


def test_el_gif_no_muestra_tareas_repetidas(eventos):
    """Se duplicaba todo porque la demo escribía en la base de verdad."""
    assert textos(eventos).count("Limpiar la heladera") == 2  # el alta y la lista


def test_los_botones_del_gif_son_los_de_verdad(eventos):
    botones = [t for e in eventos if e.tipo == "burbuja" and e.burbuja.botones
               for fila in e.burbuja.botones for t, _ in fila]
    assert "Hoy" in botones and "Algún día" in botones      # la pregunta de fecha
    assert "✅ Hecho" in botones and "⏰ Posponer" in botones  # el recordatorio


def test_el_dia_entero_pasa_por_todas_las_piezas(eventos):
    """Integración: alta en lote, súper, recado, listado y recordatorio, de una."""
    tipos = [e.tipo for e in eventos]
    assert tipos.count("burbuja") >= 10
    assert "separador" in tipos      # el corte de las 20:00
    assert "edicion" in tipos        # los mensajes que se editan al tocar un botón
