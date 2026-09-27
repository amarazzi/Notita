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
from .dates import ahora, de_iso, formato_humano, iso

log = logging.getLogger("notita.reminders")

PREGUNTAS = [
    "¿{tarea}? ¿Lo hicieron?",
    "¿{tarea}? ¿Salió?",
    "¿{tarea}? Contame 👀",
]

# Después de esta cantidad de noches preguntando por lo mismo, se dejan de mandar
# mensajes sueltos y las vencidas van todas juntas.
UMBRAL_CANSANCIO = 3

INSISTENTES = [
    "Van {n} veces que la pateamos, ya es un clásico de la casa 😅 ¿la hacemos o la borramos?",
    "{n} posposiciones. Esta tarea tiene más aguante que nosotros 🐢 ¿la hacemos o chau?",
    "Con {n} veces pospuesta, ya le tengo cariño 🙈 ¿la hacemos o la dejamos ir?",
]


def correr_rutina_diaria(ref: date | None = None, forzar: bool = False,
                         chat_id: int | None = None, momento=None) -> dict:
    """Manda lo que toca mandar en este momento.

    Se puede llamar una vez por día (como en PythonAnywhere gratis) o cada rato
    (con un cron externo). En el segundo caso las tareas y los recados con hora
    propia salen a su hora; lo que no tiene hora sale en la pasada principal, la de
    `config.HORA_RUTINA`.

    - `momento`: el instante exacto, para tests o para una pasada puntual.
    - `ref`: el día. Si se pasa sin `momento`, se asume la pasada principal de ese día.
    - `forzar`: ignora la hora y la marca de "ya lo recordé hoy" (para probar).
    """
    db.init_db()
    chat_id = chat_id or config.ALLOWED_CHAT_ID
    if not chat_id:
        log.error("No hay ALLOWED_CHAT_ID configurado")
        return {"error": "sin chat_id"}

    momento = momento or _momento_de(ref)
    ref = momento.date()
    # Si el cron corre una sola vez por día, ESTA corrida es la del día: sale todo lo
    # que vence hoy, sin fijarse en el reloj. Antes se comparaba la hora con las 20:00
    # y una corrida a las 19:59 (o un cron con otro horario) no mandaba nada.
    # Con un cron frecuente sí hay que distinguir: lo que no tiene hora espera la
    # pasada principal para no avisar a las 7 de la mañana.
    unica_del_dia = config.CRON_MINUTOS == 0
    pasada_principal = forzar or unica_del_dia or momento.strftime("%H:%M") >= config.HORA_RUTINA
    hora_generica = "00:00" if pasada_principal else config.HORA_RUTINA

    resultado = {"fecha": ref.isoformat(), "hora": momento.strftime("%H:%M"),
                 "resumen": False, "recordatorios": 0, "recados": 0, "agrupadas": 0,
                 "de_la_cola": telegram.vaciar_cola(chat_id)}

    # Los recados van primero: son lo más lindo de recibir.
    for row in db.recados_a_entregar(chat_id, momento, hora_generica):
        # Sólo se da por entregado si Telegram lo aceptó. Antes se marcaba igual, y
        # un error de red hacía desaparecer el recado para siempre.
        if telegram.enviar(chat_id, views.render_recado(row)) is None:
            log.error("No pude entregar el recado %s: queda para la próxima", row["id"])
            continue
        db.actualizar(row["id"], estado="hecha",
                      completed_at=ahora().isoformat(timespec="seconds"))
        resultado["recados"] += 1

    if ref.weekday() == 6 and pasada_principal:  # domingo
        telegram.enviar_largo(chat_id, views.render_resumen_semanal(chat_id, ref))
        resultado["resumen"] = True

    toca = [r for r in db.toca_recordar(chat_id, momento, hora_generica)
            if forzar or r["last_reminded_on"] != iso(ref)]

    # Las que ya preguntamos muchas noches van juntas en un solo mensaje. Repetir
    # cinco mensajes por noche para siempre es la forma más rápida de que dejen de
    # leer al bot.
    cansadas = [r for r in toca if (r["recordada_veces"] or 0) >= UMBRAL_CANSANCIO]
    if len(cansadas) >= 2:
        if telegram.enviar(chat_id, views.render_vencidas_juntas(cansadas, ref),
                           views.teclado_vencidas_juntas()) is not None:
            for row in cansadas:
                _anotar_recordatorio(row, ref)
            resultado["agrupadas"] = len(cansadas)
            toca = [r for r in toca if r not in cansadas]

    for row in toca:
        enviado = telegram.enviar(
            chat_id, _texto_recordatorio(row, ref),
            views.teclado_recordatorio(row["id"], row["postpone_count"] >= 3))
        if enviado is None:
            # Si no se pudo mandar, no se anota como recordado: se reintenta mañana.
            log.error("No pude recordar la tarea %s", row["id"])
            continue
        _anotar_recordatorio(row, ref)
        resultado["recordatorios"] += 1

    log.info("Rutina diaria: %s", resultado)
    return resultado


def _momento_de(ref: date | None):
    """El instante de la corrida.

    Sin `ref` es ahora (lo que pasa en producción). Con `ref` se asume la pasada
    principal de ese día, así una llamada como `correr_rutina_diaria(ref=hoy())`
    manda todo lo del día sin depender de la hora en que se la llame.
    """
    from datetime import datetime, time

    if ref is None:
        return ahora()
    try:
        h, m = (int(x) for x in config.HORA_RUTINA.split(":"))
    except ValueError:
        h, m = 20, 0
    return datetime.combine(ref, time(h, m), tzinfo=config.TZ)


def _anotar_recordatorio(row, ref: date) -> None:
    db.actualizar(row["id"], last_reminded_on=iso(ref),
                  recordada_veces=(row["recordada_veces"] or 0) + 1)


def _texto_recordatorio(row, ref: date) -> str:
    tarea = views.texto_tarea(row)
    linea = random.choice(PREGUNTAS).format(tarea=f"<b>{tarea}</b>")

    partes = [f"{views.emoji(row)} {linea}"]
    d = de_iso(row["due_date"])
    hora = views.hora_de(row)
    if d and d < ref:
        # `formato_humano` ya dice "venció el 27/9" para las viejas: si le pegábamos
        # "Venció" adelante salía "Venció venció el 27/9".
        cuando = formato_humano(d, ref)
        partes.append(f"<i>{cuando.capitalize()}.</i>" if cuando.startswith("venció")
                      else f"<i>Venció {cuando}.</i>")
    elif hora:
        partes.append(f"<i>Era a las {hora}.</i>")
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
