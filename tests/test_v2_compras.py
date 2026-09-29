"""«Súper» pasó a llamarse «Compras», y el tipo dejó de depender de la fecha.

El bug que lo motivó: un ítem de compras con fecha apareció en el tablero bajo
MAÑANA, con 🛒 y conservando el «Comprar» adelante.
"""
from datetime import timedelta

import pytest

from notita import cb, db, handlers, menus, parte, tablero, telegram, views
from notita.dates import hoy

from .conftest import CHAT
from .test_v2_captura import botones, click, item, mensaje, responde, textos

MANANA = hoy() + timedelta(days=1)


# --------------------------------------------------------------------------
# El tipo lo define qué es la cosa, no si tiene fecha
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto,titulo,tipo_llm,fecha_kind,espera_tipo,espera_fecha", [
    ("falta leche", "Leche", "compras", "desconocida", "compras", None),
    ("mañana compramos leche", "Leche", "compras", "manana", "compras", MANANA),
    ("para el asado del sábado falta carbón", "Carbón", "compras", "dia_semana",
     "compras", None),           # el weekday lo resuelve el parser; acá importa el tipo
    ("comprar la cómoda mañana", "Comprar la cómoda", "tarea", "manana", "casa", MANANA),
    ("comprar regalo para mamá antes del domingo", "Comprar el regalo de mamá",
     "tarea", "dia_semana", "casa", None),
])
def test_donde_cae_cada_cosa(enviados, monkeypatch, texto, titulo, tipo_llm,
                             fecha_kind, espera_tipo, espera_fecha):
    extra = {"fecha_weekday": 5} if fecha_kind == "dia_semana" else {}
    responde(monkeypatch, items=[item(titulo, tipo=tipo_llm, fecha_kind=fecha_kind,
                                      **extra)])

    handlers.handle_update(mensaje(texto))

    filas = db.pendientes(CHAT, tipo=espera_tipo)
    assert len(filas) == 1, f"{texto!r} no fue a {espera_tipo}"
    if espera_fecha is not None:
        assert filas[0]["due_date"] == espera_fecha.isoformat()


def test_una_compra_con_fecha_no_lleva_el_verbo(enviados, monkeypatch):
    responde(monkeypatch, items=[item("comprar carbón", tipo="compras",
                                      fecha_kind="dia_semana", fecha_weekday=5)])

    handlers.handle_update(mensaje("para el asado del sábado hay que comprar carbón"))

    fila = db.pendientes(CHAT, tipo="compras")[0]
    assert fila["texto"] == "carbón", "en compras el verbo no va"
    assert fila["due_date"] is not None, "pero la fecha sí"


def test_una_compra_con_fecha_no_aparece_en_los_dias_del_tablero(enviados):
    """El bug: apareció bajo MAÑANA, entre las tareas."""
    db.crear_tarea(CHAT, "carbón", tipo="compras", categoria="compras", due=MANANA)
    db.crear_tarea(CHAT, "sacar la basura", due=MANANA)

    texto, filas = tablero.render(CHAT)
    secciones = tablero.repartir(db.pendientes(CHAT, tipo="casa"), hoy())

    assert all("arbón" not in r["texto"] for rows in secciones.values() for r in rows)
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert not any("Carbón" in e and e.startswith("✅") for e in etiquetas)
    assert "COMPRAS" in texto


def test_el_tablero_avisa_cuantas_compras_son_para_manana(enviados):
    db.crear_tarea(CHAT, "carbón", tipo="compras", categoria="compras", due=MANANA)
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

    texto, filas = tablero.render(CHAT)

    assert "· 1 para mañana" in texto
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert "🛒 Compras · 2 (1 para mañana)" in etiquetas


def test_si_la_compra_era_para_hoy_no_dice_mañana(enviados):
    """Decir «para mañana» cuando era para hoy es mentir."""
    db.crear_tarea(CHAT, "carbón", tipo="compras", categoria="compras", due=hoy())

    texto, _ = tablero.render(CHAT)

    assert "1 para hoy" in texto
    assert "para mañana" not in texto


def test_la_confirmacion_dice_donde_fue_y_para_cuando(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Carbón", tipo="compras", fecha_kind="manana")])

    handlers.handle_update(mensaje("mañana hay que comprar carbón"))

    confirmacion = textos(enviados)[0]
    assert "a compras" in confirmacion
    assert "para mañana" in confirmacion


def test_una_compra_sin_fecha_dice_solo_a_compras(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Leche", tipo="compras", fecha_kind="desconocida")])
    handlers.handle_update(mensaje("falta leche"))

    confirmacion = textos(enviados)[0]
    assert "a compras" in confirmacion
    assert "para" not in confirmacion.split("a compras")[1]


def test_mover_entre_tipos_conserva_la_fecha(enviados):
    tid = db.crear_tarea(CHAT, "comprar carbón", due=MANANA)

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="a"))
    assert db.obtener(tid)["tipo"] == "compras"
    assert db.obtener(tid)["due_date"] == MANANA.isoformat()

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="b"))
    assert db.obtener(tid)["tipo"] == "casa"
    assert db.obtener(tid)["due_date"] == MANANA.isoformat()


def test_el_parte_nombra_las_compras_de_mañana(enviados):
    db.crear_tarea(CHAT, "carbón", tipo="compras", categoria="compras", due=MANANA)

    texto, _ = parte.render(CHAT, hoy())

    assert "🛒 <b>Para mañana:</b> Carbón" in texto


# --------------------------------------------------------------------------
# El renombre
# --------------------------------------------------------------------------

def test_ningun_texto_visible_dice_super():
    """El único «super» que puede quedar es el alias /super, que no se muestra."""
    import ast
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parent.parent
    culpables = []
    for archivo in sorted((raiz / "notita").glob("*.py")):
        if archivo.name in ("llm.py", "heuristica.py"):
            continue            # prompt y vocabulario de entrada, no se muestran
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        # Los docstrings cuentan bugs viejos («anunció una cómoda al súper»):
        # renombrarlos sería falsear la historia. Sólo importan los textos que salen.
        docstrings = {id(n.body[0].value) for n in ast.walk(arbol)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                      and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(n.body[0].value, ast.Constant)}
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
                    and id(nodo) not in docstrings):
                if re.search(r"súper|Súper|SÚPER", nodo.value):
                    culpables.append(f"{archivo.name}:{nodo.lineno}")
    assert culpables == [], f"«súper» en {culpables}"


def test_el_menu_de_telegram_ofrece_compras():
    registrados = {c for c, _ in telegram.COMANDOS}
    assert "compras" in registrados
    assert "super" not in registrados, "el alias no se muestra"


def test_compras_y_super_hacen_lo_mismo(enviados):
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")

    handlers.handle_update(mensaje("/compras", update_id=1))
    handlers.handle_update(mensaje("/super", update_id=2))

    listas = [t for t in textos(enviados) if "Compras" in t]
    assert len(listas) == 2, "el alias viejo tiene que seguir andando"


def test_la_ayuda_y_la_bienvenida_hablan_de_compras():
    for texto in (views.ayuda(), views.bienvenida()):
        assert "súper" not in texto.lower()
    assert "/compras" in views.ayuda()


def test_la_lista_muestra_la_fecha_de_cada_cosa(enviados):
    db.crear_tarea(CHAT, "carbón", tipo="compras", categoria="compras", due=MANANA)
    db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras")
    enviados.clear()

    menus.abrir_compras(CHAT)

    lista = textos(enviados)[0]
    assert "Compras" in lista and "súper" not in lista.lower()
    assert "mañana" in lista, "la compra con día tiene que decirlo"


# --------------------------------------------------------------------------
# Los menores del reporte
# --------------------------------------------------------------------------

def test_la_propuesta_de_mover_no_arranca_con_un_si_suelto(enviados, monkeypatch):
    """Una variable local `prefijo` pisaba el parámetro (el renglón del audio), así

    que el «Sí, » del botón terminaba arriba del mensaje, como un renglón huérfano.
    """
    for cosa in ("una cosa", "otra cosa"):
        db.crear_tarea(CHAT, cosa, due=MANANA)
    responde(monkeypatch, intencion="modificar", accion="mover", conjunto="manana",
             destino_fecha_kind="hoy")

    handlers.handle_update(mensaje("pasá todo lo de mañana para hoy"))

    propuesta = textos(enviados)[0]
    assert propuesta.startswith("¿Paso estas 2"), repr(propuesta[:40])
    assert "Sí," not in propuesta.split("\n")[0]
    # Pero el botón sí lo dice.
    assert any("Sí, las dos" in b["text"].lower() or "sí, las dos" in b["text"].lower()
               for b in botones(enviados))


def test_con_audio_el_prefijo_sigue_apareciendo(enviados, monkeypatch):
    """El arreglo no puede llevarse puesto el renglón del audio."""
    db.crear_tarea(CHAT, "una cosa", due=MANANA)
    from notita import propuestas

    propuestas.ofrecer(CHAT, "mover", db.pendientes(CHAT), {"fecha": hoy().isoformat()},
                       hoy(), prefijo="🎤 <i>«pasalo a hoy»</i>")

    assert textos(enviados)[0].startswith("🎤")


def test_el_boton_de_elegir_dia_aparece_con_varios_items(enviados, monkeypatch):
    """Antes sólo salía si el mensaje traía un ítem: con dos se perdía."""
    responde(monkeypatch, items=[
        item("pagar la expensa", categoria="pagos", fecha_kind="fecha_exacta",
             fecha_day=31, fecha_month=2),
        item("pagar el ABL", categoria="pagos", fecha_kind="fecha_exacta",
             fecha_day=31, fecha_month=4),
        item("sacar la basura", fecha_kind="hoy")])

    handlers.handle_update(mensaje("pagar la expensa el 31/2, el ABL el 31/4 y sacar la basura"))

    etiquetas = [b["text"] for b in botones(enviados)]
    elegir = [e for e in etiquetas if "Elegir día" in e]
    assert len(elegir) == 2, etiquetas
    assert "Pagar la expensa" in elegir[0], "con varios, dice cuál"


@pytest.mark.parametrize("uno,otro,iguales", [
    ("Comprar la cómoda para la habitación", "Comprar cómoda para la habitación", True),
    ("sacar la basura", "sacar las basuras", False),
    ("llamar al plomero", "llamar plomero", True),
    ("regar las plantas", "regar el balcón", False),
])
def test_los_duplicados_ignoran_los_articulos(enviados, uno, otro, iguales):
    db.crear_tarea(CHAT, uno, due=hoy())

    encontrado = db.pendiente_igual(CHAT, otro, "casa")

    assert (encontrado is not None) is iguales


def test_no_anota_dos_veces_la_comoda(enviados, monkeypatch):
    responde(monkeypatch, items=[item("Comprar la cómoda para la habitación",
                                      fecha_kind="manana")])
    handlers.handle_update(mensaje("comprar la cómoda para la habitación mañana",
                                   update_id=1))
    responde(monkeypatch, items=[item("Comprar cómoda para la habitación",
                                      fecha_kind="manana")])
    handlers.handle_update(mensaje("comprar cómoda para la habitación mañana",
                                   update_id=2))

    assert len(db.pendientes(CHAT, tipo="casa")) == 1
    assert "Ya estaba" in textos(enviados)[-1]
