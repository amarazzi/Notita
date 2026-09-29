"""El doctor tiene que correr. Suena obvio, y no lo estaba probando nadie.

Pasó de verdad: cambié la firma de `db.pendientes()` y el doctor quedó llamándola con
el argumento viejo. Explotó con un TypeError en producción, justo en la herramienta que
existe para decirte si algo está mal.
"""
import importlib
import sqlite3

import pytest

from notita import config, db
from notita.dates import hoy

from .conftest import CHAT

doctor = importlib.import_module("doctor")


@pytest.fixture
def sin_ruido(monkeypatch, capsys):
    """El doctor imprime; acá sólo importa que no explote."""
    monkeypatch.setattr(config, "ALLOWED_CHAT_ID", CHAT)
    yield
    capsys.readouterr()


# Las revisiones que sólo miran la base: las que se pueden correr sin red.
SIN_RED = ("revisar_entorno", "revisar_personas", "revisar_base", "revisar_esquema")


@pytest.mark.parametrize("nombre", SIN_RED)
def test_las_revisiones_locales_corren(nombre, sin_ruido):
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())
    db.crear_tarea(CHAT, "Falta leche", compra=True)

    getattr(doctor, nombre)()      # si tira, el test falla


@pytest.mark.parametrize("nombre", SIN_RED)
def test_las_revisiones_locales_corren_con_la_base_vacia(nombre, sin_ruido):
    getattr(doctor, nombre)()


def test_el_resumen_cuenta_las_compras_aparte(sin_ruido, capsys):
    db.crear_tarea(CHAT, "Sacar la basura", due=hoy())
    db.crear_tarea(CHAT, "Falta leche", compra=True)

    doctor.revisar_base()

    salida = capsys.readouterr().out
    assert "pendiente 1" in salida
    assert "pendiente 🛒 1" in salida


def test_avisa_si_falta_la_columna_de_la_etiqueta(sin_ruido, capsys, tmp_path,
                                                 monkeypatch):
    """El doctor existe para encontrar justo esto."""
    ruta = tmp_path / "vieja.db"
    c = sqlite3.connect(ruta)
    c.executescript("""
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
 texto TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente', created_at TEXT);
CREATE TABLE ajustes (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE tablero (chat_id INTEGER PRIMARY KEY);
CREATE TABLE propuestas (id INTEGER PRIMARY KEY);
CREATE TABLE deshacer (id INTEGER PRIMARY KEY);
CREATE TABLE partes_enviados (fecha TEXT PRIMARY KEY);
CREATE TABLE pausa (chat_id INTEGER PRIMARY KEY);
CREATE TABLE updates_vistos (update_id TEXT PRIMARY KEY, visto_en TEXT NOT NULL);
""")
    c.commit()
    c.close()
    monkeypatch.setattr(config, "DB_PATH", str(ruta))

    doctor.revisar_esquema()

    salida = capsys.readouterr().out
    assert "compra" in salida and "init_db" in salida


def test_informa_la_version_de_sqlite(sin_ruido, capsys):
    """DROP COLUMN necesita 3.35: si es más vieja, hay que saberlo (no es un error)."""
    doctor.revisar_esquema()

    salida = capsys.readouterr().out
    assert f"SQLite {sqlite3.sqlite_version}" in salida


def test_ninguna_revision_usa_el_vocabulario_viejo():
    """Una red de seguridad barata para el resto de los scripts."""
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parent.parent
    for archivo in ("doctor.py", "migrar_chat.py", "run_parte.py", "app.py",
                    "install.py", "set_webhook.py"):
        texto = (raiz / archivo).read_text(encoding="utf-8")
        assert not re.search(r"pendientes\([^)]*tipo\s*=", texto), archivo
        assert not re.search(r"crear_tarea\([^)]*tipo\s*=", texto), archivo
