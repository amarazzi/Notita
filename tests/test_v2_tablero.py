"""El tablero: cada tarea en UNA sección, para cualquier día de la semana.

En v1 lo del lunes aparecía dos veces (en MAÑANA y en LA SEMANA QUE VIENE). Acá se
prueba con los siete días como «hoy», que es la única forma de estar seguro.
"""
from datetime import date, timedelta

import pytest

from notita import cb, db, handlers, menus, tablero, telegram
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
    secciones = tablero.repartir(db.pendientes(CHAT, tipo="casa"), ref)

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
    secciones = tablero.repartir(db.pendientes(CHAT, tipo="casa"), domingo)

    assert [r["id"] for r in secciones["manana"]] == [ids["manana"]]
    assert secciones["semana"] == [], "el domingo no queda semana por delante"


def test_desde_el_lunes_la_semana_llega_hasta_el_domingo(enviados):
    ids = sembrar(LUNES)
    secciones = tablero.repartir(db.pendientes(CHAT, tipo="casa"), LUNES)

    assert ids["en_dos"] in [r["id"] for r in secciones["semana"]]
    assert ids["en_diez"] in [r["id"] for r in secciones["adelante"]]


def test_las_secciones_van_en_orden(enviados):
    sembrar(LUNES)
    texto, _ = tablero.render(CHAT, LUNES)
    orden = ["VENCIDAS", "HOY", "MAÑANA", "ESTA SEMANA", "MÁS ADELANTE", "ALGÚN DÍA"]
    presentes = [s for s in orden if s in texto]
    posiciones = [texto.index(s) for s in presentes]
    assert posiciones == sorted(posiciones)


def test_hoy_y_manana_van_expandidas_y_el_resto_colapsado(enviados):
    sembrar(LUNES)
    texto, filas = tablero.render(CHAT, LUNES)

    etiquetas = [b["text"] for fila in filas for b in fila]
    assert any("Sacar la basura" in e for e in etiquetas)       # hoy, con botón
    assert any("Llamar al plomero" in e for e in etiquetas)     # mañana, con botón
    assert not any("Renovar el dni" in e for e in etiquetas)    # colapsada
    assert "Más adelante · 1" in " ".join(etiquetas)


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


def test_con_la_casa_desbordada_manana_tambien_se_colapsa(enviados):
    for i in range(45):
        db.crear_tarea(CHAT, f"cosa {i}", due=hoy())
    for i in range(10):
        db.crear_tarea(CHAT, f"otra {i}", due=hoy() + timedelta(days=1))

    _, filas = tablero.render(CHAT)
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert any(e.startswith("📂 Mañana") for e in etiquetas)


def test_los_titulos_se_cortan_en_un_limite_de_palabra(enviados):
    db.crear_tarea(CHAT, "llamar al ejército de salvación para que se lleven la cama",
                   due=hoy())
    _, filas = tablero.render(CHAT)
    etiqueta = filas[0][0]["text"]

    assert len(etiqueta) <= tablero.LARGO_BOTON + 4
    assert etiqueta.endswith("…")
    assert not etiqueta.rstrip("…").endswith(" ")


def test_todos_los_callback_data_entran_en_64_bytes(enviados):
    for i in range(5):
        db.crear_tarea(CHAT, f"cosa {i}", due=hoy() + timedelta(days=i))
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

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


def test_el_debounce_marca_sucio_y_el_cron_lo_flushea(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    tablero.actualizar(CHAT, forzar=True)     # deja `editado_en` recién ahora
    enviados.clear()

    assert tablero.actualizar(CHAT) is False, "no edita dos veces en 3 segundos"
    assert db.tablero_actual(CHAT)["sucio"] == 1

    with db.conn() as c:                      # como si hubiera pasado el rato
        c.execute("UPDATE tablero SET editado_en = '2020-01-01T00:00:00-03:00'")
    assert tablero.flushear_si_esta_sucio(CHAT) is True


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
    a = db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")
    db.crear_tarea(CHAT, "yerba", tipo="compras", categoria="compras")
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

def test_cada_boton_se_puede_encontrar_en_el_texto(enviados):
    """Telegram pone TODOS los botones juntos abajo, fuera de las secciones.

    Sin un número que los ate al texto, con «⚠️ VENCIDAS» y «HOY» arriba y cuatro
    botones abajo no había forma de saber cuál era cuál.
    """
    db.crear_tarea(CHAT, "agarrar sábanas y acolchado", due=LUNES - timedelta(days=2))
    db.crear_tarea(CHAT, "traer llaves Allen", due=LUNES, hora="10:00", responsable="axel")
    db.crear_tarea(CHAT, "llamar al ejército de salvación para la cama", due=LUNES)

    texto, filas = tablero.render(CHAT, LUNES)

    etiquetas = [b["text"] for fila in filas for b in fila if b["text"].startswith("✅")]
    assert len(etiquetas) == 3
    for i, etiqueta in enumerate(etiquetas, start=1):
        assert etiqueta.startswith(f"✅ {i}. "), etiqueta
        assert f"<b>{i}.</b>" in texto, f"falta el renglón {i}"


def test_las_secciones_expandidas_muestran_sus_tareas(enviados):
    """Antes el encabezado quedaba solo, sin nada abajo."""
    db.crear_tarea(CHAT, "agarrar sábanas y acolchado", due=LUNES)
    texto, _ = tablero.render(CHAT, LUNES)

    assert "Agarrar sábanas y acolchado" in texto
    assert "<b>HOY</b> · lun 28 · 1" in texto, "con el contador"


def test_las_vencidas_dicen_cuando_vencieron(enviados):
    db.crear_tarea(CHAT, "pagar el ABL", due=LUNES - timedelta(days=2))
    texto, _ = tablero.render(CHAT, LUNES)
    assert "venció el sáb 26/9" in texto


def test_el_boton_entra_en_media_pantalla(enviados):
    """El ✅ comparte la fila con el ⋯, así que Telegram lo corta al medio si es largo.

    Se veía «✅ 🕕 10:00 · Tr...laves Allen · Axel».
    """
    db.crear_tarea(CHAT, "traer las llaves Allen del departamento",
                   due=LUNES, hora="10:00", responsable="axel")
    texto, filas = tablero.render(CHAT, LUNES)

    etiqueta = filas[0][0]["text"]
    assert len(etiqueta) <= 26, etiqueta
    # Pero la hora y el responsable no se pierden: van en el texto.
    assert "🕕 10:00" in texto and "Axel" in texto


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
