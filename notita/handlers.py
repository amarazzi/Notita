"""Qué hace Notita con cada update de Telegram."""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta

from . import config, db, heuristica, llm, telegram, views
from .dates import (
    DateSpec,
    ahora,
    de_iso,
    formato_humano,
    hoy,
    parse_solo_fecha,
    proximo_fin_de_semana,
    resolve,
)

log = logging.getLogger("notita.handlers")

CHISTES_POSPONER = [
    "Esta ya la pospusimos {n} veces, se está haciendo la difícil 😅",
    "Van {n} veces que la pateamos. ¿La hacemos o la dejamos ir? 🙈",
    "{n} posposiciones. Creo que esta tarea ya es parte de la familia 🐢",
]


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------

def handle_update(update: dict) -> None:
    db.init_db()
    # Telegram reenvía el update si el webhook tarda o falla, y con Gemini lento
    # eso pasa: sin esta guarda, el mismo mensaje se anotaba dos veces.
    if not db.update_nuevo(update.get("update_id")):
        log.info("Update repetido, lo ignoro: %s", update.get("update_id"))
        return
    if "callback_query" in update:
        _callback(update["callback_query"])
        return
    if "edited_message" in update and "message" not in update:
        # Editar un mensaje creaba una tarea nueva: corregir un tipeo terminaba en
        # dos tareas. Mejor no hacer nada que duplicar.
        log.info("Mensaje editado, lo ignoro")
        return
    msg = update.get("message")
    if msg:
        _mensaje(msg)


def _autorizado(chat_id: int) -> bool:
    return bool(config.ALLOWED_CHAT_ID) and chat_id == config.ALLOWED_CHAT_ID


def _mensaje(msg: dict) -> None:
    chat_id = msg.get("chat", {}).get("id")
    texto = (msg.get("text") or msg.get("caption") or "").strip()
    autor = config.persona_de_user_id(msg.get("from", {}).get("id", 0))

    # /chatid anda en cualquier lado: sirve para configurar el bot la primera vez.
    if texto and texto.split()[0].split("@")[0].lower() == "/chatid":
        telegram.enviar(chat_id, f"El chat_id de acá es <code>{chat_id}</code>")
        return

    if not _autorizado(chat_id):
        log.info("Ignorando chat %s", chat_id)
        return
    if not texto:
        return

    if texto.startswith("/"):
        _comando(chat_id, texto, autor)
        return

    pendiente = db.get_pending(chat_id)
    if pendiente and pendiente["kind"] == "fecha" and _responder_fecha_libre(chat_id, pendiente, texto):
        return
    if pendiente and pendiente["kind"] == "aclaracion":
        db.clear_pending(chat_id)
        previo = pendiente["data"].get("texto")
        _interpretar_y_guardar(chat_id, texto, autor, contexto_previo=previo)
        return

    _interpretar_y_guardar(chat_id, texto, autor)


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

def _comando(chat_id: int, texto: str, autor: str) -> None:
    partes = texto.split()
    cmd = partes[0].split("@")[0].lower()
    arg = " ".join(partes[1:]).strip().lower()

    if cmd in ("/todo", "/tareas"):
        categoria = None
        if arg:
            categoria = _match_categoria(arg)
            if categoria is None:
                telegram.enviar(chat_id, f"No conozco la categoría «{telegram.escapar(arg)}». "
                                         f"Probá con: {', '.join(config.CATEGORIAS)}")
                return
        telegram.enviar_largo(chat_id, views.render_todo(chat_id, categoria))
    elif cmd in ("/algundia", "/algun_dia"):
        telegram.enviar_largo(chat_id, views.render_algun_dia(chat_id))
    elif cmd in ("/super", "/compras"):
        t, kb = views.render_super(chat_id)
        telegram.enviar_largo(chat_id, t, kb)
    elif cmd in ("/ayuda", "/help", "/start"):
        telegram.enviar(chat_id, views.AYUDA)
    elif cmd in ("/recordatorios", "/probar"):
        from .reminders import correr_rutina_diaria

        correr_rutina_diaria(forzar=True)
    else:
        telegram.enviar(chat_id, "Ese comando no lo tengo. Probá /ayuda 🤍")


def _match_categoria(arg: str) -> str | None:
    from .dates import normalizar

    a = normalizar(arg)
    if not a:
        return None
    for c in config.CATEGORIAS:
        if a in (c, c + "s") or (len(a) >= 3 and c.startswith(a)):
            return c
    return None


# --------------------------------------------------------------------------
# Alta de tareas
# --------------------------------------------------------------------------

def _despachar_intencion(chat_id: int, data: dict, autor: str) -> bool:
    """Si el mensaje pedía algo que no es anotar, lo hace y devuelve True.

    Notita ya sabía mostrar, completar y borrar, pero sólo por comando. Nadie se
    acuerda de los comandos, así que también se entiende hablando normal.
    """
    intencion = data.get("intencion") or ("anotar" if data.get("es_tarea") else "charla")
    if intencion in ("anotar", "charla"):
        return False

    if intencion == "ver_pendientes":
        categoria = data.get("categoria_filtro") or None
        if categoria not in config.CATEGORIAS:
            categoria = None
        telegram.enviar_largo(chat_id, views.render_todo(chat_id, categoria))
    elif intencion == "ver_super":
        t, kb = views.render_super(chat_id)
        telegram.enviar_largo(chat_id, t, kb)
    elif intencion == "ver_algun_dia":
        telegram.enviar_largo(chat_id, views.render_algun_dia(chat_id))
    elif intencion == "ver_ayuda":
        telegram.enviar(chat_id, views.AYUDA)
    elif intencion in ("completar", "borrar"):
        _resolver_por_texto(chat_id, intencion, data.get("referencia") or "", autor)
    else:
        return False
    return True


def _resolver_por_texto(chat_id: int, accion: str, referencia: str, autor: str) -> None:
    """«ya limpié la heladera» / «borrá la del plomero»."""
    if not referencia.strip():
        telegram.enviar(chat_id, "¿Cuál de todas? Pasame /todo y decime 🤍")
        return

    candidatas = db.buscar(chat_id, referencia)
    if not candidatas:
        telegram.enviar(
            chat_id,
            f"No encontré nada parecido a «{telegram.escapar(referencia)}». Mirá /todo 👀")
        return

    # Si la mejor le saca clara ventaja a la segunda, no preguntamos.
    clara = len(candidatas) == 1 or candidatas[0][0] - candidatas[1][0] >= 0.25
    if not clara:
        opciones = candidatas[:4]
        prefijo = "h" if accion == "completar" else "b"
        teclado = [[{"text": r["texto"][:50], "callback_data": f"{prefijo}:{r['id']}"}]
                   for _, r in opciones]
        telegram.enviar(chat_id, "¿Cuál de estas?", teclado)
        return

    row = candidatas[0][1]
    if accion == "completar":
        nueva = db.marcar_hecha(row["id"], autor)
        texto = f"✅ <s>{views.texto_tarea(row)}</s>"
        if nueva is not None:
            texto += f"\n🔁 La próxima: {formato_humano(de_iso(nueva['due_date']), hoy())}"
    else:
        db.borrar(row["id"])
        texto = f"🗑️ <s>{views.texto_tarea(row)}</s>"
    telegram.enviar(chat_id, texto)


def _interpretar_y_guardar(chat_id: int, texto: str, autor: str,
                           contexto_previo: str | None = None) -> None:
    ref = hoy()
    data = llm.interpretar_mensaje(texto, autor, ref, contexto_previo) if llm.disponible() else None

    # Sin Gemini configurado, o si no contestó, lo interpretamos acá nomás.
    # Peor que el LLM, pero la tarea nunca se pierde.
    if data is None:
        data = heuristica.interpretar(texto, autor)

    if _despachar_intencion(chat_id, data, autor):
        return

    items = data.get("items") or []
    if not data.get("es_tarea") or not items:
        comentario = (data.get("comentario") or "").strip()
        if comentario:
            telegram.enviar(chat_id, telegram.escapar(comentario))
        return

    confirmaciones: list[str] = []
    sin_fecha: list[int] = []
    a_entregar: list[int] = []
    repetidas: list[str] = []
    for item in items:
        if item.get("necesita_aclaracion") and len(items) == 1:
            pregunta = (item.get("pregunta") or "").strip() or "¿Cómo era esto? Contame un poco más."
            db.set_pending(chat_id, "aclaracion", data={"texto": texto})
            telegram.enviar(chat_id, telegram.escapar(pregunta))
            return

        tipo = item.get("tipo") if item.get("tipo") in ("casa", "compras", "recado") else "casa"
        categoria = item.get("categoria") if item.get("categoria") in config.CATEGORIAS else "otros"
        responsable = item.get("responsable") if item.get("responsable") in config.PERSONAS else "ninguno"
        if tipo == "recado" and responsable in ("ninguno", ""):
            # Un recado sin destinatario no se puede entregar: es una tarea común.
            tipo = "casa"
        if tipo == "casa" and categoria == "compras":
            # El LLM a veces mezcla: una tarea de casa con categoría compras quedaba
            # con el carrito 🛒 en la lista, como si fuera del súper. Manda `tipo`.
            categoria = "otros"
        if tipo == "compras":
            categoria, due, rec, spec = "compras", None, None, DateSpec("algun_dia")
        elif tipo == "recado":
            # Sin fecha, se entrega en la próxima pasada de las 20:00.
            spec = llm.spec_de_item(item)
            if spec.kind in ("desconocida", "algun_dia"):
                spec = DateSpec("hoy")
            categoria, rec, due = "otros", None, resolve(spec, ref)
        else:
            spec = llm.spec_de_item(item)
            rec = llm.recurrencia_de_item(item)
            due = resolve(spec, ref)
            if due is None and rec is not None:
                # Recurrente sin fecha explícita: la primera ocurrencia es la más cercana.
                from .dates import proxima_ocurrencia

                due = proxima_ocurrencia(rec, ref - timedelta(days=1))

        texto_item = item.get("texto") or texto
        # Si ya hay algo casi igual pendiente, no se anota de nuevo: los dos
        # anotando "pagar expensas" el mismo día terminaban con dos tareas.
        if tipo != "recado":
            iguales = [r for _, r in db.buscar(chat_id, texto_item, minimo=0.85)
                       if r["tipo"] == tipo]
            if iguales:
                repetidas.append(views.linea_tarea(iguales[0], ref))
                continue

        task_id = db.crear_tarea(
            chat_id,
            texto_item,
            tipo=tipo,
            categoria=categoria,
            responsable=responsable,
            due=due,
            recurrencia=rec,
            created_by=autor,
        )
        confirmaciones.append(views.confirmacion(db.obtener(task_id), ref))
        if tipo == "casa" and due is None and spec.kind not in ("algun_dia",):
            sin_fecha.append(task_id)
        if tipo == "recado" and due == ref:
            a_entregar.append(task_id)   # los de hoy no esperan a las 20:00

    if repetidas and not confirmaciones:
        telegram.enviar(chat_id, "Eso ya estaba anotado 👀\n" + "\n".join(repetidas))
        return

    solo_recados = all(i.get("tipo") == "recado" for i in items)
    encabezado = "Anotado 🤍" if len(confirmaciones) == 1 else f"Anoté {len(confirmaciones)} cositas 🤍"
    if solo_recados:
        encabezado = "Dale, se lo digo 🤍" if len(confirmaciones) == 1 else "Dale, se los digo 🤍"
    # Si Gemini estaba configurado pero no contestó, avisamos que lo leímos a mano.
    if data.get("local") and llm.disponible():
        encabezado = "Lo anoté a mano, no me salió pensar 🤍 revisá que esté bien:"
    cuerpo = encabezado + "\n" + "\n".join(confirmaciones)
    if repetidas:
        cuerpo += "\n\n<i>Ya estaban anotadas:</i>\n" + "\n".join(repetidas)
    telegram.enviar(chat_id, cuerpo)

    for task_id in a_entregar:
        _entregar_recado(chat_id, task_id)
    for task_id in sin_fecha:
        _preguntar_fecha(chat_id, task_id)


def _entregar_recado(chat_id: int, task_id: int) -> None:
    """Un recado para hoy se dice en el momento: «avisale que llego en 10» no espera."""
    row = db.obtener(task_id)
    if row is None or row["estado"] != "pendiente":
        return
    if telegram.enviar(chat_id, views.render_recado(row)) is None:
        return   # no salió: queda pendiente y lo agarra la pasada de las 20:00
    db.actualizar(task_id, estado="hecha",
                  completed_at=ahora().isoformat(timespec="seconds"))


def _preguntar_fecha(chat_id: int, task_id: int) -> None:
    row = db.obtener(task_id)
    if row is None:
        return
    db.set_pending(chat_id, "fecha", task_id=task_id)
    telegram.enviar(
        chat_id,
        f"¿Para cuándo «{views.texto_tarea(row)}»?",
        views.teclado_para_cuando(task_id),
    )


def _responder_fecha_libre(chat_id: int, pendiente: dict, texto: str) -> bool:
    """Intenta leer el mensaje como la fecha que estábamos esperando.

    Devuelve True si lo consumió; False si no parecía una fecha (y entonces el
    mensaje sigue su camino normal, como tarea nueva).
    """
    task_id = pendiente["task_id"]
    row = db.obtener(task_id) if task_id else None
    if row is None or row["estado"] != "pendiente":
        db.clear_pending(chat_id)
        return False

    # A propósito no se usa el LLM ni `parse_natural` suelto: tienen que ser
    # mensajes que sean SÓLO una fecha. Si no, un «el martes me traen el colchón»
    # se consume como respuesta, le pone fecha a otra tarea y se pierde.
    spec = parse_solo_fecha(texto)
    if spec is None or spec.kind == "desconocida":
        return False

    db.clear_pending(chat_id)
    _fijar_fecha(chat_id, task_id, resolve(spec, hoy()))
    return True


def _fijar_fecha(chat_id: int, task_id: int, due: date | None, message_id: int | None = None) -> None:
    db.actualizar(task_id, due_date=due.isoformat() if due else None)
    row = db.obtener(task_id)
    texto = f"Listo 🤍\n{views.confirmacion(row, hoy())}"
    if message_id:
        telegram.editar(chat_id, message_id, texto)
    else:
        telegram.enviar(chat_id, texto)


# --------------------------------------------------------------------------
# Botones
# --------------------------------------------------------------------------

def _callback(cq: dict) -> None:
    data = cq.get("data") or ""
    cq_id = cq.get("id")
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    quien = config.persona_de_user_id(cq.get("from", {}).get("id", 0))

    if not _autorizado(chat_id):
        telegram.responder_callback(cq_id)
        return

    partes = data.split(":")
    accion = partes[0]
    # Estos dos no son de una tarea puntual, así que se atienden antes de buscarla.
    if accion == "vg":   # el mensaje que agrupa las vencidas de siempre
        _vencidas_juntas(chat_id, message_id, cq_id, partes[1] if len(partes) > 1 else "")
        return
    if accion == "ct":   # «compramos todo» en la lista del súper
        _compramos_todo(chat_id, message_id, cq_id, quien)
        return
    task_id = int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else 0
    extra = partes[2] if len(partes) > 2 else None
    row = db.obtener(task_id) if task_id else None

    if row is None:
        telegram.responder_callback(cq_id, "Esa ya no está")
        return
    # Los mensajes con botones viven para siempre en Telegram: alguien puede tocar
    # uno de hace tres semanas. Ninguna acción tiene sentido sobre algo ya resuelto
    # (antes el botón de fecha, "f", se colaba y le ponía vencimiento a una tarea hecha).
    if row["estado"] != "pendiente":
        telegram.responder_callback(cq_id, "Ya estaba resuelta")
        telegram.editar(chat_id, message_id, f"✅ <s>{views.texto_tarea(row)}</s>")
        return

    if accion == "h":
        _marcar_hecha(chat_id, message_id, cq_id, row, quien)
    elif accion == "c":
        db.marcar_hecha(task_id, quien)
        telegram.responder_callback(cq_id, "Tachado 🛒")
        t, kb = views.render_super(chat_id)
        telegram.editar(chat_id, message_id, t, kb)

    elif accion == "b":
        db.borrar(task_id)
        telegram.responder_callback(cq_id, "Borrada")
        telegram.editar(chat_id, message_id, f"🗑️ <s>{views.texto_tarea(row)}</s>")
    elif accion == "p":
        _posponer(chat_id, message_id, cq_id, row, extra)
    elif accion == "f":
        _respuesta_para_cuando(chat_id, message_id, cq_id, row, extra)
    else:
        telegram.responder_callback(cq_id)


def _vencidas_juntas(chat_id: int, message_id: int, cq_id: str, opcion: str) -> None:
    """Botones del mensaje agrupado. Se recalcula qué está vencido al tocarlo."""
    from .reminders import UMBRAL_CANSANCIO

    ref = hoy()
    rows = [r for r in db.vencen_hasta(chat_id, ref)
            if de_iso(r["due_date"]) and de_iso(r["due_date"]) < ref
            and (r["recordada_veces"] or 0) >= UMBRAL_CANSANCIO]
    if not rows:
        telegram.responder_callback(cq_id, "Ya no quedan")
        telegram.editar(chat_id, message_id, "✨ Ya no queda ninguna vencida")
        return

    if opcion == "s":
        nueva = ref + timedelta(days=7)
        for row in rows:
            db.posponer(row["id"], nueva)
            db.actualizar(row["id"], recordada_veces=0)
        telegram.responder_callback(cq_id, "Dale, la semana que viene")
        telegram.editar(
            chat_id, message_id,
            f"⏰ Listo: {len(rows)} para {formato_humano(nueva, ref)}.\n"
            f"<i>Si alguna ya no va, borrala con «borrá la de…».</i>")
        return

    if opcion == "u":
        telegram.responder_callback(cq_id)
        telegram.editar(chat_id, message_id, "📋 Ahí van, de a una:")
        for row in rows:
            telegram.enviar(chat_id, f"{views.emoji(row)} <b>{views.texto_tarea(row)}</b>",
                            views.teclado_recordatorio(row["id"], True))
        return
    telegram.responder_callback(cq_id)


def _compramos_todo(chat_id: int, message_id: int, cq_id: str, quien: str) -> None:
    """Tachar 15 cosas de a una, volviendo del súper, es un castigo."""
    compras = db.pendientes(chat_id, tipo="compras")
    if not compras:
        telegram.responder_callback(cq_id, "Ya no queda nada")
        telegram.editar(chat_id, message_id, "La lista del super está vacía 🛒")
        return
    for row in compras:
        db.marcar_hecha(row["id"], quien)
    telegram.responder_callback(cq_id, "¡Listo! 🛒")
    telegram.editar(
        chat_id, message_id,
        f"✅ Tachadas las {len(compras)}:\n"
        + "\n".join(f"<s>{views.texto_tarea(r)}</s>" for r in compras[:10]))


def _marcar_hecha(chat_id: int, message_id: int, cq_id: str, row, quien: str) -> None:
    nueva = db.marcar_hecha(row["id"], quien)
    telegram.responder_callback(cq_id, "¡Hecho! 🎉")
    final = f"✅ <s>{views.texto_tarea(row)}</s>"
    if quien != "ninguno":
        final += f" — {telegram.escapar(config.NOMBRES.get(quien, quien))}"
    if nueva is not None:
        final += f"\n🔁 La próxima: {formato_humano(de_iso(nueva['due_date']), hoy())}"
    telegram.editar(chat_id, message_id, final)


def _posponer(chat_id: int, message_id: int, cq_id: str, row, extra: str | None) -> None:
    task_id = row["id"]
    if extra is None:
        telegram.responder_callback(cq_id)
        telegram.editar(
            chat_id, message_id,
            f"⏰ ¿Para cuándo movemos «{views.texto_tarea(row)}»?",
            views.teclado_posponer(task_id),
        )
        return

    ref = hoy()
    if extra == "o":
        db.set_pending(chat_id, "fecha", task_id=task_id)
        telegram.responder_callback(cq_id)
        telegram.editar(chat_id, message_id,
                        f"Decime para cuándo movemos «{views.texto_tarea(row)}» 🗓️")
        return

    nueva = {
        "m": ref + timedelta(days=1),
        "f": proximo_fin_de_semana(ref),
        "s": resolve(DateSpec("semana_que_viene"), ref),
    }.get(extra)
    if nueva is None:
        telegram.responder_callback(cq_id)
        return

    veces = db.posponer(task_id, nueva)
    telegram.responder_callback(cq_id, "Dale, después")
    texto = f"⏰ «{views.texto_tarea(row)}» pasa para {formato_humano(nueva, ref)}"
    if veces >= 3:
        texto += "\n" + random.choice(CHISTES_POSPONER).format(n=veces)
    telegram.editar(chat_id, message_id, texto)


def _respuesta_para_cuando(chat_id: int, message_id: int, cq_id: str, row, extra: str | None) -> None:
    task_id = row["id"]
    ref = hoy()
    if extra == "o":
        db.set_pending(chat_id, "fecha", task_id=task_id)
        telegram.responder_callback(cq_id)
        telegram.editar(chat_id, message_id,
                        f"Decime la fecha de «{views.texto_tarea(row)}» 🗓️")
        return

    mapa = {
        "h": DateSpec("hoy"),
        "m": DateSpec("manana"),
        "e": DateSpec("esta_semana"),
        "v": DateSpec("semana_que_viene"),
        "a": DateSpec("algun_dia"),
    }
    spec = mapa.get(extra or "")
    if spec is None:
        telegram.responder_callback(cq_id)
        return
    db.clear_pending(chat_id)
    telegram.responder_callback(cq_id, "Anotado")
    _fijar_fecha(chat_id, task_id, resolve(spec, ref), message_id=message_id)
