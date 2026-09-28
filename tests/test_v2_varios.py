"""Lo demás que pide el brief: calendario, menús, recurrencias, «che» y sin Gemini."""
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from notita import calendario, cb, config, db, handlers, menus, telegram, views
from notita.dates import Recurrencia, hoy, proxima_ocurrencia

from .conftest import CHAT
from .test_v2_captura import botones, click, item, mensaje, responde, textos


# --------------------------------------------------------------------------
# Calendario
# --------------------------------------------------------------------------

def test_el_link_de_calendario_esta_bien_armado(enviados):
    tid = db.crear_tarea(CHAT, "llevar a Milo al veterinario & control (ñoño)",
                         due=date(2026, 10, 1), hora="18:30")

    url = calendario.link(db.obtener(tid))
    partes = parse_qs(urlparse(url).query)

    assert partes["text"] == ["Llevar a Milo al veterinario & control (ñoño)"]
    assert partes["dates"] == ["20261001T183000/20261001T193000"]
    assert partes["ctz"] == ["America/Argentina/Buenos_Aires"]
    # El & y la ñ van escapados en la URL cruda, no cortan la query.
    assert "%26" in url and "%C3%B1" in url


def test_el_ics_es_valido(enviados):
    tid = db.crear_tarea(CHAT, "turno con el dentista; llevar la orden",
                         due=date(2026, 10, 1), hora="09:00")

    texto = calendario.ics(db.obtener(tid))

    assert texto.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in texto and "END:VEVENT" in texto
    assert "DTSTART;TZID=America/Argentina/Buenos_Aires:20261001T090000" in texto
    assert "DTEND;TZID=America/Argentina/Buenos_Aires:20261001T100000" in texto
    assert "TRIGGER:-PT1H" in texto
    assert r"\;" in texto, "el punto y coma se escapa"
    assert texto.endswith("END:VCALENDAR\r\n")


def test_el_boton_de_calendario_aparece_si_hay_hora(enviados, monkeypatch):
    responde(monkeypatch, items=[item("llevar a Milo al veterinario",
                                      fecha_kind="manana", fecha_hora=18,
                                      fecha_minuto=0)])
    handlers.handle_update(mensaje("llevar a Milo al veterinario mañana a las 18"))

    assert any("calendario" in b["text"].lower() and "url" in b
               for b in botones(enviados))


def test_sin_hora_no_hay_boton_de_calendario(enviados, monkeypatch):
    responde(monkeypatch, items=[item("limpiar la heladera", fecha_kind="manana")])
    handlers.handle_update(mensaje("limpiar la heladera mañana"))

    assert not any("calendario" in b["text"].lower() for b in botones(enviados))


def test_con_ics_manda_un_archivo(enviados, monkeypatch):
    monkeypatch.setattr(config, "CALENDARIO", "ics")
    tid = db.crear_tarea(CHAT, "turno", due=hoy(), hora="18:00")

    handlers.handle_update(click(cb.armar("ics", tid)))

    assert any(e["metodo"] == "sendDocument" for e in enviados)


# --------------------------------------------------------------------------
# El menú «⋯»
# --------------------------------------------------------------------------

def test_el_menu_muestra_el_titulo_completo(enviados):
    largo = "llamar al ejército de salvación para que se lleven la cama de arriba"
    tid = db.crear_tarea(CHAT, largo, due=hoy())
    enviados.clear()

    handlers.handle_update(click(cb.armar("m", tid)))

    assert largo.capitalize() in textos(enviados)[0]


def test_mover_a_manana_desde_el_menu(enviados):
    tid = db.crear_tarea(CHAT, "limpiar el horno", due=hoy())

    handlers.handle_update(click(cb.armar("d", tid, "m")))

    assert db.obtener(tid)["due_date"] == (hoy() + timedelta(days=1)).isoformat()


def test_algun_dia_saca_la_fecha(enviados):
    tid = db.crear_tarea(CHAT, "pintar el balcón", due=hoy())
    handlers.handle_update(click(cb.armar("d", tid, "a")))
    assert db.obtener(tid)["due_date"] is None


def test_cambiar_el_responsable(enviados):
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    handlers.handle_update(click(cb.armar("q", tid, "barbu")))
    assert db.obtener(tid)["responsable"] == "barbu"


def test_mover_al_super_y_volver(enviados):
    tid = db.crear_tarea(CHAT, "comprar pan", due=hoy(), hora="18:00")

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="a"))
    fila = db.obtener(tid)
    assert fila["tipo"] == "compras"
    assert fila["due_date"] is None and fila["due_hora"] is None

    handlers.handle_update(click(cb.armar("sw", tid), cq_id="b"))
    assert db.obtener(tid)["tipo"] == "casa"


def test_renombrar_pide_respuesta_y_no_pasa_por_el_llm(enviados, monkeypatch):
    from notita import llm

    monkeypatch.setattr(llm, "interpretar_mensaje",
                        lambda *a, **k: pytest.fail("renombrar no usa el LLM"))
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    handlers.handle_update(click(cb.armar("r", tid)))

    pregunta = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    assert pregunta["reply_markup"] == {"force_reply": True, "selective": True}

    respuesta = mensaje("regar las plantas del balcón", update_id=50)
    respuesta["message"]["reply_to_message"] = {"from": {"is_bot": True}}
    handlers.handle_update(respuesta)

    assert db.obtener(tid)["texto"] == "regar las plantas del balcón"


def test_un_mensaje_cualquiera_no_se_come_el_force_reply(enviados, monkeypatch):
    """El principio 3: si no es una respuesta, es un mensaje nuevo."""
    tid = db.crear_tarea(CHAT, "regar", due=hoy())
    handlers.handle_update(click(cb.armar("r", tid)))
    responde(monkeypatch, items=[item("comprar pan", fecha_kind="hoy")])

    handlers.handle_update(mensaje("hay que comprar pan", update_id=51))

    assert db.obtener(tid)["texto"] == "regar", "no lo renombró"
    assert any(r["texto"] == "comprar pan" for r in db.pendientes(CHAT))


def test_borrar_ofrece_deshacer(enviados):
    tid = db.crear_tarea(CHAT, "pintar el balcón", due=hoy())
    db.anotar_temporal(CHAT, 55, "menu", 5)      # como si el menú estuviera abierto
    enviados.clear()

    handlers.handle_update(click(cb.armar("x", tid), message_id=55, cq_id="del"))

    assert db.obtener(tid)["estado"] == "borrada"
    editados = [e for e in enviados if e["metodo"] == "editMessageText"]
    etiquetas = [b["text"] for e in editados
                 for fila in (e.get("reply_markup") or {}).get("inline_keyboard", [])
                 for b in fila]
    assert any("Deshacer" in e for e in etiquetas)


def test_una_recurrente_pregunta_si_es_una_o_todas(enviados):
    tid = db.crear_tarea(CHAT, "pagar el ABL", due=hoy(),
                         recurrencia=Recurrencia("mensual", monthday=10))
    handlers.handle_update(click(cb.armar("m", tid), cq_id="menu"))
    enviados.clear()

    handlers.handle_update(click(cb.armar("x", tid), message_id=5, cq_id="del"))

    assert db.obtener(tid)["estado"] == "pendiente", "todavía no borró nada"
    editado = [e for e in enviados if e["metodo"] == "editMessageText"][-1]
    etiquetas = [b["text"] for fila in editado["reply_markup"]["inline_keyboard"]
                 for b in fila]
    assert "Sólo esta vez" in etiquetas and "Todas" in etiquetas


# --------------------------------------------------------------------------
# Recurrencias
# --------------------------------------------------------------------------

def test_cada_tres_dias_arranca_hoy(enviados, monkeypatch):
    responde(monkeypatch, items=[item("regar las plantas", fecha_kind="desconocida",
                                      recur_kind="diaria", recur_interval=3)])
    handlers.handle_update(mensaje("regar las plantas cada 3 días"))

    assert db.pendientes(CHAT)[0]["due_date"] == hoy().isoformat()


def test_al_completarla_la_proxima_sale_de_la_fecha_que_debia(enviados):
    """Sin generar ocurrencias atrasadas: la próxima se cuenta desde el vencimiento."""
    vencia = hoy() - timedelta(days=10)
    tid = db.crear_tarea(CHAT, "regar", due=vencia,
                         recurrencia=Recurrencia("diaria", interval=3))

    nueva = db.marcar_hecha(tid, "axel")

    from notita.dates import de_iso

    assert de_iso(nueva["due_date"]) > hoy(), "la próxima no puede quedar vencida"


def test_el_intervalo_se_ve_en_el_tablero_y_en_el_menu(enviados):
    from notita import tablero

    tid = db.crear_tarea(CHAT, "regar", due=hoy(),
                         recurrencia=Recurrencia("diaria", interval=3))
    _, filas = tablero.render(CHAT)
    etiquetas = " ".join(b["text"] for fila in filas for b in fila)
    assert "🔁" in etiquetas, "en el botón se ve que se repite"

    handlers.handle_update(click(cb.armar("m", tid)))
    assert "cada 3 días" in textos(enviados)[-1], "y el intervalo, en el menú"


# --------------------------------------------------------------------------
# Textos: nada de «che»
# --------------------------------------------------------------------------

# `llm.py` tiene el "che" de la prohibición en el prompt, y `heuristica.py` lo tiene
# como palabra a IGNORAR de los mensajes que entran. Ninguno se le dice a nadie.
SIN_TEXTOS_AL_USUARIO = {"llm.py", "heuristica.py"}


def test_ningun_texto_al_usuario_dice_che():
    """El brief lo prohíbe. Se recorren los strings de los módulos que hablan."""
    import ast
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parent.parent
    culpables = []
    for archivo in sorted((raiz / "notita").glob("*.py")):
        if archivo.name in SIN_TEXTOS_AL_USUARIO:
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
                if re.search(r"\bche\b", nodo.value, re.IGNORECASE):
                    culpables.append(f"{archivo.name}:{nodo.lineno}")
    assert culpables == [], f"«che» en {culpables}"


def test_el_prompt_prohibe_el_che():
    from notita import llm

    assert 'NUNCA uses "che"' in llm._sistema()


def test_la_ayuda_cuenta_el_modelo_nuevo():
    texto = views.ayuda()
    for pista in ("tablero", "✅", "⋯", "parte", "calendario"):
        assert pista in texto, pista
    assert "horas exactas" in texto


# --------------------------------------------------------------------------
# Sin Gemini, todo lo que no es capturar funciona igual
# --------------------------------------------------------------------------

def test_sin_gemini_el_tablero_los_botones_y_el_parte(enviados, monkeypatch):
    from notita import llm, parte, tablero

    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm, "_call", lambda *a, **k: pytest.fail("ni una llamada al LLM"))

    tid = db.crear_tarea(CHAT, "sacar la basura", due=hoy())
    texto, filas = tablero.render(CHAT)
    assert "Sacar la basura" in " ".join(b["text"] for fila in filas for b in fila)

    handlers.handle_update(click(cb.armar("ok", tid)))
    assert db.obtener(tid)["estado"] == "hecha"

    texto_parte, _ = parte.render(CHAT)
    assert texto_parte


def test_sin_gemini_se_puede_capturar(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    handlers.handle_update(mensaje("hay que limpiar la heladera el lunes"))

    assert db.pendientes(CHAT)[0]["texto"] == "limpiar la heladera"


# --------------------------------------------------------------------------
# Bienvenida
# --------------------------------------------------------------------------

def test_la_bienvenida_sale_una_sola_vez(enviados, casa_nueva, monkeypatch):
    responde(monkeypatch, intencion="charla", comentario="hola 🤍")

    handlers.handle_update(mensaje("hola", update_id=1))
    handlers.handle_update(mensaje("hola", update_id=2))

    bienvenidas = [t for t in textos(enviados) if "Notita cambió" in t]
    assert len(bienvenidas) == 1
    assert db.tablero_actual(CHAT)["message_id"] is not None


def test_la_bienvenida_cuenta_los_recados_descartados(enviados, casa_nueva, monkeypatch):
    db.ajuste("recados_descartados", "2")
    responde(monkeypatch, intencion="charla", comentario="hola")

    handlers.handle_update(mensaje("hola"))

    bienvenida = [t for t in textos(enviados) if "Notita cambió" in t][0]
    assert "2 recados" in bienvenida
