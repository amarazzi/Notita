import pytest

from notita import config, db, telegram
from notita.config import Persona

CHAT = -1001234567890

# La casa de prueba: dos personas, como el caso original.
CASA = (Persona("axel", "Axel", 111), Persona("barbu", "Barbu", 222))


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    """Base limpia por test y Telegram desconectado (guardamos lo que se enviaría)."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "ALLOWED_CHAT_ID", CHAT)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "CONTEXTO_CASA", "")
    # definir_personas recalcula varios globals, así que se restaura a mano.
    originales = config.PERSONAS_CASA
    config.definir_personas(CASA)
    db.init_db()

    enviados = []

    def fake_llamar(metodo, **payload):
        enviados.append({"metodo": metodo, **payload})
        return {"message_id": len(enviados), "chat": {"id": payload.get("chat_id")}}

    monkeypatch.setattr(telegram, "llamar", fake_llamar)
    yield enviados

    config.definir_personas(originales)


@pytest.fixture
def enviados(entorno):
    return entorno
