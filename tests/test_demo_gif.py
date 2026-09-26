"""El GIF del README lo actúa el código real, así que puede romperse sin avisar.

Estos tests corren la misma conversación que el GIF (sin dibujar nada) para que,
si mañana cambia un texto o un flujo, falle acá y no quede un GIF que miente.
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
    assert "💌 A Axel · <i>hoy a las 20:00</i>" in todo
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


def test_el_gif_existe_y_no_pesa_de_mas():
    gif = RAIZ / "docs" / "notita.gif"
    assert gif.exists(), "falta el GIF del README: python3 docs/demo/generar_gif.py"
    assert gif.stat().st_size < 2_000_000, "el GIF pesa demasiado para un README"
