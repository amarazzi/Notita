"""Tests de la lógica de fechas, que es la parte delicada."""
from datetime import date

import pytest

from notita.dates import (
    DateSpec,
    domingo_de_la_semana,
    formato_humano,
    lunes_de_la_semana,
    parse_natural,
    parse_y_resolver,
    proximo_fin_de_semana,
    resolve,
)

# Referencias conocidas (semana del 28/9/2026 al 4/10/2026)
DOMINGO = date(2026, 9, 27)
LUNES = date(2026, 9, 28)
MARTES = date(2026, 9, 29)
VIERNES = date(2026, 10, 2)


def test_referencias_son_los_dias_que_creemos():
    assert DOMINGO.weekday() == 6
    assert LUNES.weekday() == 0
    assert MARTES.weekday() == 1
    assert VIERNES.weekday() == 4


# --------------------------------------------------------------------------
# "el lunes"
# --------------------------------------------------------------------------

def test_el_lunes_dicho_un_martes_es_el_lunes_que_viene():
    _, d = parse_y_resolver("el lunes", MARTES)
    assert d == date(2026, 10, 5)


def test_el_lunes_dicho_un_domingo_es_el_dia_siguiente():
    _, d = parse_y_resolver("el lunes", DOMINGO)
    assert d == date(2026, 9, 28)


def test_el_lunes_dicho_un_lunes_es_el_lunes_siguiente():
    # "el próximo lunes" nunca es hoy: se va siete días adelante.
    _, d = parse_y_resolver("el lunes", LUNES)
    assert d == date(2026, 10, 5)


@pytest.mark.parametrize("texto", ["el lunes", "lunes", "el lunes que viene", "el próximo lunes"])
def test_variantes_de_lunes(texto):
    _, d = parse_y_resolver(texto, MARTES)
    assert d == date(2026, 10, 5)


def test_lunes_de_la_semana_que_viene_dicho_un_domingo():
    # El domingo es el último día de la semana, así que "la semana que viene"
    # es la que arranca mañana. Coherente con "esta semana" = hoy.
    _, d = parse_y_resolver("el lunes de la semana que viene", DOMINGO)
    assert d == date(2026, 9, 28)


def test_lunes_de_la_semana_que_viene_dicho_un_martes():
    _, d = parse_y_resolver("el lunes de la semana que viene", MARTES)
    assert d == date(2026, 10, 5)


def test_viernes_de_la_semana_que_viene():
    _, d = parse_y_resolver("el viernes de la semana que viene", MARTES)
    assert d == date(2026, 10, 9)


# --------------------------------------------------------------------------
# semanas
# --------------------------------------------------------------------------

def test_esta_semana_vence_el_domingo():
    _, d = parse_y_resolver("esta semana", MARTES)
    assert d == date(2026, 10, 4)
    assert d.weekday() == 6


def test_esta_semana_dicha_un_domingo_es_hoy():
    _, d = parse_y_resolver("esta semana", DOMINGO)
    assert d == DOMINGO


@pytest.mark.parametrize("texto", ["la semana que viene", "semana que viene", "la próxima semana"])
def test_la_semana_que_viene_vence_el_domingo_siguiente(texto):
    _, d = parse_y_resolver(texto, MARTES)
    assert d == date(2026, 10, 11)
    assert d.weekday() == 6


def test_la_semana_que_viene_dicha_un_domingo():
    _, d = parse_y_resolver("la semana que viene", DOMINGO)
    assert d == date(2026, 10, 4)


def test_semana_lunes_a_domingo():
    assert lunes_de_la_semana(DOMINGO) == date(2026, 9, 21)
    assert domingo_de_la_semana(DOMINGO) == DOMINGO
    assert lunes_de_la_semana(MARTES) == LUNES
    assert domingo_de_la_semana(MARTES) == date(2026, 10, 4)


# --------------------------------------------------------------------------
# relativas simples
# --------------------------------------------------------------------------

def test_manana_y_pasado():
    assert parse_y_resolver("mañana", MARTES)[1] == date(2026, 9, 30)
    assert parse_y_resolver("pasado", MARTES)[1] == date(2026, 10, 1)
    assert parse_y_resolver("pasado mañana", MARTES)[1] == date(2026, 10, 1)


def test_hoy():
    assert parse_y_resolver("hoy", MARTES)[1] == MARTES


def test_en_n_dias_y_semanas():
    assert parse_y_resolver("en 3 días", MARTES)[1] == date(2026, 10, 2)
    assert parse_y_resolver("en dos semanas", MARTES)[1] == date(2026, 10, 13)


def test_fin_de_semana():
    assert proximo_fin_de_semana(MARTES) == date(2026, 10, 3)      # sábado
    assert proximo_fin_de_semana(date(2026, 10, 3)) == date(2026, 10, 4)  # sábado -> domingo
    assert proximo_fin_de_semana(DOMINGO) == date(2026, 10, 3)


# --------------------------------------------------------------------------
# algún día
# --------------------------------------------------------------------------

@pytest.mark.parametrize("texto", ["sin fecha", "no sé", "cuando se pueda", "ni idea", "sin fecha"])
def test_algun_dia_no_tiene_vencimiento(texto):
    spec, d = parse_y_resolver(texto, MARTES)
    assert spec.kind == "algun_dia"
    assert d is None


def test_desconocida_no_resuelve():
    assert resolve(DateSpec("desconocida"), MARTES) is None
    assert parse_natural("dale gracias") is None


# --------------------------------------------------------------------------
# fechas concretas
# --------------------------------------------------------------------------

def test_fecha_concreta_con_mes_en_palabras():
    _, d = parse_y_resolver("el 3 de octubre", MARTES)
    assert d == date(2026, 10, 3)


def test_fecha_concreta_que_ya_paso_va_al_anio_siguiente():
    _, d = parse_y_resolver("el 3 de marzo", MARTES)
    assert d == date(2027, 3, 3)


def test_fecha_numerica():
    assert parse_y_resolver("3/10", MARTES)[1] == date(2026, 10, 3)
    assert parse_y_resolver("3/10/2027", MARTES)[1] == date(2027, 10, 3)


def test_fecha_exacta_invalida_se_recorta_al_ultimo_dia():
    # "31 de febrero" no existe: lo dejamos en el último día del mes.
    _, d = parse_y_resolver("31 de febrero", date(2026, 1, 5))
    assert d == date(2026, 2, 28)


def test_dia_del_mes_suelto():
    assert parse_y_resolver("el 10", date(2026, 9, 5))[1] == date(2026, 9, 10)
    assert parse_y_resolver("el 10", date(2026, 9, 20))[1] == date(2026, 10, 10)
    assert parse_y_resolver("el 10", date(2026, 9, 10))[1] == date(2026, 9, 10)


def test_dia_del_mes_31_en_mes_corto():
    assert parse_y_resolver("el 31", date(2026, 2, 5))[1] == date(2026, 2, 28)


# --------------------------------------------------------------------------
# formato
# --------------------------------------------------------------------------

def test_formato_humano():
    assert formato_humano(MARTES, MARTES) == "hoy"
    assert formato_humano(date(2026, 9, 30), MARTES) == "mañana"
    assert formato_humano(date(2026, 10, 1), MARTES) == "pasado mañana"
    assert formato_humano(None, MARTES) == "sin fecha"
    assert "venció" in formato_humano(date(2026, 9, 20), MARTES)
    assert formato_humano(date(2026, 10, 3), MARTES) == "el sábado 3/10"
