import pytest

from notita import config, db, telegram

CHAT = -1001234567890


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    """Base limpia por test y Telegram desconectado (guardamos lo que se enviaría)."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "ALLOWED_CHAT_ID", CHAT)
    monkeypatch.setattr(config, "AXEL_USER_ID", 111)
    monkeypatch.setattr(config, "BARBU_USER_ID", 222)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    db.init_db()

    enviados = []

    def fake_llamar(metodo, **payload):
        enviados.append({"metodo": metodo, **payload})
        return {"message_id": len(enviados), "chat": {"id": payload.get("chat_id")}}

    monkeypatch.setattr(telegram, "llamar", fake_llamar)
    yield enviados


@pytest.fixture
def enviados(entorno):
    return entorno
