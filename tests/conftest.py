import pytest
import requests

from notita import config, db, telegram
from notita.config import Persona

CHAT = -1001234567890

# La casa de prueba: dos personas, como el caso original.
CASA = (Persona("axel", "Axel", 111), Persona("barbu", "Barbu", 222))


class SalidaAInternet(BaseException):
    """Hereda de BaseException a propósito: así no la tragan los `except Exception`."""


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    """Base limpia por test y Telegram desconectado (guardamos lo que se enviaría)."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "ALLOWED_CHAT_ID", CHAT)
    # Con key: el modo normal es "con LLM". Los tests que quieran el modo local
    # la ponen en "" a mano.
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "CONTEXTO_CASA", "")

    def sin_red(*a, **k):
        raise SalidaAInternet("un test intentó salir a internet de verdad")

    # Se corta en Session.request, que es por donde pasan post, get, put y compañía:
    # antes sólo estaba tapado `post` y un test nuevo con `get` habría salido a la red.
    monkeypatch.setattr(requests.sessions.Session, "request", sin_red)
    monkeypatch.setattr(requests, "post", sin_red)
    # definir_personas recalcula varios globals, así que se restaura a mano.
    originales = config.PERSONAS_CASA
    config.definir_personas(CASA)
    db.init_db()
    # La bienvenida de v2 sale una sola vez; en los tests estorba. El test que la
    # prueba usa el fixture `casa_nueva`.
    db.ajuste("bienvenida_v2", "1")
    db.guardar_tablero(CHAT, 1)   # como si el tablero ya estuviera publicado

    enviados = []

    def fake_llamar(metodo, **payload):
        enviados.append({"metodo": metodo, **payload})
        return {"message_id": len(enviados), "chat": {"id": payload.get("chat_id")}}

    monkeypatch.setattr(telegram, "llamar", fake_llamar)
    # Estado de módulo: si no se limpia, el «not modified» de un test se le cuela al
    # siguiente (y ahí el tablero nunca se republica).
    telegram._ULTIMO_ERROR.clear()
    yield enviados

    config.definir_personas(originales)


@pytest.fixture
def enviados(entorno):
    return entorno


@pytest.fixture
def casa_nueva():
    """Una casa recién instalada: sin bienvenida ni tablero."""
    with db.conn() as c:
        c.execute("DELETE FROM ajustes WHERE clave = 'bienvenida_v2'")
        c.execute("DELETE FROM tablero")
