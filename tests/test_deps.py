"""Tests del chequeo de dependencias: que el mensaje sea útil y no un traceback."""
import sys

import pytest

from notita import deps


def test_no_hace_nada_si_estan_todas():
    deps.exigir("sys", "json")  # siempre están


def test_corta_con_un_mensaje_entendible():
    with pytest.raises(SystemExit) as e:
        deps.exigir("modulo_que_no_existe_12345")
    mensaje = str(e.value)
    assert "Falta instalar" in mensaje
    assert "modulo_que_no_existe_12345" in mensaje
    assert "requirements.txt" in mensaje


def test_un_modulo_desconocido_usa_el_texto_generico():
    with pytest.raises(SystemExit) as e:
        deps.exigir("modulo_raro_xyz")
    assert "para funcionar" in str(e.value)


def test_menciona_el_para_que_conocido(monkeypatch):
    monkeypatch.setitem(deps.PARA_QUE, "inexistente_xyz", "hablar con Telegram")
    with pytest.raises(SystemExit) as e:
        deps.exigir("inexistente_xyz")
    assert "hablar con Telegram" in str(e.value)


def test_avisa_del_entorno_virtual_si_se_usa_el_python_del_sistema(monkeypatch, tmp_path):
    # El python del venv es un symlink al binario del sistema, así que comparar
    # sys.executable resuelto no sirve: hay que mirar sys.prefix.
    raiz = tmp_path / "Notita"
    (raiz / ".venv" / "bin").mkdir(parents=True)
    (raiz / ".venv" / "bin" / "python").touch()
    monkeypatch.setattr(deps, "__file__", str(raiz / "notita" / "deps.py"))
    monkeypatch.setattr(sys, "prefix", "/usr")  # estamos fuera del venv

    with pytest.raises(SystemExit) as e:
        deps.exigir("modulo_que_no_existe_12345")
    mensaje = str(e.value)
    assert "hay un entorno virtual" in mensaje
    assert str(raiz / ".venv" / "bin" / "python") in mensaje


def test_no_avisa_si_ya_estas_dentro_del_venv(monkeypatch, tmp_path):
    raiz = tmp_path / "Notita"
    (raiz / ".venv" / "bin").mkdir(parents=True)
    (raiz / ".venv" / "bin" / "python").touch()
    monkeypatch.setattr(deps, "__file__", str(raiz / "notita" / "deps.py"))
    monkeypatch.setattr(sys, "prefix", str(raiz / ".venv"))

    with pytest.raises(SystemExit) as e:
        deps.exigir("modulo_que_no_existe_12345")
    assert "entorno virtual" not in str(e.value)
