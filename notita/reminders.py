"""La rutina de las 20:00: recordatorios del día y resumen de los domingos.

Todo vive en `correr_rutina_diaria()` para que se pueda disparar desde el
scheduler de PythonAnywhere, desde un cron de una VM o desde un comando del
grupo, sin cambiar nada.
"""
from __future__ import annotations

import logging
import random
from datetime import date

from . import config, db, telegram, views
from .dates import ahora, de_iso, formato_humano, hoy, iso

log = logging.getLogger("notita.reminders")

PREGUNTAS = [
    "¿{tarea}? ¿Lo hicieron?",
    "¿{tarea}? ¿Salió?",
    "¿{tarea}? Contame 👀",
]

INSISTENTES = [
    "Van {n} veces que la pateamos, ya es un clásico de la casa 😅 ¿la hacemos o la borramos?",
    "{n} posposiciones. Esta tarea tiene más aguante que nosotros 🐢 ¿la hacemos o chau?",
    "Con {n} veces pospuesta, ya le tengo cariño 🙈 ¿la hacemos o la dejamos ir?",
]


def correr_rutina_diaria(ref: date | None = None, forzar: bool = False,
                         chat_id: int | None = None) -> dict:
    """Manda el resumen (si es domingo) y un recordatorio por tarea vencida o que vence hoy.

    `forzar` ignora la marca de "ya recordé esto hoy" (útil para probar).
    Devuelve un pequeño resumen de lo que hizo, para logs y tests.
    """
    db.init_db()
    ref = ref or hoy()
    chat_id = chat_id or config.ALLOWED_CHAT_ID
    if not chat_id:
        log.error("No hay ALLOWED_CHAT_ID configurado")
        return {"error": "sin chat_id"}

    resultado = {"fecha": ref.isoformat(), "resumen": False, "recordatorios": 0, "recados": 0}

    # Los recados van primero: son lo más lindo de recibir.
    for row in db.recados_hasta(chat_id, ref):
        # Sólo se da por entregado si Telegram lo aceptó. Antes se marcaba igual, y
        # un error de red hacía desaparecer el recado para siempre.
        if telegram.enviar(chat_id, views.render_recado(row)) is None:
            log.error("No pude entregar el recado %s: queda para la próxima", row["id"])
            continue
        db.actualizar(row["id"], estado="hecha",
                      completed_at=ahora().isoformat(timespec="seconds"))
        resultado["recados"] += 1

    if ref.weekday() == 6:  # domingo
        telegram.enviar(chat_id, views.render_resumen_semanal(chat_id, ref))
        resultado["resumen"] = True

    for row in db.vencen_hasta(chat_id, ref):
        if not forzar and row["last_reminded_on"] == iso(ref):
            continue
        enviado = telegram.enviar(
            chat_id, _texto_recordatorio(row, ref),
            views.teclado_recordatorio(row["id"], row["postpone_count"] >= 3))
        if enviado is None:
            # Si no se pudo mandar, no se anota como recordado: se reintenta mañana.
            log.error("No pude recordar la tarea %s", row["id"])
            continue
        db.actualizar(row["id"], last_reminded_on=iso(ref))
        resultado["recordatorios"] += 1

    log.info("Rutina diaria: %s", resultado)
    return resultado


def _texto_recordatorio(row, ref: date) -> str:
    tarea = views.texto_tarea(row)
    linea = random.choice(PREGUNTAS).format(tarea=f"<b>{tarea}</b>")

    partes = [f"{views.emoji(row)} {linea}"]
    d = de_iso(row["due_date"])
    if d and d < ref:
        partes.append(f"<i>Venció {formato_humano(d, ref)}.</i>")
    if row["responsable"] == "ambos":
        quienes = [telegram.mencion(p.slug) for p in config.PERSONAS_CASA]
        if quienes:
            lista = " y ".join([", ".join(quienes[:-1]), quienes[-1]] if len(quienes) > 2 else quienes)
            partes.append(f"Es de {config.NOMBRES['ambos']}: {lista}.")
    elif row["responsable"] != "ninguno":
        partes.append(f"Quedó a cargo de {telegram.mencion(row['responsable'])}.")
    if row["postpone_count"] >= 3:
        partes.append(random.choice(INSISTENTES).format(n=row["postpone_count"]))
    return "\n".join(partes)
