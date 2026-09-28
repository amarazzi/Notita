"""Qué hace Notita con cada update.

El orden de v2 es siempre el mismo: **leer → escribir en una transacción → leer de
nuevo → responder**. Las confirmaciones salen de la base, nunca de lo que devolvió el
LLM: en v1 eso hizo que anunciara una cómoda "al súper" que no estaba guardada en
ningún lado.

Y el texto libre sólo CREA. Cualquier modificación se propone con botones.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import (
    calendario,
    cb,
    config,
    db,
    heuristica,
    llm,
    menus,
    parte,
    propuestas,
    tablero,
    telegram,
    views,
)
from .dates import (
    DateSpec,
    ahora,
    de_iso,
    fecha_imposible,
    hoy,
    limpiar_item_de_super,
    normalizar,
    parse_solo_fecha,
    proxima_ocurrencia,
    proximo_dia_semana,
    resolve,
    resolver_momento,
)

log = logging.getLogger("notita.handlers")

FUERA_DE_CASA = (
    "¡Hola! 🧲 Yo funciono en el grupo de la casa: escribime ahí y te anoto todo.\n"
    "<i>Si querés uno para tu casa: github.com/amarazzi/Notita</i>")

SIN_TEXTO = "Todavía no entiendo audios ni fotos 🙈 Escribímelo y lo anoto."

# Avisos de Telegram, no mensajes de nadie: «fijó un mensaje», «entró al grupo»…
# Llegan sin texto, así que antes se les contestaba «no entiendo audios ni fotos».
SERVICIO = (
    "pinned_message", "new_chat_members", "left_chat_member", "new_chat_title",
    "new_chat_photo", "delete_chat_photo", "group_chat_created",
    "supergroup_chat_created", "channel_chat_created", "message_auto_delete_timer_changed",
    "migrate_from_chat_id", "video_chat_started", "video_chat_ended",
    "video_chat_participants_invited", "successful_payment",
)

# Comandos de v1 que ya no existen. Decir dónde está eso ahora es más útil que
# «no lo tengo», sobre todo porque quedaron en el historial del grupo.
JUBILADOS = {
    "algundia": "«Algún día» ahora es una sección del tablero 📋 Tocá /tablero y "
                "después el botón <b>Algún día</b>.",
    "recordatorios": "Eso ahora es el parte de la noche: /parte lo manda al toque.",
    "probar": "Eso ahora es /parte 🤍",
    "resumen": "El resumen semanal ya no existe: ahora hay un parte todos los días. "
               "Mirá /parte.",
}

# Entre estas horas, "mañana" es el día calendario actual: a las 00:40 nadie piensa
# en el día siguiente.
MADRUGADA_HASTA = 5


def handle_update(update: dict) -> None:
    db.init_db()
    telegram.vaciar_cola()
    _comandos_al_dia()

    clave = _clave_de(update)
    if not db.update_nuevo(clave):
        log.info("Update repetido, lo ignoro: %s", clave)
        return

    if "callback_query" in update:
        _callback(update["callback_query"])
        return
    if "edited_message" in update and "message" not in update:
        log.info("Mensaje editado, lo ignoro")
        return
    msg = update.get("message")
    if msg:
        _mensaje(msg)


def _clave_de(update: dict) -> str | None:
    """La clave de idempotencia: el update_id, o el id del callback."""
    if "callback_query" in update:
        return f"cb:{update['callback_query'].get('id')}"
    return str(update.get("update_id")) if update.get("update_id") else None


# --------------------------------------------------------------------------
# Mensajes
# --------------------------------------------------------------------------

def _autorizado(chat_id: int) -> bool:
    return config.ALLOWED_CHAT_ID and chat_id == config.ALLOWED_CHAT_ID


def _mensaje(msg: dict) -> None:
    chat_id = (msg.get("chat") or {}).get("id")
    texto = (msg.get("text") or msg.get("caption") or "").strip()
    autor = config.persona_de_user_id((msg.get("from") or {}).get("id"))

    if texto.startswith("/chatid"):
        telegram.enviar(chat_id, f"El chat_id de acá es <code>{chat_id}</code>")
        return

    nuevo = msg.get("migrate_to_chat_id")
    if nuevo and _autorizado(chat_id):
        _mudarse(chat_id, nuevo)
        return

    if not _autorizado(chat_id):
        if (msg.get("chat") or {}).get("type") == "private":
            telegram.enviar(chat_id, FUERA_DE_CASA)
        log.info("Ignorando chat %s", chat_id)
        return

    _bienvenida_si_hace_falta(chat_id)

    servicio = next((c for c in SERVICIO if c in msg), None)
    if servicio:
        _limpiar_aviso_de_servicio(chat_id, msg, servicio)
        return

    if not texto:
        telegram.enviar(chat_id, SIN_TEXTO, silencioso=True)
        return

    # Respuesta a un force_reply nuestro: se aplica a ESE ítem y nada más. Sólo se
    # toma si es una respuesta de verdad (v1 se comía el mensaje siguiente cualquiera).
    if _respuesta_a_notita(chat_id, msg, texto, autor):
        return

    if texto.startswith("/") or normalizar(texto) in ("tablero", "📋"):
        _comando(chat_id, texto, autor)
        return

    _interpretar(chat_id, texto, autor, msg.get("message_id"))


def _limpiar_aviso_de_servicio(chat_id: int, msg: dict, tipo: str) -> None:
    """No se contesta, y si el aviso lo generamos nosotros, se borra del chat.

    Fijar el tablero mete un «Notita fijó un mensaje» en la conversación. Es ruido
    que pusimos nosotros, así que lo sacamos.
    """
    log.info("Aviso de servicio (%s), no contesto", tipo)
    nuestro = (msg.get("from") or {}).get("is_bot")
    if tipo == "pinned_message" and nuestro:
        telegram.borrar(chat_id, msg.get("message_id"))


def _respuesta_a_notita(chat_id: int, msg: dict, texto: str, autor: str) -> bool:
    respondido = (msg.get("reply_to_message") or {}).get("from") or {}
    if not respondido.get("is_bot"):
        return False
    pendiente = db.get_pending(chat_id)
    if not pendiente or pendiente["kind"] not in ("renombrar", "fecha_item"):
        return False
    row = db.obtener(pendiente["task_id"] or 0)
    db.clear_pending(chat_id)
    if row is None or row["estado"] != "pendiente":
        telegram.enviar(chat_id, "Eso ya no está 👀", silencioso=True)
        return True

    if pendiente["kind"] == "renombrar":
        nuevo = " ".join(texto.split())[:200]
        db.actualizar(row["id"], texto=nuevo)
        db.vencer_deshacer_de([row["id"]])
        telegram.enviar(chat_id, f"✏️ Ahora es <b>{telegram.escapar(nuevo)}</b>",
                        silencioso=True)
    else:
        spec = parse_solo_fecha(texto) or llm.interpretar_fecha(texto)
        fecha, hora = resolver_momento(spec)
        if spec is None or (fecha is None and spec.kind != "algun_dia"):
            telegram.enviar(chat_id, "No le agarré la fecha 🤔 Probá «el lunes» o «3/10»",
                            silencioso=True)
            return True
        db.actualizar(row["id"], due_date=fecha.isoformat() if fecha else None,
                      due_hora=hora)
        db.vencer_deshacer_de([row["id"]])
        fila = db.obtener(row["id"])
        telegram.enviar(chat_id, f"📅 {views.linea(fila)}", silencioso=True)
    tablero.actualizar(chat_id)
    return True


def _mudarse(viejo: int, nuevo: int) -> None:
    """Telegram convirtió el grupo en supergrupo y le cambió el número.

    Pasa al hacer admin a alguien, por ejemplo. Se muda todo solo: si sólo se cambiara
    el `.env`, las tareas quedarían guardadas con el número viejo y el bot arrancaría
    vacío.
    """
    log.error("El grupo cambió de id: %s -> %s", viejo, nuevo)
    movidas = db.migrar_chat(viejo, nuevo)
    cuantas = movidas.get("tasks", 0)
    config.ALLOWED_CHAT_ID = nuevo          # para no quedar mudo hasta el reload
    guardado = config.guardar_chat_id(nuevo)

    partes = ["📦 Telegram convirtió el grupo y le cambió el número. Ya me mudé."]
    if cuantas:
        partes.append(f"Traje las <b>{cuantas}</b> cosas que teníamos anotadas 🤍")
    if not guardado:
        partes.append(f"<b>Ojo:</b> poné <code>ALLOWED_CHAT_ID={nuevo}</code> en el "
                      f"<code>.env</code> y recargá, o cuando reinicie me pierdo.")
    telegram.enviar(nuevo, "\n".join(partes))
    tablero.publicar(nuevo)


def _comando(chat_id: int, texto: str, autor: str) -> None:
    partes = texto.split(maxsplit=1)
    comando = partes[0].lower().split("@")[0].lstrip("/")
    if comando in ("tablero", "todo", "tareas", "start"):
        tablero.publicar(chat_id)
    elif comando in ("super", "compras"):
        menus.abrir_super(chat_id)
    elif comando in ("ayuda", "help"):
        telegram.enviar(chat_id, views.ayuda())
    elif comando == "parte":
        parte.correr(chat_id, forzar=True)
    elif comando in JUBILADOS:
        telegram.enviar(chat_id, JUBILADOS[comando], silencioso=True)
    else:
        telegram.enviar(chat_id, "Ese comando no lo tengo. Probá /ayuda 🤍",
                        silencioso=True)


# --------------------------------------------------------------------------
# Capturar: el texto crea
# --------------------------------------------------------------------------

def _interpretar(chat_id: int, texto: str, autor: str, mensaje_id: int | None) -> None:
    ref = hoy()
    data = llm.interpretar_mensaje(texto, autor, ref) if llm.disponible() else None
    if data is None:
        data = heuristica.interpretar(texto, autor)
    intencion = data.get("intencion") or "charla"

    if intencion == "ver":
        que = data.get("ver_que") or "tablero"
        if que == "super":
            menus.abrir_super(chat_id)
        elif que == "ayuda":
            telegram.enviar(chat_id, views.ayuda(), silencioso=True)
        else:
            tablero.publicar(chat_id)
        return
    if intencion == "recado":
        _recado(chat_id, data, autor)
        return
    if intencion == "modificar":
        _proponer(chat_id, data, autor, ref)
        return
    if intencion == "crear" and (data.get("items") or []):
        _crear(chat_id, data["items"], autor, mensaje_id, ref)
        return

    comentario = (data.get("comentario") or "").strip()
    if comentario:
        telegram.enviar(chat_id, telegram.escapar(comentario), silencioso=True)


def _crear(chat_id: int, items: list, autor: str, mensaje_id: int | None,
           ref: date) -> None:
    """Guarda todo y después CONFIRMA LEYENDO DE LA BASE."""
    de_madrugada = ahora().hour < MADRUGADA_HASTA
    creados: list[int] = []
    repetidos: list[str] = []
    sin_fecha_posible: list[str] = []

    try:
        for item in items:
            titulo = _titulo_de(item)
            if not titulo:
                continue
            tipo = "compras" if item.get("tipo") == "super" else "casa"
            if tipo == "compras":
                titulo = limpiar_item_de_super(titulo)

            ya = db.pendiente_igual(chat_id, titulo, tipo)
            if ya is not None:
                repetidos.append(views.titulo(ya))
                continue

            spec = llm.spec_de_item(item)
            if de_madrugada and spec.kind == "manana":
                # A las 00:40 «mañana» es hoy. Se guarda así y se ofrece corregir.
                spec = DateSpec("hoy", hora=spec.hora, minuto=spec.minuto)
            rec = llm.recurrencia_de_item(item)
            imposible = fecha_imposible(spec)
            if imposible:
                sin_fecha_posible.append(titulo)
                fecha, hora = None, None
            else:
                fecha, hora = resolver_momento(spec)
            if tipo == "compras":
                fecha, hora, rec = None, None, None
            elif fecha is None and rec is not None:
                fecha = _primera_ocurrencia(rec, ref)

            creados.append(db.crear_tarea(
                chat_id, titulo, tipo=tipo,
                categoria="compras" if tipo == "compras" else _categoria(item),
                responsable=_responsable(item), due=fecha, recurrencia=rec,
                created_by=autor, hora=hora, mensaje_origen_id=mensaje_id))
    except Exception:
        log.exception("No pude guardar los items")
        telegram.enviar(chat_id, "No pude guardar eso 😕 Probá de nuevo en un ratito")
        return

    # Acá empieza la verdad: se relee lo que quedó guardado.
    filas = [db.obtener(i) for i in creados]
    filas = [f for f in filas if f is not None]
    if not filas and not repetidos:
        telegram.enviar(chat_id, "No pude guardar eso 😕 Probá de nuevo en un ratito")
        return

    texto, teclado = _confirmacion(chat_id, filas, repetidos, sin_fecha_posible,
                                   de_madrugada, ref)
    telegram.enviar(chat_id, texto, teclado, silencioso=True)
    tablero.actualizar(chat_id)


def _confirmacion(chat_id: int, filas: list, repetidos: list[str],
                  imposibles: list[str], de_madrugada: bool,
                  ref: date) -> tuple[str, list]:
    lineas = []
    if filas:
        fechas = {f["due_date"] for f in filas if f["tipo"] == "casa"}
        una_sola = len(filas) > 1 and len(fechas) == 1 and None not in fechas
        cuantas = "Anoté 1 cosita 🤍" if len(filas) == 1 else f"Anoté {len(filas)} cositas 🤍"
        if una_sola:
            comun = views.cuando(de_iso(filas[0]["due_date"]), None, ref)
            cuantas = f"Anoté {len(filas)} cositas para {comun} 🤍"
        if de_madrugada:
            cuantas += " 🌙"
        lineas.append(cuantas)
        for f in filas:
            lineas.append(views.linea(f, ref, con_fecha=not una_sola))
    for repetido in repetidos:
        lineas.append(f"👀 Ya estaba: {telegram.escapar(repetido)}")
    for imposible in imposibles:
        lineas.append(f"📅 Esa fecha no existe, dejé «{telegram.escapar(imposible)}» "
                      f"para algún día")

    ids = [f["id"] for f in filas]
    teclado: list[list[dict]] = []
    if ids:
        deshacer_id = db.guardar_deshacer(chat_id, ids)
        primera = [{"text": "✏️ Corregir", "callback_data": cb.armar("fix", deshacer_id)},
                   {"text": "↩️ Deshacer", "callback_data": cb.armar("u", deshacer_id)}]
        if de_madrugada:
            manana = ref + timedelta(days=1)
            primera = [{"text": f"Era el {views.dia_corto(manana)}",
                        "callback_data": cb.armar("mad", deshacer_id)},
                       {"text": "↩️ Deshacer", "callback_data": cb.armar("u", deshacer_id)}]
        teclado.append(primera)
        con_hora = [f for f in filas if f["due_hora"]]
        for f in con_hora[:2]:
            boton = calendario.boton(f)
            if boton:
                teclado.append([boton])
        if imposibles and len(filas) == 1:
            teclado.append([{"text": "📅 Elegir día",
                             "callback_data": cb.armar("d+", filas[0]["id"])}])
    return "\n".join(lineas), teclado


def _titulo_de(item: dict) -> str:
    return " ".join((item.get("titulo") or item.get("texto") or "").split())[:200]


def _categoria(item: dict) -> str:
    cat = item.get("categoria")
    return cat if cat in config.CATEGORIAS else "otros"


def _responsable(item: dict) -> str:
    quien = item.get("responsable")
    return quien if quien in config.PERSONAS else "ninguno"


def _primera_ocurrencia(rec, ref: date) -> date:
    """Una recurrente sin fecha arranca hoy, salvo que tenga día propio."""
    if rec.kind == "semanal" and rec.weekday is not None and 0 <= rec.weekday <= 6:
        return ref if ref.weekday() == rec.weekday else proximo_dia_semana(ref, rec.weekday)
    if rec.kind == "mensual" and rec.monthday:
        return proxima_ocurrencia(rec, ref - timedelta(days=1)) or ref
    return ref


def _recado(chat_id: int, data: dict, autor: str) -> None:
    """Un recado es para ahora: se dice y listo. No se guarda nada."""
    para = data.get("recado_para")
    mensaje = " ".join((data.get("recado_mensaje") or "").split())
    if para not in config.PERSONAS or para in ("ninguno", "ambos") or not mensaje:
        comentario = (data.get("comentario") or "").strip()
        telegram.enviar(chat_id, telegram.escapar(comentario) or "¿A quién se lo digo? 🤔",
                        silencioso=True)
        return
    de = config.NOMBRES.get(autor, "alguien")
    if para == autor:
        telegram.enviar(chat_id, f"🔔 {telegram.mencion(para)}, te acordás de:\n"
                                 f"«{telegram.escapar(mensaje)}»")
        return
    telegram.enviar(chat_id, f"💌 {telegram.mencion(para)}, {telegram.escapar(de)} "
                             f"te manda a decir:\n«{telegram.escapar(mensaje)}»")


# --------------------------------------------------------------------------
# Proponer: el texto pide, el botón ejecuta
# --------------------------------------------------------------------------

def _proponer(chat_id: int, data: dict, autor: str, ref: date) -> None:
    accion = data.get("accion") or "ninguna"
    if accion == "vaciar_super":
        rows = db.pendientes(chat_id, tipo="compras")
        if not rows:
            telegram.enviar(chat_id, "La lista del súper ya está vacía 🛒", silencioso=True)
            return
        propuestas.ofrecer(chat_id, "completar", rows, ref=ref)
        return
    if accion == "pausar":
        _proponer_pausa(chat_id, data, ref)
        return
    if accion not in propuestas.ACCIONES:
        comentario = (data.get("comentario") or "").strip()
        telegram.enviar(chat_id, telegram.escapar(comentario) or
                        "No me quedó claro qué querés cambiar 🤔 Mirá el tablero",
                        silencioso=True)
        return

    candidatos = _resolver_candidatos(chat_id, data, ref)
    if not candidatos:
        referencias = ", ".join(f"«{telegram.escapar(r)}»"
                                for r in (data.get("referencias") or [])) or "eso"
        telegram.enviar(chat_id, f"No encontré nada parecido a {referencias} 👀",
                        [[{"text": "📋 Ver el tablero", "callback_data": cb.armar("tab")}]],
                        silencioso=True)
        return

    extra: dict = {}
    if accion == "mover":
        spec = llm.spec_de_item(data, prefijo="destino_fecha")
        fecha, hora = resolver_momento(spec)
        if fecha is None and spec.kind != "algun_dia":
            telegram.enviar(chat_id, "¿Para cuándo las querés? Decime el día 🗓️",
                            silencioso=True)
            return
        extra = {"fecha": fecha.isoformat() if fecha else None, "hora": hora}
    elif accion == "renombrar":
        nuevo = " ".join((data.get("destino_texto") or "").split())
        if not nuevo:
            telegram.enviar(chat_id, "¿Cómo le pongo? 🤔", silencioso=True)
            return
        extra = {"texto": nuevo}
    elif accion == "reasignar":
        quien = data.get("destino_responsable")
        if quien not in config.PERSONAS:
            quien = autor
        extra = {"responsable": quien}

    propuestas.ofrecer(chat_id, accion, candidatos, extra, ref)


def _resolver_candidatos(chat_id: int, data: dict, ref: date) -> list:
    """Los ids los elige el servidor, no el LLM."""
    conjunto = data.get("conjunto") or "ninguno"
    if conjunto and conjunto != "ninguno":
        return _por_conjunto(chat_id, conjunto, ref)

    vistos, candidatos = set(), []
    for referencia in (data.get("referencias") or []):
        for _, row in db.buscar(chat_id, referencia):
            if row["id"] not in vistos:
                vistos.add(row["id"])
                candidatos.append(row)
                break
    return candidatos


def _por_conjunto(chat_id: int, conjunto: str, ref: date) -> list:
    casa = db.pendientes(chat_id, tipo="casa")
    manana = ref + timedelta(days=1)
    secciones = tablero.repartir(casa, ref)
    if conjunto == "super":
        return db.pendientes(chat_id, tipo="compras")
    if conjunto == "todo":
        return casa + db.pendientes(chat_id, tipo="compras")
    if conjunto == "manana":
        return [r for r in casa if de_iso(r["due_date"]) == manana]
    return secciones.get(conjunto, [])


def _proponer_pausa(chat_id: int, data: dict, ref: date) -> None:
    spec = llm.spec_de_item(data, prefijo="destino_fecha")
    hasta = resolve(spec, ref) or (ref + timedelta(days=7))
    propuesta_id = db.guardar_propuesta(chat_id, {"accion": "pausa",
                                                  "hasta": hasta.isoformat()})
    enviado = telegram.enviar(
        chat_id,
        f"¿Me tomo vacaciones hasta el {views.dia_corto(hasta, con_mes=True)}? "
        f"No mando el parte hasta entonces 😴",
        [[{"text": "⏸ Sí, pausá", "callback_data": cb.armar("pz", propuesta_id)},
          {"text": "No", "callback_data": cb.armar("p", propuesta_id, "no")}]],
        silencioso=True)
    if enviado:
        db.anotar_temporal(chat_id, enviado["message_id"], "propuesta", 30)


# --------------------------------------------------------------------------
# Callbacks: acá se ejecuta
# --------------------------------------------------------------------------

def _callback(cq: dict) -> None:
    chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
    message_id = (cq.get("message") or {}).get("message_id")
    cq_id = cq.get("id")
    quien = config.persona_de_user_id((cq.get("from") or {}).get("id"))
    if not _autorizado(chat_id):
        telegram.responder_callback(cq_id, "Esto no es para mí")
        return

    leido = cb.leer(cq.get("data") or "")
    if leido is None:
        telegram.responder_callback(cq_id, "Ese botón es de la versión anterior 🙈")
        return
    accion, args = leido
    item_id = cb.entero(args)
    ref = hoy()

    if accion == "c":
        menus.cerrar(chat_id, message_id)
        telegram.responder_callback(cq_id)
        return
    if accion == "tab":
        telegram.responder_callback(cq_id)
        tablero.publicar(chat_id)
        return
    if accion == "sec":
        telegram.responder_callback(cq_id)
        menus.abrir_seccion(chat_id, args[0] if args else "hoy", ref)
        return
    if accion == "elegir":
        telegram.responder_callback(cq_id)
        menus.abrir_elegir(chat_id, ref)
        return
    if accion == "sup":
        telegram.responder_callback(cq_id)
        menus.abrir_super(chat_id, message_id if _es_temporal(chat_id, message_id) else None)
        return
    if accion == "supx":
        _super_todo(chat_id, message_id, cq_id, quien, args)
        return
    if accion == "p":
        _ejecutar_propuesta(chat_id, message_id, cq_id, quien, args, ref)
        return
    if accion == "pz":
        _confirmar_pausa(chat_id, message_id, cq_id, args)
        return
    if accion == "u":
        _deshacer(chat_id, message_id, cq_id, args)
        return
    if accion == "fix":
        _corregir(chat_id, message_id, cq_id, args)
        return
    if accion == "mad":
        _era_manana(chat_id, message_id, cq_id, args, ref)
        return
    if accion == "pt":
        movidas = parte.pasar_todas_a_manana(chat_id, ref)
        telegram.responder_callback(cq_id, f"Listo, {movidas} para mañana")
        telegram.editar(chat_id, message_id,
                        f"⏰ Pasé <b>{movidas}</b> para mañana, {views.dia_corto(ref + timedelta(days=1))}")
        tablero.actualizar(chat_id)
        return
    if accion == "ics":
        _mandar_ics(chat_id, cq_id, item_id)
        return

    # De acá para abajo, todo es sobre un ítem puntual.
    row = db.obtener(item_id) if item_id else None
    if row is None:
        telegram.responder_callback(cq_id, "Eso ya no está ✨")
        tablero.actualizar(chat_id)
        return
    if row["estado"] != "pendiente":
        telegram.responder_callback(cq_id, _quien_lo_hizo(row))
        _refrescar(chat_id, message_id)
        return

    if accion == "ok":
        _completar(chat_id, message_id, cq_id, row, quien, ref)
    elif accion == "m":
        telegram.responder_callback(cq_id)
        # Si vino de la lista de «⋯ Cambiar algo», el menú la reemplaza en el lugar.
        desde = message_id if _es_temporal(chat_id, message_id) else None
        menus.abrir(chat_id, row["id"], desde, ref)
    elif accion == "d":
        _mover(chat_id, message_id, cq_id, row, args[1] if len(args) > 1 else "m", ref)
    elif accion == "d+":
        telegram.responder_callback(cq_id)
        menus.submenu_dia(chat_id, message_id, row["id"], ref)
    elif accion == "df":
        _pedir_texto(chat_id, cq_id, row, "fecha_item",
                     f"¿Para cuándo «{views.titulo_html(row)}»? Escribime el día 🗓️")
    elif accion == "q+":
        telegram.responder_callback(cq_id)
        menus.submenu_quien(chat_id, message_id, row["id"], ref)
    elif accion == "q":
        quien_nuevo = args[1] if len(args) > 1 else "ninguno"
        db.actualizar(row["id"], responsable=quien_nuevo if quien_nuevo in config.PERSONAS else "ninguno")
        db.vencer_deshacer_de([row["id"]])
        telegram.responder_callback(cq_id, "Listo")
        menus.abrir(chat_id, row["id"], message_id, ref)
        tablero.actualizar(chat_id)
    elif accion == "r":
        _pedir_texto(chat_id, cq_id, row, "renombrar",
                     f"¿Cómo le ponemos a «{views.titulo_html(row)}»?")
    elif accion == "sw":
        nuevo_tipo = "casa" if row["tipo"] == "compras" else "compras"
        cambios = {"tipo": nuevo_tipo}
        if nuevo_tipo == "compras":
            cambios.update({"categoria": "compras", "due_date": None, "due_hora": None})
        db.actualizar(row["id"], **cambios)
        db.vencer_deshacer_de([row["id"]])
        telegram.responder_callback(cq_id, "🛒 Al súper" if nuevo_tipo == "compras" else "📌 A tareas")
        menus.cerrar(chat_id, message_id)
        tablero.actualizar(chat_id)
    elif accion == "x":
        _borrar(chat_id, message_id, cq_id, row, args)
    else:
        telegram.responder_callback(cq_id)


def _es_temporal(chat_id: int, message_id: int | None) -> bool:
    if not message_id:
        return False
    with db.conn() as c:
        return c.execute("SELECT 1 FROM mensajes_temporales WHERE chat_id = ? "
                         "AND message_id = ?", (chat_id, message_id)).fetchone() is not None


def _quien_lo_hizo(row) -> str:
    nombre = config.NOMBRES.get(row["completed_by"] or "", "")
    if row["estado"] == "hecha" and nombre:
        return f"Ya lo había tachado {nombre}"
    return "Eso ya estaba resuelto ✨"


def _refrescar(chat_id: int, message_id: int | None) -> None:
    """Después de tocar algo, el tablero; y si era un menú, se cierra."""
    if message_id and _es_temporal(chat_id, message_id):
        menus.cerrar(chat_id, message_id)
    tablero.actualizar(chat_id)


def _completar(chat_id: int, message_id: int, cq_id: str, row, quien: str,
               ref: date) -> None:
    nueva = db.marcar_hecha(row["id"], quien)
    db.vencer_deshacer_de([row["id"]])
    # Un ✅ mal tocado no tenía vuelta atrás: la tarea desaparecía del tablero y había
    # que anotarla de nuevo. Ahora el tablero ofrece deshacerlo un rato.
    db.guardar_deshacer(chat_id, [row["id"]], accion="completar")
    aviso = "✅ Hecho"
    if nueva is not None:
        aviso = f"✅ Hecho · la próxima {views.cuando(de_iso(nueva['due_date']), None, ref)}"
    telegram.responder_callback(cq_id, aviso)
    if message_id and _es_temporal(chat_id, message_id):
        if _era_del_super(row):
            menus.abrir_super(chat_id, message_id)
        else:
            menus.cerrar(chat_id, message_id)
    tablero.actualizar(chat_id)


def _era_del_super(row) -> bool:
    return row["tipo"] == "compras"


def _mover(chat_id: int, message_id: int, cq_id: str, row, cual: str, ref: date) -> None:
    destinos = {
        "h": ref,
        "m": ref + timedelta(days=1),
        "s": proximo_dia_semana(ref, 5),
        "l": resolve(DateSpec("dia_semana_prox", weekday=0), ref),
        "a": None,
    }
    fecha = destinos.get(cual, ref + timedelta(days=1))
    db.actualizar(row["id"], due_date=fecha.isoformat() if fecha else None)
    db.vencer_deshacer_de([row["id"]])
    telegram.responder_callback(cq_id, f"📅 {views.cuando(fecha, None, ref)}")
    if message_id and _es_temporal(chat_id, message_id):
        menus.cerrar(chat_id, message_id)
    tablero.actualizar(chat_id)


def _borrar(chat_id: int, message_id: int, cq_id: str, row, args: list) -> None:
    alcance = args[1] if len(args) > 1 else None
    if row["recur_kind"] and alcance is None:
        telegram.responder_callback(cq_id)
        menus.confirmar_borrar_recurrente(chat_id, message_id, row["id"])
        return
    if alcance == "una":
        # Sólo esta vez: se completa, así la recurrencia sigue viva.
        db.marcar_hecha(row["id"], "ninguno")
    else:
        db.borrar(row["id"])
    db.vencer_deshacer_de([row["id"]])
    deshacer_id = db.guardar_deshacer(chat_id, [row["id"]], accion="borrar")
    telegram.responder_callback(cq_id, "🗑 Borrada")
    if message_id and _es_temporal(chat_id, message_id):
        telegram.editar(chat_id, message_id,
                        f"🗑 <s>{views.titulo_html(row)}</s>",
                        [[{"text": "↩️ Deshacer", "callback_data": cb.armar("u", deshacer_id)}]])
        db.anotar_temporal(chat_id, message_id, "menu", 10)
    tablero.actualizar(chat_id)


def _pedir_texto(chat_id: int, cq_id: str, row, kind: str, pregunta: str) -> None:
    db.set_pending(chat_id, kind, task_id=row["id"])
    telegram.responder_callback(cq_id, "Escribime la respuesta")
    telegram.enviar(chat_id, pregunta, silencioso=True, forzar_respuesta=True)


def _super_todo(chat_id: int, message_id: int, cq_id: str, quien: str,
                args: list) -> None:
    if not args:
        telegram.responder_callback(cq_id)
        menus.confirmar_super_todo(chat_id, message_id)
        return
    rows = db.pendientes(chat_id, tipo="compras")
    for row in rows:
        db.marcar_hecha(row["id"], quien)
    db.vencer_deshacer_de([r["id"] for r in rows])
    telegram.responder_callback(cq_id, f"✅ {len(rows)} tachadas" if rows else "Ya estaba vacía")
    menus.abrir_super(chat_id, message_id)
    tablero.actualizar(chat_id)


def _ejecutar_propuesta(chat_id: int, message_id: int, cq_id: str, quien: str,
                        args: list, ref: date) -> None:
    propuesta_id = cb.entero(args)
    opcion = args[1] if len(args) > 1 else "todas"
    if propuesta_id is None:
        telegram.responder_callback(cq_id)
        return
    texto, aviso = propuestas.ejecutar(chat_id, propuesta_id, opcion, quien, ref)
    telegram.responder_callback(cq_id, aviso)
    if texto:
        telegram.editar(chat_id, message_id, texto, [])
        db.anotar_temporal(chat_id, message_id, "resuelto", 5)
    tablero.actualizar(chat_id)


def _confirmar_pausa(chat_id: int, message_id: int, cq_id: str, args: list) -> None:
    propuesta_id = cb.entero(args)
    propuesta = db.leer_propuesta(propuesta_id) if propuesta_id else None
    if not propuesta or propuesta.get("accion") != "pausa":
        telegram.responder_callback(cq_id, "Esto ya venció")
        return
    hasta = de_iso(propuesta["hasta"])
    db.pausar(chat_id, hasta)
    db.borrar_propuesta(propuesta_id)
    telegram.responder_callback(cq_id, "Pausada 😴")
    telegram.editar(chat_id, message_id,
                    f"😴 Me tomo vacaciones hasta el {views.dia_corto(hasta, con_mes=True)}.\n"
                    f"<i>Podés seguir anotando; el parte vuelve solo.</i>")


def _deshacer(chat_id: int, message_id: int, cq_id: str, args: list) -> None:
    deshacer_id = cb.entero(args)
    registro = db.leer_deshacer(deshacer_id) if deshacer_id else None
    if registro is None:
        telegram.responder_callback(cq_id, "Ya no se puede deshacer")
        telegram.editar_teclado(chat_id, message_id, [])
        return
    if registro["accion"] in ("borrar", "completar"):
        for item_id in registro["item_ids"]:
            db.actualizar(item_id, estado="pendiente", completed_by=None,
                          completed_at=None)
        aviso = "↩️ Recuperada"
    else:
        for item_id in registro["item_ids"]:
            db.borrar(item_id)
        aviso = "↩️ Deshecho"
    db.borrar_deshacer(deshacer_id)
    telegram.responder_callback(cq_id, aviso)
    guardado = db.tablero_actual(chat_id)
    if not guardado or guardado.get("message_id") != message_id:
        # Si se tocó desde una confirmación, esa confirmación queda resuelta. Si se
        # tocó desde el tablero, no: el tablero se refresca solo.
        telegram.editar(chat_id, message_id, f"{aviso} 🤍", [])
    tablero.actualizar(chat_id, forzar=True)


def _corregir(chat_id: int, message_id: int, cq_id: str, args: list) -> None:
    """«✏️ Corregir»: un botón por ítem creado por ese mensaje."""
    deshacer_id = cb.entero(args)
    registro = db.leer_deshacer(deshacer_id) if deshacer_id else None
    ids = registro["item_ids"] if registro else []
    filas = [db.obtener(i) for i in ids]
    filas = [f for f in filas if f is not None and f["estado"] == "pendiente"]
    if not filas:
        telegram.responder_callback(cq_id, "Ya no hay nada para corregir")
        telegram.editar_teclado(chat_id, message_id, [])
        return
    telegram.responder_callback(cq_id)
    if len(filas) == 1:
        menus.abrir(chat_id, filas[0]["id"], None, hoy())
        return
    teclado = [[{"text": f"⋯ {views.recortar(views.titulo(f), 28)}",
                 "callback_data": cb.armar("m", f["id"])}] for f in filas]
    telegram.editar_teclado(chat_id, message_id, teclado)


def _era_manana(chat_id: int, message_id: int, cq_id: str, args: list,
                ref: date) -> None:
    """El botón de la madrugada: mover todo lo de ese mensaje al día siguiente."""
    deshacer_id = cb.entero(args)
    registro = db.leer_deshacer(deshacer_id) if deshacer_id else None
    if registro is None:
        telegram.responder_callback(cq_id, "Ya pasó el momento")
        telegram.editar_teclado(chat_id, message_id, [])
        return
    manana = (ref + timedelta(days=1)).isoformat()
    movidas = 0
    for item_id in registro["item_ids"]:
        row = db.obtener(item_id)
        if row is not None and row["estado"] == "pendiente" and row["tipo"] == "casa":
            db.actualizar(item_id, due_date=manana)
            movidas += 1
    telegram.responder_callback(cq_id, f"📅 {movidas} para mañana")
    filas = [db.obtener(i) for i in registro["item_ids"]]
    filas = [f for f in filas if f is not None]
    texto, _ = _confirmacion(chat_id, filas, [], [], False, ref)
    telegram.editar(chat_id, message_id, texto,
                    [[{"text": "↩️ Deshacer", "callback_data": cb.armar("u", deshacer_id)}]])
    tablero.actualizar(chat_id)


def _mandar_ics(chat_id: int, cq_id: str, item_id: int | None) -> None:
    row = db.obtener(item_id) if item_id else None
    if row is None or not row["due_hora"]:
        telegram.responder_callback(cq_id, "Eso no tiene hora")
        return
    telegram.responder_callback(cq_id, "Ahí va 📅")
    telegram.mandar_archivo(chat_id, f"{views.titulo(row)[:40]}.ics",
                            calendario.ics(row).encode("utf-8"),
                            "📅 Agregalo a tu calendario")


# --------------------------------------------------------------------------
# Bienvenida de v2
# --------------------------------------------------------------------------

def _comandos_al_dia() -> None:
    """Registra el menú «/» cuando cambia la versión.

    Los comandos cambian entre versiones y nadie se acuerda de actualizarlos a mano:
    el menú de Telegram estuvo vacío desde el primer día.
    """
    if db.ajuste("comandos") == config.VERSION:
        return
    if telegram.registrar_comandos():
        db.ajuste("comandos", config.VERSION)


def _bienvenida_si_hace_falta(chat_id: int) -> None:
    if db.ajuste("bienvenida_v2"):
        return
    db.ajuste("bienvenida_v2", "1")
    descartados = int(db.ajuste("recados_descartados") or 0)
    telegram.enviar(chat_id, views.bienvenida(descartados))
    tablero.publicar(chat_id)
