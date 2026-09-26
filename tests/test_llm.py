"""Tests de la capa del LLM: parseo de la respuesta y diagnóstico de errores.

Nada de red: se simula lo que devuelve la API.
"""
import pytest
import requests

from notita import config, llm
from notita.dates import DateSpec, Recurrencia


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload


def fake_post(monkeypatch, respuesta):
    def _post(*a, **k):
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta
    monkeypatch.setattr(requests, "post", _post)


# --------------------------------------------------------------------------
# probar_conexion: los mensajes que ven install.py y doctor.py
# --------------------------------------------------------------------------

def test_sin_key():
    assert llm.probar_conexion(key="") == (False, "falta la API key")


def test_conexion_ok(monkeypatch):
    fake_post(monkeypatch, FakeResponse(200, {"candidates": []}))
    assert llm.probar_conexion(key="k") == (True, "")


def test_key_invalida(monkeypatch):
    fake_post(monkeypatch, FakeResponse(
        400, {"error": {"message": "API key not valid. Please pass a valid API key."}}))
    bien, detalle = llm.probar_conexion(key="k")
    assert bien is False
    assert detalle == "la API key no es válida"


def test_modelo_inexistente(monkeypatch):
    fake_post(monkeypatch, FakeResponse(404, {"error": {"message": "models/x is not found"}}))
    bien, detalle = llm.probar_conexion(key="k", model="gemini-fantasma")
    assert bien is False
    assert "gemini-fantasma" in detalle


def test_cuota_agotada(monkeypatch):
    fake_post(monkeypatch, FakeResponse(429, {"error": {"message": "quota"}}))
    assert llm.probar_conexion(key="k")[1] == "te pasaste de la cuota gratuita, probá en un rato"


def test_error_raro_muestra_el_http(monkeypatch):
    fake_post(monkeypatch, FakeResponse(418, {"error": {"message": "soy una tetera"}}))
    bien, detalle = llm.probar_conexion(key="k")
    assert bien is False
    assert "418" in detalle and "tetera" in detalle


def test_sin_internet(monkeypatch):
    fake_post(monkeypatch, requests.exceptions.ConnectionError())
    bien, detalle = llm.probar_conexion(key="k")
    assert bien is False
    assert "no se pudo conectar" in detalle


def test_respuesta_que_no_es_json(monkeypatch):
    fake_post(monkeypatch, FakeResponse(500, None, text="<html>Bad Gateway</html>"))
    assert llm.probar_conexion(key="k")[0] is False


# --------------------------------------------------------------------------
# interpretar_mensaje: qué hace con lo que contesta el modelo
# --------------------------------------------------------------------------

def respuesta_llm(payload_json: str) -> FakeResponse:
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": payload_json}]}}]})


def test_interpretar_mensaje_parsea_el_json(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    fake_post(monkeypatch, respuesta_llm(
        '{"es_tarea": true, "items": [{"texto": "limpiar la heladera"}]}'))
    data = llm.interpretar_mensaje("limpiar la heladera", "axel")
    assert data["items"][0]["texto"] == "limpiar la heladera"


def test_interpretar_mensaje_sin_key_devuelve_none(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert llm.interpretar_mensaje("algo", "axel") is None


def test_interpretar_mensaje_con_json_roto_devuelve_none(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    fake_post(monkeypatch, respuesta_llm('{"es_tarea": tru'))
    assert llm.interpretar_mensaje("algo", "axel") is None


def test_interpretar_mensaje_con_error_http_devuelve_none(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    fake_post(monkeypatch, FakeResponse(400, {"error": {"message": "boom"}}))
    assert llm.interpretar_mensaje("algo", "axel") is None


# --------------------------------------------------------------------------
# Reintentos: los 503 de Gemini son comunes y se arreglan solos
# --------------------------------------------------------------------------

@pytest.fixture
def sin_esperas(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def respuestas_en_orden(monkeypatch, respuestas):
    """Devuelve una respuesta distinta por llamada, y cuenta cuántas hubo."""
    llamadas = []

    def _post(*a, **k):
        r = respuestas[min(len(llamadas), len(respuestas) - 1)]
        llamadas.append(r)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(requests, "post", _post)
    return llamadas


def test_un_503_se_reintenta_y_sale_bien(monkeypatch, sin_esperas):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    llamadas = respuestas_en_orden(monkeypatch, [
        FakeResponse(503, {"error": {"message": "high demand"}}),
        respuesta_llm('{"es_tarea": true, "items": [{"texto": "regar"}]}'),
    ])
    data = llm.interpretar_mensaje("regar", "axel")
    assert data["items"][0]["texto"] == "regar"
    assert len(llamadas) == 2


def test_503_siempre_se_rinde_despues_de_tres_intentos(monkeypatch, sin_esperas):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    llamadas = respuestas_en_orden(monkeypatch, [FakeResponse(503, {"error": {}})])
    assert llm.interpretar_mensaje("algo", "axel") is None
    assert len(llamadas) == llm.INTENTOS


def test_timeout_se_reintenta(monkeypatch, sin_esperas):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    llamadas = respuestas_en_orden(monkeypatch, [
        requests.exceptions.Timeout(),
        respuesta_llm('{"es_tarea": true, "items": []}'),
    ])
    assert llm.interpretar_mensaje("algo", "axel") is not None
    assert len(llamadas) == 2


def test_un_400_no_se_reintenta(monkeypatch, sin_esperas):
    # Si la key está mal, reintentar no sirve de nada.
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    llamadas = respuestas_en_orden(monkeypatch, [
        FakeResponse(400, {"error": {"message": "API key not valid"}})])
    assert llm.interpretar_mensaje("algo", "axel") is None
    assert len(llamadas) == 1


def test_probar_conexion_explica_el_503(monkeypatch):
    fake_post(monkeypatch, FakeResponse(503, {"error": {"message": "high demand"}}))
    bien, detalle = llm.probar_conexion(key="k")
    assert bien is False
    assert "sobrecargado" in detalle


# --------------------------------------------------------------------------
# El -1 que usamos como "no aplica"
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [-1, "-1", None, "", "hola", -5])
def test_los_numeros_que_no_aplican_quedan_en_none(valor):
    spec = llm.spec_de_item({"fecha_kind": "manana", "fecha_day": valor})
    assert spec.day is None


def test_spec_de_item():
    spec = llm.spec_de_item({"fecha_kind": "fecha_exacta", "fecha_day": 3, "fecha_month": 10,
                             "fecha_year": -1, "fecha_weekday": -1, "fecha_dias": -1})
    assert spec == DateSpec("fecha_exacta", day=3, month=10)


def test_spec_de_item_con_kind_inventado():
    # Si el modelo devuelve cualquier cosa, no explotamos: preguntamos la fecha.
    assert llm.spec_de_item({"fecha_kind": "el_jueves_de_la_semana_pasada"}).kind == "desconocida"
    assert llm.spec_de_item({}).kind == "desconocida"


def test_recurrencia_de_item():
    assert llm.recurrencia_de_item({"recur_kind": "ninguna"}) is None
    assert llm.recurrencia_de_item({}) is None
    assert llm.recurrencia_de_item({"recur_kind": "mensual", "recur_monthday": 10,
                                    "recur_interval": -1, "recur_weekday": -1}) == \
        Recurrencia("mensual", interval=1, monthday=10)


def test_recurrencia_con_intervalo_basura():
    assert llm.recurrencia_de_item({"recur_kind": "semanal", "recur_interval": "dos"}).interval == 1
