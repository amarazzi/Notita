"""Tests del flujo completo, con el LLM y Telegram simulados."""
from datetime import date, timedelta

from notita import db, handlers, llm, reminders, views
from notita.dates import Recurrencia, hoy

from .conftest import CHAT


def item(texto, **kw):
    base = {
        "texto": texto,
        "tipo": "casa",
        "categoria": "otros",
        "responsable": "ninguno",
        "fecha_kind": "desconocida",
        "fecha_weekday": -1,
        "fecha_day": -1,
        "fecha_month": -1,
        "fecha_year": -1,
        "fecha_dias": -1,
        "recur_kind": "ninguna",
        "recur_interval": 1,
        "recur_weekday": -1,
        "recur_monthday": -1,
        "necesita_aclaracion": False,
        "pregunta": "",
    }
    base.update(kw)
    return base


def mensaje(texto, user_id=111):
    return {"message": {"chat": {"id": CHAT}, "from": {"id": user_id}, "text": texto,
                        "message_id": 1}}


def click(data, user_id=111, message_id=10):
    return {"callback_query": {"id": "cb1", "data": data, "from": {"id": user_id},
                               "message": {"message_id": message_id, "chat": {"id": CHAT}}}}


def fake_llm(monkeypatch, items, es_tarea=True, comentario=""):
    monkeypatch.setattr(
        llm, "interpretar_mensaje",
        lambda *a, **k: {"es_tarea": es_tarea, "comentario": comentario, "items": items},
    )


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


# --------------------------------------------------------------------------

def test_carga_en_lote_separa_items(monkeypatch, enviados):
    fake_llm(monkeypatch, [
        item("limpiar la heladera", categoria="limpieza", fecha_kind="manana"),
        item("llamar al plomero", categoria="arreglos", fecha_kind="manana"),
        item("comprar focos", tipo="compras", categoria="compras", fecha_kind="algun_dia"),
    ])
    handlers.handle_update(mensaje("hay que limpiar la heladera, llamar al plomero y comprar focos"))

    casa = db.pendientes(CHAT, tipo="casa")
    compras = db.pendientes(CHAT, tipo="compras")
    assert len(casa) == 2
    assert len(compras) == 1
    assert compras[0]["texto"] == "comprar focos"
    assert compras[0]["due_date"] is None
    assert "Anoté 3 cositas" in textos(enviados)[0]


def test_pregunta_para_cuando_si_no_hay_fecha(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("limpiar la heladera", categoria="limpieza")])
    handlers.handle_update(mensaje("hay que limpiar la heladera"))

    pregunta = [e for e in enviados if "Para cuándo" in e.get("text", "")
                or "para cuándo" in e.get("text", "")]
    assert pregunta, textos(enviados)
    assert pregunta[0]["reply_markup"]["inline_keyboard"]
    assert db.get_pending(CHAT)["kind"] == "fecha"


def test_responder_la_fecha_por_texto_libre(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("limpiar la heladera")])
    handlers.handle_update(mensaje("hay que limpiar la heladera"))
    handlers.handle_update(mensaje("mañana"))

    tarea = db.pendientes(CHAT)[0]
    assert tarea["due_date"] == (hoy() + timedelta(days=1)).isoformat()
    assert db.get_pending(CHAT) is None


def test_responder_la_fecha_con_boton(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("regar las plantas")])
    handlers.handle_update(mensaje("regar las plantas"))
    tid = db.pendientes(CHAT)[0]["id"]

    handlers.handle_update(click(f"f:{tid}:a"))  # algún día
    assert db.obtener(tid)["due_date"] is None


def test_una_tarea_de_casa_no_lleva_el_carrito(monkeypatch, enviados):
    """El LLM a veces devuelve tipo=casa con categoria=compras: manda el tipo."""
    fake_llm(monkeypatch, [item("comprar la cómoda", tipo="casa", categoria="compras",
                                fecha_kind="hoy")])
    handlers.handle_update(mensaje("hay que comprar la cómoda hoy"))

    tarea = db.pendientes(CHAT, tipo="casa")[0]
    assert tarea["categoria"] == "otros"
    assert "🛒" not in views.linea_tarea(tarea, hoy())
    assert tarea["due_date"] == hoy().isoformat()   # conserva la fecha


def test_una_tarea_vieja_inconsistente_tampoco_muestra_el_carrito(enviados):
    # Las que quedaron guardadas antes de arreglar esto.
    tid = db.crear_tarea(CHAT, "comprar la cómoda", tipo="casa", categoria="compras",
                         due=hoy())
    assert "🛒" not in views.linea_tarea(db.obtener(tid), hoy())


def test_compras_no_pregunta_fecha(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("leche", tipo="compras", categoria="compras",
                                fecha_kind="algun_dia")])
    handlers.handle_update(mensaje("falta leche"))
    assert db.get_pending(CHAT) is None
    assert all("cuándo" not in t for t in textos(enviados))


def test_responsable_se_guarda_y_se_confirma(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("llamar al veterinario", categoria="mascotas",
                                responsable="barbu", fecha_kind="dia_semana",
                                fecha_weekday=0)])
    handlers.handle_update(mensaje("Barbu tiene que llamar al veterinario el lunes"))
    tarea = db.pendientes(CHAT)[0]
    assert tarea["responsable"] == "barbu"
    assert date.fromisoformat(tarea["due_date"]).weekday() == 0
    assert "Barbu" in textos(enviados)[0]


def test_mensaje_que_no_es_tarea(monkeypatch, enviados):
    fake_llm(monkeypatch, [], es_tarea=False, comentario="Hola! Acá estoy 🤍")
    handlers.handle_update(mensaje("hola notita"))
    assert db.pendientes(CHAT) == []
    assert "Hola" in textos(enviados)[0]


def test_ambiguo_pregunta_y_reintenta(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("eso", necesita_aclaracion=True, pregunta="¿A qué te referís?")])
    handlers.handle_update(mensaje("eso"))
    assert db.pendientes(CHAT) == []
    assert db.get_pending(CHAT)["kind"] == "aclaracion"

    fake_llm(monkeypatch, [item("arreglar la canilla", categoria="arreglos",
                                fecha_kind="algun_dia")])
    handlers.handle_update(mensaje("arreglar la canilla"))
    assert db.pendientes(CHAT)[0]["texto"] == "arreglar la canilla"


def test_marcar_hecha_recurrente_crea_la_proxima(enviados):
    tid = db.crear_tarea(CHAT, "cambiar las piedritas del gato", categoria="mascotas",
                         due=hoy(), recurrencia=Recurrencia("semanal"))
    handlers.handle_update(click(f"h:{tid}"))

    assert db.obtener(tid)["estado"] == "hecha"
    pendientes = db.pendientes(CHAT)
    assert len(pendientes) == 1
    assert pendientes[0]["due_date"] == (hoy() + timedelta(days=7)).isoformat()
    assert pendientes[0]["recur_kind"] == "semanal"


def test_posponer_cuenta_y_se_pone_cargoso(enviados):
    tid = db.crear_tarea(CHAT, "colgar el cuadro", due=hoy())
    for _ in range(3):
        handlers.handle_update(click(f"p:{tid}:m"))
    row = db.obtener(tid)
    assert row["postpone_count"] == 3
    chistoso = [e for e in enviados if e["metodo"] == "editMessageText"
                and ("😅" in e.get("text", "") or "🐢" in e.get("text", "")
                     or "🙈" in e.get("text", ""))]
    assert chistoso


def test_borrar(enviados):
    tid = db.crear_tarea(CHAT, "tirar las cajas", due=hoy())
    handlers.handle_update(click(f"b:{tid}"))
    assert db.obtener(tid)["estado"] == "borrada"
    assert db.pendientes(CHAT) == []


def test_tachar_del_super(enviados):
    tid = db.crear_tarea(CHAT, "yerba", tipo="compras", categoria="compras")
    handlers.handle_update(click(f"c:{tid}"))
    assert db.pendientes(CHAT, tipo="compras") == []


def test_ignora_otros_chats(monkeypatch, enviados):
    fake_llm(monkeypatch, [item("algo")])
    update = mensaje("hola")
    update["message"]["chat"]["id"] = 999
    handlers.handle_update(update)
    assert enviados == []


# --------------------------------------------------------------------------
# Listados y rutina de las 20:00
# --------------------------------------------------------------------------

def test_todo_ordena_vencidas_fecha_y_algun_dia(enviados):
    db.crear_tarea(CHAT, "pagar expensas", categoria="pagos", due=hoy() - timedelta(days=2))
    db.crear_tarea(CHAT, "limpiar el baño", categoria="limpieza", due=hoy() + timedelta(days=1))
    db.crear_tarea(CHAT, "pintar el balcón", categoria="arreglos")
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

    db.crear_tarea(CHAT, "renovar el dni", categoria="tramites", due=hoy() + timedelta(days=20))
    db.crear_tarea(CHAT, "regar", categoria="otros", due=hoy() + timedelta(days=3))
    db.crear_tarea(CHAT, "sacar la basura", categoria="limpieza", due=hoy())

    texto = views.render_todo(CHAT)
    orden = ["VENCIDAS", "HOY", "MAÑANA", "ESTA SEMANA", "MÁS ADELANTE", "ALGÚN DÍA", "SÚPER"]
    posiciones = [texto.index(s) for s in orden]
    assert posiciones == sorted(posiciones)
    # Nada plegado: todo visible sin tocar flechitas.
    assert "blockquote" not in texto
    assert "Pintar el balcón" in texto  # con mayúscula


def test_texto_con_saltos_de_linea_se_muestra_en_una_sola_linea(enviados):
    db.crear_tarea(CHAT, "hacer el tr\n\namite del DNI", categoria="tramites", due=hoy())
    texto = views.render_todo(CHAT)
    assert "Hacer el tr amite del DNI" in texto
    assert "tr\n" not in texto


def test_todo_filtra_por_categoria(enviados):
    db.crear_tarea(CHAT, "limpiar el baño", categoria="limpieza", due=hoy())
    db.crear_tarea(CHAT, "pagar la luz", categoria="pagos", due=hoy())
    texto = views.render_todo(CHAT, categoria="limpieza").lower()
    assert "limpiar el baño" in texto and "pagar la luz" not in texto


def test_rutina_diaria_recuerda_vencidas_y_de_hoy(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    db.crear_tarea(CHAT, "pagar expensas", due=hoy() - timedelta(days=3))
    db.crear_tarea(CHAT, "algo lejano", due=hoy() + timedelta(days=5))
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

    lunes = date(2026, 9, 28)
    res = reminders.correr_rutina_diaria(ref=lunes)
    assert res["recordatorios"] == 2
    assert res["resumen"] is False
    assert all("leche" not in t for t in textos(enviados))


def test_rutina_no_repite_el_mismo_dia(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    lunes = date(2026, 9, 28)
    reminders.correr_rutina_diaria(ref=lunes)
    assert reminders.correr_rutina_diaria(ref=lunes)["recordatorios"] == 0
    assert reminders.correr_rutina_diaria(ref=lunes, forzar=True)["recordatorios"] == 1


def test_domingo_manda_resumen_en_el_mismo_envio(enviados):
    domingo = date(2026, 9, 27)
    db.crear_tarea(CHAT, "regar las plantas", due=domingo)
    db.crear_tarea(CHAT, "pagar expensas", due=date(2026, 10, 1))
    db.crear_tarea(CHAT, "pintar el balcón")

    res = reminders.correr_rutina_diaria(ref=domingo)
    assert res["resumen"] is True
    assert res["recordatorios"] == 1
    resumen = textos(enviados)[0].lower()
    assert "resumen de la semana" in resumen
    assert "pagar expensas" in resumen          # vence en la semana que arranca
    assert "1 para «algún día»" in resumen


def test_confirmacion_usa_el_mismo_formato_que_la_lista(enviados):
    from notita import views
    t = db.crear_tarea(CHAT, "comprar la cómoda", tipo="compras", categoria="compras",
                       responsable="ambos")
    assert views.confirmacion(db.obtener(t), hoy()) == "🛒 Comprar la cómoda · <i>al súper</i> · los dos"
    t = db.crear_tarea(CHAT, "hacer el trámite del DNI", categoria="tramites",
                       responsable="axel", due=hoy() + timedelta(days=1))
    assert views.confirmacion(db.obtener(t), hoy()) == "📄 Hacer el trámite del DNI · <i>mañana</i> · Axel"
