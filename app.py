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

    La clave va en el header X-Cron-Secret (o en ?clave=... si el servicio no deja
    poner headers). Llamarla dos veces el mismo día no repite recordatorios.
    """
    if not config.CRON_SECRET:
        return jsonify(ok=False, error="cron apagado"), 404
    recibido = request.headers.get("X-Cron-Secret") or request.args.get("clave", "")
    if not hmac.compare_digest(recibido, config.CRON_SECRET):
        log.warning("Cron con clave inválida")
        return jsonify(ok=False), 403
    return jsonify(ok=True, resultado=correr_rutina_diaria())


if __name__ == "__main__":  # desarrollo local
    app.run(port=5000, debug=True)
