"""Tests de la configuración automática de la web app de PythonAnywhere."""
import pytest

from notita import pythonanywhere as pa


@pytest.fixture
def var_www(tmp_path):
    d = tmp_path / "var_www"
    d.mkdir()
    return d


def sitio_de(tmp_path, var_www, usuario="unusuario", crear=True):
    if crear:
        (var_www / f"{usuario}_pythonanywhere_com_wsgi.py").write_text("# lo que venía\n")
    return pa.detectar(entorno={"USER": usuario, "PYTHONANYWHERE_DOMAIN": "pythonanywhere.com"},
                       var_www=var_www)


# --------------------------------------------------------------------------
# Detección
# --------------------------------------------------------------------------

def test_fuera_de_pythonanywhere_no_detecta_nada(var_www):
    assert pa.detectar(entorno={"USER": "alguien"}, var_www=var_www) is None


def test_detecta_por_la_variable_de_entorno(var_www):
    sitio = pa.detectar(entorno={"USER": "unusuario",
                                 "PYTHONANYWHERE_DOMAIN": "pythonanywhere.com"},
                        var_www=var_www)
    assert sitio is not None
    assert sitio.usuario == "unusuario"
    assert sitio.url == "https://unusuario.pythonanywhere.com"
    assert sitio.existe is False  # todavía no creó la web app


def test_detecta_la_web_app_ya_creada(tmp_path, var_www):
    sitio = sitio_de(tmp_path, var_www)
    assert sitio.existe is True
    assert sitio.dominio == "unusuario.pythonanywhere.com"
    assert sitio.wsgi.name == "unusuario_pythonanywhere_com_wsgi.py"


def test_detecta_aunque_no_haya_variable_de_entorno(var_www):
    # Una consola donde no está seteada: alcanza con que exista el archivo WSGI.
    (var_www / "unusuario_pythonanywhere_com_wsgi.py").touch()
    sitio = pa.detectar(entorno={"USER": "unusuario"}, var_www=var_www)
    assert sitio is not None and sitio.existe


def test_con_dominio_propio(var_www):
    (var_www / "www_micasa_com_wsgi.py").touch()
    sitio = pa.detectar(entorno={"USER": "unusuario"}, var_www=var_www)
    assert sitio.dominio == "www.micasa.com"
    assert sitio.url == "https://www.micasa.com"


def test_con_varias_web_apps_elige_la_del_usuario(var_www):
    (var_www / "www_otracosa_com_wsgi.py").touch()
    (var_www / "unusuario_pythonanywhere_com_wsgi.py").touch()
    sitio = pa.detectar(entorno={"USER": "unusuario"}, var_www=var_www)
    assert sitio.dominio == "unusuario.pythonanywhere.com"


def test_usa_el_home_si_no_hay_USER(var_www):
    (var_www / "desdehome_pythonanywhere_com_wsgi.py").touch()
    sitio = pa.detectar(entorno={"PYTHONANYWHERE_DOMAIN": "pythonanywhere.com"},
                        home="/home/desdehome", var_www=var_www)
    assert sitio.usuario == "desdehome"


# --------------------------------------------------------------------------
# Escribir el WSGI
# --------------------------------------------------------------------------

def test_escribe_el_wsgi_y_deja_backup(tmp_path, var_www):
    sitio = sitio_de(tmp_path, var_www)
    proyecto = "/home/unusuario/Notita"

    backup = pa.escribir_wsgi(sitio, proyecto)

    contenido = sitio.wsgi.read_text()
    assert proyecto in contenido
    assert "from app import app as application" in contenido
    assert backup is not None and "lo que venía" in backup.read_text()


def test_sin_contenido_previo_no_hace_backup(tmp_path, var_www):
    sitio = sitio_de(tmp_path, var_www)
    sitio.wsgi.write_text("   \n")  # PythonAnywhere lo deja con comentarios/vacío
    assert pa.escribir_wsgi(sitio, "/home/unusuario/Notita") is None


def test_el_wsgi_generado_es_python_valido(tmp_path, var_www):
    sitio = sitio_de(tmp_path, var_www)
    pa.escribir_wsgi(sitio, "/home/unusuario/Notita")
    compile(sitio.wsgi.read_text(), str(sitio.wsgi), "exec")  # explota si está roto


def test_ya_configurado(tmp_path, var_www):
    sitio = sitio_de(tmp_path, var_www)
    proyecto = "/home/unusuario/Notita"
    assert pa.ya_configurado(sitio, proyecto) is False

    pa.escribir_wsgi(sitio, proyecto)
    assert pa.ya_configurado(sitio, proyecto) is True
    # Si apunta a otro proyecto, hay que reescribirlo.
    assert pa.ya_configurado(sitio, "/home/unusuario/OtraCosa") is False


def test_ya_configurado_sin_archivo(var_www):
    sitio = pa.detectar(entorno={"USER": "unusuario", "PYTHONANYWHERE_DOMAIN": "x.com"},
                        var_www=var_www)
    assert pa.ya_configurado(sitio, "/cualquier/cosa") is False


# --------------------------------------------------------------------------
# Recargar
# --------------------------------------------------------------------------

def test_recargar_toca_el_archivo(tmp_path, var_www):
    sitio = sitio_de(tmp_path, var_www)
    antes = sitio.wsgi.stat().st_mtime_ns
    import os
    os.utime(sitio.wsgi, (0, 0))  # lo mandamos al pasado
    assert sitio.wsgi.stat().st_mtime_ns != antes

    assert pa.recargar(sitio) is True
    assert sitio.wsgi.stat().st_mtime_ns > 0


def test_recargar_sin_archivo_no_explota(var_www):
    sitio = pa.detectar(entorno={"USER": "unusuario", "PYTHONANYWHERE_DOMAIN": "x.com"},
                        var_www=var_www)
    assert pa.recargar(sitio) is False
