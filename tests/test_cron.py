from notita import config


def _cliente(monkeypatch, clave="secreto-de-prueba"):
    monkeypatch.setattr(config, "CRON_SECRET", clave)
    import app as modulo
    return modulo.app.test_client()


def test_cron_sin_clave_configurada_esta_apagado(monkeypatch):
    c = _cliente(monkeypatch, clave="")
    assert c.get("/cron/recordatorios").status_code == 404


def test_cron_rechaza_clave_incorrecta(monkeypatch, enviados):
    c = _cliente(monkeypatch)
    assert c.get("/cron/recordatorios", headers={"X-Cron-Secret": "mal"}).status_code == 403
    assert c.get("/cron/recordatorios").status_code == 403
    assert enviados == []


def test_cron_con_clave_corre_la_rutina(monkeypatch):
    c = _cliente(monkeypatch)
    r = c.get("/cron/recordatorios", headers={"X-Cron-Secret": "secreto-de-prueba"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    r = c.post("/cron/recordatorios?clave=secreto-de-prueba")
    assert r.status_code == 200
