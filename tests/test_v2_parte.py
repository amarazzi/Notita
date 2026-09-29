"""El parte diario: el único envío programado.

Dos garantías que se prueban acá: sale **una sola vez por día** (el cron lo llama
cada 15 minutos) y **nada se mueve solo** (en v1 las vencidas se auto-posponían).
"""
from datetime import date, datetime, time, timedelta

import pytest

from notita import cb, config, db, handlers, parte
from notita.dates import hoy

from .conftest import CHAT
from .test_v2_captura import click, textos

SABADO = date(2026, 9, 26)


def momento(d: date, hora: int, minuto: int = 0):
    return datetime.combine(d, time(hora, minuto), tzinfo=config.TZ)


# --------------------------------------------------------------------------
# Idempotencia
# --------------------------------------------------------------------------

def test_sale_una_sola_vez_por_dia(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))

    primera = parte.correr(CHAT, momento=momento(SABADO, 20, 0))
    segunda = parte.correr(CHAT, momento=momento(SABADO, 20, 15))
    tercera = parte.correr(CHAT, momento=momento(SABADO, 20, 30))

    assert [primera["parte"], segunda["parte"], tercera["parte"]] == [True, False, False]
    partes = [t for t in textos(enviados) if "Para mañana" in t]
    assert len(partes) == 1


def test_no_sale_antes_de_la_hora(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))

    assert parte.correr(CHAT, momento=momento(SABADO, 19, 45))["parte"] is False
    assert textos(enviados) == []


def test_el_parte_es_el_unico_que_suena(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))
    parte.correr(CHAT, momento=momento(SABADO, 20, 0))

    envio = [e for e in enviados if e["metodo"] == "sendMessage"][-1]
    assert envio.get("disable_notification") is not True


def test_si_no_salio_anoche_avisa_al_dia_siguiente(enviados):
    db.anotar_parte(SABADO - timedelta(days=2))    # el último fue anteayer
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))

    hecho = parte.correr(CHAT, momento=momento(SABADO, 20, 0))

    assert hecho["atrasado"] is True
    assert any("se me pasó el parte" in t for t in textos(enviados))
    assert any("Para mañana" in t for t in textos(enviados))


def test_no_se_manda_tarde_el_mismo_dia(enviados):
    """A las 23:59 ya no tiene sentido: se avisa al otro día."""
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))
    parte.correr(CHAT, momento=momento(SABADO, 23, 59))
    enviados.clear()

    # El del sábado ya quedó marcado, así que el domingo no lo repite.
    hecho = parte.correr(CHAT, momento=momento(SABADO, 23, 59))
    assert hecho["parte"] is False


def test_en_una_instalacion_nueva_no_se_disculpa(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))
    hecho = parte.correr(CHAT, momento=momento(SABADO, 20, 0))
    assert hecho["atrasado"] is False


# --------------------------------------------------------------------------
# Contenido
# --------------------------------------------------------------------------

def test_lo_que_tiene_hora_va_primero(enviados):
    manana = SABADO + timedelta(days=1)
    db.crear_tarea(CHAT, "comprar la cómoda", due=manana, responsable="ambos")
    db.crear_tarea(CHAT, "llevar a Milo al veterinario", due=manana, hora="18:00",
                   responsable="barbu", categoria="mascotas")

    texto, _ = parte.render(CHAT, SABADO)

    assert texto.index("18:00") < texto.index("cómoda")
    assert "Para mañana, dom 27" in texto


def test_las_vencidas_de_hoy_traen_botones(enviados):
    db.crear_tarea(CHAT, "agarrar sábanas", due=SABADO)
    texto, filas = parte.render(CHAT, SABADO)

    assert "Quedó de hoy: Agarrar sábanas" in texto
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert any("✅" in e for e in etiquetas)
    assert any("A mañana" in e for e in etiquetas)


def test_con_muchas_vencidas_se_agrupan(enviados):
    for i in range(8):
        db.crear_tarea(CHAT, f"cosa {i}", due=SABADO - timedelta(days=1))
    texto, filas = parte.render(CHAT, SABADO)

    assert "Quedaron <b>8</b> sin hacer" in texto
    etiquetas = [b["text"] for fila in filas for b in fila]
    assert any("Pasar todas a mañana" in e for e in etiquetas)


def test_las_compras_se_cuentan(enviados):
    db.crear_tarea(CHAT, "leche", compra=True, categoria="compras")
    db.crear_tarea(CHAT, "yerba", compra=True, categoria="compras")
    texto, _ = parte.render(CHAT, SABADO)
    assert "En compras hay <b>2</b> cosas" in texto


def test_una_compra_con_fecha_va_como_cualquier_cosa(enviados):
    """Ya no hay sección aparte: una compra de mañana es una cosa de mañana."""
    db.crear_tarea(CHAT, "Falta carbón", compra=True, due=SABADO + timedelta(days=1))
    db.crear_tarea(CHAT, "Falta yerba", compra=True)

    texto, _ = parte.render(CHAT, SABADO)

    assert "Falta carbón" in texto
    assert "En compras hay <b>1</b> cosas" in texto, "las sueltas sólo se cuentan"


def test_sin_nada_manda_el_corto(enviados):
    texto, filas = parte.render(CHAT, SABADO)
    assert "Mañana libre" in texto
    assert filas == []


def test_sin_nada_y_configurado_para_callarse(enviados, monkeypatch):
    monkeypatch.setattr(config, "PARTE_VACIO", False)
    texto, _ = parte.render(CHAT, SABADO)
    assert texto == ""


# --------------------------------------------------------------------------
# Nada se mueve solo
# --------------------------------------------------------------------------

def test_una_vencida_sigue_vencida_despues_del_parte(enviados):
    item_id = db.crear_tarea(CHAT, "agarrar sábanas", due=SABADO)

    parte.correr(CHAT, momento=momento(SABADO, 20, 0))

    assert db.obtener(item_id)["due_date"] == SABADO.isoformat(), "nada se mueve solo"
    assert db.obtener(item_id)["estado"] == "pendiente"


def test_pasar_todas_a_manana_lo_pide_una_persona(enviados):
    a = db.crear_tarea(CHAT, "una", due=SABADO - timedelta(days=2))
    b = db.crear_tarea(CHAT, "dos", due=SABADO)
    c_ = db.crear_tarea(CHAT, "de mañana", due=SABADO + timedelta(days=1))

    movidas = parte.pasar_todas_a_manana(CHAT, SABADO)

    assert movidas == 2
    manana = (SABADO + timedelta(days=1)).isoformat()
    assert db.obtener(a)["due_date"] == manana
    assert db.obtener(b)["due_date"] == manana
    assert db.obtener(c_)["due_date"] == manana


def test_el_boton_del_parte_mueve_las_vencidas(enviados):
    db.crear_tarea(CHAT, "una", due=hoy() - timedelta(days=1))
    db.crear_tarea(CHAT, "dos", due=hoy())

    handlers.handle_update(click(cb.armar("pt")))

    manana = (hoy() + timedelta(days=1)).isoformat()
    assert all(r["due_date"] == manana for r in db.pendientes(CHAT, compra=False))


# --------------------------------------------------------------------------
# Pausa y limpieza
# --------------------------------------------------------------------------

def test_en_pausa_no_manda_el_parte(enviados):
    db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))
    db.pausar(CHAT, SABADO + timedelta(days=5))

    hecho = parte.correr(CHAT, momento=momento(SABADO, 20, 0))

    assert hecho["parte"] is False
    assert hecho["pausada_hasta"] == (SABADO + timedelta(days=5)).isoformat()
    assert textos(enviados) == []


def test_el_cron_limpia_los_temporales(enviados):
    db.anotar_temporal(CHAT, 42, "menu", 5)
    with db.conn() as c:
        c.execute("UPDATE mensajes_temporales SET expira_at = '2020-01-01T00:00:00-03:00'")

    hecho = parte.correr(CHAT, momento=momento(SABADO, 10, 0))

    assert hecho["temporales"] == 1


def test_el_cron_manda_lo_que_quedo_en_la_cola(enviados, monkeypatch):
    db.encolar_saliente(CHAT, "una confirmación que no salió", None)

    hecho = parte.correr(CHAT, momento=momento(SABADO, 10, 0))

    assert hecho["de_la_cola"] == 1


def test_el_cron_vence_las_propuestas(enviados):
    db.guardar_propuesta(CHAT, {"accion": "completar", "ids": [1]})
    with db.conn() as c:
        c.execute("UPDATE propuestas SET expira_at = '2020-01-01T00:00:00-03:00'")

    parte.correr(CHAT, momento=momento(SABADO, 10, 0))

    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM propuestas").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# Sin Gemini
# --------------------------------------------------------------------------

def test_el_parte_y_el_tablero_no_usan_el_llm(enviados, monkeypatch):
    from notita import llm, tablero

    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm, "interpretar_mensaje",
                        lambda *a, **k: pytest.fail("el parte no usa el LLM"))
    monkeypatch.setattr(llm, "_call", lambda *a, **k: pytest.fail("ni una llamada"))
    item_id = db.crear_tarea(CHAT, "sacar la basura", due=SABADO + timedelta(days=1))

    hecho = parte.correr(CHAT, momento=momento(SABADO, 20, 0))
    texto, filas = tablero.render(CHAT, SABADO)
    handlers.handle_update(click(cb.armar("ok", item_id)))

    assert hecho["parte"] is True
    assert "La casa" in texto
    assert db.obtener(item_id)["estado"] == "hecha"
