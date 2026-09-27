"""Los bugs que encontró la revisión adversarial. Cada test es uno, con su historia.

Todos fueron verificados ejecutándolos antes de arreglarlos: el test falla contra
el código viejo.
"""
from datetime import timedelta

import pytest

from notita import db, handlers, reminders, telegram
from notita.dates import RELLENO_FECHA, hoy, parse_solo_fecha

from .conftest import CHAT


def mensaje(texto, uid=111, update_id=None, clave="message"):
    cuerpo = {clave: {"chat": {"id": CHAT}, "from": {"id": uid}, "text": texto,
                      "message_id": 7}}
    if update_id is not None:
        cuerpo["update_id"] = update_id
    return cuerpo


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


# --------------------------------------------------------------------------
# 1. «ya compré el pan» tachaba «comprar pantuflas»
# --------------------------------------------------------------------------

@pytest.mark.parametrize("referencia,texto", [
    ("pan", "comprar pantuflas"),
    ("gas", "comprar gaseosa"),
    ("te", "llamar al veterinario"),
    ("sol", "llamar al soldador"),
])
def test_un_substring_no_alcanza_para_tachar(referencia, texto):
    """Sin límites de palabra, `pan` daba 1.0 contra `pantuflas` y tachaba sin preguntar."""
    assert db.puntaje(referencia, texto) < 0.5


@pytest.mark.parametrize("referencia,texto", [
    ("pan", "comprar pan"),
    ("heladera", "limpiar la heladera"),
    ("la de la heladera", "limpiar la heladera"),
    ("plomero", "llamar al plomero"),
    ("balcon", "pintar el balcón"),          # sin tilde
])
def test_pero_lo_que_de_verdad_coincide_sigue_valiendo(referencia, texto):
    assert db.puntaje(referencia, texto) == 1.0


def test_no_tacha_la_tarea_equivocada(enviados, monkeypatch):
    from notita import llm

    db.crear_tarea(CHAT, "comprar pantuflas", due=hoy())
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "completar", "referencia": "pan", "es_tarea": False, "items": []})

    handlers.handle_update(mensaje("ya compré el pan"))

    assert [r["texto"] for r in db.pendientes(CHAT)] == ["comprar pantuflas"]
    assert "No encontré" in textos(enviados)[0]


# --------------------------------------------------------------------------
# 2. La pregunta «¿para cuándo?» se comía mensajes ajenos
# --------------------------------------------------------------------------

@pytest.mark.parametrize("frase", [
    "el lunes", "mañana", "pasado mañana", "el 3 de octubre", "3/10",
    "esta semana", "la semana que viene", "el lunes de la semana que viene",
    "para el lunes", "el lunes a la mañana", "cuando se pueda", "ni idea",
    "algún día", "en dos semanas", "el finde",
])
def test_una_fecha_sola_si_contesta_la_pregunta(frase):
    assert parse_solo_fecha(frase) is not None, frase


@pytest.mark.parametrize("frase", [
    "el lunes voy al dentista",
    "el martes me traen el colchón",
    "mañana vamos a lo de mamá",
    "hoy no puedo, estoy reventado",
    "esta semana se pasó volando",
])
def test_un_mensaje_con_fecha_adentro_no_la_contesta(frase):
    """Antes cualquier mención de un día se robaba la respuesta y el mensaje se perdía."""
    assert parse_solo_fecha(frase) is None, frase


def test_el_mensaje_que_no_era_una_fecha_se_anota(enviados, monkeypatch):
    from notita import llm

    tid = db.crear_tarea(CHAT, "comprar pilas")
    db.set_pending(CHAT, "fecha", task_id=tid)
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "anotar", "es_tarea": True, "items": [
            {"texto": "ir al dentista", "tipo": "casa", "categoria": "tramites",
             "responsable": "ninguno", "fecha_kind": "dia_semana", "fecha_weekday": 0,
             "recur_kind": "ninguna", "necesita_aclaracion": False}]})

    handlers.handle_update(mensaje("el lunes voy al dentista"))

    pendientes = {r["texto"]: r["due_date"] for r in db.pendientes(CHAT)}
    assert "ir al dentista" in pendientes          # no se perdió
    assert pendientes["comprar pilas"] is None     # y no le robaron la fecha


def test_la_pregunta_caduca(enviados):
    tid = db.crear_tarea(CHAT, "comprar pilas")
    db.set_pending(CHAT, "fecha", task_id=tid)
    assert db.get_pending(CHAT) is not None

    with db.conn() as c:   # la envejecemos a mano
        c.execute("UPDATE pending SET created_at = ?", ("2020-01-01T10:00:00-03:00",))

    assert db.get_pending(CHAT) is None, "una pregunta de hace años no es una pregunta"


def test_una_fecha_suelta_despues_de_la_caducidad_no_toca_nada(enviados, monkeypatch):
    from notita import llm

    tid = db.crear_tarea(CHAT, "comprar pilas")
    db.set_pending(CHAT, "fecha", task_id=tid)
    with db.conn() as c:
        c.execute("UPDATE pending SET created_at = ?", ("2020-01-01T10:00:00-03:00",))
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "charla", "es_tarea": False, "comentario": "", "items": []})

    handlers.handle_update(mensaje("el lunes"))
    assert db.obtener(tid)["due_date"] is None


# --------------------------------------------------------------------------
# 3. Editar un mensaje duplicaba la tarea
# --------------------------------------------------------------------------

def test_editar_un_mensaje_no_duplica(enviados, monkeypatch):
    from notita import llm

    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "anotar", "es_tarea": True, "items": [
            {"texto": "limpiar la heladera", "tipo": "casa", "categoria": "limpieza",
             "responsable": "ninguno", "fecha_kind": "hoy", "recur_kind": "ninguna",
             "necesita_aclaracion": False}]})

    handlers.handle_update(mensaje("hay que limpiar la heldera", update_id=1))
    handlers.handle_update(mensaje("hay que limpiar la heladera", update_id=2,
                                   clave="edited_message"))

    assert len(db.pendientes(CHAT)) == 1


# --------------------------------------------------------------------------
# 4. Telegram reenviaba el update y se anotaba dos veces
# --------------------------------------------------------------------------

def test_un_update_repetido_se_ignora(enviados, monkeypatch):
    from notita import llm

    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "anotar", "es_tarea": True, "items": [
            {"texto": "sacar la basura", "tipo": "casa", "categoria": "limpieza",
             "responsable": "ninguno", "fecha_kind": "hoy", "recur_kind": "ninguna",
             "necesita_aclaracion": False}]})

    handlers.handle_update(mensaje("sacar la basura", update_id=42))
    handlers.handle_update(mensaje("sacar la basura", update_id=42))   # el reintento

    assert len(db.pendientes(CHAT)) == 1
    assert len(textos(enviados)) == 1    # tampoco contesta dos veces


def test_updates_distintos_se_procesan(enviados, monkeypatch):
    from notita import llm

    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "anotar", "es_tarea": True, "items": [
            {"texto": "sacar la basura", "tipo": "casa", "categoria": "limpieza",
             "responsable": "ninguno", "fecha_kind": "hoy", "recur_kind": "ninguna",
             "necesita_aclaracion": False}]})

    # Textos distintos a propósito: dos updates iguales los frena el anti-duplicados,
    # que es otro mecanismo.
    llm.interpretar_mensaje = lambda texto, *a, **k: {
        "intencion": "anotar", "es_tarea": True, "items": [
            {"texto": texto, "tipo": "casa", "categoria": "otros",
             "responsable": "ninguno", "fecha_kind": "hoy", "recur_kind": "ninguna",
             "necesita_aclaracion": False}]}
    handlers.handle_update(mensaje("sacar la basura", update_id=1))
    handlers.handle_update(mensaje("regar las plantas", update_id=2))
    assert len(db.pendientes(CHAT)) == 2


def test_sin_update_id_no_se_bloquea(enviados, monkeypatch):
    # Los tests y las llamadas internas no traen update_id.
    assert db.update_nuevo(None) is True
    assert db.update_nuevo(None) is True


# --------------------------------------------------------------------------
# 5. Un recado se marcaba entregado aunque Telegram fallara
# --------------------------------------------------------------------------

def test_si_falla_el_envio_el_recado_queda_pendiente(enviados, monkeypatch):
    rid = db.crear_tarea(CHAT, "comprá pan", tipo="recado", responsable="axel",
                         due=hoy(), created_by="barbu")
    monkeypatch.setattr(telegram, "llamar", lambda *a, **k: None)   # Telegram caído

    res = reminders.correr_rutina_diaria(ref=hoy())

    assert res["recados"] == 0
    assert db.obtener(rid)["estado"] == "pendiente"
    assert db.recados_hasta(CHAT, hoy()), "se tiene que reintentar mañana"


def test_si_falla_el_envio_la_tarea_se_recuerda_manana(enviados, monkeypatch):
    tid = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    monkeypatch.setattr(telegram, "llamar", lambda *a, **k: None)

    reminders.correr_rutina_diaria(ref=hoy())

    assert db.obtener(tid)["last_reminded_on"] is None


# --------------------------------------------------------------------------
# 6. El botón de fecha actuaba sobre tareas ya resueltas
# --------------------------------------------------------------------------

@pytest.mark.parametrize("accion", ["f:{}:m", "h:{}", "b:{}", "p:{}", "c:{}"])
def test_ningun_boton_toca_una_tarea_ya_hecha(enviados, accion):
    tid = db.crear_tarea(CHAT, "colgar el cuadro", due=hoy())
    db.marcar_hecha(tid, "axel")
    antes = dict(db.obtener(tid))

    handlers.handle_update({"callback_query": {
        "id": "x", "data": accion.format(tid), "from": {"id": 111},
        "message": {"message_id": 3, "chat": {"id": CHAT}}}})

    assert dict(db.obtener(tid)) == antes, "no se puede modificar algo ya resuelto"


def test_el_boton_de_fecha_sigue_andando_en_una_tarea_viva(enviados):
    tid = db.crear_tarea(CHAT, "colgar el cuadro")
    handlers.handle_update({"callback_query": {
        "id": "x", "data": f"f:{tid}:m", "from": {"id": 111},
        "message": {"message_id": 3, "chat": {"id": CHAT}}}})
    assert db.obtener(tid)["due_date"] == (hoy() + timedelta(days=1)).isoformat()


# --------------------------------------------------------------------------
# 7. El 429 de Telegram se descartaba en el primer intento
# --------------------------------------------------------------------------

class Respuesta:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def real(monkeypatch):
    """conftest reemplaza telegram.llamar; acá hace falta la función de verdad."""
    import importlib

    modulo = importlib.reload(telegram)
    monkeypatch.setattr(modulo.config, "TELEGRAM_TOKEN", "123:abc")
    return modulo


def test_el_429_se_reintenta_respetando_lo_que_pide_telegram(monkeypatch):
    tg = real(monkeypatch)
    dormidas, respuestas = [], [
        Respuesta({"ok": False, "error_code": 429, "parameters": {"retry_after": 2}}),
        Respuesta({"ok": True, "result": {"message_id": 1}}),
    ]
    monkeypatch.setattr(tg.time, "sleep", lambda s: dormidas.append(s))
    monkeypatch.setattr(tg.requests, "post", lambda *a, **k: respuestas.pop(0))

    assert tg.llamar("sendMessage", chat_id=1, text="hola") == {"message_id": 1}
    assert dormidas == [2]


def test_una_espera_larguisima_se_recorta(monkeypatch):
    """Telegram puede pedir minutos; el webhook no puede esperar tanto."""
    tg = real(monkeypatch)
    dormidas = []
    monkeypatch.setattr(tg.time, "sleep", lambda s: dormidas.append(s))
    monkeypatch.setattr(tg.requests, "post", lambda *a, **k: Respuesta(
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 300}}))

    assert tg.llamar("sendMessage", chat_id=1, text="hola") is None
    # Una espera por intento menos el último, todas recortadas al techo.
    assert dormidas == [tg.ESPERA_MAXIMA] * (tg.INTENTOS - 1)


def test_un_error_que_no_es_429_no_se_reintenta(monkeypatch):
    tg = real(monkeypatch)
    llamadas = []

    def post(*a, **k):
        llamadas.append(1)
        return Respuesta({"ok": False, "error_code": 400, "description": "chat not found"})

    monkeypatch.setattr(tg.requests, "post", post)
    assert tg.llamar("sendMessage", chat_id=1, text="hola") is None
    assert len(llamadas) == 1


# --------------------------------------------------------------------------
# 8. Los backups del .env con secretos adentro
# --------------------------------------------------------------------------

def test_el_backup_del_env_esta_ignorado_por_git():
    """install.py crea .env.bak con el token y las claves; no puede subirse al repo."""
    import subprocess
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    for nombre in (".env.bak", ".env.viejo.bak"):
        r = subprocess.run(["git", "check-ignore", nombre], cwd=raiz, capture_output=True)
        assert r.returncode == 0, f"{nombre} no está en el .gitignore"


def test_relleno_de_fechas_no_tiene_palabras_peligrosas():
    """Si «voy» o «dentista» entraran acá, volvería el bug de comerse mensajes."""
    assert "voy" not in RELLENO_FECHA
    assert "dentista" not in RELLENO_FECHA
    assert all(len(p) <= 12 for p in RELLENO_FECHA)
