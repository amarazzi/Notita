"""El botón «Agregar al calendario».

Notita no avisa a horas exactas (un cron gratuito no da esa garantía), así que para
los turnos delega en el calendario del teléfono, que sí sabe hacerlo.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from . import config
from .dates import de_iso

DURACION_HORAS = 1
PLANTILLA = ("https://calendar.google.com/calendar/render?action=TEMPLATE"
             "&text={titulo}&dates={inicio}/{fin}&ctz={tz}")


def momento_de(row) -> datetime | None:
    d = de_iso(row["due_date"])
    if d is None:
        return None
    hora = row["due_hora"] or "09:00"
    try:
        h, m = (int(x) for x in hora.split(":"))
    except ValueError:
        h, m = 9, 0
    return datetime.combine(d, time(h, m))


def link(row) -> str:
    """El link de Google Calendar, con el título URL-encodeado."""
    inicio = momento_de(row) or datetime.combine(date.today(), time(9, 0))
    fin = inicio + timedelta(hours=DURACION_HORAS)
    titulo = " ".join((row["texto"] or "").split())
    return PLANTILLA.format(
        # `quote` con safe="" escapa también el & y el /, que si no cortan la URL.
        titulo=quote(titulo[:1].upper() + titulo[1:], safe=""),
        inicio=inicio.strftime("%Y%m%dT%H%M%S"),
        fin=fin.strftime("%Y%m%dT%H%M%S"),
        tz=quote(config.TZ_NOMBRE, safe=""),
    )


def ics(row) -> str:
    """Un .ics con alarma una hora antes, para quien no use Google."""
    inicio = momento_de(row)
    if inicio is None:
        raise ValueError("un evento sin fecha no se puede agendar")
    fin = inicio + timedelta(hours=DURACION_HORAS)
    titulo = " ".join((row["texto"] or "").split())
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Notita//v2//ES",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:notita-{row['id']}@notita",
        f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}",
        f"DTSTART;TZID={config.TZ_NOMBRE}:{inicio.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID={config.TZ_NOMBRE}:{fin.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{_escapar_ics(titulo[:1].upper() + titulo[1:])}",
        "BEGIN:VALARM",
        "TRIGGER:-PT1H",
        "ACTION:DISPLAY",
        "DESCRIPTION:Recordatorio",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])


def _escapar_ics(texto: str) -> str:
    """En iCalendar la coma, el punto y coma y la barra se escapan."""
    return (texto.replace("\\", "\\\\").replace(";", r"\;")
            .replace(",", r"\,").replace("\n", r"\n"))


def boton(row) -> dict | None:
    """El botón para la confirmación o el menú. None si no tiene hora."""
    if not row["due_hora"]:
        return None
    if config.CALENDARIO == "ics":
        # El .ics se manda como archivo desde el handler; acá va un callback.
        from . import cb

        return {"text": "📅 Agregar al calendario", "callback_data": cb.armar("ics", row["id"])}
    return {"text": "📅 Agregar al calendario", "url": link(row)}
