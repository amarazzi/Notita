"""Cliente mínimo de la Bot API de Telegram."""
from __future__ import annotations

import logging

import requests

from . import config

log = logging.getLogger("notita.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20


def llamar(metodo: str, **payload) -> dict | None:
    if not config.TELEGRAM_TOKEN:
        log.error("Falta TELEGRAM_TOKEN")
        return None
    url = API.format(token=config.TELEGRAM_TOKEN, method=metodo)
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram %s falló: %s", metodo, data)
            return None
        return data.get("result")
    except Exception:
        log.exception("Error llamando a Telegram %s", metodo)
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
