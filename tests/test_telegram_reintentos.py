import requests

from notita import config, telegram


class _Resp:
    def json(self):
        return {"ok": True, "result": {"message_id": 7}}


def test_reintenta_si_falla_la_conexion(monkeypatch):
    # conftest reemplaza telegram.llamar; acá probamos la función real.
    import importlib
    real = importlib.reload(telegram)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setattr(real, "ESPERA", 0)
    llamadas = []

    def post(url, json, timeout):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise requests.exceptions.ProxyError("proxy caído")
        return _Resp()

    monkeypatch.setattr(real.requests, "post", post)
    assert real.llamar("sendMessage", chat_id=1, text="hola") == {"message_id": 7}
    assert len(llamadas) == 3   # falló dos veces y salió en el tercero


def test_se_rinde_despues_de_los_intentos(monkeypatch, caplog):
    import importlib
    real = importlib.reload(telegram)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setattr(real, "ESPERA", 0)
    llamadas = []

    def post(url, json, timeout):
        llamadas.append(1)
        raise requests.exceptions.ProxyError("proxy caído " + url)

    monkeypatch.setattr(real.requests, "post", post)
    assert real.llamar("sendMessage", chat_id=1, text="hola") is None
    assert len(llamadas) == real.INTENTOS
    assert "123:abc" not in caplog.text


def test_el_ultimo_error_no_se_queda_pegado(monkeypatch):
    """Un «not modified» de hace rato no puede contaminar el fallo siguiente.

    Si pasaba, el tablero no se republicaba nunca: cada edición fallada se leía como
    inofensiva.
    """
    import importlib

    real = importlib.reload(telegram)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "123:abc")
    respuestas = [
        {"ok": False, "description": "Bad Request: message is not modified"},
        {"ok": False, "description": "Bad Request: message to edit not found"},
    ]

    class Resp:
        def json(self):
            return respuestas.pop(0)

    monkeypatch.setattr(real.requests, "post", lambda *a, **k: Resp())

    real.llamar("editMessageText", chat_id=1, message_id=2, text="igual")
    assert real.ultimo_error_fue_no_modificado() is True

    real.llamar("editMessageText", chat_id=1, message_id=2, text="otra")
    assert real.ultimo_error_fue_no_modificado() is False, "no puede quedar pegado"
