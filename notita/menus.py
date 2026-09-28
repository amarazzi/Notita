"""Los menús: mensajes temporales que se borran solos.

El tablero nunca navega (lo comparten dos personas), así que cada menú es un mensaje
nuevo con su propio TTL.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import calendario, cb, config, db, tablero, telegram, views
from .dates import DateSpec, hoy, proximo_dia_semana, resolve

log = logging.getLogger("notita.menus")

TTL_MENU = 5          # minutos
TTL_SUPER = 5         # se renueva con cada toque: se usa mientras se compra


def _publicar_temporal(chat_id: int, texto: str, filas: list, tipo: str,
                       minutos: int = TTL_MENU) -> int | None:
    enviado = telegram.enviar(chat_id, texto, filas, silencioso=True)
    if enviado:
        db.anotar_temporal(chat_id, enviado["message_id"], tipo, minutos)
        return enviado["message_id"]
    return None


# --------------------------------------------------------------------------
# El menú de una tarea
# --------------------------------------------------------------------------

def abrir(chat_id: int, item_id: int, message_id: int | None = None,
          ref: date | None = None) -> None:
    ref = ref or hoy()
    row = db.obtener(item_id)
    if row is None or row["estado"] != "pendiente":
        return
    texto = views.detalle(row, ref)
    filas = _filas_super(item_id) if row["tipo"] == "compras" else _filas_tarea(row)
    if message_id:
        telegram.editar(chat_id, message_id, texto, filas)
        db.anotar_temporal(chat_id, message_id, "menu", TTL_MENU)
    else:
        _publicar_temporal(chat_id, texto, filas, "menu")


def _filas_tarea(row) -> list:
    item_id = row["id"]
    filas = [
        [{"text": "✅ Hecho", "callback_data": cb.armar("ok", item_id)},
         {"text": "⏰ Mañana", "callback_data": cb.armar("d", item_id, "m")},
         {"text": "📅 Otro día", "callback_data": cb.armar("d+", item_id)}],
        [{"text": "👤 Quién", "callback_data": cb.armar("q+", item_id)},
         {"text": "✏️ Renombrar", "callback_data": cb.armar("r", item_id)},
         {"text": "🛒 Mover al súper", "callback_data": cb.armar("sw", item_id)}],
    ]
    ultima = [{"text": "🗑 Borrar", "callback_data": cb.armar("x", item_id)},
              {"text": "✖️ Cerrar", "callback_data": cb.armar("c")}]
    boton_cal = calendario.boton(row)
    if boton_cal:
        ultima.insert(0, boton_cal)
    filas.append(ultima)
    return filas


def _filas_super(item_id: int) -> list:
    return [
        [{"text": "✅ Tachar", "callback_data": cb.armar("ok", item_id)},
         {"text": "📌 Mover a tareas", "callback_data": cb.armar("sw", item_id)}],
        [{"text": "✏️ Renombrar", "callback_data": cb.armar("r", item_id)},
         {"text": "🗑 Borrar", "callback_data": cb.armar("x", item_id)},
         {"text": "✖️ Cerrar", "callback_data": cb.armar("c")}],
    ]


def submenu_dia(chat_id: int, message_id: int, item_id: int, ref: date | None = None) -> None:
    """Las opciones rápidas de fecha, en el mismo mensaje temporal."""
    ref = ref or hoy()
    row = db.obtener(item_id)
    if row is None:
        return
    sabado = proximo_dia_semana(ref, 5)
    lunes = resolve(DateSpec("dia_semana_prox", weekday=0), ref)
    filas = [
        [{"text": f"Hoy, {views.dia_corto(ref)}", "callback_data": cb.armar("d", item_id, "h")},
         {"text": f"Mañana, {views.dia_corto(ref + timedelta(days=1))}",
          "callback_data": cb.armar("d", item_id, "m")}],
        [{"text": f"Sáb {sabado.day}", "callback_data": cb.armar("d", item_id, "s")},
         {"text": f"Lun {lunes.day}", "callback_data": cb.armar("d", item_id, "l")},
         {"text": "Algún día", "callback_data": cb.armar("d", item_id, "a")}],
        [{"text": "⌨️ Escribir fecha", "callback_data": cb.armar("df", item_id)},
         {"text": "✖️ Cerrar", "callback_data": cb.armar("c")}],
    ]
    telegram.editar(chat_id, message_id,
                    f"{views.detalle(row, ref)}\n\n¿Para cuándo?", filas)
    db.anotar_temporal(chat_id, message_id, "menu", TTL_MENU)


def submenu_quien(chat_id: int, message_id: int, item_id: int, ref: date | None = None) -> None:
    row = db.obtener(item_id)
    if row is None:
        return
    botones = [{"text": p.nombre, "callback_data": cb.armar("q", item_id, p.slug)}
               for p in config.PERSONAS_CASA]
    botones.append({"text": config.NOMBRES["ambos"].capitalize(),
                    "callback_data": cb.armar("q", item_id, "ambos")})
    botones.append({"text": "Nadie", "callback_data": cb.armar("q", item_id, "ninguno")})
    filas = [botones[i:i + 2] for i in range(0, len(botones), 2)]
    filas.append([{"text": "✖️ Cerrar", "callback_data": cb.armar("c")}])
    telegram.editar(chat_id, message_id,
                    f"{views.detalle(row, ref or hoy())}\n\n¿Quién la hace?", filas)
    db.anotar_temporal(chat_id, message_id, "menu", TTL_MENU)


def confirmar_borrar_recurrente(chat_id: int, message_id: int, item_id: int) -> None:
    row = db.obtener(item_id)
    if row is None:
        return
    telegram.editar(
        chat_id, message_id,
        f"{views.detalle(row)}\n\nEsta se repite. ¿Borro sólo esta vez o todas?",
        [[{"text": "Sólo esta vez", "callback_data": cb.armar("x", item_id, "una")},
          {"text": "Todas", "callback_data": cb.armar("x", item_id, "todas")}],
         [{"text": "✖️ Cerrar", "callback_data": cb.armar("c")}]])
    db.anotar_temporal(chat_id, message_id, "menu", TTL_MENU)


# --------------------------------------------------------------------------
# Secciones colapsadas y súper
# --------------------------------------------------------------------------

def abrir_seccion(chat_id: int, clave: str, ref: date | None = None) -> None:
    """Un mensaje temporal con esa sección expandida. El tablero no se toca."""
    ref = ref or hoy()
    secciones = tablero.repartir(db.pendientes(chat_id, tipo="casa"), ref)
    rows = secciones.get(clave) or []
    if not rows:
        _publicar_temporal(chat_id, "Ahí no quedó nada ✨",
                           [[{"text": "✖️ Cerrar", "callback_data": cb.armar("c")}]], "seccion")
        return
    titulo = {"vencidas": "⚠️ Vencidas", "semana": "Esta semana",
              "adelante": "Más adelante", "algun_dia": "Algún día",
              "hoy": "Hoy", "manana": "Mañana"}.get(clave, clave)
    lineas = [f"<b>{titulo}</b> · {len(rows)}"]
    filas = []
    for row in rows[:40]:
        lineas.append(views.linea(row, ref))
        filas.append([
            {"text": f"✅ {tablero.etiqueta(row, ref)}", "callback_data": cb.armar("ok", row["id"])},
            {"text": "⋯", "callback_data": cb.armar("m", row["id"])},
        ])
    filas.append([{"text": "✖️ Cerrar", "callback_data": cb.armar("c")}])
    _publicar_temporal(chat_id, "\n".join(lineas), filas, "seccion")


def abrir_super(chat_id: int, message_id: int | None = None) -> None:
    rows = db.pendientes(chat_id, tipo="compras")
    if not rows:
        texto = "La lista del súper está vacía 🛒"
        filas = [[{"text": "✖️ Cerrar", "callback_data": cb.armar("c")}]]
    else:
        texto = (f"🛒 <b>Súper</b> · {len(rows)}\n"
                 + "\n".join(f"• {views.titulo_html(r)}" for r in rows)
                 + "\n\n<i>Tocá lo que ya compraste.</i>")
        filas = [[{"text": f"🛒 {views.recortar(views.titulo(r), 28)}",
                   "callback_data": cb.armar("ok", r["id"])}] for r in rows[:40]]
        filas.append([{"text": "✅ Compramos todo", "callback_data": cb.armar("supx")},
                      {"text": "✖️ Cerrar", "callback_data": cb.armar("c")}])
    if message_id:
        telegram.editar(chat_id, message_id, texto, filas)
        db.renovar_temporal(chat_id, message_id, TTL_SUPER)
    else:
        _publicar_temporal(chat_id, texto, filas, "super", TTL_SUPER)


def confirmar_super_todo(chat_id: int, message_id: int) -> None:
    rows = db.pendientes(chat_id, tipo="compras")
    if not rows:
        abrir_super(chat_id, message_id)
        return
    telegram.editar(
        chat_id, message_id,
        f"¿Tacho {'la' if len(rows) == 1 else f'las {len(rows)}'} del súper?\n"
        + "\n".join(f"· {views.titulo_html(r)}" for r in rows),
        [[{"text": "✅ Sí", "callback_data": cb.armar("supx", "si")},
          {"text": "No", "callback_data": cb.armar("sup")}]])
    db.renovar_temporal(chat_id, message_id, TTL_SUPER)


# --------------------------------------------------------------------------
# Limpieza
# --------------------------------------------------------------------------

def limpiar_vencidos() -> int:
    """Borra del chat los menús que quedaron abiertos. Lo llama el cron y cada update."""
    borrados = 0
    for fila in db.temporales_vencidos():
        telegram.borrar(fila["chat_id"], fila["message_id"])
        db.olvidar_temporal(fila["chat_id"], fila["message_id"])
        borrados += 1
    return borrados


def cerrar(chat_id: int, message_id: int) -> None:
    telegram.borrar(chat_id, message_id)
    db.olvidar_temporal(chat_id, message_id)
