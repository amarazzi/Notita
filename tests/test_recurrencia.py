"""Tests de tareas recurrentes, sobre todo las mensuales en meses cortos."""
from datetime import date

import pytest

from notita.dates import Recurrencia, proxima_ocurrencia


def test_sin_recurrencia():
    assert proxima_ocurrencia(None, date(2026, 3, 10)) is None
    assert proxima_ocurrencia(Recurrencia("ninguna"), date(2026, 3, 10)) is None


def test_diaria():
    assert proxima_ocurrencia(Recurrencia("diaria"), date(2026, 3, 10)) == date(2026, 3, 11)
    assert proxima_ocurrencia(Recurrencia("diaria", interval=3), date(2026, 3, 10)) == date(2026, 3, 13)


def test_semanal_simple():
    # "cambiar las piedritas del gato cada semana"
    assert proxima_ocurrencia(Recurrencia("semanal"), date(2026, 3, 10)) == date(2026, 3, 17)


def test_semanal_con_dia_fijo():
    # "todos los martes" (weekday=1), completada un martes -> el martes siguiente
    martes = date(2026, 9, 29)
    assert martes.weekday() == 1
    assert proxima_ocurrencia(Recurrencia("semanal", weekday=1), martes) == date(2026, 10, 6)


def test_semanal_cada_dos_semanas_con_dia_fijo():
    martes = date(2026, 9, 29)
    assert proxima_ocurrencia(Recurrencia("semanal", interval=2, weekday=1), martes) == date(2026, 10, 13)


def test_mensual_dia_10_pagar_expensas():
    rec = Recurrencia("mensual", monthday=10)
    assert proxima_ocurrencia(rec, date(2026, 1, 10)) == date(2026, 2, 10)
    assert proxima_ocurrencia(rec, date(2026, 2, 10)) == date(2026, 3, 10)
    assert proxima_ocurrencia(rec, date(2026, 12, 10)) == date(2027, 1, 10)


@pytest.mark.parametrize(
    "desde,esperado",
    [
        (date(2026, 1, 31), date(2026, 2, 28)),   # febrero de 28 días
        (date(2026, 3, 31), date(2026, 4, 30)),   # abril de 30 días
        (date(2026, 4, 30), date(2026, 5, 31)),   # mayo de 31: vuelve al 31 por el ancla
        (date(2026, 5, 31), date(2026, 6, 30)),
        (date(2024, 1, 31), date(2024, 2, 29)),   # febrero bisiesto
    ],
)
def test_mensual_dia_31_se_recorta_y_se_recupera(desde, esperado):
    # El ancla es 31: en los meses cortos cae al último día, pero al mes
    # siguiente vuelve al 31. Nunca se "pierde" el día original.
    rec = Recurrencia("mensual", monthday=31)
    assert proxima_ocurrencia(rec, desde) == esperado


def test_mensual_sin_ancla_usa_el_dia_de_la_fecha():
    rec = Recurrencia("mensual")
    assert proxima_ocurrencia(rec, date(2026, 1, 31)) == date(2026, 2, 28)
    # sin ancla, la serie se "achica" a partir del mes corto
    assert proxima_ocurrencia(rec, date(2026, 2, 28)) == date(2026, 3, 28)


def test_mensual_cada_dos_meses():
    rec = Recurrencia("mensual", interval=2, monthday=15)
    assert proxima_ocurrencia(rec, date(2026, 1, 15)) == date(2026, 3, 15)


def test_anual():
    rec = Recurrencia("anual", monthday=29)
    assert proxima_ocurrencia(rec, date(2024, 2, 29)) == date(2025, 2, 28)
    assert proxima_ocurrencia(Recurrencia("anual"), date(2026, 7, 9)) == date(2027, 7, 9)
