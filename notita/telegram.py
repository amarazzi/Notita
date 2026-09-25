"""Cliente mínimo de la Bot API de Telegram."""
from __future__ import annotations

import logging
import time

import requests

from . import config

log = logging.getLogger("notita.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20
INTENTOS = 3
ESPERA = 1  # segundos (1, despues 2)


def llamar(metodo: str, **payload) -> dict | None:
    if not config.TELEGRAM_TOKEN:
        log.error("Falta TELEGRAM_TOKEN")
        return None
    url = API.format(token=config.TELEGRAM_TOKEN, method=metodo)
    for intento in range(1, INTENTOS + 1):
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT)
            data = r.json()
            if not data.get("ok"):
                log.error("Telegram %s falló: %s", metodo, data)
                return None
            return data.get("result")
        except requests.exceptions.ConnectionError as e:
            # El proxy de PythonAnywhere (cuentas gratis) a veces falla un instante: reintentamos.
            # Logueamos solo el tipo de error, asi el token (que va en la URL) no queda en los logs.
            log.warning("Telegram %s: error de conexion (%s), intento %d/%d",
                        metodo, type(e).__name__, intento, INTENTOS)
            if intento < INTENTOS:
                time.sleep(ESPERA * intento)
        except Exception as e:
            log.error("Error llamando a Telegram %s: %s", metodo, type(e).__name__)
            return None
    log.error("Telegram %s: sin conexion despues de %d intentos", metodo, INTENTOS)
    return None


def enviar(chat_id: int, texto: str, teclado: list | None = None,
           responder_a: int | None = None) -> dict | None:
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if teclado:
        payload["reply_markup"] = {"inline_keyboard": teclado}
    if responder_a:
        payload["reply_to_message_id"] = responder_a
        payload["allow_sending_without_reply"] = True
    return llamar("sendMessage", **payload)


def editar(chat_id: int, message_id: int, texto: str, teclado: list | None = None) -> dict | None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    payload["reply_markup"] = {"inline_keyboard": teclado or []}
    return llamar("editMessageText", **payload)


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
