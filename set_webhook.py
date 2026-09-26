#!/usr/bin/env python3
"""Registra (o borra, o consulta) el webhook de Telegram.

    python3 set_webhook.py https://USUARIO.pythonanywhere.com/telegram
    python3 set_webhook.py --info
    python3 set_webhook.py --borrar
"""
from __future__ import annotations

import argparse
import json

from notita import deps

deps.exigir("requests")

from notita import config, telegram  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url", nargs="?", help="URL pública del endpoint /telegram")
    p.add_argument("--info", action="store_true")
    p.add_argument("--borrar", action="store_true")
    args = p.parse_args()

    if args.info:
        print(json.dumps(telegram.llamar("getWebhookInfo"), indent=2, ensure_ascii=False))
        return
    if args.borrar:
        print(telegram.llamar("deleteWebhook", drop_pending_updates=True))
        return
    if not args.url:
        p.error("falta la URL (o usá --info / --borrar)")

    res = telegram.llamar(
        "setWebhook",
        url=args.url,
        secret_token=config.TELEGRAM_WEBHOOK_SECRET or None,
        allowed_updates=["message", "edited_message", "callback_query"],
        drop_pending_updates=True,
    )
    print("Webhook:", res)
    print(json.dumps(telegram.llamar("getWebhookInfo"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
