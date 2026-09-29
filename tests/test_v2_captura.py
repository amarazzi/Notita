"""Capturar: el texto crea, y la confirmación sale de la base.

El principio 4 de v2 («todo lo que Notita dice es verdad») nació de este bug real:
«comprar la cómoda para la habitación mañana» contestó «Anotado · Cómoda para la
habitación · al súper», y el /super siguiente decía que la lista estaba vacía.
"""
from datetime import timedelta

import pytest

from notita import db, handlers, llm
from notita.dates import hoy

from .conftest import CHAT


# --------------------------------------------------------------------------
# Andamios
# --------------------------------------------------------------------------

def mensaje(texto, uid=111, update_id=1, tipo_chat="group", chat_id=CHAT, message_id=7):
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id, "type": tipo_chat},
                        "from": {"id": uid}, "text": texto, "message_id": message_id}}


def click(data, uid=111, message_id=10, cq_id="cb1"):
    return {"update_id": 900 + message_id,
            "callback_query": {"id": cq_id, "data": data, "from": {"id": uid},
                               "message": {"message_id": message_id,
                                           "chat": {"id": CHAT}}}}


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


def item(titulo, **campos):
    base = {"titulo": titulo, "tipo": "tarea", "categoria": "otros",
            "responsable": "ninguno", "fecha_kind": "desconocida", "fecha_weekday": -1,
            "fecha_day": -1, "fecha_month": -1, "fecha_year": -1, "fecha_dias": -1,
            "fecha_hora": -1, "fecha_minuto": -1, "recur_kind": "ninguna",
            "recur_interval": 1, "recur_weekday": -1, "recur_monthday": -1}
    base.update(campos)
    return base


def responde(monkeypatch, **campos):
    base = {"intencion": "crear", "comentario": "", "items": [], "accion": "ninguna",
            "referencias": [], "conjunto": "ninguno",
            "destino_fecha_kind": "desconocida", "destino_responsable": "ninguno",
            "destino_texto": "", "recado_para": "ninguno", "recado_mensaje": "",
            "ver_que": "ninguno"}
    base.update(campos)
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: dict(base))
    return base


def botones(enviados, indice=-1):
    envio = [e for e in enviados if e["metodo"] == "sendMessage"][indice]
    kb = (envio.get("reply_markup") or {}).get("inline_keyboard") or []
    return [b for fila in kb for b in fila]


# --------------------------------------------------------------------------
# La cómoda: el caso que dio origen al principio 4
# --------------------------------------------------------------------------

def test_la_comoda_es_tarea_con_fecha_y_la_confirmacion_sale_de_la_base(
        enviados, monkeypatch):
    responde(monkeypatch, items=[item("Comprar la cómoda para la habitación",
                                      categoria="otros", fecha_kind="manana")])

    handlers.handle_update(mensaje("comprar la cómoda para la habitación mañana"))

    guardada = db.pendientes(CHAT, tipo="casa")
    assert len(guardada) == 1, "tiene que quedar guardada como tarea"
    assert guardada[0]["due_date"] == (hoy() + timedelta(days=1)).isoformat()
    assert db.pendientes(CHAT, tipo="compras") == [], "no va al súper"

    confirmacion = textos(enviados)[0]
    assert "Comprar la cómoda para la habitación" in confirmacion
    assert "al súper" not in confirmacion
    assert "mañana" in confirmacion


def test_una_confirmacion_nunca_menciona_algo_que_no_esta_en_la_base(
        enviados, monkeypatch):
    """Si la escritura falla, Notita lo dice y no confirma nada."""
    responde(monkeypatch, items=[item("limpiar la heladera", fecha_kind="hoy")])

    def no_guarda(*a, **k):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(db, "crear_tarea", no_guarda)
    handlers.handle_update(mensaje("hay que limpiar la heladera"))

    salida = textos(enviados)
    assert salida == ["No pude guardar eso 😕 Probá de nuevo en un ratito"]
    assert "heladera" not in " ".join(salida)


def test_si_la_relectura_no_encuentra_nada_no_confirma(enviados, monkeypatch):
    responde(monkeypatch, items=[item("limpiar la heladera", fecha_kind="hoy")])
    monkeypatch.setattr(db, "obtener", lambda *a, **k: None)

    handlers.handle_update(mensaje("hay que limpiar la heladera"))
    assert "No pude guardar" in textos(enviados)[0]


# --------------------------------------------------------------------------
# Fechas: siempre con día de la semana
# --------------------------------------------------------------------------

def test_la_fecha_se_muestra_con_dia_de_la_semana(enviados, monkeypatch):
    responde(monkeypatch, items=[item("llamar al plomero", fecha_kind="manana")])
    handlers.handle_update(mensaje("llamar al plomero mañana"))

    confirmacion = textos(enviados)[0]
    manana = hoy() + timedelta(days=1)
    from notita.views import dia_corto

    assert f"mañana, {dia_corto(manana)}" in confirmacion


def test_si_todas_comparten_fecha_va_una_sola_vez(enviados, monkeypatch):
    responde(monkeypatch, items=[item("una cosa", fecha_kind="hoy"),
                                 item("otra cosa", fecha_kind="hoy"),
                                 item("y otra", fecha_kind="hoy")])
    handlers.handle_update(mensaje("tres cosas para hoy"))

    confirmacion = textos(enviados)[0]
    assert "Anoté 3 cositas para hoy" in confirmacion
    assert confirmacion.count("hoy") == 1, "la fecha va en el encabezado, no por ítem"


# --------------------------------------------------------------------------
# Madrugada
# --------------------------------------------------------------------------

def de_madrugada(monkeypatch, hora=0, minuto=40):
    """Congela el reloj en la madrugada, sin tocar el día."""
    from notita import dates

    real = dates.ahora()
    monkeypatch.setattr(dates, "ahora",
                        lambda: real.replace(hour=hora, minute=minuto))
    monkeypatch.setattr(handlers, "ahora",
                        lambda: real.replace(hour=hora, minute=minuto))


def test_a_las_0040_manana_es_hoy(enviados, monkeypatch):
    de_madrugada(monkeypatch, 0, 40)
    responde(monkeypatch, items=[item("sacar la basura", fecha_kind="manana")])

    handlers.handle_update(mensaje("Recordamos mañana: sacar la basura"))

    guardada = db.pendientes(CHAT, tipo="casa")[0]
    assert guardada["due_date"] == hoy().isoformat(), "a las 00:40 «mañana» es hoy"
    confirmacion = textos(enviados)[0]
    assert "🌙" in confirmacion
    etiquetas = [b["text"] for b in botones(enviados)]
    assert any("Era el" in e for e in etiquetas)


def test_a_las_cinco_manana_es_manana(enviados, monkeypatch):
    de_madrugada(monkeypatch, 5, 0)
    responde(monkeypatch, items=[item("sacar la basura", fecha_kind="manana")])

    handlers.handle_update(mensaje("mañana sacar la basura"))

    guardada = db.pendientes(CHAT, tipo="casa")[0]
    assert guardada["due_date"] == (hoy() + timedelta(days=1)).isoformat()
    assert not any("Era el" in b["text"] for b in botones(enviados))


def test_el_boton_era_el_lunes_mueve_todo_al_dia_siguiente(enviados, monkeypatch):
    de_madrugada(monkeypatch, 1, 15)
    responde(monkeypatch, items=[item("sacar la basura", fecha_kind="manana"),
                                 item("regar las plantas", fecha_kind="manana")])
    handlers.handle_update(mensaje("mañana: sacar la basura y regar"))
    boton = [b for b in botones(enviados) if "Era el" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))

    manana = (hoy() + timedelta(days=1)).isoformat()
    assert all(r["due_date"] == manana for r in db.pendientes(CHAT, tipo="casa"))


# --------------------------------------------------------------------------
# Súper vs tarea
# --------------------------------------------------------------------------

@pytest.mark.parametrize("titulo,tipo_llm,esperado", [
    ("Leche", "super", "compras"),
    ("Detergente para los platos", "super", "compras"),
    ("Comprar la cómoda", "tarea", "casa"),
    ("Comprar tornillos", "tarea", "casa"),
])
def test_donde_cae_cada_cosa(enviados, monkeypatch, titulo, tipo_llm, esperado):
    responde(monkeypatch, items=[item(titulo, tipo=tipo_llm,
                                      fecha_kind="algun_dia" if tipo_llm == "super" else "desconocida")])
    handlers.handle_update(mensaje(titulo))
    assert len(db.pendientes(CHAT, tipo=esperado)) == 1


def test_el_super_guarda_solo_el_producto(enviados, monkeypatch):
    responde(monkeypatch, items=[item("comprar detergente para los platos",
                                      tipo="super", fecha_kind="algun_dia")])
    handlers.handle_update(mensaje("hay que comprar detergente para los platos"))

    assert db.pendientes(CHAT, tipo="compras")[0]["texto"] == "detergente para los platos"
    assert "Detergente para los platos" in textos(enviados)[0]


def test_lo_del_super_no_tiene_fecha(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Leche", tipo="super", fecha_kind="manana")])
    handlers.handle_update(mensaje("falta leche mañana"))
    assert db.pendientes(CHAT, tipo="compras")[0]["due_date"] is None


# --------------------------------------------------------------------------
# Duplicados, fechas imposibles y mensajes sin texto
# --------------------------------------------------------------------------

def test_no_anota_dos_veces_lo_mismo(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Leche", tipo="super", fecha_kind="algun_dia")])
    handlers.handle_update(mensaje("falta leche", update_id=1))
    handlers.handle_update(mensaje("falta leche", update_id=2))

    assert len(db.pendientes(CHAT, tipo="compras")) == 1
    assert "Ya estaba: Leche" in textos(enviados)[-1]


def test_el_31_de_febrero_queda_para_algun_dia_y_lo_dice(enviados, monkeypatch):
    responde(monkeypatch, items=[item("pagar la expensa", categoria="pagos",
                                      fecha_kind="fecha_exacta", fecha_day=31,
                                      fecha_month=2)])
    handlers.handle_update(mensaje("pagar la expensa el 31 de febrero"))

    guardada = db.pendientes(CHAT, tipo="casa")[0]
    assert guardada["due_date"] is None, "nunca bloquear: se guarda sin fecha"
    confirmacion = textos(enviados)[0]
    assert "no existe" in confirmacion
    assert any("Elegir día" in b["text"] for b in botones(enviados))


@pytest.mark.parametrize("clave", ["voice", "photo", "sticker", "document"])
def test_audios_y_fotos_no_crean_nada(enviados, clave):
    update = {"update_id": 5, "message": {"chat": {"id": CHAT, "type": "group"},
                                          "from": {"id": 111}, "message_id": 3,
                                          clave: {"file_id": "x"}}}
    handlers.handle_update(update)

    assert db.pendientes(CHAT) == []
    assert "audios ni fotos" in textos(enviados)[0]


def test_la_charla_no_crea_nada(enviados, monkeypatch):
    responde(monkeypatch, intencion="charla", comentario="¡Hola! Acá estoy 🤍")
    handlers.handle_update(mensaje("hola notita"))

    assert db.pendientes(CHAT) == []
    assert textos(enviados) == ["¡Hola! Acá estoy 🤍"]


# --------------------------------------------------------------------------
# Recados: sólo inmediatos
# --------------------------------------------------------------------------

def test_un_recado_se_dice_al_instante(enviados, monkeypatch):
    responde(monkeypatch, intencion="recado", recado_para="axel",
             recado_mensaje="ya salí")
    handlers.handle_update(mensaje("decile a Axel que ya salí", uid=222))

    salida = textos(enviados)[0]
    assert "tg://user?id=111" in salida
    assert "ya salí" in salida
    assert db.pendientes(CHAT) == [], "un recado no se guarda"


def test_un_recado_para_manana_es_una_tarea(enviados, monkeypatch):
    """El brief: si el recado tiene un momento futuro, es una tarea de esa persona."""
    responde(monkeypatch, items=[item("comprar pan", responsable="axel",
                                      fecha_kind="manana")])
    handlers.handle_update(mensaje("decile a Axel que mañana compre pan", uid=222))

    guardada = db.pendientes(CHAT, tipo="casa")[0]
    assert guardada["responsable"] == "axel"
    assert guardada["due_date"] == (hoy() + timedelta(days=1)).isoformat()


# --------------------------------------------------------------------------
# Deshacer
# --------------------------------------------------------------------------

def test_deshacer_borra_solo_lo_de_ese_mensaje(enviados, monkeypatch):
    db.crear_tarea(CHAT, "algo de antes", due=hoy())
    responde(monkeypatch, items=[item("una", fecha_kind="hoy"),
                                 item("dos", fecha_kind="hoy")])
    handlers.handle_update(mensaje("una y dos"))
    boton = [b for b in botones(enviados) if "Deshacer" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))

    quedan = [r["texto"] for r in db.pendientes(CHAT)]
    assert quedan == ["algo de antes"]


def test_deshacer_vence_a_los_diez_minutos(enviados, monkeypatch):
    responde(monkeypatch, items=[item("una", fecha_kind="hoy")])
    handlers.handle_update(mensaje("una"))
    boton = [b for b in botones(enviados) if "Deshacer" in b["text"]][0]

    with db.conn() as c:      # lo envejecemos
        c.execute("UPDATE deshacer SET expira_at = '2020-01-01T00:00:00-03:00'")

    handlers.handle_update(click(boton["callback_data"]))

    assert len(db.pendientes(CHAT)) == 1, "no se deshizo"
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert "Ya no se puede deshacer" in avisos


def test_deshacer_vence_al_modificar_uno_de_sus_items(enviados, monkeypatch):
    responde(monkeypatch, items=[item("una", fecha_kind="hoy")])
    handlers.handle_update(mensaje("una"))
    boton = [b for b in botones(enviados) if "Deshacer" in b["text"]][0]
    item_id = db.pendientes(CHAT)[0]["id"]

    from notita import cb

    handlers.handle_update(click(cb.armar("ok", item_id), message_id=11, cq_id="c2"))
    handlers.handle_update(click(boton["callback_data"], message_id=12, cq_id="c3"))

    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert "Ya no se puede deshacer" in avisos


def test_corregir_con_un_solo_item_abre_su_menu(enviados, monkeypatch):
    responde(monkeypatch, items=[item("limpiar la heladera", fecha_kind="hoy")])
    handlers.handle_update(mensaje("limpiar la heladera"))
    boton = [b for b in botones(enviados) if "Corregir" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))

    assert any("Limpiar la heladera" in t and "🔁" in t for t in textos(enviados))


# --------------------------------------------------------------------------
# Idempotencia
# --------------------------------------------------------------------------

def test_un_update_repetido_no_tiene_efecto_doble(enviados, monkeypatch):
    responde(monkeypatch, items=[item("una", fecha_kind="hoy")])
    handlers.handle_update(mensaje("una", update_id=77))
    handlers.handle_update(mensaje("una", update_id=77))

    assert len(db.pendientes(CHAT)) == 1
    assert len(textos(enviados)) == 1


def test_un_callback_repetido_tampoco(enviados, monkeypatch):
    from notita import cb

    item_id = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    handlers.handle_update(click(cb.armar("ok", item_id), cq_id="igual"))
    antes = len(enviados)
    handlers.handle_update(click(cb.armar("ok", item_id), cq_id="igual"))

    assert len(enviados) == antes, "el segundo no hace nada"


# --------------------------------------------------------------------------
# El responsable, sólo si el mensaje lo dice
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto,dijo_el_modelo,queda", [
    # Lo que pasó de verdad con un audio: «comprar leche y yerba» quedó como
    # «Leche · al súper · Axel» porque el modelo dedujo que lo hace quien escribió.
    ("comprar leche y yerba", "axel", "ninguno"),
    ("pintar el balcón", "ambos", "ninguno"),
    # Con evidencia en el texto, se respeta.
    ("que Barbu compre pan", "barbu", "barbu"),
    ("Barbu tiene que llevar a Milo al veterinario", "barbu", "barbu"),
    ("avisale a barbu que compre pan", "barbu", "barbu"),
    ("limpiar el horno, lo hago yo", "axel", "axel"),
    ("pintar el balcón entre los dos", "ambos", "ambos"),
    ("hay que sacar la basura", "ninguno", "ninguno"),
])
def test_el_responsable_necesita_estar_en_el_mensaje(texto, dijo_el_modelo, queda):
    assert handlers._responsable({"responsable": dijo_el_modelo}, texto, "axel") == queda


def test_un_responsable_inventado_no_llega_a_la_base(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Leche", tipo="super", responsable="axel",
                                      fecha_kind="algun_dia")])

    handlers.handle_update(mensaje("comprar leche"))

    assert db.pendientes(CHAT, tipo="compras")[0]["responsable"] == "ninguno"
    assert "Axel" not in textos(enviados)[0]


def test_pero_el_que_si_dijeron_llega(enviados, monkeypatch):
    responde(monkeypatch, items=[item("llevar a Milo al veterinario",
                                      responsable="barbu", fecha_kind="manana")])

    handlers.handle_update(mensaje("Barbu tiene que llevar a Milo al veterinario mañana"))

    assert db.pendientes(CHAT, tipo="casa")[0]["responsable"] == "barbu"
    assert "Barbu" in textos(enviados)[0]
