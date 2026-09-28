"""El tablero: un solo mensaje fijado que se edita en el lugar.

Reglas que valen la pena tener a mano:

- Cada tarea aparece UNA sola vez. Las secciones tienen precedencia y cada tarea cae
  en la primera que le corresponde (en v1 lo del lunes salía en «mañana» y otra vez en
  «la semana que viene»).
- El tablero NUNCA navega: los menús son mensajes nuevos. Son dos personas mirando la
  misma pantalla; si uno abriera un submenú acá, al otro le cambiaría lo que ve.
- Se edita como mucho una vez por update. Si la última edición fue hace menos de
  `DEBOUNCE`, se marca sucio y lo flushea el próximo evento (update o cron).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import cb, config, db, telegram, views
from .dates import ahora, de_iso, domingo_de_la_semana, hoy

log = logging.getLogger("notita.tablero")

DEBOUNCE = 3            # segundos entre ediciones
LARGO_BOTON = 28        # caracteres del título en el botón
MAX_BOTONES = 80        # antes de colapsar «Mañana» también
MAX_EN_SECCION = 3      # más que esto y la sección de vencidas va colapsada


def render(chat_id: int, ref: date | None = None) -> tuple[str, list]:
    """El texto y el teclado del tablero. Sin efectos: se puede testear sola."""
    ref = ref or hoy()
    casa = db.pendientes(chat_id, tipo="casa")
    compras = db.pendientes(chat_id, tipo="compras")
    secciones = repartir(casa, ref)

    lineas = [f"📋 <b>La casa</b> · {views.dia_largo(ref)}"]
    filas: list[list[dict]] = []

    expandidas = {"hoy", "manana"}
    if sum(len(s) for s in secciones.values()) and _cuantos_botones(secciones, expandidas) > MAX_BOTONES:
        expandidas = {"hoy"}     # con la casa desbordada, mañana también se colapsa
    if len(secciones["vencidas"]) <= MAX_EN_SECCION:
        expandidas.add("vencidas")

    for clave, titulo in _TITULOS(ref):
        rows = secciones[clave]
        if not rows:
            continue
        if clave in expandidas:
            lineas.append(f"\n{titulo}")
            for r in rows:
                filas.extend(_fila_tarea(r, ref))
        else:
            lineas.append(f"\n{titulo} · {len(rows)} ›")
            filas.append([{"text": f"{_icono(clave)} {_nombre_corto(clave)} · {len(rows)}",
                           "callback_data": cb.armar("sec", clave)}])

    if compras:
        lineas.append(f"\n🛒 <b>SÚPER</b> · {len(compras)} ›")
        filas.append([{"text": f"🛒 Súper · {len(compras)}", "callback_data": cb.armar("sup")}])

    if len(lineas) == 1:
        lineas.append("\nNo hay nada pendiente. Qué lujo ✨")

    texto = "\n".join(lineas)
    if len(texto) > telegram.LARGO_MAXIMO:
        texto = texto[:telegram.LARGO_MAXIMO] + "\n<i>…</i>"
    return texto, filas


def repartir(casa, ref: date) -> dict[str, list]:
    """Cada tarea en UNA sección. El orden de los `if` es la precedencia."""
    domingo = domingo_de_la_semana(ref)
    manana = ref + timedelta(days=1)
    secciones: dict[str, list] = {"vencidas": [], "hoy": [], "manana": [],
                                  "semana": [], "adelante": [], "algun_dia": []}
    for r in casa:
        d = de_iso(r["due_date"])
        if d is None:
            secciones["algun_dia"].append(r)
        elif d < ref:
            secciones["vencidas"].append(r)
        elif d == ref:
            secciones["hoy"].append(r)
        elif d == manana:
            secciones["manana"].append(r)
        elif d <= domingo:
            # Hasta el domingo inclusive. Un domingo, `manana` ya se llevó el lunes y
            # esta sección queda vacía, que es lo correcto.
            secciones["semana"].append(r)
        else:
            secciones["adelante"].append(r)
    for clave, rows in secciones.items():
        rows.sort(key=lambda r: (r["due_date"] or "9999", r["due_hora"] or "99:99", r["id"]))
        del clave
    return secciones


def _TITULOS(ref: date) -> list[tuple[str, str]]:
    manana = ref + timedelta(days=1)
    return [
        ("vencidas", "⚠️ <b>VENCIDAS</b>"),
        ("hoy", f"<b>HOY</b> · {views.dia_corto(ref)}"),
        ("manana", f"<b>MAÑANA</b> · {views.dia_corto(manana)}"),
        ("semana", "<b>ESTA SEMANA</b>"),
        ("adelante", "<b>MÁS ADELANTE</b>"),
        ("algun_dia", "<b>ALGÚN DÍA</b>"),
    ]


def _nombre_corto(clave: str) -> str:
    return {"vencidas": "Vencidas", "hoy": "Hoy", "manana": "Mañana",
            "semana": "Esta semana", "adelante": "Más adelante",
            "algun_dia": "Algún día"}[clave]


def _icono(clave: str) -> str:
    return "⚠️" if clave == "vencidas" else "📂"


def _cuantos_botones(secciones: dict, expandidas: set[str]) -> int:
    sueltos = sum(len(rows) * 2 for clave, rows in secciones.items() if clave in expandidas)
    colapsadas = sum(1 for clave, rows in secciones.items()
                     if rows and clave not in expandidas)
    return sueltos + colapsadas + 1      # +1 por el súper


def _fila_tarea(row, ref: date) -> list[list[dict]]:
    return [[
        {"text": f"✅ {etiqueta(row, ref)}", "callback_data": cb.armar("ok", row["id"])},
        {"text": "⋯", "callback_data": cb.armar("m", row["id"])},
    ]]


def etiqueta(row, ref: date, largo: int = LARGO_BOTON) -> str:
    """El texto del botón: hora si tiene, título recortado en un límite de palabra."""
    partes = []
    if row["due_hora"]:
        partes.append(f"🕕 {row['due_hora']}")
    partes.append(views.recortar(views.titulo(row), largo))
    if row["responsable"] not in ("ninguno", ""):
        partes.append(config.NOMBRES.get(row["responsable"], row["responsable"]))
    etiqueta = " · ".join(partes)
    if row["recur_kind"]:
        etiqueta += " 🔁"     # el intervalo completo se ve en el menú «⋯»
    return etiqueta


# --------------------------------------------------------------------------
# Publicar y actualizar
# --------------------------------------------------------------------------

def actualizar(chat_id: int, forzar: bool = False) -> bool:
    """Edita el tablero en el lugar. Devuelve True si se editó."""
    guardado = db.tablero_actual(chat_id)
    if not guardado or not guardado["message_id"]:
        return publicar(chat_id) is not None

    if not forzar and _muy_seguido(guardado):
        db.marcar_tablero_sucio(chat_id)
        return False

    texto, teclado = render(chat_id)
    r = telegram.editar(chat_id, guardado["message_id"], texto, teclado, encolar=False)
    if r is None:
        # Puede ser "message is not modified" (no pasa nada) o que el mensaje ya no
        # exista (lo borraron): en ese caso se publica de nuevo.
        if telegram.ultimo_error_fue_no_modificado():
            db.guardar_tablero(chat_id, guardado["message_id"])
            return False
        log.warning("No pude editar el tablero: lo publico de nuevo")
        return publicar(chat_id) is not None
    db.guardar_tablero(chat_id, guardado["message_id"])
    return True


def publicar(chat_id: int, borrar_anterior: bool = True) -> int | None:
    """Manda el tablero al final del chat, lo fija y guarda el message_id."""
    anterior = db.tablero_actual(chat_id)
    texto, teclado = render(chat_id)
    enviado = telegram.enviar(chat_id, texto, teclado, silencioso=True)
    if enviado is None:
        db.marcar_tablero_sucio(chat_id)
        return None

    if borrar_anterior and anterior and anterior["message_id"]:
        telegram.borrar(chat_id, anterior["message_id"])

    message_id = enviado["message_id"]
    db.guardar_tablero(chat_id, message_id)
    _fijar(chat_id, message_id)
    return message_id


def _fijar(chat_id: int, message_id: int) -> None:
    if telegram.fijar(chat_id, message_id) is None and not db.ajuste("aviso_fijar"):
        # Sin permiso de fijar sigue funcionando igual: el tablero es un mensaje más.
        telegram.enviar(chat_id,
                        "Necesito ser admin con permiso de fijar mensajes para mostrar "
                        "el tablero 📌", silencioso=True)
        db.ajuste("aviso_fijar", "1")


def _muy_seguido(guardado) -> bool:
    from datetime import datetime

    editado = guardado.get("editado_en")
    if not editado:
        return False
    try:
        cuando = datetime.fromisoformat(editado)
    except ValueError:
        return False
    return (ahora() - cuando).total_seconds() < DEBOUNCE


def flushear_si_esta_sucio(chat_id: int) -> bool:
    """Lo llama el cron y cada update: si quedó algo sin reflejar, lo refleja."""
    guardado = db.tablero_actual(chat_id)
    if guardado and guardado.get("sucio"):
        return actualizar(chat_id, forzar=True)
    return False
