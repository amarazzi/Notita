"""Notas de voz: se transcriben y entran por la misma puerta que un mensaje escrito.

Decisiones que vale tener a mano:

- **El tope se chequea con `voice.duration`, ANTES de descargar.** Un audio largo no
  se baja ni se le manda a Google.
- **No es por cuota, es por la espera.** Transcribir 14 segundos tardó 20 en las
  pruebas; 30 segundos serían ~40 de espera, y eso ya se lee como «está roto».
- **Se avisa que está escuchando**, porque 10 o 20 segundos de silencio parecen un
  bot colgado.
- **El mensaje de «escuchando» se borra y la respuesta se manda nueva**, en vez de
  editarlo: así la respuesta pasa por `telegram.enviar`, que tiene la cola de salida.
  Editando, un fallo se perdería, que es justo el bug que ya nos pasó.
"""
from __future__ import annotations

import logging

from . import config, llm, telegram

log = logging.getLogger("notita.audio")

ESCUCHANDO = "🎤 <i>Escuchando el audio…</i>"
NO_ENTENDI = "No pude escucharlo bien 🙈 ¿me lo escribís?"
APAGADO = "Todavía no entiendo audios ni fotos 🙈 Escribímelo y lo anoto."


def muy_largo() -> str:
    return (f"Muy largo para mí 🙈 Mandámelo en menos de "
            f"{config.AUDIO_SEGUNDOS} segundos o escrito.")


def hay_voz(msg: dict) -> bool:
    return bool(msg.get("voice"))


def disponible() -> bool:
    """Hace falta la key de Gemini: sin LLM no hay transcripción posible."""
    return config.AUDIOS and llm.disponible()


def transcribir(chat_id: int, msg: dict) -> str | None:
    """El texto de la nota de voz, o None si no se pudo (ya avisó en el grupo)."""
    voz = msg.get("voice") or {}
    if not disponible():
        telegram.enviar(chat_id, APAGADO, silencioso=True)
        return None

    duracion = int(voz.get("duration") or 0)
    if duracion > config.AUDIO_SEGUNDOS:
        # Sin descargar nada: el tope se chequea con lo que ya vino en el update.
        log.info("Audio de %ds, más que el tope de %ds", duracion, config.AUDIO_SEGUNDOS)
        telegram.enviar(chat_id, muy_largo(), silencioso=True)
        return None

    file_id = voz.get("file_id")
    if not file_id:
        telegram.enviar(chat_id, NO_ENTENDI, silencioso=True)
        return None

    avisando = telegram.enviar(chat_id, ESCUCHANDO, silencioso=True, encolar=False)
    try:
        crudo = telegram.descargar(file_id)
        if crudo is None:
            telegram.enviar(chat_id, NO_ENTENDI, silencioso=True)
            return None
        texto = llm.transcribir(crudo, voz.get("mime_type") or llm.MIME_VOZ)
    finally:
        if avisando:
            telegram.borrar(chat_id, avisando["message_id"])

    if not texto or texto == llm.INDESCIFRABLE:
        telegram.enviar(chat_id, NO_ENTENDI, silencioso=True)
        return None
    log.info("Audio de %ds transcripto: %d caracteres", duracion, len(texto))
    return texto


def prefijo(transcripcion: str) -> str:
    """El renglón que muestra lo que escuchó. Sin esto, un audio mal entendido es
    indistinguible de un bug."""
    return f"🎤 <i>«{telegram.escapar(transcripcion)}»</i>"
