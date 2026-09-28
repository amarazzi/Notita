"""El parte diario: el único envío programado, y el único que suena.

Dos reglas que definen todo lo demás:

- **Nada se mueve solo.** Una tarea vencida queda vencida hasta que alguien toque un
  botón. En v1 se auto-posponían y aparecían movidas sin que nadie lo pidiera.
- **Sale una sola vez por día.** `partes_enviados` tiene la fecha como clave, así que
  el cron puede llamar cuantas veces quiera. Y si no salió a tiempo, no se manda
  tarde: se avisa al día siguiente.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import cb, config, db, tablero, telegram, views
from .dates import ahora, de_iso, hoy

log = logging.getLogger("notita.parte")

MAX_BOTONES_VENCIDAS = 5


def correr(chat_id: int | None = None, momento=None, forzar: bool = False) -> dict:
    """Lo que hace el cron. Devuelve un resumen para logs y tests."""
    db.init_db()
    chat_id = chat_id or config.ALLOWED_CHAT_ID
    if not chat_id:
        log.error("No hay ALLOWED_CHAT_ID configurado")
        return {"error": "sin chat_id"}

    momento = momento or ahora()
    hecho = {"fecha": momento.date().isoformat(), "hora": momento.strftime("%H:%M"),
             "parte": False, "atrasado": False, "de_la_cola": 0, "temporales": 0}

    hecho["de_la_cola"] = telegram.vaciar_cola(chat_id)

    from . import menus

    hecho["temporales"] = menus.limpiar_vencidos()
    db.limpiar_propuestas()
    db.vencer_deshacer_de([])          # sólo limpia los vencidos

    pausada = db.pausada_hasta(chat_id)
    if pausada and not forzar:
        hecho["pausada_hasta"] = pausada.isoformat()
        tablero.flushear_si_esta_sucio(chat_id)
        return hecho

    hoy_ = momento.date()
    # ¿Se nos pasó el de ayer? Se avisa, no se manda tarde.
    if _falto_el_de_ayer(hoy_):
        telegram.enviar(chat_id, "Perdón, anoche se me pasó el parte 🙈")
        db.anotar_parte(hoy_ - timedelta(days=1))
        hecho["atrasado"] = True

    if forzar or (momento.strftime("%H:%M") >= config.HORA_RUTINA
                  and not db.parte_ya_enviado(hoy_)):
        if forzar or db.anotar_parte(hoy_):     # el INSERT es la guarda
            texto, filas = render(chat_id, hoy_)
            if texto:
                telegram.enviar(chat_id, texto, filas)   # el único con notificación
            hecho["parte"] = True

    tablero.flushear_si_esta_sucio(chat_id)
    return hecho


def _falto_el_de_ayer(hoy_: date) -> bool:
    ayer = hoy_ - timedelta(days=1)
    if db.parte_ya_enviado(ayer):
        return False
    ultimo = db.ultimo_parte()
    # Si nunca se mandó ninguno (instalación nueva), no hay nada que disculpar.
    return ultimo is not None and ultimo < ayer


def render(chat_id: int, ref: date | None = None) -> tuple[str, list]:
    """El texto y los botones del parte. Sin efectos: se puede testear sola."""
    ref = ref or hoy()
    manana = ref + timedelta(days=1)
    casa = db.pendientes(chat_id, tipo="casa")
    compras = db.pendientes(chat_id, tipo="compras")

    de_manana = [r for r in casa if de_iso(r["due_date"]) == manana]
    de_hoy = [r for r in casa if de_iso(r["due_date"]) == ref]
    viejas = [r for r in casa if de_iso(r["due_date"]) and de_iso(r["due_date"]) < ref]

    lineas, filas = [], []
    if de_manana:
        lineas.append(f"🌙 <b>Para mañana, {views.dia_corto(manana)}</b>")
        con_hora = sorted([r for r in de_manana if r["due_hora"]],
                          key=lambda r: r["due_hora"])
        sin_hora = [r for r in de_manana if not r["due_hora"]]
        for row in con_hora + sin_hora:
            lineas.append(views.linea(row, ref, con_fecha=False))
    else:
        lineas.append(f"🌙 <b>Mañana libre</b> ✨ ({views.dia_corto(manana)})")

    pendientes_hoy = de_hoy + viejas
    if pendientes_hoy:
        lineas.append("")
        if len(de_hoy) <= MAX_BOTONES_VENCIDAS and not viejas:
            for row in de_hoy:
                lineas.append(f"⚠️ Quedó de hoy: {views.titulo_html(row)}")
                filas.append([
                    {"text": f"✅ {tablero.etiqueta(row, ref, largo=20)}",
                     "callback_data": cb.armar("ok", row["id"])},
                    {"text": "⏰ A mañana", "callback_data": cb.armar("d", row["id"], "m")},
                ])
        else:
            lineas.append(f"⚠️ Quedaron <b>{len(pendientes_hoy)}</b> sin hacer")
            filas.append([
                {"text": "⏰ Pasar todas a mañana", "callback_data": cb.armar("pt")},
                {"text": "Ver", "callback_data": cb.armar("sec", "vencidas")},
            ])

    if compras:
        lineas.append(f"\n🛒 En el súper hay <b>{len(compras)}</b> cosas")

    if not de_manana and not pendientes_hoy and not compras:
        # Nada para contar: un mensaje corto, o nada si así está configurado.
        if not config.PARTE_VACIO:
            return "", []
        return f"🌙 Mañana libre ✨ ({views.dia_corto(manana)})", []

    return "\n".join(lineas), filas


def pasar_todas_a_manana(chat_id: int, ref: date | None = None) -> int:
    """El botón del parte. Lo pide una persona: no es automático."""
    ref = ref or hoy()
    manana = (ref + timedelta(days=1)).isoformat()
    movidas = 0
    for row in db.pendientes(chat_id, tipo="casa"):
        d = de_iso(row["due_date"])
        if d and d <= ref:
            db.actualizar(row["id"], due_date=manana)
            movidas += 1
    return movidas
