#!/usr/bin/env python3
"""Rutina de las 20:00 (hora Argentina).

PythonAnywhere: tarea diaria a las 23:00 UTC ->
    python3 /home/USUARIO/Notita/run_reminders.py

Modo prueba (dispara todo a mano, sin esperar las 20:00):
    python3 run_reminders.py --forzar
    python3 run_reminders.py --forzar --fecha 2026-10-05   # simula un domingo
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from notita import deps

deps.exigir("requests")

from notita.parte import correr  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    p = argparse.ArgumentParser(description="El parte diario de Notita")
    p.add_argument("--forzar", action="store_true",
                   help="manda el parte aunque ya se haya mandado hoy")
    p.add_argument("--fecha", help="simula otro día (YYYY-MM-DD)")
    args = p.parse_args()

    momento = None
    if args.fecha:
        from datetime import datetime, time

        from notita import config

        momento = datetime.combine(date.fromisoformat(args.fecha), time(20, 0),
                                   tzinfo=config.TZ)
    print(correr(momento=momento, forzar=args.forzar))


if __name__ == "__main__":
    main()
