"""Las respuestas que se perdían: la acción se hacía y la confirmación no llegaba.

Pasó de verdad en el grupo, tres veces. Reproducido con dos updates casi
simultáneos no se caía, así que la causa no era un lock ni estado compartido: era
que `telegram.llamar` se rendía (el proxy de PythonAnywhere falla de a ratos) y el
mensaje se perdía en silencio. Ahora queda en una cola de salida.
"""
import threading
import time

import pytest
import requests

from notita import db, handlers, llm, reminders, telegram
from notita.dates import hoy

from .conftest import CHAT
from .test_bugs_grupo import item, mensaje, responde, textos


# --------------------------------------------------------------------------
# Dos mensajes casi simultáneos
# --------------------------------------------------------------------------

def test_dos_updates_casi_simultaneos_dan_dos_respuestas(enviados, monkeypatch):
    """Lo que pidió el reporte: dos updates encimados → dos respuestas."""
    def lenta(texto, *a, **k):
        time.sleep(0.4)          # la ventana donde entra el segundo mensaje
        return {"intencion": "anotar", "es_tarea": True,
                "items": [item(texto[:30], fecha_kind="hoy")]}

    monkeypatch.setattr(llm, "interpretar_mensaje", lenta)
    errores = []

    def procesar(u):
        try:
            handlers.handle_update(u)
        except BaseException as e:            # noqa: BLE001 - queremos ver cualquiera
            errores.append(repr(e))

    hilos = [threading.Thread(target=procesar, args=(mensaje(t, update_id=i),))
             for i, t in enumerate(["sacar la basura", "limpiar el horno"], start=1)]
    hilos[0].start()
    time.sleep(0.1)
    hilos[1].start()
    for h in hilos:
        h.join()

    assert errores == []
    assert len(textos(enviados)) == 2, "las dos confirmaciones tienen que llegar"
    assert len(db.pendientes(CHAT, tipo="casa")) == 2


def test_una_accion_larga_con_otro_mensaje_encima(enviados, monkeypatch):
    """El caso del «borrá» con 7 ítems y otro mensaje 10 segundos después."""
    for i in range(7):
        db.crear_tarea(CHAT, f"cosa {i}", due=hoy())

    def lenta(texto, *a, **k):
        time.sleep(0.4)
        if texto.startswith("borrá"):
            return {"intencion": "borrar", "es_tarea": False, "items": [],
                    "objetivos": [f"cosa {i}" for i in range(7)]}
        return {"intencion": "anotar", "es_tarea": True,
                "items": [item("regar las plantas", fecha_kind="hoy")]}

    monkeypatch.setattr(llm, "interpretar_mensaje", lenta)
    a = threading.Thread(target=handlers.handle_update,
                         args=(mensaje("borrá todas esas cosas", update_id=1),))
    b = threading.Thread(target=handlers.handle_update,
                         args=(mensaje("regar las plantas", update_id=2),))
    a.start()
    time.sleep(0.1)
    b.start()
    a.join()
    b.join()

    salida = textos(enviados)
    assert len(salida) == 2
    assert any("Cosa 0" in t and "Cosa 6" in t for t in salida), "las 7 en un mensaje"
    assert any("Regar las plantas" in t for t in salida)


# --------------------------------------------------------------------------
# La cola de salida
# --------------------------------------------------------------------------

@pytest.fixture
def telegram_caido(monkeypatch):
    """Telegram no contesta: `llamar` devuelve None, como con el proxy roto."""
    caido = {"si": True}
    original = telegram.llamar
    enviados = []

    def fake(metodo, **payload):
        if caido["si"]:
            return None
        enviados.append(payload)
        return original(metodo, **payload)

    monkeypatch.setattr(telegram, "llamar", fake)
    return caido, enviados


def test_si_no_se_puede_mandar_queda_en_la_cola(telegram_caido, monkeypatch):
    caido, _ = telegram_caido
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("sacar la basura", fecha_kind="hoy")])

    handlers.handle_update(mensaje("sacar la basura", update_id=1))

    # La acción se hizo…
    assert len(db.pendientes(CHAT, tipo="casa")) == 1
    # …y la confirmación no se perdió: está esperando.
    cola = db.salientes_pendientes()
    assert len(cola) == 1
    assert "Sacar la basura" in cola[0]["texto"]


def test_la_cola_se_manda_en_el_mensaje_siguiente(telegram_caido, monkeypatch):
    caido, salieron = telegram_caido
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("sacar la basura", fecha_kind="hoy")])
    handlers.handle_update(mensaje("sacar la basura", update_id=1))
    assert db.salientes_pendientes()

    caido["si"] = False        # volvió el proxy
    responde(monkeypatch, intencion="charla", es_tarea=False, comentario="dale 🤍")
    handlers.handle_update(mensaje("gracias", update_id=2))

    assert db.salientes_pendientes() == [], "la cola se vació"
    textos_enviados = [p.get("text", "") for p in salieron]
    assert any("Sacar la basura" in t for t in textos_enviados), "llegó la perdida"
    assert any("dale" in t for t in textos_enviados), "y la nueva también"


def test_la_cola_tambien_se_manda_en_la_rutina_diaria(telegram_caido, monkeypatch):
    caido, salieron = telegram_caido
    telegram.enviar(CHAT, "una confirmación que no salió")
    assert len(db.salientes_pendientes()) == 1

    caido["si"] = False
    resultado = reminders.correr_rutina_diaria(ref=hoy())

    assert resultado["de_la_cola"] == 1
    assert db.salientes_pendientes() == []


def test_el_teclado_sobrevive_a_la_cola(telegram_caido):
    caido, salieron = telegram_caido
    teclado = [[{"text": "Hoy", "callback_data": "f:1:h"}]]

    telegram.enviar(CHAT, "¿Para cuándo «X»?", teclado)
    caido["si"] = False
    telegram.vaciar_cola()

    assert salieron[-1]["reply_markup"] == {"inline_keyboard": teclado}


def test_un_mensaje_que_nunca_sale_se_descarta(telegram_caido):
    """Si no hay forma, se abandona: una cola vieja volcada días después es peor."""
    caido, _ = telegram_caido
    telegram.enviar(CHAT, "esto no va a salir nunca")

    for _ in range(db.INTENTOS_SALIENTE + 1):
        telegram.vaciar_cola()

    assert db.salientes_pendientes() == []


def test_si_falla_la_cola_no_insiste_con_los_demas(telegram_caido):
    caido, _ = telegram_caido
    for i in range(3):
        telegram.enviar(CHAT, f"mensaje {i}")
    assert len(db.salientes_pendientes()) == 3

    telegram.vaciar_cola()

    # Sigue habiendo 3 (el primero sumó un intento, los otros no se tocaron).
    assert len(db.salientes_pendientes()) == 3


def test_reintentar_no_duplica_el_mensaje(telegram_caido):
    """La cola usa `llamar` directo: si falla de nuevo, no se encola otra copia."""
    caido, _ = telegram_caido
    telegram.enviar(CHAT, "una sola vez")
    telegram.vaciar_cola()
    assert len(db.salientes_pendientes()) == 1


# --------------------------------------------------------------------------
# Que el proxy tenga más aire antes de rendirse
# --------------------------------------------------------------------------

def test_aguanta_un_proxy_caido_varios_segundos(monkeypatch):
    import importlib

    tg = importlib.reload(telegram)
    monkeypatch.setattr(tg.config, "TELEGRAM_TOKEN", "123:abc")
    dormidas, llamadas = [], []
    monkeypatch.setattr(tg.time, "sleep", lambda s: dormidas.append(s))

    class Resp:
        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def post(*a, **k):
        llamadas.append(1)
        if len(llamadas) < tg.INTENTOS:
            raise requests.exceptions.ProxyError("proxy caído")
        return Resp()

    monkeypatch.setattr(tg.requests, "post", post)

    assert tg.llamar("sendMessage", chat_id=1, text="hola") == {"message_id": 1}
    assert sum(dormidas) >= 7, "con 3 segundos no alcanzaba: el proxy falla de a ratos"


# --------------------------------------------------------------------------
# Los menores del reporte
# --------------------------------------------------------------------------

def test_una_sola_compra_se_tacha_en_singular(enviados):
    db.crear_tarea(CHAT, "café", tipo="compras", categoria="compras")

    handlers.handle_update({"callback_query": {
        "id": "cb", "data": "ct", "from": {"id": 111},
        "message": {"message_id": 10, "chat": {"id": CHAT}}}})

    editado = [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]
    assert "Tachada:" in editado
    assert "las 1" not in editado


def test_la_fecha_imposible_muestra_los_botones(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("pagar la expensa", categoria="pagos",
                         fecha_kind="fecha_exacta", fecha_day=31, fecha_month=2)])

    handlers.handle_update(mensaje("pagar la expensa el 31 de febrero"))

    envios = [e for e in enviados if e["metodo"] == "sendMessage"]
    aviso = [e for e in envios if "no existe" in e["text"]][0]
    etiquetas = [b["text"] for fila in aviso["reply_markup"]["inline_keyboard"]
                 for b in fila]
    assert etiquetas[:2] == ["Hoy", "Mañana"]
    # Y la confirmación no dice «algún día», que era engañoso.
    assert "algún día" not in envios[0]["text"]
    assert "¿para cuándo?" in envios[0]["text"]


def test_cada_tres_dias_arranca_hoy(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("regar las plantas", fecha_kind="desconocida",
                         recur_kind="diaria", recur_interval=3)])

    handlers.handle_update(mensaje("regar las plantas cada 3 días"))

    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert tarea["due_date"] == hoy().isoformat(), "antes arrancaba pasado mañana"


def test_una_semanal_sin_fecha_arranca_en_su_dia(enviados, monkeypatch):
    responde(monkeypatch, intencion="anotar", es_tarea=True,
             items=[item("sacar la basura", fecha_kind="desconocida",
                         recur_kind="semanal", recur_weekday=2)])   # miércoles

    handlers.handle_update(mensaje("sacar la basura todos los miércoles"))

    from datetime import date
    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert date.fromisoformat(tarea["due_date"]).weekday() == 2


def test_el_prompt_le_prohibe_arrancar_con_che():
    assert "Che" in llm._sistema(), "tiene que estar la prohibición explícita"
    assert "NUNCA arranques" in llm._sistema()


# --------------------------------------------------------------------------
# La hora que se promete es la que se va a cumplir
# --------------------------------------------------------------------------

def test_con_una_corrida_diaria_no_promete_la_hora(enviados, monkeypatch):
    from notita import config, views

    monkeypatch.setattr(config, "CRON_MINUTOS", 0)
    tid = db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                         due=hoy(), hora="22:07", created_by="barbu")

    texto = views.confirmacion(db.obtener(tid), hoy())

    assert "22:07" not in texto, "no puede prometer una hora a la que no corre"
    assert "20:00" in texto


def test_con_cron_cada_5_minutos_redondea_y_lo_dice(enviados, monkeypatch):
    from notita import config, views

    monkeypatch.setattr(config, "CRON_MINUTOS", 5)
    tid = db.crear_tarea(CHAT, "fijate el horno", tipo="recado", responsable="axel",
                         due=hoy(), hora="22:07", created_by="barbu")

    assert "22:10" in views.confirmacion(db.obtener(tid), hoy())


def test_el_redondeo_cruza_la_medianoche(monkeypatch):
    from datetime import date, timedelta

    from notita import config
    from notita.dates import cuando_se_entrega

    monkeypatch.setattr(config, "CRON_MINUTOS", 15)
    hoy_ = date(2026, 9, 26)
    assert cuando_se_entrega(hoy_, "23:58", hoy_) == (hoy_ + timedelta(days=1), "00:00")
