"""Armado de textos y teclados. Todo pensado para leerse en el celular."""
from __future__ import annotations

import sqlite3
from datetime import date

from . import config, db
from .dates import DIAS_NOMBRE, de_iso, formato_humano, hoy
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


def texto_tarea(row: sqlite3.Row) -> str:
    """Texto de la tarea limpio para mostrar: sin saltos de línea raros y con mayúscula."""
    t = " ".join((row["texto"] or "").split())
    return escapar(t[:1].upper() + t[1:])


def linea_tarea(row: sqlite3.Row, ref: date, mostrar_fecha: bool = True,
                fecha_txt: str | None = None) -> str:
    """Una tarea por línea: emoji de categoría, texto, y los detalles detrás de «·»."""
    partes = [f"{emoji(row)} {texto_tarea(row)}"]
    d = de_iso(row["due_date"])
    if mostrar_fecha and d:
        partes.append(f"<i>{fecha_txt or formato_humano(d, ref)}</i>")
    resp = sufijo_responsable(row).removeprefix(" · ")
    if resp:
        partes.append(resp)
    linea = " · ".join(partes)
    if row["recur_kind"]:
        linea += " 🔁"
    if row["postpone_count"] >= 3:
        linea += f" 😅×{row['postpone_count']}"
    return linea


def confirmacion(row: sqlite3.Row, ref: date) -> str:
    """Misma línea que en /tareas, pero siempre dice para cuándo (o que va al súper)."""
    cuando = "al súper" if row["tipo"] == "compras" else formato_humano(de_iso(row["due_date"]), ref)
    partes = [f"{emoji(row)} {texto_tarea(row)}", f"<i>{cuando}</i>"]
    resp = sufijo_responsable(row).removeprefix(" · ")
    if resp:
        partes.append(resp)
    linea = " · ".join(partes)
    if row["recur_kind"]:
        linea += " 🔁"
    return linea


# --------------------------------------------------------------------------
# Listados
# --------------------------------------------------------------------------

DIAS_CORTO = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _dia_corto(d: date, con_mes: bool = False) -> str:
    base = f"{DIAS_CORTO[d.weekday()].capitalize()} {d.day}"
    return f"{base}/{d.month}" if con_mes else base


def _por_dia(rows, con_mes: bool, ref: date) -> list[str]:
    """Agrupa tareas bajo un subtítulo por día (ej. «Dom 27»)."""
    lineas: list[str] = []
    dia_actual = None
    for r in rows:
        d = de_iso(r["due_date"])
        if d != dia_actual:
            if dia_actual is not None:
                lineas.append("")  # un renglón vacío entre días
            dia_actual = d
            lineas.append(f"<b>{_dia_corto(d, con_mes)}</b>")
        lineas.append(linea_tarea(r, ref, mostrar_fecha=False))
    return lineas


def render_todo(chat_id: int, categoria: str | None = None, ref: date | None = None) -> str:
    """Lista de pendientes pensada para el celular.

    Agrupa por horizonte de tiempo (como Todoist o Things): primero lo urgente
    con un subtítulo por día, y lo lejano o sin fecha compacto, una línea por tarea.
    """
    from datetime import timedelta

    ref = ref or hoy()
    casa = db.pendientes(chat_id, tipo="casa", categoria=categoria)
    compras = db.pendientes(chat_id, tipo="compras") if not categoria or categoria == "compras" else []

    if not casa and not compras:
        if categoria:
            return f"No hay nada pendiente de <b>{escapar(categoria)}</b> ✨"
        return "No hay nada pendiente. Qué lujo ✨"

    def fecha(r):
        return de_iso(r["due_date"])

    manana = ref + timedelta(days=1)
    fin_semana = ref + timedelta(days=6)
    vencidas = [r for r in casa if fecha(r) and fecha(r) < ref]
    de_hoy = [r for r in casa if fecha(r) == ref]
    de_manana = [r for r in casa if fecha(r) == manana]
    semana = [r for r in casa if fecha(r) and manana < fecha(r) <= fin_semana]
    despues = [r for r in casa if fecha(r) and fecha(r) > fin_semana]
    algun_dia = [r for r in casa if not fecha(r)]

    titulo = "<b>Pendientes</b>" + (f" · {escapar(categoria)}" if categoria else "")
    bloques: list[str] = [f"{titulo} · {len(casa)}"]

    if vencidas:
        bloques.append("⚠️ <b>VENCIDAS</b>\n" + "\n".join(linea_tarea(r, ref) for r in vencidas))
    if de_hoy:
        bloques.append(f"<b>HOY</b> · {_dia_corto(ref)}\n"
                       + "\n".join(linea_tarea(r, ref, mostrar_fecha=False) for r in de_hoy))
    if de_manana:
        bloques.append(f"<b>MAÑANA</b> · {_dia_corto(manana)}\n"
                       + "\n".join(linea_tarea(r, ref, mostrar_fecha=False) for r in de_manana))
    if semana:
        bloques.append("<b>ESTA SEMANA</b>\n\n" + "\n".join(_por_dia(semana, False, ref)))
    if despues:
        # Lo lejano va compacto: una línea por tarea con la fecha al lado, sin subtítulos.
        bloques.append("<b>MÁS ADELANTE</b>\n" + "\n".join(
            linea_tarea(r, ref, fecha_txt=_dia_corto(fecha(r), True).lower()) for r in despues))
    if algun_dia:
        bloques.append("<b>ALGÚN DÍA</b>\n" + "\n".join(linea_tarea(r, ref) for r in algun_dia))
    if compras:
        items = ", ".join(escapar(" ".join(r["texto"].split())) for r in compras)
        bloques.append(f"<b>SÚPER</b> · {len(compras)}\n{items}\n<i>Tocá /super para tacharlos</i>")

    return "\n\n".join(bloques)


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
