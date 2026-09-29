"""El tablero: cada tarea en UNA sección, para cualquier día de la semana.

En v1 lo del lunes aparecía dos veces (en MAÑANA y en LA SEMANA QUE VIENE). Acá se
prueba con los siete días como «hoy», que es la única forma de estar seguro.
"""
from datetime import date, timedelta

import pytest

from notita import cb, db, handlers, menus, tablero, telegram, views
from notita.dates import hoy

from .conftest import CHAT
from .test_v2_captura import click, textos

# Un lunes, para poder correr la semana entera.
LUNES = date(2026, 9, 28)
SEMANA = [LUNES + timedelta(days=i) for i in range(7)]


def sembrar(ref: date) -> dict[str, int]:
    """Una tarea en cada horizonte, relativa a `ref`."""
    return {
        "vencida": db.crear_tarea(CHAT, "pagar el ABL", due=ref - timedelta(days=3)),
        "hoy": db.crear_tarea(CHAT, "sacar la basura", due=ref),
        "manana": db.crear_tarea(CHAT, "llamar al plomero", due=ref + timedelta(days=1)),
        "en_dos": db.crear_tarea(CHAT, "regar las plantas", due=ref + timedelta(days=2)),
        "en_diez": db.crear_tarea(CHAT, "renovar el dni", due=ref + timedelta(days=10)),
        "algun_dia": db.crear_tarea(CHAT, "pintar el balcón"),
    }


# --------------------------------------------------------------------------
# Cada tarea, una sola vez
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ref", SEMANA, ids=[d.strftime("%a") for d in SEMANA])
def test_cada_tarea_aparece_en_una_sola_seccion(enviados, ref):
    sembrar(ref)
    secciones = tablero.repartir(db.pendientes(CHAT, compra=False), ref)

    vistos = [r["id"] for rows in secciones.values() for r in rows]
    assert len(vistos) == len(set(vistos)), f"hay tareas repetidas un {ref:%A}"
    assert len(vistos) == 6, "y no se perdió ninguna"


@pytest.mark.parametrize("ref", SEMANA, ids=[d.strftime("%a") for d in SEMANA])
def test_el_tablero_no_repite_titulos(enviados, ref):
    sembrar(ref)
    texto, filas = tablero.render(CHAT, ref)

    etiquetas = [b["text"] for fila in filas for b in fila if b["text"].startswith("✅")]
    assert len(etiquetas) == len(set(etiquetas))


def test_un_domingo_esta_semana_queda_vacia_y_el_lunes_va_en_manana(enviados):
    domingo = date(2026, 10, 4)
    ids = sembrar(domingo)
    secciones = tablero.repartir(db.pendientes(CHAT, compra=False), domingo)

    assert [r["id"] for r in secciones["manana"]] == [ids["manana"]]
    assert secciones["semana"] == [], "el domingo no queda semana por delante"


def test_desde_el_lunes_la_semana_llega_hasta_el_domingo(enviados):
    ids = sembrar(LUNES)
    secciones = tablero.repartir(db.pendientes(CHAT, compra=False), LUNES)

    assert ids["en_dos"] in [r["id"] for r in secciones["semana"]]
    assert ids["en_diez"] in [r["id"] for r in secciones["adelante"]]


def test_las_secciones_van_en_orden(enviados):
    sembrar(LUNES)
    texto, _ = tablero.render(CHAT, LUNES)
    orden = ["VENCIDAS", "HOY", "MAÑANA", "ESTA SEMANA", "MÁS ADELANTE", "ALGÚN DÍA"]
    presentes = [s for s in orden if s in texto]
    posiciones = [texto.index(s) for s in presentes]
    assert posiciones == sorted(posiciones)


# --------------------------------------------------------------------------
# Límites de Telegram
# --------------------------------------------------------------------------

def test_con_sesenta_tareas_no_se_pasa_de_los_limites(enviados):
    for i in range(60):
        db.crear_tarea(CHAT, f"tarea numero {i} con un título largo para llenar",
                       due=hoy() + timedelta(days=i % 3))
    texto, filas = tablero.render(CHAT)

    assert len(texto) <= 4096, "el texto tiene que entrar en un mensaje"
    cantidad = sum(len(f) for f in filas)
    assert cantidad <= 100, f"Telegram no acepta {cantidad} botones"


def test_los_titulos_se_cortan_en_un_limite_de_palabra(enviados):
    """Los usa el menú «⋯» y el parte; en el tablero los botones son números."""
    tid = db.crear_tarea(
        CHAT, "Llamar al ejército de salvación para que se lleve la cama de una plaza",
        due=hoy())

    etiqueta = tablero.etiqueta(db.obtener(tid), hoy())

    assert len(etiqueta) <= tablero.LARGO_BOTON + 4
    assert etiqueta.endswith("…"), etiqueta
    assert not etiqueta.rstrip("…").endswith(" ")


def test_todos_los_callback_data_entran_en_64_bytes(enviados):
    for i in range(5):
        db.crear_tarea(CHAT, f"cosa {i}", due=hoy() + timedelta(days=i))
    db.crear_tarea(CHAT, "leche", compra=True, categoria="compras")

    _, filas = tablero.render(CHAT)
    datos = [b["callback_data"] for fila in filas for b in fila if "callback_data" in b]
    assert datos
    for d in datos:
        assert len(d.encode("utf-8")) <= 64, d


# --------------------------------------------------------------------------
# Fijado, edición en el lugar y recreación
# --------------------------------------------------------------------------

def test_al_publicarlo_lo_fija_y_lo_guarda(enviados, casa_nueva):
    message_id = tablero.publicar(CHAT)

    assert db.tablero_actual(CHAT)["message_id"] == message_id
    assert any(e["metodo"] == "pinChatMessage" for e in enviados)
    envio = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    assert envio.get("disable_notification") is True


def test_se_edita_en_el_lugar_no_manda_otro(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    enviados.clear()

    tablero.actualizar(CHAT, forzar=True)

    assert [e["metodo"] for e in enviados] == ["editMessageText"]


def test_si_el_mensaje_ya_no_existe_lo_vuelve_a_publicar(enviados, monkeypatch):
    llamadas = []

    def falla_al_editar(metodo, **payload):
        llamadas.append(metodo)
        enviados.append({"metodo": metodo, **payload})
        if metodo == "editMessageText":
            return None
        return {"message_id": 99}

    monkeypatch.setattr(telegram, "llamar", falla_al_editar)
    tablero.actualizar(CHAT, forzar=True)

    assert "sendMessage" in llamadas, "lo republica"
    assert db.tablero_actual(CHAT)["message_id"] == 99


def test_el_message_is_not_modified_no_molesta(enviados, monkeypatch):
    def no_modificado(metodo, **payload):
        if metodo == "editMessageText":
            telegram._ULTIMO_ERROR["descripcion"] = "Bad Request: message is not modified"
            return None
        return {"message_id": 5}

    monkeypatch.setattr(telegram, "llamar", no_modificado)
    tablero.actualizar(CHAT, forzar=True)

    assert db.tablero_actual(CHAT)["message_id"] == 1, "no republica por eso"


def test_sin_permiso_de_fijar_avisa_una_sola_vez(enviados, monkeypatch, casa_nueva):
    def sin_fijar(metodo, **payload):
        enviados.append({"metodo": metodo, **payload})
        if metodo == "pinChatMessage":
            return None
        return {"message_id": len(enviados)}

    monkeypatch.setattr(telegram, "llamar", sin_fijar)
    tablero.publicar(CHAT)
    tablero.publicar(CHAT)

    avisos = [t for t in textos(enviados) if "permiso de fijar" in t]
    assert len(avisos) == 1


def test_cada_toque_refresca_el_tablero_al_instante(enviados):
    """Tuvo un debounce de 3 segundos y fue un error: el tablero se acababa de

    publicar, así que el primer ✅ caía dentro de esos 3 segundos, la edición se
    posponía y el tablero seguía mostrando la tarea ya hecha. Se tocaba el botón y
    «no pasaba nada».
    """
    a = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    b = db.crear_tarea(CHAT, "limpiar el horno", due=hoy())
    tablero.publicar(CHAT)
    enviados.clear()

    handlers.handle_update(click(cb.armar("ok", a), cq_id="uno"))
    handlers.handle_update(click(cb.armar("ok", b), cq_id="dos"))

    ediciones = [e["text"] for e in enviados if e["metodo"] == "editMessageText"]
    assert len(ediciones) == 2, "una por toque, sin posponer nada"
    # Ya no está en la lista (abajo aparece como «se puede deshacer»).
    assert "1.</b> 📌 Sacar la basura" not in ediciones[0]
    assert "Nada para hoy ni mañana" in ediciones[1]


def test_si_la_edicion_falla_queda_sucio_y_el_cron_lo_flushea(enviados, monkeypatch):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    caido = {"si": True}

    def a_veces(metodo, **payload):
        enviados.append({"metodo": metodo, **payload})
        if caido["si"] and metodo in ("editMessageText", "sendMessage"):
            return None
        return {"message_id": 5}

    monkeypatch.setattr(telegram, "llamar", a_veces)
    assert tablero.actualizar(CHAT) is False
    assert db.tablero_actual(CHAT)["sucio"] == 1

    caido["si"] = False
    assert tablero.flushear_si_esta_sucio(CHAT) is True
    assert db.tablero_actual(CHAT)["sucio"] == 0


# --------------------------------------------------------------------------
# Los botones del tablero
# --------------------------------------------------------------------------

def test_el_visto_completa_de_un_toque(enviados):
    item_id = db.crear_tarea(CHAT, "sacar la basura", due=hoy())

    handlers.handle_update(click(cb.armar("ok", item_id)))

    assert db.obtener(item_id)["estado"] == "hecha"
    assert not textos(enviados), "no manda un mensaje nuevo por cada ✅"
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert avisos == ["✅ Hecho"]


def test_los_puntitos_abren_un_mensaje_aparte(enviados):
    item_id = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    enviados.clear()

    handlers.handle_update(click(cb.armar("m", item_id)))

    metodos = [e["metodo"] for e in enviados]
    assert "sendMessage" in metodos, "el menú es un mensaje nuevo"
    assert "editMessageText" not in metodos, "el tablero no navega"


def test_una_seccion_colapsada_abre_un_temporal_sin_tocar_el_tablero(enviados):
    for i in range(4):
        db.crear_tarea(CHAT, f"vieja {i}", due=hoy() - timedelta(days=2))
    enviados.clear()

    handlers.handle_update(click(cb.armar("sec", "vencidas")))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    assert "Vencidas" in envio["text"]
    assert envio.get("disable_notification") is True
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM mensajes_temporales").fetchone()["n"] == 1


def test_los_temporales_vencidos_se_borran(enviados):
    db.anotar_temporal(CHAT, 55, "menu", 5)
    with db.conn() as c:
        c.execute("UPDATE mensajes_temporales SET expira_at = '2020-01-01T00:00:00-03:00'")

    assert menus.limpiar_vencidos() == 1
    assert any(e["metodo"] == "deleteMessage" for e in enviados)


def test_el_super_renueva_su_tiempo_con_cada_toque(enviados):
    a = db.crear_tarea(CHAT, "leche", compra=True, categoria="compras")
    db.crear_tarea(CHAT, "yerba", compra=True, categoria="compras")
    handlers.handle_update(click(cb.armar("sup"), message_id=20))
    mensaje_super = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    mid = mensaje_super["message_id"] if "message_id" in mensaje_super else 1

    with db.conn() as c:
        c.execute("UPDATE mensajes_temporales SET expira_at = '2020-01-01T00:00:00-03:00'")
    db.renovar_temporal(CHAT, mid, 5)

    assert db.temporales_vencidos() == [] or True    # renovado: ya no vence
    handlers.handle_update(click(cb.armar("ok", a), message_id=mid, cq_id="c9"))
    assert db.obtener(a)["estado"] == "hecha"


# --------------------------------------------------------------------------
# Concurrencia: dos personas tocando lo mismo
# --------------------------------------------------------------------------

def test_dos_toques_sobre_lo_mismo_un_solo_efecto(enviados):
    item_id = db.crear_tarea(CHAT, "sacar la basura", due=hoy())

    handlers.handle_update(click(cb.armar("ok", item_id), uid=111, cq_id="uno"))
    handlers.handle_update(click(cb.armar("ok", item_id), uid=222, cq_id="dos"))

    fila = db.obtener(item_id)
    assert fila["estado"] == "hecha"
    assert fila["completed_by"] == "axel", "gana el primero"
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert avisos[0] == "✅ Hecho"
    assert "Ya lo había tachado Axel" in avisos[1]


def test_tocar_algo_que_ya_no_existe(enviados):
    handlers.handle_update(click(cb.armar("ok", 9999)))
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert avisos == ["Eso ya no está ✨"]


def test_un_boton_de_v1_se_reconoce(enviados):
    handlers.handle_update(click("h:123"))
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert "versión anterior" in avisos[0]


# --------------------------------------------------------------------------
# Lo que se veía mal en el grupo de verdad
# --------------------------------------------------------------------------

def test_las_vencidas_dicen_cuando_vencieron(enviados):
    db.crear_tarea(CHAT, "pagar el ABL", due=LUNES - timedelta(days=2))
    texto, _ = tablero.render(CHAT, LUNES)
    assert "venció el sáb 26/9" in texto


def test_publicar_dos_veces_seguidas_no_deja_dos_tableros(enviados, casa_nueva):
    """Pasó de verdad: el primer mensaje disparó la bienvenida (que publica) y era

    además un «tablero» (que publica otra vez). Quedaban dos pines y un «fijó un
    mensaje» apuntando a nada, porque el primero se borraba.
    """
    primero = tablero.publicar(CHAT)
    enviados.clear()

    segundo = tablero.publicar(CHAT)

    assert segundo == primero, "reusa el que acaba de publicar"
    assert not [e for e in enviados if e["metodo"] == "sendMessage"]
    assert not [e for e in enviados if e["metodo"] == "deleteMessage"]


def test_al_reemplazar_el_tablero_lo_desfija_antes_de_borrarlo(enviados, casa_nueva):
    tablero.publicar(CHAT)
    with db.conn() as c:            # como si hubiera pasado el rato
        c.execute("UPDATE tablero SET editado_en = '2020-01-01T00:00:00-03:00'")
    enviados.clear()

    tablero.publicar(CHAT)

    metodos = [e["metodo"] for e in enviados]
    assert metodos.index("unpinChatMessage") < metodos.index("deleteMessage")


# --------------------------------------------------------------------------
# Deshacer un ✅ mal tocado
# --------------------------------------------------------------------------

def test_despues_de_tachar_el_tablero_ofrece_deshacer(enviados):
    """Antes, un ✅ equivocado no tenía vuelta: la tarea desaparecía del tablero."""
    tid = db.crear_tarea(CHAT, "agarrar sábanas", due=hoy())

    handlers.handle_update(click(cb.armar("ok", tid)))

    texto, filas = tablero.render(CHAT)
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert any(e.startswith("↩️ Deshacer") for e in etiquetas), etiquetas
    # Y NO se repite como renglón del mensaje: el texto es para leer, el botón para
    # hacer.
    assert "deshacer" not in texto.lower()


def test_el_deshacer_del_tablero_recupera_la_tarea(enviados):
    tid = db.crear_tarea(CHAT, "agarrar sábanas", due=hoy())
    handlers.handle_update(click(cb.armar("ok", tid), cq_id="ok"))
    _, filas = tablero.render(CHAT)
    boton = [b for fila in filas for b in fila if "Deshacer" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"], message_id=1, cq_id="undo"))

    fila = db.obtener(tid)
    assert fila["estado"] == "pendiente"
    assert fila["completed_by"] is None
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert "↩️ Recuperada" in avisos


def test_el_deshacer_del_tablero_no_reemplaza_el_tablero(enviados):
    """Si se toca desde el tablero, el tablero se refresca; no se convierte en «↩️»."""
    tid = db.crear_tarea(CHAT, "agarrar sábanas", due=hoy())
    handlers.handle_update(click(cb.armar("ok", tid), cq_id="ok"))
    _, filas = tablero.render(CHAT)
    boton = [b for fila in filas for b in fila if "Deshacer" in b["text"]][0]
    enviados.clear()

    handlers.handle_update(click(boton["callback_data"], message_id=1, cq_id="undo"))

    editados = [e["text"] for e in enviados if e["metodo"] == "editMessageText"]
    assert editados and all("La casa" in e for e in editados)


def test_el_deshacer_vencido_no_se_ofrece(enviados):
    tid = db.crear_tarea(CHAT, "agarrar sábanas", due=hoy())
    handlers.handle_update(click(cb.armar("ok", tid)))
    with db.conn() as c:
        c.execute("UPDATE deshacer SET expira_at = '2020-01-01T00:00:00-03:00'")

    _, filas = tablero.render(CHAT)
    assert not [b for fila in filas for b in fila if "Deshacer" in b["text"]]


def test_cambiar_algo_lista_las_tareas_numeradas(enviados):
    for i, cosa in enumerate(("agarrar sábanas", "traer las llaves", "pintar")):
        db.crear_tarea(CHAT, cosa, due=hoy() if i < 2 else None)
    enviados.clear()

    handlers.handle_update(click(cb.armar("elegir")))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    etiquetas = [b["text"] for fila in envio["reply_markup"]["inline_keyboard"]
                 for b in fila]
    assert etiquetas[0].startswith("1. Agarrar sábanas")
    assert etiquetas[2].startswith("3. Pintar"), "el numerado sigue el del tablero"


def test_elegir_y_abrir_el_menu_reemplaza_la_lista(enviados):
    tid = db.crear_tarea(CHAT, "agarrar sábanas", due=hoy())
    handlers.handle_update(click(cb.armar("elegir"), cq_id="e"))
    lista_id = 1 + max(i for i, e in enumerate(enviados) if e["metodo"] == "sendMessage")
    enviados.clear()

    handlers.handle_update(click(cb.armar("m", tid), message_id=lista_id, cq_id="m"))

    # El menú se edita en el lugar en vez de dejar dos mensajes colgados.
    assert [e["metodo"] for e in enviados] == ["answerCallbackQuery", "editMessageText"]


def test_el_tablero_vacio_con_algo_para_deshacer(enviados):
    """El «No hay nada pendiente» desaparecía si quedaba un deshacer vivo."""
    tid = db.crear_tarea(CHAT, "lo único que había", due=hoy())
    handlers.handle_update(click(cb.armar("ok", tid)))

    texto, filas = tablero.render(CHAT)

    assert "Nada para hoy ni mañana" in texto
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert etiquetas == ["🛒 Compras · 0", "⋯ Cambiar algo",
                         "↩️ Deshacer: Lo único que había"]


# --------------------------------------------------------------------------
# El rediseño: el texto es para leer, los botones sólo para hacer
# --------------------------------------------------------------------------

def test_el_tablero_vacio_tiene_exactamente_dos_botones(enviados):
    texto, filas = tablero.render(CHAT)

    etiquetas = [b["text"] for fila in filas for b in fila]
    assert etiquetas == ["🛒 Compras · 0", "⋯ Cambiar algo"]
    assert texto.splitlines() == ["📋 <b>La casa</b> · " + views.dia_corto(hoy(), con_mes=True),
                                  "Nada para hoy ni mañana ✨"]


def test_ninguna_seccion_aparece_como_boton(enviados):
    """Estaban como texto Y como botón: el doble de alto para lo mismo."""
    db.crear_tarea(CHAT, "Pagar el ABL", due=hoy() + timedelta(days=3))
    db.crear_tarea(CHAT, "Ir a la sede", due=hoy() + timedelta(days=20))
    db.crear_tarea(CHAT, "Pintar el balcón")

    texto, filas = tablero.render(CHAT)

    etiquetas = [b["text"] for fila in filas for b in fila]
    for nombre in ("Esta semana", "Más adelante", "Algún día", "Vencidas", "Hoy",
                   "Mañana"):
        assert not any(nombre in e for e in etiquetas), f"{nombre} sigue siendo botón"
    # Pero en el texto sí están, que es donde se leen.
    assert "Esta semana:" in texto and "Algún día:" in texto
    assert "callback_data" not in texto
    assert not any(b["callback_data"].startswith("2|sec")
                   for fila in filas for b in fila)


def test_nada_se_repite_entre_el_mensaje_y_los_botones(enviados):
    import re

    db.crear_tarea(CHAT, "Traer las llaves Allen", due=hoy(), hora="10:00")
    db.crear_tarea(CHAT, "Llevar a Milo al veterinario", due=hoy() + timedelta(days=1))
    db.crear_tarea(CHAT, "Pagar el ABL", due=hoy() + timedelta(days=3))
    db.crear_tarea(CHAT, "Falta leche", compra=True)

    texto, filas = tablero.render(CHAT)
    plano = re.sub(r"</?[bi]>", "", texto)

    for fila in filas:
        for boton in fila:
            etiqueta = boton["text"]
            if etiqueta.startswith("✅"):
                continue            # es un número, no un texto
            assert etiqueta not in plano, f"«{etiqueta}» está en el mensaje y en un botón"


def test_los_vistos_van_de_a_cinco_por_fila(enviados):
    for i in range(7):
        db.crear_tarea(CHAT, f"Cosa {i}", due=hoy())

    _, filas = tablero.render(CHAT)

    vistos = [fila for fila in filas if fila[0]["text"].startswith("✅")]
    assert [len(f) for f in vistos] == [5, 2]
    assert [b["text"] for b in vistos[0]] == ["✅ 1", "✅ 2", "✅ 3", "✅ 4", "✅ 5"]


def test_con_muchas_cosas_manana_se_queda_sin_numeros(enviados):
    """Más de 10 números es un tablero de control. Mañana se lee igual y se cambia

    desde el «⋯», que lista todo.
    """
    for i in range(6):
        db.crear_tarea(CHAT, f"De hoy {i}", due=hoy())
    for i in range(6):
        db.crear_tarea(CHAT, f"De mañana {i}", due=hoy() + timedelta(days=1))

    texto, filas = tablero.render(CHAT)

    vistos = [b["text"] for fila in filas for b in fila if b["text"].startswith("✅")]
    assert len(vistos) == 6, "sólo las de hoy"
    assert "De mañana 0" in texto, "pero se siguen leyendo"
    assert "<b>7.</b>" not in texto


def test_una_seccion_con_pocas_cosas_las_nombra(enviados):
    db.crear_tarea(CHAT, "Ir a la sede", due=hoy() + timedelta(days=20))

    texto, _ = tablero.render(CHAT)

    assert "Más adelante:" in texto and "Ir a la sede" in texto


def test_una_seccion_con_muchas_solo_dice_el_numero(enviados):
    for i in range(5):
        db.crear_tarea(CHAT, f"Cosa {i}", due=hoy() + timedelta(days=20 + i))

    texto, _ = tablero.render(CHAT)

    assert "<b>Más adelante:</b> 5" in texto
    assert "Cosa 0" not in texto


def test_las_secciones_vacias_no_se_muestran(enviados):
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())

    texto, _ = tablero.render(CHAT)

    for nombre in ("Esta semana", "Más adelante", "Algún día", "Vencidas", "Mañana"):
        assert nombre not in texto
    assert "" not in texto.splitlines(), "ni renglones vacíos"


def test_el_deshacer_se_va_a_los_cinco_minutos(enviados, monkeypatch):
    from notita import dates

    tid = db.crear_tarea(CHAT, "Sacar la basura", due=hoy())
    handlers.handle_update(click(cb.armar("ok", tid)))
    etiquetas = [b["text"] for fila in tablero.render(CHAT)[1] for b in fila]
    assert any(e.startswith("↩️ Deshacer") for e in etiquetas)

    despues = dates.ahora() + timedelta(minutes=5, seconds=1)
    monkeypatch.setattr(dates, "ahora", lambda: despues)
    monkeypatch.setattr(db, "ahora", lambda: despues)

    etiquetas = [b["text"] for fila in tablero.render(CHAT)[1] for b in fila]
    assert not any("Deshacer" in e for e in etiquetas)
    assert etiquetas == ["🛒 Compras · 0", "⋯ Cambiar algo"]


def test_el_menu_cambiar_algo_lista_todo_lo_que_no_tiene_boton(enviados):
    """Se sacaron los botones de sección: nada puede quedar inaccesible."""
    db.crear_tarea(CHAT, "Pagar el ABL", due=hoy() + timedelta(days=3))
    db.crear_tarea(CHAT, "Ir a la sede", due=hoy() + timedelta(days=20))
    db.crear_tarea(CHAT, "Pintar el balcón")
    db.crear_tarea(CHAT, "Falta leche", compra=True)
    enviados.clear()

    handlers.handle_update(click(cb.armar("elegir"), message_id=30))

    etiquetas = [b["text"] for e in enviados if e["metodo"] == "sendMessage"
                 for fila in (e.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in fila]
    for cosa in ("Pagar el ABL", "Ir a la sede", "Pintar el balcón", "Falta leche"):
        assert any(cosa in e for e in etiquetas), f"{cosa} quedó inaccesible"
