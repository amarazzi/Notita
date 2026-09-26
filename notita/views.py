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


def teclado_vencidas_juntas() -> list:
    return [
        [{"text": "⏰ Patearlas una semana", "callback_data": "vg:s"}],
        [{"text": "📋 Verlas de a una", "callback_data": "vg:u"}],
    ]


def render_vencidas_juntas(rows, ref: date) -> str:
    """Las que hace noches que nadie toca, en un solo mensaje en vez de N."""
    lineas = [linea_tarea(r, ref) for r in rows[:8]]
    if len(rows) > 8:
        lineas.append(f"<i>…y {len(rows) - 8} más</i>")
    return (f"😅 Hace varios días que pregunto por estas <b>{len(rows)}</b>:\n"
            + "\n".join(lineas)
            + "\n\n¿Las hacemos, las pateamos, o las borramos de una vez?")


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
    filas = [[{"text": f"🛒 {r['texto'][:40]}", "callback_data": f"c:{r['id']}"}] for r in rows]
    if len(rows) > 1:   # volver del súper y tocar 15 botones es un castigo
        filas.append([{"text": "✅ Compramos todo", "callback_data": "ct"}])
    return filas


# --------------------------------------------------------------------------
# Piezas de texto
# --------------------------------------------------------------------------

def emoji(row: sqlite3.Row) -> str:
    if row["tipo"] == "recado":
        return "💌"
    if row["tipo"] != "compras" and row["categoria"] == "compras":
        return config.CATEGORIA_EMOJI["otros"]  # no es del súper: que no lleve carrito
    return config.CATEGORIA_EMOJI.get(row["categoria"], "📌")


def render_recado(row: sqlite3.Row) -> str:
    """Lo que se manda al grupo el día que toca entregarlo."""
    de = config.NOMBRES.get(row["created_by"], "alguien")
    para = mencion(row["responsable"])
    return f"💌 {para}, {escapar(de)} te manda a decir:\n«{texto_tarea(row)}»"


def sufijo_responsable(row: sqlite3.Row, con_mencion: bool = False) -> str:
    r = row["responsable"]
    if r == "ninguno":
        return ""
    if r == "ambos":
        return f" · {config.NOMBRES['ambos']}"
    # .get por si la tarea quedó a nombre de alguien que ya no está en la config.
    return f" · {mencion(r) if con_mencion else escapar(config.NOMBRES.get(r, r))}"


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
    if row["tipo"] == "recado":
        a_quien = config.NOMBRES.get(row["responsable"], row["responsable"])
        d = de_iso(row["due_date"])
        # Los de hoy se entregan en el momento; los de otro día, en la pasada de la noche.
        cuando = "ahora mismo" if d == ref else f"{formato_humano(d, ref)} a las 20:00"
        return (f"💌 A {escapar(a_quien)} · <i>{cuando}</i>\n"
                f"   «{texto_tarea(row)}»")
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
    compras = db.pendientes(chat_id, tipo="compras")
    extras = []
    if vencidas:
        extras.append(f"⚠️ {len(vencidas)} vencida{'s' if len(vencidas) > 1 else ''} sin hacer")
    if compras:
        # Sin esto la lista del súper es invisible: no tiene fecha ni recordatorio,
        # así que si nadie escribe /super se pudre sin que nadie se entere.
        extras.append(f"🛒 {len(compras)} en el súper")
    if pendientes_algun_dia:
        extras.append(f"🌥️ {len(pendientes_algun_dia)} para «algún día»")
    if extras:
        bloques.append("\n" + " · ".join(extras))
    return "\n".join(bloques)


AYUDA = """Hola, soy <b>Notita</b> 🧲

<b>Para anotar</b>, escribime así nomás:
• «hay que limpiar la heladera y comprar focos» → lo separo en dos
• «llamar al veterinario el lunes» → con fecha y responsable si lo nombrás
• «falta leche» → va derecho a la lista del super
• «cambiar las piedritas del gato cada semana» → se repite sola 🔁

<b>Para pedirme cosas</b>, también hablando normal:
• «¿qué hay que hacer?» o «mostrame las de limpieza»
• «mostrame la lista del super»
• «ya limpié la heladera» → la tacho
• «borrá la del plomero» → la borro

<b>Para mandar un recado</b> 💌
• «avisale a Axel que llego en 10» → se lo digo en el momento
• «decile a Axel mañana que compre pan» → se lo digo mañana a las 20:00

Si no me decís cuándo es algo, te pregunto.
A las 20:00 del día que vence te recuerdo, con ✅ Hecho, ⏰ Posponer y 🗑️ Borrar.
Los domingos a las 20:00 te paso el resumen de la semana.

Si preferís los comandos: /todo, /todo limpieza, /algundia, /super, /ayuda.

Categorías: limpieza, arreglos, tramites, pagos, mascotas, compras, otros."""
