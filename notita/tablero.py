"""El tablero: un solo mensaje fijado que se edita en el lugar.

Reglas que valen la pena tener a mano:

- **El texto es para leer; los botones, sólo para hacer.** Cada cosa aparece UNA vez.
  El tablero llegó a mostrar las secciones como texto Y como botón, y el deshacer como
  renglón Y como botón: el doble de alto para la misma información, y las tareas —lo
  único que importa— quedaban abajo, escondidas.
- Cada tarea cae en UNA sección: tienen precedencia (en v1 lo del lunes salía en
  «mañana» y otra vez en «la semana que viene»).
- El tablero NUNCA navega: los menús son mensajes nuevos. Son dos personas mirando la
  misma pantalla; si uno abriera un submenú acá, al otro le cambiaría lo que ve.
- Se edita al final de cada update, siempre. Tuvo un debounce de 3 segundos para no
  golpear los límites de Telegram, y fue un error: un toque entraba en esos 3
  segundos y el tablero se quedaba mostrando la tarea que ya estaba hecha hasta el
  próximo mensaje. Un tablero que miente es mucho peor que una llamada de más, y un
  toque es una acción humana: el ritmo lo pone el dedo, no el código.
- Si la edición falla, queda marcado sucio y lo reintenta el próximo evento.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from . import cb, db, telegram, views
from .dates import ahora, de_iso, domingo_de_la_semana, hoy

log = logging.getLogger("notita.tablero")

LARGO_BOTON = 34        # lo que entra en un botón de fila completa
POR_FILA = 5            # cuántos ✅ por fila
# Cuántas cosas se listan una por una. Lo que no entra se resume en una línea y se
# maneja por «⋯ Cambiar algo», que lista todo.
#
# Quince, no diez: cuando el ✅ se llevaba la fila entera, diez ya eran diez filas de
# botones. Ahora son números de a cinco por fila, así que quince entran en tres filas
# y en una casa normal eso alcanza para listar TODO.
MAX_NUMERADAS = 15

# En qué orden se listan las cosas, que es también el orden en que se muestran. NO es
# el orden del calendario: es cuánto te está pidiendo atención.
#
# Lo sin fecha va ANTES de lo que tiene día futuro, por una razón concreta: algo con
# fecha va a aparecer solo cuando llegue su día, y algo sin fecha no aparece nunca si
# no se lista. Puesto como número en un resumen, se podre ahí para siempre.
PRIORIDAD = ("vencidas", "hoy", "manana", "algun_dia", "semana", "adelante")
# En una línea de resumen entran los títulos si son pocos; si no, sólo el número.
MAX_EN_RESUMEN = 2
# Y si los títulos no entran en un renglón, se nombra el primero y se cuenta el resto:
# una línea que se parte en tres es peor que un número.
LARGO_RESUMEN = 48


def render(chat_id: int, ref: date | None = None) -> tuple[str, list]:
    """El texto y el teclado del tablero. Sin efectos: se puede testear sola."""
    ref = ref or hoy()
    cosas = db.pendientes(chat_id)
    # La etiqueta 🛒 cuenta TODO lo etiquetado: tiene que dar lo mismo que /compras, o
    # son dos números para lo mismo y ninguno se cree.
    compras = [r for r in cosas if r["compra"]]
    secciones = repartir(cosas, ref)

    listadas = _listadas(secciones)
    numerables = [r for clave in listadas for r in secciones[clave]]
    numeros = {r["id"]: i for i, r in enumerate(numerables, start=1)}

    # Los bloques se separan con un renglón en blanco (el tablero apretado se lee
    # como una pared de texto), pero las líneas de resumen van juntas entre sí: son
    # una sola idea, «lo que puede esperar».
    bloques = [[f"📋 <b>La casa</b> · {views.dia_corto(ref, con_mes=True)}"]]
    bloques += _bloques_listados(secciones, listadas, numeros, ref)
    resumen = _texto_de_las_otras(secciones, listadas, ref)
    if resumen:
        bloques.append(resumen)
    lineas = []
    for bloque in bloques:
        if lineas:
            lineas.append("")
        lineas += bloque

    filas = _botones_de_tachar(numerables, numeros)
    filas.append([
        {"text": f"🛒 Compras · {len(compras)}", "callback_data": cb.armar("sup")},
        {"text": "⋯ Cambiar algo", "callback_data": cb.armar("elegir")},
    ])
    # El deshacer va en su propia fila y sólo un rato: es una oportunidad, no una
    # parte del tablero. Y no se repite como renglón de texto.
    deshacer = db.ultimo_deshacer(chat_id)
    if deshacer:
        filas.append([{"text": f"↩️ Deshacer{_que_deshace(deshacer)}",
                       "callback_data": cb.armar("u", deshacer["id"])}])

    texto = "\n".join(lineas)
    if len(texto) > telegram.LARGO_MAXIMO:
        texto = texto[:telegram.LARGO_MAXIMO] + "\n<i>…</i>"
    return texto, filas


def _listadas(secciones: dict) -> list[str]:
    """Qué secciones se listan una por una (con número y ✅) y cuáles se resumen.

    Se lista todo lo que entre, en orden de prioridad; lo que sobra se cae del final.
    Con la casa tranquila eso es TODO: el tablero es una lista de cosas para hacer,
    no una agenda.
    """
    claves = list(PRIORIDAD)
    while len(claves) > 2 and sum(len(secciones[c]) for c in claves) > MAX_NUMERADAS:
        claves.pop()        # primero se caen las que tienen día futuro
    return claves


def _bloques_listados(secciones: dict, listadas: list[str], numeros: dict,
                      ref: date) -> list[list[str]]:
    """Un bloque por sección listada, así queda un renglón en blanco entre medio."""
    bloques = []
    if not secciones["hoy"] and not secciones["manana"]:
        bloques.append(["Nada para hoy ni mañana ✨"])
    for clave in listadas:
        rows = secciones[clave]
        if not rows:
            continue
        bloques.append([_encabezado(clave, ref)]
                       + [_renglon(r, numeros, ref, con_fecha=_lleva_fecha(clave))
                          for r in rows])
    return bloques


def _lleva_fecha(clave: str) -> bool:
    """El día se muestra cuando el encabezado no lo dice.

    «Hoy» y «Mañana» ya lo dicen, y «Sin fecha» no tiene. En las otras es lo que más
    importa: «Pagar el ABL» sin el «vie 2/10» no sirve de nada.
    """
    return clave in ("vencidas", "semana", "adelante")


def _encabezado(clave: str, ref: date) -> str:
    if clave == "vencidas":
        return "⚠️ <b>Vencidas</b>"
    if clave == "hoy":
        return "<b>Hoy</b>"                 # la fecha ya está en el encabezado
    if clave == "manana":
        return f"<b>Mañana</b> · {views.dia_corto(ref + timedelta(days=1))}"
    return f"<b>{_nombre_corto(clave)}</b>"


def _renglon(row, numeros: dict, ref: date, con_fecha: bool = False) -> str:
    """«3. 🧽 Limpiar la heladera · 🕕 10:00 · Axel»."""
    numero = numeros.get(row["id"])
    cabeza = f"<b>{numero}.</b> " if numero else "· "
    return cabeza + views.linea(row, ref, con_fecha=con_fecha)


def _texto_de_las_otras(secciones: dict, listadas: list[str], ref: date) -> list[str]:
    """Una línea por sección que no se listó, sin botón. Con pocas cosas, se nombran.

    Un número solo («Más adelante: 5») no dice nada; el nombre de la única cosa que
    hay, sí. Pero cinco títulos tapan lo de hoy, así que ahí sólo va el número.
    """
    lineas = []
    for clave in PRIORIDAD:
        rows = secciones[clave]
        if rows and clave not in listadas:
            lineas.append(_una_linea(_nombre_corto(clave), rows, ref,
                                     con_fecha=clave != "algun_dia"))
    return lineas


def _una_linea(nombre: str, rows: list, ref: date, con_fecha: bool) -> str:
    if len(rows) > MAX_EN_RESUMEN:
        return f"<b>{nombre}:</b> {len(rows)}"
    detalle = ", ".join(views.linea(r, ref, con_fecha=con_fecha) for r in rows)
    if _ancho(detalle) <= LARGO_RESUMEN:
        return f"<b>{nombre}:</b> {detalle}"
    # No entra en un renglón: se nombra la primera y se cuentan las otras.
    primera = f"{views.emoji(rows[0])} {views.recortar(views.titulo(rows[0]), 28)}"
    resto = f" +{len(rows) - 1}" if len(rows) > 1 else ""
    return f"<b>{nombre}:</b> {primera}{resto}"


def _ancho(texto: str) -> int:
    """Lo que se ve, sin las etiquetas HTML."""
    return len(re.sub(r"</?[a-z]+>", "", texto))


def _botones_de_tachar(numerables: list, numeros: dict) -> list[list[dict]]:
    """Sólo el número: el texto ya está en el mensaje, arriba."""
    filas = []
    for i in range(0, len(numerables), POR_FILA):
        filas.append([{"text": f"✅ {numeros[r['id']]}",
                       "callback_data": cb.armar("ok", r["id"])}
                      for r in numerables[i:i + POR_FILA]])
    return filas


def repartir(cosas, ref: date) -> dict[str, list]:
    """Cada cosa en UNA sección. El orden de los `if` es la precedencia.

    Las compras sin fecha no entran en ninguna: viven en el botón de Compras.
    """
    domingo = domingo_de_la_semana(ref)
    manana = ref + timedelta(days=1)
    secciones: dict[str, list] = {"vencidas": [], "hoy": [], "manana": [],
                                  "semana": [], "adelante": [], "algun_dia": []}
    for r in cosas:
        d = de_iso(r["due_date"])
        if d is None:
            if not r["compra"]:
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



# El orden en que se muestran y se numeran. Lo usa también el menú «⋯», que lista
# TODO: ninguna cosa queda inaccesible por no tener botón propio en el tablero.
ORDEN = ("vencidas", "hoy", "manana", "semana", "adelante", "algun_dia")


def _nombre_corto(clave: str) -> str:
    return {"vencidas": "Vencidas", "hoy": "Hoy", "manana": "Mañana",
            "semana": "Esta semana", "adelante": "Más adelante",
            "algun_dia": "Sin fecha"}[clave]



def _que_deshace(deshacer: dict) -> str:
    """«↩️ Deshacer: Agarrar sábanas», para saber qué se va a revertir.

    Corto: el botón ocupa la fila entera, pero Telegram igual corta lo que no entra.
    """
    if len(deshacer["item_ids"]) != 1:
        return f" ({len(deshacer['item_ids'])})"
    row = db.obtener(deshacer["item_ids"][0])
    if row is None:
        return ""
    return f": {views.recortar(views.titulo(row), 20)}"




def etiqueta(row, ref: date, largo: int = LARGO_BOTON,
             numero: int | None = None) -> str:
    """El texto del botón: corto a propósito.

    La hora, el responsable y la recurrencia se ven en el renglón del texto; acá
    entra el número (para encontrarlo) y el título recortado.
    """
    cabeza = f"{numero}. " if numero else ""
    if row["compra"]:
        cabeza += "🛒 "
    return cabeza + views.recortar(views.titulo(row), largo)


# --------------------------------------------------------------------------
# Publicar y actualizar
# --------------------------------------------------------------------------

def actualizar(chat_id: int, forzar: bool = False) -> bool:
    """Edita el tablero en el lugar. Devuelve True si se editó.

    `forzar` quedó por compatibilidad con los llamadores: hoy siempre se edita.
    """
    del forzar
    guardado = db.tablero_actual(chat_id)
    if not guardado or not guardado["message_id"]:
        return publicar(chat_id) is not None

    texto, teclado = render(chat_id)
    r = telegram.editar(chat_id, guardado["message_id"], texto, teclado, encolar=False)
    if r is None:
        # Puede ser "message is not modified" (no pasa nada) o que el mensaje ya no
        # exista (lo borraron): en ese caso se publica de nuevo.
        if telegram.ultimo_error_fue_no_modificado():
            db.guardar_tablero(chat_id, guardado["message_id"])
            return False
        log.warning("No pude editar el tablero: lo publico de nuevo")
        # `reusar_si_es_reciente=False` porque venimos de que editar falló: si no,
        # publicar nos devolvería acá y sería un ida y vuelta infinito.
        return publicar(chat_id, reusar_si_es_reciente=False) is not None
    db.guardar_tablero(chat_id, guardado["message_id"])
    return True


# Si se acaba de publicar, no se publica de nuevo: el primer mensaje del grupo
# dispara la bienvenida (que publica) y puede ser además un «tablero» (que publica
# otra vez). Antes quedaban dos pines y un «fijó un mensaje» sin nada, porque el
# primero se borraba.
RECIEN_PUBLICADO = 15    # segundos


def publicar(chat_id: int, borrar_anterior: bool = True,
             reusar_si_es_reciente: bool = True, pedido: bool = False) -> int | None:
    """Manda el tablero al final del chat, lo fija y guarda el message_id.

    `pedido=True` cuando lo pidió una persona («tablero», /tablero): entonces va al
    final del chat SIEMPRE. Sin eso, si el tablero se había editado hace un segundo
    (por ejemplo por un ✅), se reusaba el de arriba y abajo no aparecía nada:
    «escribo tablero y no pasa nada».
    """
    anterior = db.tablero_actual(chat_id)
    if (reusar_si_es_reciente and not pedido and anterior and anterior.get("message_id")
            and _recien(anterior)):
        actualizar(chat_id, forzar=True)
        return anterior["message_id"]
    texto, teclado = render(chat_id)
    # `encolar=False`: un tablero en la cola de salida sería una foto vieja mandada
    # más tarde, y encima como mensaje nuevo. Si falla, queda sucio y se rearma.
    enviado = telegram.enviar(chat_id, texto, teclado, silencioso=True, encolar=False)
    if enviado is None:
        db.marcar_tablero_sucio(chat_id)
        return None

    if borrar_anterior and anterior and anterior["message_id"]:
        # Desfijar primero: si se borra un mensaje fijado, Telegram deja el aviso
        # «fijó un mensaje» apuntando a la nada.
        telegram.desfijar(chat_id, anterior["message_id"])
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


def _recien(guardado) -> bool:
    return _hace_cuanto(guardado) < RECIEN_PUBLICADO


def _hace_cuanto(guardado) -> float:
    from datetime import datetime

    editado = guardado.get("editado_en")
    if not editado:
        return 1e9
    try:
        return (ahora() - datetime.fromisoformat(editado)).total_seconds()
    except ValueError:
        return 1e9


def flushear_si_esta_sucio(chat_id: int) -> bool:
    """Lo llama el cron y cada update: si quedó algo sin reflejar, lo refleja."""
    guardado = db.tablero_actual(chat_id)
    if guardado and guardado.get("sucio"):
        return actualizar(chat_id, forzar=True)
    return False
