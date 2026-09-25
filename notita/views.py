"""Armado de textos y teclados. Todo pensado para leerse en el celular."""
from __future__ import annotations

import sqlite3
from datetime import date

from . import config, db
from .dates import DIAS_NOMBRE, de_iso, formato_dia, formato_humano, hoy
from .telegram import escapar, mencion

# --------------------------------------------------------------------------
# Teclados
# --------------------------------------------------------------------------

def teclado_recordatorio(task_id: int, insistente: bool = False) -> list:
    filas = [[
        {"text": "✅ Hecho", "callback_data": f"h:{task_id}"},
        {"text": "⏰ Posponer", "callback_data": f"p:{task_id}"},
        {"text": "🗑️ Borrar", "callback_data": f"b:{task_id}"},
    ]]
    if insistente:
        filas.append([{"text": "🙈 La borro y listo", "callback_data": f"b:{task_id}"}])
    return filas


def teclado_posponer(task_id: int) -> list:
    return [
        [
            {"text": "Mañana", "callback_data": f"p:{task_id}:m"},
            {"text": "Finde", "callback_data": f"p:{task_id}:f"},
        ],
        [
            {"text": "Semana que viene", "callback_data": f"p:{task_id}:s"},
            {"text": "Elegir fecha", "callback_data": f"p:{task_id}:o"},
        ],
    ]


def teclado_para_cuando(task_id: int) -> list:
    return [
        [
            {"text": "Hoy", "callback_data": f"f:{task_id}:h"},
            {"text": "Mañana", "callback_data": f"f:{task_id}:m"},
        ],
        [
            {"text": "Esta semana", "callback_data": f"f:{task_id}:e"},
            {"text": "La que viene", "callback_data": f"f:{task_id}:v"},
        ],
        [
            {"text": "Algún día", "callback_data": f"f:{task_id}:a"},
            {"text": "Otra fecha", "callback_data": f"f:{task_id}:o"},
        ],
    ]


def teclado_super(rows) -> list:
    return [[{"text": f"🛒 {r['texto'][:40]}", "callback_data": f"c:{r['id']}"}] for r in rows]


# --------------------------------------------------------------------------
# Piezas de texto
# --------------------------------------------------------------------------

def emoji(row: sqlite3.Row) -> str:
    return config.CATEGORIA_EMOJI.get(row["categoria"], "📌")


def sufijo_responsable(row: sqlite3.Row, con_mencion: bool = False) -> str:
    r = row["responsable"]
    if r == "ninguno":
        return ""
    if r == "ambos":
        return " · los dos"
    return f" · {mencion(r) if con_mencion else escapar(config.NOMBRES[r])}"


def linea_tarea(row: sqlite3.Row, ref: date, mostrar_fecha: bool = True) -> str:
    texto = f"{emoji(row)} {escapar(row['texto'])}"
    d = de_iso(row["due_date"])
    if mostrar_fecha and d:
        texto += f" — {formato_humano(d, ref)}"
    texto += sufijo_responsable(row)
    if row["postpone_count"] >= 3:
        texto += f" 😅 x{row['postpone_count']}"
    if row["recur_kind"]:
        texto += " 🔁"
    return texto


def confirmacion(row: sqlite3.Row, ref: date) -> str:
    d = de_iso(row["due_date"])
    partes = [f"{emoji(row)} <b>{escapar(row['texto'])}</b>"]
    if row["tipo"] == "compras":
        partes.append("a la lista del super")
    else:
        partes.append(f"para {formato_humano(d, ref)}")
    r = row["responsable"]
    if r == "ambos":
        partes.append("los dos")
    elif r != "ninguno":
        partes.append(escapar(config.NOMBRES[r]))
    if row["recur_kind"]:
        partes.append("🔁")
    return " · ".join(partes)


# --------------------------------------------------------------------------
# Listados
# --------------------------------------------------------------------------

def render_todo(chat_id: int, categoria: str | None = None, ref: date | None = None) -> str:
    ref = ref or hoy()
    casa = db.pendientes(chat_id, tipo="casa", categoria=categoria)
    compras = db.pendientes(chat_id, tipo="compras") if not categoria or categoria == "compras" else []

    if not casa and not compras:
        if categoria:
            return f"No hay nada pendiente de <b>{escapar(categoria)}</b> ✨"
        return "No hay nada pendiente. Qué lujo ✨"

    vencidas = [r for r in casa if de_iso(r["due_date"]) and de_iso(r["due_date"]) < ref]
    con_fecha = [r for r in casa if de_iso(r["due_date"]) and de_iso(r["due_date"]) >= ref]
    algun_dia = [r for r in casa if not r["due_date"]]

    bloques: list[str] = []
    titulo = "📋 <b>Pendientes</b>" + (f" · {escapar(categoria)}" if categoria else "")
    bloques.append(titulo)

    if vencidas:
        bloques.append("\n⚠️ <b>Vencidas</b>\n" + "\n".join(linea_tarea(r, ref) for r in vencidas))

    if con_fecha:
        lineas = []
        dia_actual = None
        for r in con_fecha:
            d = de_iso(r["due_date"])
            if d != dia_actual:
                dia_actual = d
                lineas.append(f"<b>{formato_dia(d)}</b>")
            lineas.append("  " + linea_tarea(r, ref, mostrar_fecha=False))
        bloques.append("\n🗓️ <b>Con fecha</b>\n" + "\n".join(lineas))

    if algun_dia:
        bloques.append("\n🌥️ <b>Algún día</b>\n" + "\n".join(linea_tarea(r, ref) for r in algun_dia))

    if compras:
        bloques.append("\n🛒 <b>Super</b>\n" + "\n".join(f"• {escapar(r['texto'])}" for r in compras))

    return "\n".join(bloques)


def render_algun_dia(chat_id: int, ref: date | None = None) -> str:
    ref = ref or hoy()
    rows = db.sin_fecha(chat_id)
    if not rows:
        return "No hay nada en «algún día». Estamos al día 🌤️"
    return "🌥️ <b>Algún día</b>\n" + "\n".join(linea_tarea(r, ref) for r in rows)


def render_super(chat_id: int) -> tuple[str, list]:
    rows = db.pendientes(chat_id, tipo="compras")
    if not rows:
        return "La lista del super está vacía 🛒", []
    texto = "🛒 <b>Lista del super</b>\n" + "\n".join(f"• {escapar(r['texto'])}" for r in rows)
    texto += "\n\n<i>Tocá lo que ya compraste.</i>"
    return texto, teclado_super(rows)


def render_resumen_semanal(chat_id: int, ref: date) -> str:
    """Resumen del domingo: la semana que arranca mañana (lunes a domingo)."""
    from datetime import timedelta

    inicio = ref + timedelta(days=1)
    fin = inicio + timedelta(days=6)
    rows = db.vencen_entre(chat_id, inicio, fin)

    bloques = [f"☕ <b>Resumen de la semana</b> ({inicio.day}/{inicio.month} al {fin.day}/{fin.month})"]
    if rows:
        dia_actual = None
        lineas = []
        for r in rows:
            d = de_iso(r["due_date"])
            if d != dia_actual:
                dia_actual = d
                lineas.append(f"\n<b>{DIAS_NOMBRE[d.weekday()].capitalize()} {d.day}/{d.month}</b>")
            lineas.append("  " + linea_tarea(r, ref, mostrar_fecha=False))
        bloques.append("\n".join(lineas))
    else:
        bloques.append("\nNo hay nada agendado para esta semana 🎉")

    vencidas = [r for r in db.vencen_hasta(chat_id, ref) if de_iso(r["due_date"]) < ref]
    pendientes_algun_dia = db.sin_fecha(chat_id)
    extras = []
    if vencidas:
        extras.append(f"⚠️ {len(vencidas)} vencida{'s' if len(vencidas) > 1 else ''} sin hacer")
    if pendientes_algun_dia:
        extras.append(f"🌥️ {len(pendientes_algun_dia)} para «algún día»")
    if extras:
        bloques.append("\n" + " · ".join(extras))
    return "\n".join(bloques)


AYUDA = """Hola, soy <b>Notita</b> 🧲

Escribime en el grupo lo que haya que hacer, así nomás:
• «hay que limpiar la heladera y comprar focos» → lo separo en dos
• «Barbu tiene que llamar al veterinario el lunes» → con fecha y responsable
• «falta leche» → va derecho a la lista del super
• «cambiar las piedritas del gato cada semana» → se repite sola 🔁

Si no me decís cuándo, te pregunto.
A las 20:00 del día que vence te recuerdo y podés marcar ✅ Hecho, ⏰ Posponer o 🗑️ Borrar.
Los domingos a las 20:00 te paso el resumen de la semana.

Comandos:
/todo — todo lo pendiente
/todo limpieza — filtrado por categoría
/algundia — lo que no tiene fecha
/super — la lista del super
/ayuda — esto

Categorías: limpieza, arreglos, tramites, pagos, mascotas, compras, otros."""
