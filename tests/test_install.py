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
            # Con offset ya consumido no hay nada nuevo, como en la API real.
            if payload.get("offset"):
                return True, []
            return True, [
                {"update_id": 1,
                 "message": {"chat": {"id": GRUPO, "type": "group", "title": "Casita"},
                             "from": {"id": 111, "first_name": "Axel", "is_bot": False},
                             "text": "hola"}},
                {"update_id": 2,
                 "message": {"chat": {"id": GRUPO, "type": "group", "title": "Casita"},
                             "from": {"id": 222, "first_name": "Barbu", "is_bot": False},
                             "text": "buenas"}},
            ]
        if metodo == "sendMessage":
            return True, {"message_id": 1}
        return True, {}

    monkeypatch.setattr(install, "tg", fake_tg)
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeGet())
    # Sin esperas reales: en producción escucha 90s y aguanta 6s de silencio.
    monkeypatch.setattr(install.time, "sleep", lambda s: None)
    monkeypatch.setattr(install, "ESPERA", 2)
    monkeypatch.setattr(install, "QUIETO", 0)
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


# El camino feliz, con nombre para cada respuesta: así agregar o mover un prompt
# no rompe todos los tests por un índice corrido.
GUION = (
    ("token", TOKEN),
    ("lista_completa", ""),          # ¿está completa la lista? -> sí
    ("nombre_1", ""),                # ¿cómo le pongo a Axel? -> el detectado
    ("nombre_2", ""),                # idem Barbu
    ("falta_alguien", ""),           # ¿falta alguien? -> no
    ("gemini_key", "una-key-de-gemini"),
    ("modelo", ""),                  # -> el default
    ("url", "https://miusuario.pythonanywhere.com"),
    ("contexto", ""),
    ("mensaje_de_prueba", ""),       # -> sí
)


def guion(**cambios) -> list[str]:
    """El camino feliz. `campo="otra cosa"` cambia una respuesta, `campo=None` la saca."""
    desconocidos = set(cambios) - {n for n, _ in GUION}
    assert not desconocidos, f"no existe ese prompt: {desconocidos}"
    respuestas = []
    for nombre, valor in GUION:
        if nombre in cambios:
            if cambios[nombre] is None:
                continue
            valor = cambios[nombre]
        respuestas.append(valor)
    return respuestas


def leer(env_path) -> dict:
    return install.leer_env(env_path)


# --------------------------------------------------------------------------

def test_instalacion_completa(tmp_path, monkeypatch, telegram_falso, capsys):
    llamadas, estado = telegram_falso
    env = tmp_path / ".env"

    correr(monkeypatch, env, guion())

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
    correr(monkeypatch, env, guion())
    # Tiene secretos: sólo el dueño puede leerlo.
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_le_agrega_telegram_a_la_url(tmp_path, monkeypatch, telegram_falso):
    _, estado = telegram_falso
    # sin https y con barra al final
    correr(monkeypatch, tmp_path / ".env", guion(url="miusuario.pythonanywhere.com/"))
    assert estado["webhook"] == "https://miusuario.pythonanywhere.com/telegram"


def test_rechaza_un_token_con_mala_forma(tmp_path, monkeypatch, telegram_falso, capsys):
    correr(monkeypatch, tmp_path / ".env", ["no-es-un-token", *guion()])
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
    correr(monkeypatch, tmp_path / ".env", [TOKEN, "s", *guion(token=None)])

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

    correr(monkeypatch, env, guion())

    valores = leer(env)
    assert valores["CRON_SECRET"] == "secreto-que-ya-estaba"
    assert valores["NOTITA_DB"] == "/datos/notita.db"


def test_hace_backup_del_env_anterior(tmp_path, monkeypatch, telegram_falso):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_TOKEN=viejo\n")
    correr(monkeypatch, env, guion())

    backup = tmp_path / ".env.bak"
    assert backup.exists(), list(tmp_path.iterdir())
    assert "viejo" in backup.read_text()


def test_usa_lo_anterior_como_default(tmp_path, monkeypatch, telegram_falso):
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_TOKEN={TOKEN}\nGEMINI_API_KEY=key-vieja\n"
                   "GEMINI_MODEL=gemini-2.5-flash-lite\nNOTITA_CONTEXTO=Tenemos un gato\n")

    # Enter en el token y en la key -> los que ya estaban
    correr(monkeypatch, env, guion(token="", gemini_key=""))

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

    correr(monkeypatch, env, guion(url=""))  # Enter -> la URL que ya tenía

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
    correr(monkeypatch, env, guion(url=None, mensaje_de_prueba=None))

    assert any(m == "deleteWebhook" for m, _ in llamadas)
    assert estado["webhook"] == "https://yaandaba.pythonanywhere.com/telegram"
    assert "como estaba" in capsys.readouterr().out


def test_si_en_la_segunda_vuelta_nadie_escribe_no_se_pierde_el_grupo(tmp_path, monkeypatch,
                                                                    telegram_falso):
    """Contestar «no» a «¿está completa la lista?» no puede borrar lo ya detectado."""
    env = tmp_path / ".env"
    # Primera vuelta: hay mensajes, pero contesta que falta gente. Segunda vuelta:
    # nadie escribe (los updates ya se consumieron). Igual tiene que recordar todo.
    correr(monkeypatch, env, [TOKEN, "n", *guion(token=None)])

    valores = install.leer_env(env)
    assert valores["ALLOWED_CHAT_ID"] == str(GRUPO)
    assert valores["NOTITA_PERSONAS"] == "Axel:111,Barbu:222"


def test_sin_webhook_previo_no_inventa_nada(tmp_path, monkeypatch, telegram_falso):
    _, estado = telegram_falso
    correr(monkeypatch, tmp_path / ".env", guion(url="", mensaje_de_prueba=None))
    assert estado["webhook"] == ""


# --------------------------------------------------------------------------
# Preguntas de sí o no
# --------------------------------------------------------------------------

@pytest.mark.parametrize("respuesta,esperado", [
    ("s", True), ("si", True), ("sí", True), ("SI", True), ("dale", True),
    ("n", False), ("no", False), ("NO", False), ("nop", False),
])
def test_confirmar_entiende_las_respuestas_normales(monkeypatch, respuesta, esperado):
    monkeypatch.setattr("builtins.input", lambda p="": respuesta)
    assert install.confirmar("¿?") is esperado


@pytest.mark.parametrize("default", [True, False])
def test_enter_usa_el_default(monkeypatch, default):
    monkeypatch.setattr("builtins.input", lambda p="": "")
    assert install.confirmar("¿?", default=default) is default


def test_una_respuesta_rara_vuelve_a_preguntar(monkeypatch, capsys):
    """El bug de la «W»: cualquier tecla contaba como «no» y cortaba el instalador."""
    respuestas = iter(["W", "jsjs", "42", "s"])
    monkeypatch.setattr("builtins.input", lambda p="": next(respuestas))

    assert install.confirmar("¿Espero de nuevo?") is True
    assert capsys.readouterr().out.count("No te entendí") == 3


# --------------------------------------------------------------------------
# Escuchar el grupo
# --------------------------------------------------------------------------

def test_esperar_mensajes_detecta_grupo_y_gente(telegram_falso, monkeypatch, capsys):
    monkeypatch.setattr(install.time, "sleep", lambda s: None)
    grupos, gente = install.esperar_mensajes(TOKEN, espera=2, quieto=0)

    assert grupos == {GRUPO: "Casita"}
    assert gente == {111: "Axel", 222: "Barbu"}
    # Va avisando a medida que los ve, no al final.
    assert "Te escuché, Axel" in capsys.readouterr().out


def test_esperar_mensajes_confirma_lo_leido(telegram_falso, monkeypatch):
    """Sin confirmar el offset, Telegram le re-entrega esos mensajes al webhook."""
    llamadas, _ = telegram_falso
    monkeypatch.setattr(install.time, "sleep", lambda s: None)
    install.esperar_mensajes(TOKEN, espera=2, quieto=0)

    offsets = [p.get("offset") for m, p in llamadas if m == "getUpdates"]
    assert offsets[0] is None       # la primera vez se pide todo
    assert offsets[-1] == 3         # y al final se confirma: último update_id + 1


def test_al_restaurar_el_webhook_no_le_llegan_los_mensajes_de_la_deteccion(
        tmp_path, monkeypatch, telegram_falso):
    llamadas, estado = telegram_falso
    estado["webhook"] = "https://yaandaba.pythonanywhere.com/telegram"
    monkeypatch.setattr(install, "paso_url", lambda previo="": "")

    correr(monkeypatch, tmp_path / ".env", guion(url=None, mensaje_de_prueba=None))

    restaurado = [p for m, p in llamadas if m == "setWebhook"][-1]
    assert restaurado["drop_pending_updates"] is True


def test_esperar_mensajes_sin_nada_no_se_cuelga(telegram_falso, monkeypatch):
    llamadas, _ = telegram_falso
    tg_base = install.tg
    monkeypatch.setattr(install, "tg",
                        lambda t, m, **p: (True, []) if m == "getUpdates" else tg_base(t, m, **p))
    monkeypatch.setattr(install.time, "sleep", lambda s: None)

    grupos, gente = install.esperar_mensajes(TOKEN, espera=0.05, quieto=0)
    assert grupos == {} and gente == {}


def test_ignora_los_chats_privados_y_los_bots(telegram_falso, monkeypatch):
    tg_base = install.tg

    def solo_privado(token, metodo, **payload):
        if metodo == "getUpdates":
            return True, [
                {"update_id": 1, "message": {"chat": {"id": 5, "type": "private"},
                                             "from": {"id": 5, "first_name": "Yo"}}},
                {"update_id": 2, "message": {"chat": {"id": GRUPO, "type": "supergroup",
                                                      "title": "Casita"},
                                             "from": {"id": 9, "first_name": "OtroBot",
                                                      "is_bot": True}}},
            ]
        return tg_base(token, metodo, **payload)

    monkeypatch.setattr(install, "tg", solo_privado)
    monkeypatch.setattr(install.time, "sleep", lambda s: None)

    grupos, gente = install.esperar_mensajes(TOKEN, espera=0.05, quieto=0)
    assert grupos == {GRUPO: "Casita"}  # el supergrupo sí
    assert gente == {}                  # el bot no cuenta como persona


# --------------------------------------------------------------------------
# Modo demo
# --------------------------------------------------------------------------

def test_demo_no_toca_la_red_ni_pide_credenciales(tmp_path, monkeypatch, capsys):
    from notita import demo, llm

    # activar_demo() pisa globals del módulo: los registramos para que se restauren.
    monkeypatch.setattr(install, "tg", install.tg)
    monkeypatch.setattr(install, "DEMO", False)
    monkeypatch.setattr(requests, "get", requests.get, raising=False)
    monkeypatch.setattr(llm, "probar_conexion", llm.probar_conexion)
    monkeypatch.setattr(llm, "interpretar_mensaje", llm.interpretar_mensaje)

    env = tmp_path / "demo.env"
    respuestas = guion(token=demo.TOKEN, url="https://demo.pythonanywhere.com")
    it = iter(respuestas)
    monkeypatch.setattr("builtins.input", lambda p="": next(it))
    monkeypatch.setattr(sys, "argv", ["install.py", "--demo", "--env", str(env)])

    install.main()

    salida = capsys.readouterr().out
    assert "MODO DEMO" in salida
    assert "Fin de la demo" in salida
    assert "Nada de esto era real" in salida

    # Recorrió todo y escribió un .env con los datos inventados.
    valores = install.leer_env(env)
    assert valores["ALLOWED_CHAT_ID"] == str(demo.CHAT_ID)
    assert valores["NOTITA_PERSONAS"] == "Vos:111111111,Tu pareja:222222222"
    # Y el "webhook" quedó puesto en el Telegram de mentira.
    assert install.tg.webhook == "https://demo.pythonanywhere.com/telegram"
    assert install.tg.mensajes  # el mensaje de bienvenida


def test_demo_no_escribe_el_env_real_aunque_no_le_pasen_env(monkeypatch, tmp_path):
    from notita import demo, llm

    monkeypatch.setattr(install, "tg", install.tg)
    monkeypatch.setattr(install, "DEMO", False)
    monkeypatch.setattr(requests, "get", requests.get, raising=False)
    monkeypatch.setattr(llm, "probar_conexion", llm.probar_conexion)
    monkeypatch.setattr(llm, "interpretar_mensaje", llm.interpretar_mensaje)
    monkeypatch.setattr(install, "ENV", tmp_path / "NO-TOCAR.env")
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    it = iter(guion(token=demo.TOKEN, url="https://demo.pythonanywhere.com"))
    monkeypatch.setattr("builtins.input", lambda p="": next(it))
    monkeypatch.setattr(sys, "argv", ["install.py", "--demo"])

    install.main()

    assert not (tmp_path / "NO-TOCAR.env").exists()
    assert install.ENV.name == "notita-demo.env"


def test_ctrl_c_despues_de_desconectar_deja_todo_como_estaba(telegram_falso):
    llamadas, estado = telegram_falso
    estado["webhook"] = ""
    install._A_RESTAURAR.update(token=TOKEN, url="https://yaandaba.com/telegram")

    install.restaurar_webhook()

    assert estado["webhook"] == "https://yaandaba.com/telegram"
    assert install._A_RESTAURAR == {}
