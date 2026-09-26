"""Qué hace Notita con cada update de Telegram."""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta

from . import config, db, llm, telegram, views
from .dates import (
    DateSpec,
    de_iso,
    formato_humano,
    hoy,
    parse_natural,
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
    if "callback_query" in update:
        _callback(update["callback_query"])
        return
    msg = update.get("message") or update.get("edited_message")
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
        telegram.enviar(chat_id, views.render_todo(chat_id, categoria))
    elif cmd in ("/algundia", "/algun_dia"):
        telegram.enviar(chat_id, views.render_algun_dia(chat_id))
    elif cmd in ("/super", "/compras"):
        t, kb = views.render_super(chat_id)
        telegram.enviar(chat_id, t, kb)
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

def _interpretar_y_guardar(chat_id: int, texto: str, autor: str,
                           contexto_previo: str | None = None) -> None:
    ref = hoy()
    data = llm.interpretar_mensaje(texto, autor, ref, contexto_previo)

    if data is None:  # el LLM no contestó: guardamos crudo y preguntamos la fecha
        task_id = db.crear_tarea(chat_id, texto, created_by=autor)
        telegram.enviar(chat_id, "Se me trabó la cabeza un segundo, pero lo anoté igual 🤍")
        _preguntar_fecha(chat_id, task_id)
        return

    items = data.get("items") or []
    if not data.get("es_tarea") or not items:
        comentario = (data.get("comentario") or "").strip()
        if comentario:
            telegram.enviar(chat_id, telegram.escapar(comentario))
        return

    confirmaciones: list[str] = []
    sin_fecha: list[int] = []
    for item in items:
        if item.get("necesita_aclaracion") and len(items) == 1:
            pregunta = (item.get("pregunta") or "").strip() or "¿Cómo era esto? Contame un poco más."
            db.set_pending(chat_id, "aclaracion", data={"texto": texto})
            telegram.enviar(chat_id, telegram.escapar(pregunta))
            return

        tipo = item.get("tipo") if item.get("tipo") in ("casa", "compras") else "casa"
        categoria = item.get("categoria") if item.get("categoria") in config.CATEGORIAS else "otros"
        responsable = item.get("responsable") if item.get("responsable") in config.PERSONAS else "ninguno"
        if tipo == "compras":
            categoria, due, rec, spec = "compras", None, None, DateSpec("algun_dia")
        else:
            spec = llm.spec_de_item(item)
            rec = llm.recurrencia_de_item(item)
            due = resolve(spec, ref)
            if due is None and rec is not None:
                # Recurrente sin fecha explícita: la primera ocurrencia es la más cercana.
                from .dates import proxima_ocurrencia

                due = proxima_ocurrencia(rec, ref - timedelta(days=1))

        task_id = db.crear_tarea(
            chat_id,
            item.get("texto") or texto,
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

    encabezado = "Anotado 🤍" if len(confirmaciones) == 1 else f"Anoté {len(confirmaciones)} cositas 🤍"
    telegram.enviar(chat_id, encabezado + "\n" + "\n".join(confirmaciones))

    for task_id in sin_fecha:
        _preguntar_fecha(chat_id, task_id)


def _preguntar_fecha(chat_id: int, task_id: int) -> None:
    row = db.obtener(task_id)
    if row is None:
        return
    db.set_pending(chat_id, "fecha", task_id=task_id)
    telegram.enviar(
        chat_id,
        f"¿Para cuándo «{telegram.escapar(row['texto'])}»?",
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

    ref = hoy()
    spec = parse_natural(texto)
    if spec is None and len(texto) <= 60:
        spec = llm.interpretar_fecha(texto, ref)
    if spec is None or spec.kind == "desconocida":
        db.clear_pending(chat_id)
        return False

    db.clear_pending(chat_id)
    _fijar_fecha(chat_id, task_id, resolve(spec, ref))
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
    task_id = int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else 0
    extra = partes[2] if len(partes) > 2 else None
    row = db.obtener(task_id) if task_id else None

    if row is None:
        telegram.responder_callback(cq_id, "Esa ya no está")
        return
    if row["estado"] != "pendiente" and accion in ("h", "c", "p", "b"):
        telegram.responder_callback(cq_id, "Ya estaba resuelta")
        telegram.editar(chat_id, message_id, f"✅ <s>{telegram.escapar(row['texto'])}</s>")
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
        telegram.editar(chat_id, message_id, f"🗑️ <s>{telegram.escapar(row['texto'])}</s>")
    elif accion == "p":
        _posponer(chat_id, message_id, cq_id, row, extra)
    elif accion == "f":
        _respuesta_para_cuando(chat_id, message_id, cq_id, row, extra)
    else:
        telegram.responder_callback(cq_id)


def _marcar_hecha(chat_id: int, message_id: int, cq_id: str, row, quien: str) -> None:
    nueva = db.marcar_hecha(row["id"], quien)
    telegram.responder_callback(cq_id, "¡Hecho! 🎉")
    final = f"✅ <s>{telegram.escapar(row['texto'])}</s>"
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
            f"⏰ ¿Para cuándo movemos «{telegram.escapar(row['texto'])}»?",
            views.teclado_posponer(task_id),
        )
        return

    ref = hoy()
    if extra == "o":
        db.set_pending(chat_id, "fecha", task_id=task_id)
        telegram.responder_callback(cq_id)
        telegram.editar(chat_id, message_id,
                        f"Decime para cuándo movemos «{telegram.escapar(row['texto'])}» 🗓️")
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
    texto = f"⏰ «{telegram.escapar(row['texto'])}» pasa para {formato_humano(nueva, ref)}"
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
                        f"Decime la fecha de «{telegram.escapar(row['texto'])}» 🗓️")
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
