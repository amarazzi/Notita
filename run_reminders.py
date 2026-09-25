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

from notita.reminders import correr_rutina_diaria

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    p = argparse.ArgumentParser(description="Recordatorios de Notita")
    p.add_argument("--forzar", action="store_true",
                   help="reenvía aunque ya se haya recordado hoy")
    p.add_argument("--fecha", help="simula otro día (YYYY-MM-DD)")
    args = p.parse_args()

    ref = date.fromisoformat(args.fecha) if args.fecha else None
    print(correr_rutina_diaria(ref=ref, forzar=args.forzar))


if __name__ == "__main__":
    main()
