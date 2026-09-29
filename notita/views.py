"""Cómo se ve todo lo que dice Notita.

Regla de v2: acá no se decide nada, sólo se da forma. Y toda fecha se muestra
SIEMPRE con día de la semana y número: «mañana, lun 28» en vez de «mañana» a secas,
que en un grupo que se lee a cualquier hora es ambiguo.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import config
from .dates import (
    DIAS_NOMBRE,
    MESES_NOMBRE,
    Recurrencia,
    de_iso,
    hoy,
    texto_recurrencia,
)
from .telegram import escapar, mencion

DIAS_CORTO = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


# --------------------------------------------------------------------------
# Fechas
# --------------------------------------------------------------------------

def dia_corto(d: date, con_mes: bool = False) -> str:
    """«lun 28» o «lun 28/9»."""
    base = f"{DIAS_CORTO[d.weekday()]} {d.day}"
    return f"{base}/{d.month}" if con_mes else base


def dia_largo(d: date) -> str:
    """«domingo 27/9», para el encabezado del tablero."""
    return f"{DIAS_NOMBRE[d.weekday()]} {d.day}/{d.month}"


def cuando(d: date | None, hora: str | None = None, ref: date | None = None) -> str:
    """La fecha como la dice Notita: siempre con día de la semana y número.

    «hoy, dom 27» · «mañana, lun 28» · «jue 1/10» · «algún día»
    """
    if d is None:
        return "algún día"
    ref = ref or hoy()
    dias = (d - ref).days
    if dias == 0:
        texto = f"hoy, {dia_corto(d)}"
    elif dias == 1:
        texto = f"mañana, {dia_corto(d)}"
    elif dias == -1:
        texto = f"ayer, {dia_corto(d)}"
    elif 0 < dias <= 6:
        texto = dia_corto(d, con_mes=d.month != ref.month)
    elif dias < 0:
        texto = f"venció el {dia_corto(d, con_mes=True)}"
    else:
        texto = dia_corto(d, con_mes=True)
    return f"{texto} {hora}" if hora else texto


def fecha_larga(d: date) -> str:
    return f"{DIAS_NOMBRE[d.weekday()]} {d.day} de {MESES_NOMBRE[d.month - 1]}"


# --------------------------------------------------------------------------
# Ítems
# --------------------------------------------------------------------------

def titulo(row: sqlite3.Row) -> str:
    """El título limpio, con mayúscula inicial. Sin escapar: para botones."""
    t = " ".join((row["texto"] or "").split())
    return t[:1].upper() + t[1:]


def titulo_html(row: sqlite3.Row) -> str:
    return escapar(titulo(row))


def recortar(texto: str, largo: int) -> str:
    """Corta en un límite de palabra y agrega «…»."""
    if len(texto) <= largo:
        return texto
    corte = texto[:largo - 1]
    if " " in corte[largo // 2:]:
        corte = corte[:corte.rstrip().rfind(" ")]
    return corte.rstrip(" ,.;:") + "…"


def emoji(row: sqlite3.Row) -> str:
    if row["tipo"] == "compras":
        return "🛒"
    return config.CATEGORIA_EMOJI.get(row["categoria"], "📌")


def recurrencia_de(row: sqlite3.Row) -> Recurrencia | None:
    if not row["recur_kind"]:
        return None
    return Recurrencia(kind=row["recur_kind"], interval=row["recur_interval"] or 1,
                       weekday=row["recur_weekday"], monthday=row["recur_monthday"])


def sufijo_responsable(row: sqlite3.Row, con_mencion: bool = False) -> str:
    r = row["responsable"]
    if r in ("ninguno", ""):
        return ""
    if r == "ambos":
        return config.NOMBRES["ambos"]
    return mencion(r) if con_mencion else escapar(config.NOMBRES.get(r, r))


def _donde_y_cuando(row: sqlite3.Row, ref: date) -> str:
    """El tipo y la fecha son independientes: algo de compras puede tener día.

    «falta leche» → «a compras». «para el asado del sábado falta carbón» →
    «a compras · para el sáb 3».
    """
    if row["tipo"] != "compras":
        return cuando(de_iso(row["due_date"]), row["due_hora"], ref)
    d = de_iso(row["due_date"])
    return "a compras" if d is None else f"a compras · para {cuando(d, None, ref)}"


def linea(row: sqlite3.Row, ref: date | None = None, con_fecha: bool = True) -> str:
    """Una línea de ítem: emoji, título, y los detalles detrás de «·»."""
    ref = ref or hoy()
    partes = [f"{emoji(row)} {titulo_html(row)}"]
    if con_fecha:
        partes.append(f"<i>{_donde_y_cuando(row, ref)}</i>")
    elif row["due_hora"]:
        partes.append(f"<i>🕕 {row['due_hora']}</i>")
    resp = sufijo_responsable(row)
    if resp:
        partes.append(resp)
    texto = " · ".join(partes)
    rec = texto_recurrencia(recurrencia_de(row))
    if rec:
        texto += f" 🔁 {rec}"
    return texto


def detalle(row: sqlite3.Row, ref: date | None = None) -> str:
    """El encabezado del menú «⋯»: el título entero y sus datos."""
    ref = ref or hoy()
    datos = [_donde_y_cuando(row, ref)]
    resp = sufijo_responsable(row)
    datos.append(resp if resp else "sin responsable")
    rec = texto_recurrencia(recurrencia_de(row))
    datos.append(f"🔁 {rec}" if rec else "🔁 no")
    return f"{emoji(row)} <b>{titulo_html(row)}</b>\n<i>{' · '.join(datos)}</i>"


AYUDA = """Soy <b>Notita</b> 🧲 y así nos entendemos:

<b>Para anotar, escribime normal</b>
• «hay que limpiar la heladera y falta leche» → uno va a tareas y otro a compras
• «llevar a Milo al veterinario el jueves a las 18» → con fecha, hora y responsable
• «pagar el ABL todos los 10» → se repite sola 🔁
• 🎤 <b>o mandame un audio</b> de hasta {segundos} segundos y lo transcribo

<b>Para manejar lo anotado, tocá</b>
El <b>tablero</b> está fijado arriba del grupo:
• <b>✅</b> en cada tarea la da por hecha, de un toque.
• <b>⋯ Cambiar algo</b> para el resto: el día, quién la hace, el nombre, mandarla
  a compras, borrarla o pasarla al calendario.
• <b>↩️ Deshacer</b> aparece un rato después de tachar, por si no era esa.
• Las secciones con <b>›</b> (Esta semana, Algún día, Compras) se abren aparte.

Si me pedís algo por texto («ya compré la leche», «pasá lo del horno al domingo»),
te lo propongo con botones y vos confirmás. Nunca toco nada sin que alguien toque.

<b>El parte</b>
Todos los días a las {hora} te cuento qué hay para mañana y qué quedó pendiente.
Es el único mensaje que suena.

<b>Lo que NO hago</b>
No aviso a horas exactas: para eso, el botón 📅 <b>Agregar al calendario</b> que
aparece cuando algo tiene hora. Y para cambiarle el ritmo a una tarea que se repite,
borrala y anotala de nuevo.

Comandos: /tablero · /compras · /parte · /ayuda"""


def ayuda() -> str:
    return (AYUDA.replace("{hora}", config.HORA_RUTINA)
            .replace("{segundos}", str(config.AUDIO_SEGUNDOS)))


BIENVENIDA = """🧲 <b>Notita cambió</b>

Ahora <b>hablando se anota y tocando se gestiona</b>:

• Escribime normal para anotar cosas, igual que siempre.
• Abajo tenés el <b>tablero fijado</b>: ahí está todo, con ✅ y ⋯ en cada tarea.
• Si me pedís un cambio por texto, te lo propongo con botones y vos confirmás.
• Todos los días a las {hora} mando el parte de mañana. <b>Es lo único que suena.</b>
• Ya no aviso a horas exactas: cuando algo tiene hora te doy un botón para pasarlo
  al calendario."""


def bienvenida(recados_descartados: int = 0) -> str:
    texto = BIENVENIDA.replace("{hora}", config.HORA_RUTINA)
    if recados_descartados:
        cuantos = ("un recado que estaba" if recados_descartados == 1
                   else f"{recados_descartados} recados que estaban")
        texto += f"\n\n<i>Se me quedó {cuantos} esperando para más tarde: eso ya no existe.</i>"
    return texto
