"""El lenguaje propone y el toque confirma.

El principio 2: un pedido de modificación por texto NO se ejecuta. Si el LLM
entendió mal, no pasa nada hasta que alguien toca un botón.
"""
from datetime import timedelta

import pytest

from notita import config, db, handlers, llm
from notita.dates import hoy

from .conftest import CHAT
from .test_v2_captura import botones, click, mensaje, responde, textos


def sembrar_super():
    return {
        "leche": db.crear_tarea(CHAT, "leche", tipo="compras", categoria="compras"),
        "lavandina": db.crear_tarea(CHAT, "lavandina", tipo="compras", categoria="compras"),
    }


# --------------------------------------------------------------------------
# Nada se ejecuta hasta el toque
# --------------------------------------------------------------------------

def test_ya_compre_la_leche_y_la_lavandina_no_modifica_nada_todavia(
        enviados, monkeypatch):
    sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche", "lavandina"])

    handlers.handle_update(mensaje("ya compré la leche y la lavandina"))

    assert len(db.pendientes(CHAT, tipo="compras")) == 2, "todavía no se tocó nada"
    propuesta = textos(enviados)[0]
    assert "¿Tacho estas 2?" in propuesta
    etiquetas = [b["text"] for b in botones(enviados)]
    assert any("Las dos" in e for e in etiquetas)
    assert "No" in etiquetas


def test_al_tocar_las_dos_se_tachan_las_dos(enviados, monkeypatch):
    sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche", "lavandina"])
    handlers.handle_update(mensaje("ya compré la leche y la lavandina"))
    boton = [b for b in botones(enviados) if "Las dos" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))

    assert db.pendientes(CHAT, tipo="compras") == []
    resultado = [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]
    assert "Tachadas" in resultado


def test_se_puede_tachar_una_sola(enviados, monkeypatch):
    ids = sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche", "lavandina"])
    handlers.handle_update(mensaje("ya compré la leche y la lavandina"))
    boton = [b for b in botones(enviados) if "Leche" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))

    assert db.obtener(ids["leche"])["estado"] == "hecha"
    assert db.obtener(ids["lavandina"])["estado"] == "pendiente"


def test_si_una_ya_estaba_tachada_lo_dice(enviados, monkeypatch):
    ids = sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche", "lavandina"])
    handlers.handle_update(mensaje("ya compré la leche y la lavandina"))
    db.marcar_hecha(ids["leche"], "barbu")        # Barbu se adelantó
    boton = [b for b in botones(enviados) if "Las dos" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))

    resultado = [e["text"] for e in enviados if e["metodo"] == "editMessageText"][-1]
    assert "Lavandina" in resultado
    assert "ya la había tachado Barbu" in resultado
    assert db.pendientes(CHAT, tipo="compras") == []


def test_el_no_no_toca_nada(enviados, monkeypatch):
    sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche"])
    handlers.handle_update(mensaje("ya compré la leche"))
    boton = [b for b in botones(enviados) if b["text"] == "No"][0]

    handlers.handle_update(click(boton["callback_data"]))

    assert len(db.pendientes(CHAT, tipo="compras")) == 2
    assert "no toqué nada" in [e["text"] for e in enviados
                               if e["metodo"] == "editMessageText"][-1]


def test_una_propuesta_vencida_no_se_ejecuta(enviados, monkeypatch):
    sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="completar",
             referencias=["leche"])
    handlers.handle_update(mensaje("ya compré la leche"))
    boton = [b for b in botones(enviados) if "Leche" in b["text"]][0]
    with db.conn() as c:
        c.execute("UPDATE propuestas SET expira_at = '2020-01-01T00:00:00-03:00'")

    handlers.handle_update(click(boton["callback_data"]))

    assert len(db.pendientes(CHAT, tipo="compras")) == 2
    avisos = [e.get("text") for e in enviados if e["metodo"] == "answerCallbackQuery"]
    assert "venció" in avisos[-1]


# --------------------------------------------------------------------------
# Mover en bloque: el caso «todo lo de mañana para hoy»
# --------------------------------------------------------------------------

def test_pasar_todo_lo_de_manana_para_hoy(enviados, monkeypatch):
    manana = hoy() + timedelta(days=1)
    a = db.crear_tarea(CHAT, "una cosa", due=manana)
    b = db.crear_tarea(CHAT, "otra cosa", due=manana)
    c_ = db.crear_tarea(CHAT, "de hoy", due=hoy())
    responde(monkeypatch, intencion="modificar", accion="mover", conjunto="manana",
             destino_fecha_kind="hoy")

    handlers.handle_update(mensaje("todas las cosas que están para mañana pasalas a hoy"))

    propuesta = textos(enviados)[0]
    assert "¿Paso estas 2 a hoy" in propuesta
    assert all(db.obtener(i)["due_date"] == manana.isoformat() for i in (a, b))

    boton = [b for b in botones(enviados) if "las dos" in b["text"].lower()][0]
    handlers.handle_update(click(boton["callback_data"]))

    assert all(db.obtener(i)["due_date"] == hoy().isoformat() for i in (a, b, c_))


def test_vaciar_el_super_se_propone(enviados, monkeypatch):
    sembrar_super()
    responde(monkeypatch, intencion="modificar", accion="vaciar_super")

    handlers.handle_update(mensaje("ya compramos todo"))

    assert len(db.pendientes(CHAT, tipo="compras")) == 2
    assert "¿Tacho estas 2?" in textos(enviados)[0]


def test_sin_candidatos_lo_dice_y_ofrece_el_tablero(enviados, monkeypatch):
    responde(monkeypatch, intencion="modificar", accion="borrar",
             referencias=["la bicicleta"])

    handlers.handle_update(mensaje("borrá lo de la bicicleta"))

    assert "No encontré nada parecido a «la bicicleta»" in textos(enviados)[0]
    assert any("tablero" in b["text"].lower() for b in botones(enviados))


def test_con_demasiados_candidatos_pide_ser_mas_puntual(enviados, monkeypatch):
    for i in range(12):
        db.crear_tarea(CHAT, f"cosa {i}", due=hoy())
    responde(monkeypatch, intencion="modificar", accion="borrar", conjunto="hoy")

    handlers.handle_update(mensaje("borrá todo lo de hoy"))

    assert len(db.pendientes(CHAT, tipo="casa")) == 12
    assert "más puntual" in textos(enviados)[0]


def test_renombrar_y_reasignar_se_proponen(enviados, monkeypatch):
    item_id = db.crear_tarea(CHAT, "regar", due=hoy())
    responde(monkeypatch, intencion="modificar", accion="renombrar",
             referencias=["regar"], destino_texto="regar las plantas del balcón")
    handlers.handle_update(mensaje("cambiá regar por regar las plantas del balcón"))

    assert db.obtener(item_id)["texto"] == "regar", "todavía no"
    boton = [b for b in botones(enviados) if "Regar" in b["text"]][0]
    handlers.handle_update(click(boton["callback_data"]))
    assert db.obtener(item_id)["texto"] == "regar las plantas del balcón"


def test_reasignar_por_propuesta(enviados, monkeypatch):
    item_id = db.crear_tarea(CHAT, "llevar a Milo al veterinario", due=hoy())
    responde(monkeypatch, intencion="modificar", accion="reasignar",
             referencias=["veterinario"], destino_responsable="barbu")
    handlers.handle_update(mensaje("lo del veterinario lo hace Barbu"))
    boton = [b for b in botones(enviados) if "Llevar" in b["text"]][0]

    handlers.handle_update(click(boton["callback_data"]))
    assert db.obtener(item_id)["responsable"] == "barbu"


# --------------------------------------------------------------------------
# Pausa
# --------------------------------------------------------------------------

def test_pausar_se_confirma_con_boton(enviados, monkeypatch):
    responde(monkeypatch, intencion="modificar", accion="pausar",
             destino_fecha_kind="fecha_exacta", destino_fecha_day=10,
             destino_fecha_month=10)

    handlers.handle_update(mensaje("pausá Notita hasta el 10/10"))

    assert db.pausada_hasta(CHAT) is None, "todavía no"
    boton = [b for b in botones(enviados) if "pausá" in b["text"]][0]
    handlers.handle_update(click(boton["callback_data"]))
    assert db.pausada_hasta(CHAT) is not None


def test_la_pausa_se_despausa_sola(enviados):
    db.pausar(CHAT, hoy() - timedelta(days=1))
    assert db.pausada_hasta(CHAT) is None


# --------------------------------------------------------------------------
# El modo local también propone
# --------------------------------------------------------------------------

def test_sin_gemini_las_propuestas_funcionan(enviados, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm, "interpretar_mensaje",
                        lambda *a, **k: pytest.fail("no debería llamar al LLM"))
    sembrar_super()

    handlers.handle_update(mensaje("ya compré la leche"))

    assert len(db.pendientes(CHAT, tipo="compras")) == 2, "propone, no ejecuta"
    assert "¿Tacho" in textos(enviados)[0]
