"""Tests del instalador de punta a punta, con Telegram y Gemini simulados.

Sirven para poder cambiar el wizard sin tener que correrlo a mano cada vez
(y sin desconectar el webhook de una instalación que está andando).
"""
import sys

import pytest
import requests

import install
from notita import llm

TOKEN = "123456789:AAGesteTokenEsFalsoPeroTieneLaFormaCorrecta"
GRUPO = -1001234567890


class FakeGet:
    ok = True
    status_code = 200
    text = '{"ok": true, "bot": "notita"}'


@pytest.fixture
def telegram_falso(monkeypatch):
    """Simula la Bot API y registra todas las llamadas."""
    llamadas = []
    estado = {"webhook": "", "privacy_off": True}

    def fake_tg(token, metodo, **payload):
        llamadas.append((metodo, payload))
        if metodo == "getMe":
            return True, {"id": 42, "username": "notita_test_bot", "first_name": "Notita",
                          "can_read_all_group_messages": estado["privacy_off"]}
        if metodo == "getWebhookInfo":
            return True, {"url": estado["webhook"], "pending_update_count": 0}
        if metodo == "deleteWebhook":
            estado["webhook"] = ""
            return True, True
        if metodo == "setWebhook":
            estado["webhook"] = payload.get("url", "")
            return True, True
        if metodo == "getUpdates":
            return True, [
                {"message": {"chat": {"id": GRUPO, "type": "group", "title": "Casita"},
                             "from": {"id": 111, "first_name": "Axel", "is_bot": False},
                             "text": "hola"}},
                {"message": {"chat": {"id": GRUPO, "type": "group", "title": "Casita"},
                             "from": {"id": 222, "first_name": "Barbu", "is_bot": False},
                             "text": "buenas"}},
            ]
        if metodo == "sendMessage":
            return True, {"message_id": 1}
        return True, {}

    monkeypatch.setattr(install, "tg", fake_tg)
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeGet())
    monkeypatch.setattr(llm, "probar_conexion", lambda *a, **k: (True, ""))
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "es_tarea": True,
        "items": [{"texto": "limpiar la heladera", "tipo": "casa"},
                  {"texto": "leche", "tipo": "compras"}],
    })
    return llamadas, estado


def correr(monkeypatch, env_path, respuestas, argv_extra=()):
    """Corre el wizard contestando `respuestas` en orden."""
    it = iter(respuestas)
    usadas = []

    def fake_input(prompt=""):
        try:
            r = next(it)
        except StopIteration:
            raise AssertionError(f"el wizard pidió algo más: {prompt!r}")
        usadas.append(r)
        return r

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(sys, "argv", ["install.py", "--env", str(env_path), *argv_extra])
    install.main()
    sobran = list(it)
    assert not sobran, f"quedaron respuestas sin usar: {sobran}"
    return usadas


RESPUESTAS_FELICES = [
    TOKEN,          # token
    "",             # "apretá Enter cuando todos hayan escrito"
    "",             # ¿está completa la lista? -> sí
    "",             # nombre de Axel -> el detectado
    "",             # nombre de Barbu -> el detectado
    "",             # ¿falta alguien? -> no
    "una-key-de-gemini",
    "",             # modelo -> el default
    "https://miusuario.pythonanywhere.com",
    "",             # contexto -> vacío
    "",             # ¿mando mensaje de prueba? -> sí
]


def leer(env_path) -> dict:
    return install.leer_env(env_path)


# --------------------------------------------------------------------------

def test_instalacion_completa(tmp_path, monkeypatch, telegram_falso, capsys):
    llamadas, estado = telegram_falso
    env = tmp_path / ".env"

    correr(monkeypatch, env, RESPUESTAS_FELICES)

    valores = leer(env)
    assert valores["TELEGRAM_TOKEN"] == TOKEN
    assert valores["ALLOWED_CHAT_ID"] == str(GRUPO)
    assert valores["NOTITA_PERSONAS"] == "Axel:111,Barbu:222"
    assert valores["GEMINI_API_KEY"] == "una-key-de-gemini"
    assert valores["GEMINI_MODEL"] == "gemini-2.5-flash"
    assert valores["CRON_SECRET"] == ""  # apagado por defecto
    assert len(valores["TELEGRAM_WEBHOOK_SECRET"]) >= 32  # generado solo

    # El webhook quedó puesto, con /telegram y con el secreto que se generó.
    assert estado["webhook"] == "https://miusuario.pythonanywhere.com/telegram"
    set_hook = [p for m, p in llamadas if m == "setWebhook"][-1]
    assert set_hook["secret_token"] == valores["TELEGRAM_WEBHOOK_SECRET"]
    assert "callback_query" in set_hook["allowed_updates"]

    # Y mandó el mensaje de bienvenida al grupo.
    assert any(m == "sendMessage" and p["chat_id"] == GRUPO for m, p in llamadas)

    salida = capsys.readouterr().out
    assert "privacy mode está apagado" in salida
    assert "Reload" in salida  # el paso que queda a mano


def test_permisos_del_env(tmp_path, monkeypatch, telegram_falso):
    env = tmp_path / ".env"
    correr(monkeypatch, env, RESPUESTAS_FELICES)
    # Tiene secretos: sólo el dueño puede leerlo.
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_le_agrega_telegram_a_la_url(tmp_path, monkeypatch, telegram_falso):
    _, estado = telegram_falso
    respuestas = list(RESPUESTAS_FELICES)
    respuestas[8] = "miusuario.pythonanywhere.com/"  # sin https y con barra
    correr(monkeypatch, tmp_path / ".env", respuestas)
    assert estado["webhook"] == "https://miusuario.pythonanywhere.com/telegram"


def test_rechaza_un_token_con_mala_forma(tmp_path, monkeypatch, telegram_falso, capsys):
    correr(monkeypatch, tmp_path / ".env", ["no-es-un-token", *RESPUESTAS_FELICES])
    assert "no tiene forma de token" in capsys.readouterr().out


def test_avisa_si_el_privacy_mode_esta_encendido(tmp_path, monkeypatch, telegram_falso, capsys):
    llamadas, estado = telegram_falso
    estado["privacy_off"] = False  # arranca mal

    tg_base = install.tg

    def tg_que_se_arregla(token, metodo, **payload):
        # Simula que el usuario fue a BotFather: en el segundo getMe ya está apagado.
        if metodo == "getMe" and sum(1 for m, _ in llamadas if m == "getMe") >= 1:
            estado["privacy_off"] = True
        return tg_base(token, metodo, **payload)

    monkeypatch.setattr(install, "tg", tg_que_se_arregla)
    # "s" contesta a «¿Lo apagaste? Reviso de nuevo».
    correr(monkeypatch, tmp_path / ".env", [TOKEN, "s", *RESPUESTAS_FELICES[1:]])

    salida = capsys.readouterr().out
    assert "privacy mode está ENCENDIDO" in salida
    assert "Group Privacy → Turn off" in salida   # le dice cómo
    assert "Ahora sí" in salida                   # y lo re-chequea solo


# --------------------------------------------------------------------------
# Re-instalar sobre una configuración que ya existe
# --------------------------------------------------------------------------

def test_no_pierde_el_cron_secret_ni_la_ruta_de_la_base(tmp_path, monkeypatch, telegram_falso):
    env = tmp_path / ".env"
    env.write_text("CRON_SECRET=secreto-que-ya-estaba\nNOTITA_DB=/datos/notita.db\n")

    correr(monkeypatch, env, RESPUESTAS_FELICES)

    valores = leer(env)
    assert valores["CRON_SECRET"] == "secreto-que-ya-estaba"
    assert valores["NOTITA_DB"] == "/datos/notita.db"


def test_hace_backup_del_env_anterior(tmp_path, monkeypatch, telegram_falso):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_TOKEN=viejo\n")
    correr(monkeypatch, env, RESPUESTAS_FELICES)

    backup = tmp_path / ".env.bak"
    assert backup.exists(), list(tmp_path.iterdir())
    assert "viejo" in backup.read_text()


def test_usa_lo_anterior_como_default(tmp_path, monkeypatch, telegram_falso):
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_TOKEN={TOKEN}\nGEMINI_API_KEY=key-vieja\n"
                   "GEMINI_MODEL=gemini-2.5-flash-lite\nNOTITA_CONTEXTO=Tenemos un gato\n")

    respuestas = list(RESPUESTAS_FELICES)
    respuestas[0] = ""   # Enter -> el token que ya estaba
    respuestas[6] = ""   # Enter -> la key que ya estaba
    correr(monkeypatch, env, respuestas)

    valores = leer(env)
    assert valores["TELEGRAM_TOKEN"] == TOKEN
    assert valores["GEMINI_API_KEY"] == "key-vieja"
    assert valores["GEMINI_MODEL"] == "gemini-2.5-flash-lite"
    assert valores["NOTITA_CONTEXTO"] == "Tenemos un gato"


# --------------------------------------------------------------------------
# Lo importante: no dejar el bot muerto
# --------------------------------------------------------------------------

def test_reinstalar_reusa_la_url_y_le_pone_el_secreto_nuevo(tmp_path, monkeypatch,
                                                            telegram_falso):
    """Con un webhook que ya andaba, Enter en el paso de la URL = «dejá esa»."""
    llamadas, estado = telegram_falso
    estado["webhook"] = "https://yaandaba.pythonanywhere.com/telegram"
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_WEBHOOK_SECRET=secreto-viejo\n")

    respuestas = list(RESPUESTAS_FELICES)
    respuestas[8] = ""  # Enter -> la URL que ya tenía
    correr(monkeypatch, env, respuestas)

    # Para leer el grupo hubo que desconectarlo, pero terminó puesto igual...
    assert any(m == "deleteWebhook" for m, _ in llamadas)
    assert estado["webhook"] == "https://yaandaba.pythonanywhere.com/telegram"
    # ...y con el secreto nuevo, que es el que quedó en el .env.
    nuevo = leer(env)["TELEGRAM_WEBHOOK_SECRET"]
    assert nuevo != "secreto-viejo"
    assert [p for m, p in llamadas if m == "setWebhook"][-1]["secret_token"] == nuevo


def test_si_no_hay_url_y_habia_webhook_lo_restaura(tmp_path, monkeypatch,
                                                   telegram_falso, capsys):
    # Caso raro pero posible: borró la URL a mano en el prompt.
    llamadas, estado = telegram_falso
    estado["webhook"] = "https://yaandaba.pythonanywhere.com/telegram"
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_WEBHOOK_SECRET=secreto-viejo\n")

    monkeypatch.setattr(install, "paso_url", lambda previo="": "")
    respuestas = [r for i, r in enumerate(RESPUESTAS_FELICES) if i not in (8, 10)]
    correr(monkeypatch, env, respuestas)

    assert any(m == "deleteWebhook" for m, _ in llamadas)
    assert estado["webhook"] == "https://yaandaba.pythonanywhere.com/telegram"
    assert "como estaba" in capsys.readouterr().out


def test_sin_webhook_previo_no_inventa_nada(tmp_path, monkeypatch, telegram_falso):
    _, estado = telegram_falso
    respuestas = list(RESPUESTAS_FELICES)
    respuestas[8] = ""
    respuestas = respuestas[:-1]
    correr(monkeypatch, tmp_path / ".env", respuestas)
    assert estado["webhook"] == ""


def test_ctrl_c_despues_de_desconectar_deja_todo_como_estaba(telegram_falso):
    llamadas, estado = telegram_falso
    estado["webhook"] = ""
    install._A_RESTAURAR.update(token=TOKEN, url="https://yaandaba.com/telegram")

    install.restaurar_webhook()

    assert estado["webhook"] == "https://yaandaba.com/telegram"
    assert install._A_RESTAURAR == {}
