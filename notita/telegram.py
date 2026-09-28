"""Cliente mínimo de la Bot API de Telegram."""
from __future__ import annotations

import json
import logging
import time

import requests

from . import config

log = logging.getLogger("notita.telegram")
# La descripción del último error de la API, para distinguir un «message is not
# modified» (inofensivo) de un mensaje que ya no existe.
_ULTIMO_ERROR: dict[str, str] = {}
API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20
# El proxy de las cuentas gratuitas de PythonAnywhere falla de a ratos, no de a
# instantes: con 3 intentos y 3 segundos no alcanzaba y el mensaje se perdía. Igual
# hay un techo, porque esto corre dentro del webhook (después va a la cola de salida).
INTENTOS = 4
ESPERA = 1  # segundos: 1, 2, 4
# Cuánto se acepta esperar cuando Telegram pide frenar. Más que esto no se aguanta:
# el webhook tiene que contestarle a Telegram antes de que lo reintente.
ESPERA_MAXIMA = 5


def llamar(metodo: str, **payload) -> dict | None:
    """Toda la conversación con Telegram pasa por acá: reintentos y 429 en un lugar.

    Con `_archivo=(nombre, bytes)` manda multipart en vez de JSON (para el .ics).
    """
    if not config.TELEGRAM_TOKEN:
        log.error("Falta TELEGRAM_TOKEN")
        return None
    archivo = payload.pop("_archivo", None)
    # Se limpia al entrar: si no, un «message is not modified» de hace rato hacía leer
    # el próximo fallo como si también lo fuera, y el tablero no se republicaba nunca.
    _ULTIMO_ERROR["descripcion"] = ""
    url = API.format(token=config.TELEGRAM_TOKEN, method=metodo)
    for intento in range(1, INTENTOS + 1):
        try:
            if archivo:
                r = requests.post(url, data=payload, timeout=TIMEOUT,
                                  files={"document": (*archivo, "text/calendar")})
            else:
                r = requests.post(url, json=payload, timeout=TIMEOUT)
            data = r.json()
            if data.get("ok"):
                return data.get("result")
            # 429: mandamos demasiado rápido (la rutina de las 20:00 con muchas
            # tareas lo provoca). Telegram dice cuánto esperar; antes se perdía
            # el mensaje en el primer intento.
            espera = (data.get("parameters") or {}).get("retry_after")
            if data.get("error_code") == 429 and espera and intento < INTENTOS:
                espera = min(int(espera), ESPERA_MAXIMA)
                log.warning("Telegram %s: 429, espero %ss (intento %d/%d)",
                            metodo, espera, intento, INTENTOS)
                time.sleep(espera)
                continue
            _ULTIMO_ERROR["descripcion"] = str(data.get("description") or "")
            nivel = log.info if "not modified" in _ULTIMO_ERROR["descripcion"] else log.error
            nivel("Telegram %s falló: %s", metodo, data)
            return None
        except requests.exceptions.ConnectionError as e:
            # El proxy de PythonAnywhere (cuentas gratis) a veces falla un instante: reintentamos.
            # Logueamos solo el tipo de error, asi el token (que va en la URL) no queda en los logs.
            log.warning("Telegram %s: error de conexion (%s), intento %d/%d",
                        metodo, type(e).__name__, intento, INTENTOS)
            if intento < INTENTOS:
                time.sleep(ESPERA * 2 ** (intento - 1))
        except Exception as e:
            log.error("Error llamando a Telegram %s: %s", metodo, type(e).__name__)
            return None
    log.error("Telegram %s: sin conexion despues de %d intentos", metodo, INTENTOS)
    return None


# Telegram rechaza los mensajes de más de 4096 caracteres. Antes de esto, un /todo
# con muchas tareas devolvía error y el comando moría en silencio.
LARGO_MAXIMO = 4000


def enviar_largo(chat_id: int, texto: str, teclado: list | None = None) -> dict | None:
    """Manda un texto largo partido en varios mensajes, cortando por renglón."""
    if len(texto) <= LARGO_MAXIMO:
        return enviar(chat_id, texto, teclado)

    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > LARGO_MAXIMO and actual:
            partes.append(actual)
            actual = ""
        actual += ("\n" if actual else "") + linea[:LARGO_MAXIMO]
    if actual:
        partes.append(actual)

    ultimo = None
    for i, parte in enumerate(partes, 1):
        pie = f"\n<i>({i}/{len(partes)})</i>"
        # El teclado va sólo en el último, para que no se repita.
        ultimo = enviar(chat_id, parte + pie, teclado if i == len(partes) else None)
    return ultimo


def vaciar_cola(chat_id: int | None = None, limite: int = 5) -> int:
    """Reintenta los mensajes que quedaron sin mandar. Devuelve cuántos salieron.

    Se llama al empezar a procesar cada mensaje y en la rutina diaria: así una
    confirmación que no salió por un hipo del proxy llega en la próxima, en vez de
    desaparecer.
    """
    from . import db

    salieron = 0
    for fila in db.salientes_pendientes(chat_id, limite):
        teclado = json.loads(fila["teclado"]) if fila["teclado"] else None
        # Se llama directo a `llamar` para no volver a encolar lo mismo si falla.
        if _mandar(fila["chat_id"], fila["texto"], teclado) is not None:
            db.borrar_saliente(fila["id"])
            salieron += 1
        else:
            intentos = db.sumar_intento_saliente(fila["id"])
            log.warning("Sigo sin poder mandar el mensaje %s (%d intentos)",
                        fila["id"], intentos)
            break   # si falla uno, seguro fallan los demás: no insistimos ahora
    return salieron


def _mandar(chat_id: int, texto: str, teclado: list | None = None,
            responder_a: int | None = None, silencioso: bool = False,
            forzar_respuesta: bool = False) -> dict | None:
    payload = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if silencioso:
        # Todo lo que Notita manda por iniciativa propia va sin notificación: el
        # único que suena es el parte diario.
        payload["disable_notification"] = True
    if forzar_respuesta:
        payload["reply_markup"] = {"force_reply": True, "selective": True}
    if teclado:
        payload["reply_markup"] = {"inline_keyboard": teclado}
    if responder_a:
        payload["reply_to_message_id"] = responder_a
        payload["allow_sending_without_reply"] = True
    return llamar("sendMessage", **payload)


def enviar(chat_id: int, texto: str, teclado: list | None = None,
           responder_a: int | None = None, silencioso: bool = False,
           forzar_respuesta: bool = False, encolar: bool = True) -> dict | None:
    resultado = _mandar(chat_id, texto, teclado, responder_a, silencioso, forzar_respuesta)
    if resultado is None and encolar:
        # No se pudo mandar (proxy caído, 429 largo). Antes se perdía en silencio y el
        # usuario veía que la acción se hizo pero nunca le contestamos. Ahora queda en
        # cola y sale en la próxima oportunidad.
        from . import db

        db.encolar_saliente(chat_id, texto, teclado)
        log.error("No pude mandar el mensaje: lo dejo en la cola de salida")
    return resultado


def editar(chat_id: int, message_id: int, texto: str, teclado: list | None = None,
           encolar: bool = False) -> dict | None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": teclado or []},
    }
    resultado = llamar("editMessageText", **payload)
    if resultado is None and encolar:
        from . import db

        db.encolar_saliente(chat_id, texto, teclado)
    return resultado


def editar_teclado(chat_id: int, message_id: int, teclado: list | None) -> dict | None:
    """Cambia sólo los botones: sirve para sacar el «Deshacer» cuando vence."""
    return llamar("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                  reply_markup={"inline_keyboard": teclado or []})


def borrar(chat_id: int, message_id: int) -> bool:
    """Borra un mensaje. Si ya no está o es muy viejo, no pasa nada."""
    return llamar("deleteMessage", chat_id=chat_id, message_id=message_id) is not None


def fijar(chat_id: int, message_id: int) -> dict | None:
    return llamar("pinChatMessage", chat_id=chat_id, message_id=message_id,
                  disable_notification=True)


# El menú que aparece al escribir «/» en Telegram. Estaba vacío: nadie se enteraba
# de que existían los comandos si no leía /ayuda.
COMANDOS = (
    ("tablero", "Publica el tablero al final del chat"),
    ("super", "La lista del súper"),
    ("parte", "Manda el parte de hoy ahora mismo"),
    ("ayuda", "Cómo funciona Notita"),
)


def registrar_comandos() -> bool:
    """Deja el menú «/» con los comandos de esta versión."""
    lista = [{"command": c, "description": d} for c, d in COMANDOS]
    return llamar("setMyCommands", commands=lista) is not None


def desfijar(chat_id: int, message_id: int) -> dict | None:
    return llamar("unpinChatMessage", chat_id=chat_id, message_id=message_id)


def ultimo_error_fue_no_modificado() -> bool:
    """Si el último fallo fue «message is not modified», que no es un problema."""
    return "not modified" in (_ULTIMO_ERROR.get("descripcion") or "")


def responder_callback(callback_id: str, texto: str = "") -> None:
    llamar("answerCallbackQuery", callback_query_id=callback_id, text=texto)


def escapar(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mencion(persona: str) -> str:
    """Mención clickeable si conocemos el user_id; si no, el nombre pelado."""
    nombre = config.NOMBRES.get(persona, persona)
    uid = config.user_id_de_persona(persona)
    if uid:
        return f'<a href="tg://user?id={uid}">{escapar(nombre)}</a>'
    return escapar(nombre)


def mandar_archivo(chat_id: int, nombre: str, contenido: bytes,
                   leyenda: str = "") -> dict | None:
    """Manda un archivo (el .ics del calendario)."""
    return llamar("sendDocument", chat_id=chat_id, caption=leyenda,
                  disable_notification=True, _archivo=(nombre, contenido))
