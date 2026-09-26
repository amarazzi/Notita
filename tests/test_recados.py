"""Tests de los recados: «decile a Axel mañana que lo amo».

Antes esto contestaba «¡Qué lindo mensaje! Se lo digo a Axel» y no guardaba nada:
una promesa que nunca cumplía.
"""
from datetime import date, timedelta

from notita import db, handlers, heuristica, llm, reminders, views
from notita.dates import hoy

from .conftest import CHAT


def mensaje(texto, user_id=222):  # 222 = Barbu
    return {"message": {"chat": {"id": CHAT}, "from": {"id": user_id}, "text": texto,
                        "message_id": 1}}


def textos(enviados):
    return [e.get("text", "") for e in enviados if e["metodo"] == "sendMessage"]


def fake_llm(monkeypatch, **item):
    base = {"texto": "te amo", "tipo": "recado", "categoria": "otros",
            "responsable": "axel", "fecha_kind": "manana", "recur_kind": "ninguna",
            "necesita_aclaracion": False}
    base.update(item)
    monkeypatch.setattr(llm, "interpretar_mensaje", lambda *a, **k: {
        "intencion": "anotar", "es_tarea": True, "comentario": "", "items": [base]})


# --------------------------------------------------------------------------
# Guardar
# --------------------------------------------------------------------------

def test_se_guarda_el_recado(enviados, monkeypatch):
    fake_llm(monkeypatch)
    handlers.handle_update(mensaje("decile a axel mañana que lo amo"))

    recados = db.pendientes(CHAT, tipo="recado")
    assert len(recados) == 1
    r = recados[0]
    assert r["texto"] == "te amo"
    assert r["responsable"] == "axel"          # a quién se le dice
    assert r["created_by"] == "barbu"          # quién lo manda
    assert r["due_date"] == (hoy() + timedelta(days=1)).isoformat()


def test_la_confirmacion_dice_a_quien_y_cuando(enviados, monkeypatch):
    fake_llm(monkeypatch)
    handlers.handle_update(mensaje("decile a axel mañana que lo amo"))

    salida = textos(enviados)[0]
    assert "se lo digo" in salida.lower()
    assert "Axel" in salida
    assert "mañana a las 20:00" in salida     # sin prometer horarios que no existen
    assert "Te amo" in salida                # se muestra con mayúscula, como una nota


def test_sin_fecha_se_entrega_hoy(enviados, monkeypatch):
    fake_llm(monkeypatch, fecha_kind="desconocida")
    handlers.handle_update(mensaje("decile a axel que lo amo"))

    r = db.pendientes(CHAT, tipo="recado")[0]
    assert r["due_date"] == hoy().isoformat()
    # Y no pregunta «¿para cuándo?»: un recado sin fecha es para hoy.
    assert db.get_pending(CHAT) is None
    assert all("cuándo" not in t for t in textos(enviados))


def test_sin_destinatario_es_una_tarea_comun(enviados, monkeypatch):
    fake_llm(monkeypatch, responsable="ninguno", texto="avisar del turno")
    handlers.handle_update(mensaje("avisar del turno"))

    assert db.pendientes(CHAT, tipo="recado") == []
    assert db.pendientes(CHAT, tipo="casa")[0]["texto"] == "avisar del turno"


def test_el_recado_no_ensucia_las_listas(enviados, monkeypatch):
    fake_llm(monkeypatch)
    handlers.handle_update(mensaje("decile a axel que lo amo"))
    db.crear_tarea(CHAT, "limpiar el baño", categoria="limpieza", due=hoy())

    listado = views.render_todo(CHAT)
    assert "te amo" not in listado          # no es una tarea de la casa
    assert "Limpiar el baño" in listado
    assert views.render_super(CHAT)[0].startswith("La lista del super está vacía")


# --------------------------------------------------------------------------
# Entregar
# --------------------------------------------------------------------------

def test_se_entrega_a_las_20(enviados):
    db.crear_tarea(CHAT, "te amo", tipo="recado", responsable="axel",
                   due=hoy(), created_by="barbu")

    res = reminders.correr_rutina_diaria(ref=hoy())

    assert res["recados"] == 1
    salida = textos(enviados)[0]
    assert "💌" in salida
    assert 'tg://user?id=111' in salida      # menciona a Axel de verdad
    assert "Barbu te manda a decir" in salida
    assert "Te amo" in salida
    # Queda entregado: no se repite mañana.
    assert db.pendientes(CHAT, tipo="recado") == []
    assert reminders.correr_rutina_diaria(ref=hoy() + timedelta(days=1))["recados"] == 0


def test_no_se_entrega_antes_de_la_fecha(enviados):
    db.crear_tarea(CHAT, "te amo", tipo="recado", responsable="axel",
                   due=hoy() + timedelta(days=2), created_by="barbu")
    assert reminders.correr_rutina_diaria(ref=hoy())["recados"] == 0
    assert db.pendientes(CHAT, tipo="recado")


def test_un_recado_atrasado_se_entrega_igual(enviados):
    # Si el bot estuvo caído, el recado no se pierde.
    db.crear_tarea(CHAT, "te extraño", tipo="recado", responsable="axel",
                   due=hoy() - timedelta(days=3), created_by="barbu")
    assert reminders.correr_rutina_diaria(ref=hoy())["recados"] == 1


def test_el_recado_no_lleva_botones_de_hecho(enviados):
    db.crear_tarea(CHAT, "te amo", tipo="recado", responsable="axel",
                   due=hoy(), created_by="barbu")
    reminders.correr_rutina_diaria(ref=hoy())

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][0]
    assert "reply_markup" not in envio  # no es una tarea: no hay nada que marcar


def test_el_recado_va_antes_del_resumen_del_domingo(enviados):
    domingo = date(2026, 9, 27)
    db.crear_tarea(CHAT, "te amo", tipo="recado", responsable="axel",
                   due=domingo, created_by="barbu")
    db.crear_tarea(CHAT, "regar las plantas", due=domingo)

    reminders.correr_rutina_diaria(ref=domingo)

    salida = textos(enviados)
    assert "Te amo" in salida[0]            # lo más lindo primero
    assert "Resumen de la semana" in salida[1]


# --------------------------------------------------------------------------
# Modo local (sin Gemini)
# --------------------------------------------------------------------------

def test_recado_sin_llm(enviados, monkeypatch):
    monkeypatch.setattr(__import__("notita").config, "GEMINI_API_KEY", "")
    handlers.handle_update(mensaje("decile a Axel mañana que lo amo"))

    r = db.pendientes(CHAT, tipo="recado")[0]
    assert r["responsable"] == "axel"
    assert r["texto"] == "lo amo"   # sin LLM no se corrige la persona gramatical
    assert r["due_date"] == (hoy() + timedelta(days=1)).isoformat()


def test_no_se_puede_mandar_un_recado_a_uno_mismo():
    # «decile a barbu...» escrito por Barbu no es un recado.
    assert heuristica._recado("decile a barbu que se apure", "barbu") is None
    assert heuristica._recado("decile a axel que se apure", "barbu")["responsable"] == "axel"


def test_un_nombre_que_no_vive_en_la_casa_no_es_recado():
    assert heuristica._recado("decile a la vecina que baje la música", "axel") is None
