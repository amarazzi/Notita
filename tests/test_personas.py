"""Tests de quiénes viven en la casa: la config admite cualquier cantidad de personas."""
import pytest

from notita import config, db, llm, parte, views
from notita.config import Persona, parsear_personas, slugificar
from notita.dates import hoy

from .conftest import CASA, CHAT


@pytest.fixture
def casa_de_tres():
    """Tres convivientes, para chequear que nada asuma que somos dos."""
    original = config.PERSONAS_CASA
    config.definir_personas((
        Persona("axel", "Axel", 111),
        Persona("barbu", "Barbu", 222),
        Persona("jose_luis", "José Luis", 333),
    ))
    yield
    config.definir_personas(original)


# --------------------------------------------------------------------------
# Parseo de NOTITA_PERSONAS
# --------------------------------------------------------------------------

def test_slugificar():
    assert slugificar("Barbu") == "barbu"
    assert slugificar("José Luis") == "jose_luis"
    assert slugificar("  Añí  ") == "ani"
    assert slugificar("🙈") == "persona"


def test_parsear_personas():
    personas = parsear_personas("Axel:111, Barbu:222")
    assert [p.slug for p in personas] == ["axel", "barbu"]
    assert [p.nombre for p in personas] == ["Axel", "Barbu"]
    assert [p.user_id for p in personas] == [111, 222]


def test_parsear_personas_tolera_basura():
    assert parsear_personas("") == ()
    assert parsear_personas("  ,  ,") == ()
    # sin user_id: se acepta, pero no se puede mencionar
    assert parsear_personas("Axel")[0].user_id == 0
    # user_id que no es número: no revienta
    assert parsear_personas("Axel:hola")[0].user_id == 0


def test_parsear_personas_con_nombres_repetidos():
    personas = parsear_personas("Axel:1,Axel:2")
    assert len({p.slug for p in personas}) == 2


def test_una_sola_persona():
    original = config.PERSONAS_CASA
    try:
        config.definir_personas((Persona("axel", "Axel", 111),))
        assert config.PERSONAS == ("axel", "ambos", "ninguno")
        assert config.nombres_de_la_casa() == "Axel"
    finally:
        config.definir_personas(original)


def test_nombres_de_la_casa(casa_de_tres):
    assert config.nombres_de_la_casa() == "Axel, Barbu y José Luis"


def test_dos_personas_dicen_los_dos_y_tres_dicen_todos():
    assert config.NOMBRES["ambos"] == "los dos"  # la casa de los tests tiene dos


def test_con_tres_personas_ambos_es_todos(casa_de_tres):
    assert config.NOMBRES["ambos"] == "todos"


# --------------------------------------------------------------------------
# Lo que depende de las personas
# --------------------------------------------------------------------------

def test_persona_de_user_id():
    assert config.persona_de_user_id(111) == "axel"
    assert config.persona_de_user_id(222) == "barbu"
    assert config.persona_de_user_id(999) == "ninguno"
    assert config.persona_de_user_id(0) == "ninguno"


def test_user_id_de_persona():
    assert config.user_id_de_persona("barbu") == 222
    assert config.user_id_de_persona("ninguno") is None
    assert config.user_id_de_persona("nadie") is None


def test_el_tercero_puede_ser_responsable(casa_de_tres):
    assert "jose_luis" in config.PERSONAS
    tid = db.crear_tarea(CHAT, "comprar el pan", responsable="jose_luis", due=hoy())
    assert "José Luis" in views.linea(db.obtener(tid), hoy())


def test_ambos_dice_todos_con_tres_personas(casa_de_tres, enviados):
    """Con más de dos, «los dos» no sirve: es «todos»."""
    tid = db.crear_tarea(CHAT, "ordenar el living", responsable="ambos", due=hoy())

    linea = views.linea(db.obtener(tid))

    assert "todos" in linea
    assert config.NOMBRES["ambos"] == "todos"


def test_el_schema_del_llm_ofrece_a_todos(casa_de_tres):
    props = llm.schema_mensaje()["properties"]["items"]["items"]["properties"]
    assert props["responsable"]["enum"] == ["axel", "barbu", "jose_luis", "ambos", "ninguno"]


def test_el_prompt_nombra_a_la_casa(casa_de_tres):
    sistema = llm._sistema()
    assert "Axel, Barbu y José Luis" in sistema
    assert '"jose_luis" para José Luis' in sistema
    assert "Milo" not in sistema  # nada hardcodeado de una casa en particular


def test_el_contexto_de_la_casa_llega_al_prompt(monkeypatch):
    monkeypatch.setattr(config, "CONTEXTO_CASA", "Tenemos un gato que se llama Milo.")
    assert "gato que se llama Milo" in llm._sistema()


def test_sin_personas_configuradas_no_revienta():
    original = config.PERSONAS_CASA
    try:
        config.definir_personas(())
        assert config.PERSONAS == ("ambos", "ninguno")
        assert config.persona_de_user_id(111) == "ninguno"
        assert "la gente de la casa" in llm._sistema()
    finally:
        config.definir_personas(original)


def test_tarea_de_alguien_que_ya_no_esta_en_la_config():
    # Si se saca a alguien del .env, sus tareas viejas se siguen listando.
    tid = db.crear_tarea(CHAT, "algo viejo", responsable="ex_conviviente", due=hoy())
    assert "ex_conviviente" in views.linea(db.obtener(tid), hoy())


def test_la_casa_de_los_tests_sigue_siendo_la_original():
    assert config.PERSONAS_CASA == CASA
