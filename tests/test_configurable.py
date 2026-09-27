"""Lo que antes estaba cableado al caso de esta casa y ahora se configura."""
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests

from notita import config, handlers, pythonanywhere, views

from .conftest import CHAT, SalidaAInternet

RAIZ = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Zona horaria y hora de la rutina
#
# A propósito sin `importlib.reload(config)`: recargar el módulo dentro de un test
# se filtra a los demás (pasó, y rompió dos tests de recados con una hora inventada).
# --------------------------------------------------------------------------

def test_por_defecto_es_buenos_aires_a_las_20():
    assert config.TZ_NOMBRE == "America/Argentina/Buenos_Aires"
    assert config.HORA_RUTINA == "20:00"
    assert config.hora_rutina_en_utc() == "23:00"


def test_se_puede_mudar_la_casa(monkeypatch):
    monkeypatch.setattr(config, "TZ", ZoneInfo("Europe/Madrid"))
    # Madrid está en UTC+2 en verano y +1 en invierno: la cuenta la hace tzdata.
    assert config.hora_rutina_en_utc("09:30") in ("07:30", "08:30")


def test_una_zona_mal_escrita_no_rompe_el_bot():
    nombre, zona = config.zona("Marte/Olympus_Mons")
    assert nombre == config.TZ_POR_DEFECTO
    assert zona is not None


def test_una_zona_valida_se_respeta():
    nombre, zona = config.zona("Europe/Madrid")
    assert nombre == "Europe/Madrid"


@pytest.mark.parametrize("hora", ["a la tardecita", "", "99:99", "20", "veinte"])
def test_una_hora_mal_escrita_tampoco(hora):
    assert config.hora_rutina_en_utc(hora) == "23:00"   # asume las 20:00


def test_los_textos_dicen_la_hora_configurada(monkeypatch):
    monkeypatch.setattr(config, "HORA_RUTINA", "09:30")
    assert "09:30" in views.ayuda()
    assert "20:00" not in views.ayuda()


def test_la_confirmacion_del_recado_usa_la_hora_configurada(monkeypatch, enviados):
    from notita import db
    from notita.dates import hoy

    monkeypatch.setattr(config, "HORA_RUTINA", "09:30")
    tid = db.crear_tarea(CHAT, "comprá pan", tipo="recado", responsable="axel",
                         due=hoy() + timedelta(days=1), created_by="barbu")
    assert "09:30" in views.confirmacion(db.obtener(tid), hoy())


# --------------------------------------------------------------------------
# El grupo que se convierte en supergrupo
# --------------------------------------------------------------------------

def test_si_el_grupo_cambia_de_numero_avisa(enviados):
    """Telegram avisa una sola vez y el chat_id cambia para siempre."""
    handlers.handle_update({"message": {
        "chat": {"id": CHAT}, "from": {"id": 111}, "message_id": 1,
        "migrate_to_chat_id": -1009999999999}})

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    assert envio["chat_id"] == -1009999999999      # se avisa en el grupo nuevo
    assert "ALLOWED_CHAT_ID=-1009999999999" in envio["text"]


def test_un_aviso_de_migracion_de_otro_chat_se_ignora(enviados):
    handlers.handle_update({"message": {
        "chat": {"id": -100777}, "from": {"id": 111}, "message_id": 1,
        "migrate_to_chat_id": -1009999999999}})
    assert enviados == []


# --------------------------------------------------------------------------
# PythonAnywhere: no pisar la web app de otro proyecto
# --------------------------------------------------------------------------

def test_con_varias_web_apps_ajenas_no_elige_ninguna(tmp_path):
    var_www = tmp_path / "var_www"
    var_www.mkdir()
    (var_www / "www_otroproyecto_com_wsgi.py").touch()
    (var_www / "api_otracosa_com_wsgi.py").touch()

    sitio = pythonanywhere.detectar(entorno={"USER": "unusuario",
                                             "PYTHONANYWHERE_DOMAIN": "pythonanywhere.com"},
                                    var_www=var_www)
    assert sitio is not None
    assert sitio.existe is False, "antes agarraba la primera alfabética y la sobrescribía"


def test_un_dominio_propio_unico_si_se_usa(tmp_path):
    var_www = tmp_path / "var_www"
    var_www.mkdir()
    (var_www / "www_micasa_com_wsgi.py").touch()

    sitio = pythonanywhere.detectar(entorno={"USER": "unusuario"}, var_www=var_www)
    assert sitio.existe is True and sitio.dominio == "www.micasa.com"


def test_la_propia_gana_sobre_las_ajenas(tmp_path):
    var_www = tmp_path / "var_www"
    var_www.mkdir()
    (var_www / "aaa_otroproyecto_com_wsgi.py").touch()
    (var_www / "unusuario_pythonanywhere_com_wsgi.py").touch()

    sitio = pythonanywhere.detectar(entorno={"USER": "unusuario"}, var_www=var_www)
    assert sitio.dominio == "unusuario.pythonanywhere.com"


# --------------------------------------------------------------------------
# El guard anti-red de los tests
# --------------------------------------------------------------------------

def test_ningun_test_puede_salir_a_internet():
    """Antes sólo estaba tapado `post`: un test con `get` salía a la red de verdad."""
    for llamada in (lambda: requests.get("https://example.com"),
                    lambda: requests.post("https://example.com"),
                    lambda: requests.put("https://example.com"),
                    lambda: requests.sessions.Session().get("https://example.com")):
        with pytest.raises(SalidaAInternet):
            llamada()


def test_el_guard_no_lo_tapa_un_except_generico():
    """Por eso hereda de BaseException: `llm._call` traga los Exception."""
    assert not issubclass(SalidaAInternet, Exception)
    try:
        requests.get("https://example.com")
    except Exception:
        pytest.fail("un except genérico se lo comió")
    except SalidaAInternet:
        pass


# --------------------------------------------------------------------------
# Repo público
# --------------------------------------------------------------------------

def test_hay_licencia():
    """Sin licencia, un repo público es «todos los derechos reservados»."""
    licencia = RAIZ / "LICENSE"
    assert licencia.exists()
    assert "MIT" in licencia.read_text()


def test_hay_ci_que_corre_los_tests():
    flujo = RAIZ / ".github" / "workflows" / "tests.yml"
    assert flujo.exists()
    contenido = flujo.read_text()
    assert "pytest" in contenido
    assert "3.10" in contenido, "3.10 es el mínimo que soporta el código"


def test_el_readme_no_promete_mas_de_lo_que_hay():
    readme = (RAIZ / "README.md").read_text()
    assert "NOTITA_TZ" in readme, "la zona horaria ahora se configura: hay que decirlo"
    assert "NOTITA_HORA" in readme


# --------------------------------------------------------------------------
# Saber qué versión está corriendo, sin entrar al servidor
# --------------------------------------------------------------------------

def test_la_version_es_el_commit():
    import subprocess

    esperado = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=RAIZ,
                              capture_output=True, text=True).stdout.strip()
    assert config._leer_version() == esperado


def test_sin_repo_no_explota(tmp_path, monkeypatch):
    """Sin .git (por ejemplo, subido por FTP) no tiene que reventar."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert config._leer_version() == "desconocida"


def test_la_raiz_de_la_web_app_dice_la_version():
    import app as webapp

    with webapp.app.test_client() as cliente:
        datos = cliente.get("/").get_json()
    assert datos["bot"] == "notita"
    assert datos["version"] == config.version()


def test_la_version_se_lee_al_importar_no_en_cada_pedido(monkeypatch, tmp_path):
    """Si alguien hace `git pull` y se olvida del reload, `/` tiene que seguir
    diciendo el commit VIEJO: es el que está corriendo de verdad."""
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)   # como si el .git desapareciera
    assert config.version() == config.VERSION           # no lo vuelve a leer
    assert config.version() != "desconocida"
