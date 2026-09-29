"""El lenguaje propone, el toque confirma.

Cuando alguien pide un cambio por texto («ya compré la leche», «pasá todo lo de
mañana para hoy»), Notita NO lo ejecuta: resuelve a qué ítems se refiere, guarda la
propuesta y muestra botones. Si el modelo entendió mal, no pasó nada.

Los ids los resuelve el servidor contra la base, no el LLM: el modelo devuelve las
palabras con las que nombraron cada cosa.
"""
from __future__ import annotations

import logging
from datetime import date

from . import cb, config, db, telegram, views
from .dates import de_iso, hoy

log = logging.getLogger("notita.propuestas")

MAX_BOTONES = 8          # más que esto es una lista, no una decisión
TTL_MINUTOS = 30

ACCIONES = {
    "completar": {"verbo": "Tacho", "icono": "✅", "hecho": "Tachada"},
    "borrar": {"verbo": "Borro", "icono": "🗑", "hecho": "Borrada"},
    "mover": {"verbo": "Paso", "icono": "📅", "hecho": "Movida"},
    "renombrar": {"verbo": "Renombro", "icono": "✏️", "hecho": "Renombrada"},
    "reasignar": {"verbo": "Cambio", "icono": "👤", "hecho": "Cambiada"},
    "pausar": {"verbo": "Pauso", "icono": "⏸", "hecho": "Pausada"},
}


def ofrecer(chat_id: int, accion: str, candidatos: list, extra: dict | None = None,
            ref: date | None = None, prefijo: str = "") -> bool:
    """Guarda la propuesta y la muestra con botones. No ejecuta nada.

    `prefijo` es el renglón «🎤 «…»» cuando el pedido vino por audio: importa ver qué
    se escuchó antes de confirmar algo.
    """
    ref = ref or hoy()
    if not candidatos:
        return False
    if len(candidatos) > MAX_BOTONES:
        telegram.enviar(
            chat_id,
            f"Encontré {len(candidatos)} que podrían ser 😅 Decímelo más puntual, o "
            f"manejalo desde el tablero.",
            [[{"text": "📋 Ver el tablero", "callback_data": cb.armar("tab")}]],
            silencioso=True)
        return True

    extra = extra or {}
    propuesta_id = db.guardar_propuesta(
        chat_id, {"accion": accion, "ids": [r["id"] for r in candidatos],
                  "extra": extra}, TTL_MINUTOS)

    datos = ACCIONES[accion]
    texto = _pregunta(accion, candidatos, extra, ref)
    filas = []
    if len(candidatos) > 1:
        for row in candidatos:
            filas.append([{"text": f"{datos['icono']} {views.recortar(views.titulo(row), 24)}",
                           "callback_data": cb.armar("p", propuesta_id, row["id"])}])
        prefijo = "Sí, " if accion == "mover" else ""
        filas.append([
            {"text": f"{datos['icono']} {prefijo}" + _todas(len(candidatos)).lower()
                     if prefijo else f"{datos['icono']} " + _todas(len(candidatos)),
             "callback_data": cb.armar("p", propuesta_id, "todas")},
            {"text": "No", "callback_data": cb.armar("p", propuesta_id, "no")},
        ])
    else:
        filas.append([
            {"text": f"{datos['icono']} {views.recortar(views.titulo(candidatos[0]), 24)}",
             "callback_data": cb.armar("p", propuesta_id, "todas")},
            {"text": "No", "callback_data": cb.armar("p", propuesta_id, "no")},
        ])

    if prefijo:
        texto = f"{prefijo}\n{texto}"
    enviado = telegram.enviar(chat_id, texto, filas, silencioso=True)
    if enviado:
        db.anotar_temporal(chat_id, enviado["message_id"], "propuesta", TTL_MINUTOS)
    return True


def _todas(n: int) -> str:
    return "Las dos" if n == 2 else f"Las {n}"


def _pregunta(accion: str, candidatos: list, extra: dict, ref: date) -> str:
    datos = ACCIONES[accion]
    if accion == "mover":
        destino = views.cuando(de_iso(extra.get("fecha")), extra.get("hora"), ref)
        cabeza = (f"¿{datos['verbo']} {'esta' if len(candidatos) == 1 else f'estas {len(candidatos)}'} "
                  f"a {destino}?")
    elif accion == "renombrar":
        cabeza = f"¿Le pongo «{telegram.escapar(extra.get('texto', ''))}»?"
    elif accion == "reasignar":
        quien = config.NOMBRES.get(extra.get("responsable"), extra.get("responsable"))
        cabeza = f"¿Se la paso a {telegram.escapar(str(quien))}?"
    else:
        cabeza = (f"¿{datos['verbo']} esta?" if len(candidatos) == 1
                  else f"¿{datos['verbo']} estas {len(candidatos)}?")
    lineas = [cabeza] + [f"· {views.titulo_html(r)}" for r in candidatos]
    return "\n".join(lineas)


# --------------------------------------------------------------------------
# Ejecutar (esto sí toca la base, y sólo se llega acá desde un botón)
# --------------------------------------------------------------------------

def ejecutar(chat_id: int, propuesta_id: int, opcion: str, quien: str,
             ref: date | None = None) -> tuple[str, str]:
    """Devuelve (texto para el mensaje, aviso corto para el callback)."""
    ref = ref or hoy()
    propuesta = db.leer_propuesta(propuesta_id)
    if propuesta is None:
        return "", "Esto ya venció, pedímelo de nuevo"

    if opcion == "no":
        db.borrar_propuesta(propuesta_id)
        return "Listo, no toqué nada 🤍", "Dale"

    ids = propuesta["ids"] if opcion == "todas" else [int(opcion)]
    accion, extra = propuesta["accion"], propuesta.get("extra") or {}

    hechas, ya_estaban = [], []
    for item_id in ids:
        row = db.obtener(item_id)
        if row is None or row["estado"] != "pendiente":
            if row is not None:
                ya_estaban.append(row)
            continue
        _aplicar(accion, row, extra, quien)
        hechas.append(db.obtener(item_id))

    if opcion == "todas" or not db.leer_propuesta(propuesta_id):
        db.borrar_propuesta(propuesta_id)
    db.vencer_deshacer_de(ids)

    datos = ACCIONES[accion]
    lineas = []
    if hechas:
        lineas.append(f"{datos['icono']} <b>{datos['hecho'] if len(hechas) == 1 else datos['hecho'] + 's'}</b>")
        lineas += [f"· {views.titulo_html(r)}" for r in hechas]
    for row in ya_estaban:
        quien_lo_hizo = config.NOMBRES.get(row["completed_by"], "")
        detalle = f" ya la había tachado {quien_lo_hizo}" if quien_lo_hizo else " ya estaba resuelta"
        lineas.append(f"👀 {views.titulo_html(row)}{detalle}")
    if not lineas:
        lineas.append("No quedaba nada para hacer ✨")
    aviso = "Listo" if hechas else "Ya estaba"
    return "\n".join(lineas), aviso


def _aplicar(accion: str, row, extra: dict, quien: str) -> None:
    if accion == "completar":
        db.marcar_hecha(row["id"], quien)
    elif accion == "borrar":
        db.borrar(row["id"])
    elif accion == "mover":
        db.actualizar(row["id"], due_date=extra.get("fecha"), due_hora=extra.get("hora"))
    elif accion == "renombrar":
        db.actualizar(row["id"], texto=extra.get("texto") or row["texto"])
    elif accion == "reasignar":
        db.actualizar(row["id"], responsable=extra.get("responsable") or "ninguno")
