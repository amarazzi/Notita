"""Webhook de Telegram. Es lo que sirve la web app de PythonAnywhere."""
from __future__ import annotations

import hmac
import logging

from flask import Flask, jsonify, request

from notita import config, db
from notita.handlers import handle_update
from notita.reminders import correr_rutina_diaria

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("notita.app")

app = Flask(__name__)
db.init_db()


@app.get("/")
def salud():
    return jsonify(ok=True, bot="notita")


@app.post("/telegram")
def webhook():
    # Telegram repite el secreto en este header; si no coincide, no es Telegram.
    if config.TELEGRAM_WEBHOOK_SECRET:
        recibido = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if recibido != config.TELEGRAM_WEBHOOK_SECRET:
            log.warning("Webhook con secreto inválido")
            return jsonify(ok=False), 403

    update = request.get_json(silent=True) or {}
    try:
        handle_update(update)
    except Exception:
        # Siempre 200: si devolvemos error, Telegram reintenta el mismo update en loop.
        log.exception("Error procesando update: %s", update)
    return jsonify(ok=True)


@app.route("/cron/recordatorios", methods=["GET", "POST"])
def cron_recordatorios():
    """Dispara la rutina de las 20:00. La llama un cron externo (cron-job.org).

    La clave va en el header y no en la URL a propósito: las query strings
    quedan escritas en el access log del servidor.
    """
    if not config.CRON_SECRET:
        return jsonify(ok=False, error="cron apagado"), 404
    # En bytes: compare_digest no admite str con caracteres no ASCII (tira TypeError).
    recibido = request.headers.get("X-Cron-Secret", "").encode("utf-8", "replace")
    if not hmac.compare_digest(recibido, config.CRON_SECRET.encode()):
        log.warning("Cron con clave invalida")
        return jsonify(ok=False), 403
    return jsonify(ok=True, resultado=correr_rutina_diaria())


if __name__ == "__main__":  # desarrollo local
    app.run(port=5000, debug=True)
