"""Tests del endpoint que dispara la rutina de las 20:00 desde un cron externo."""
import pytest

from notita import config, db
from notita.dates import hoy

from .conftest import CHAT

CLAVE = "clave-de-prueba-larga"


@pytest.fixture
def cliente(monkeypatch):
    import app as app_module

    monkeypatch.setattr(config, "CRON_SECRET", CLAVE)
    return app_module.app.test_client()


def test_sin_clave_configurada_la_ruta_no_existe(monkeypatch):
    import app as app_module

    monkeypatch.setattr(config, "CRON_SECRET", "")
    assert app_module.app.test_client().get("/cron/recordatorios").status_code == 404


def test_sin_header_da_403(cliente):
    assert cliente.get("/cron/recordatorios").status_code == 403


def test_clave_incorrecta_da_403(cliente):
    r = cliente.post("/cron/recordatorios", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 403


def test_clave_con_caracteres_raros_da_403_y_no_revienta(cliente):
    # compare_digest con str no ASCII tira TypeError: tiene que seguir siendo 403.
    for valor in ("ñandú", "clave-ñ", "🙈"):
        r = cliente.get("/cron/recordatorios", headers={"X-Cron-Secret": valor})
        assert r.status_code == 403, valor


def test_la_clave_en_la_query_string_ya_no_sirve(cliente):
    # El secreto no viaja por URL para que no quede en el access log.
    assert cliente.get(f"/cron/recordatorios?clave={CLAVE}").status_code == 403


def test_con_la_clave_correcta_corre_la_rutina(cliente, enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    r = cliente.post("/cron/recordatorios", headers={"X-Cron-Secret": CLAVE})
    assert r.status_code == 200
    assert r.json["ok"] is True
    assert r.json["resultado"]["recordatorios"] == 1
    assert any("basura" in e.get("text", "") for e in enviados)
