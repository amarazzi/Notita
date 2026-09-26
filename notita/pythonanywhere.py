"""Detección y configuración automática de la web app de PythonAnywhere.

Los dos pasos que más se rompen cuando alguien instala Notita a mano son editar
el archivo WSGI (hay que reemplazar TODO el contenido) y acordarse del Reload.
Los dos se pueden hacer desde la consola, así que los hace el instalador.

Tocar el archivo WSGI recarga la web app: el proceso que la sirve está mirando
ese archivo. (Ojo: no equivale al botón Reload para cosas como los mapeos de
archivos estáticos, pero sí para tomar código y configuración nuevos.)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VAR_WWW = Path("/var/www")

PLANTILLA_WSGI = '''"""Generado por el instalador de Notita."""
import sys

path = "{proyecto}"
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application  # noqa
'''


@dataclass(frozen=True)
class Sitio:
    usuario: str
    dominio: str      # ej: unusuario.pythonanywhere.com
    wsgi: Path        # /var/www/unusuario_pythonanywhere_com_wsgi.py
    existe: bool      # False si la web app todavía no está creada

    @property
    def url(self) -> str:
        return f"https://{self.dominio}"


def _dominio_de(nombre_archivo: str) -> str:
    """'unusuario_pythonanywhere_com_wsgi.py' -> 'unusuario.pythonanywhere.com'."""
    return nombre_archivo.removesuffix("_wsgi.py").replace("_", ".")


def detectar(entorno: dict | None = None, home: str | Path | None = None,
             var_www: Path | None = None) -> Sitio | None:
    """Devuelve el Sitio si estamos corriendo dentro de PythonAnywhere, o None."""
    entorno = os.environ if entorno is None else entorno
    var_www = VAR_WWW if var_www is None else var_www

    marcas = (entorno.get("PYTHONANYWHERE_DOMAIN"), entorno.get("PYTHONANYWHERE_SITE"))
    wsgis = sorted(var_www.glob("*_wsgi.py")) if var_www.is_dir() else []
    if not any(marcas) and not wsgis:
        return None

    usuario = entorno.get("USER") or Path(home or Path.home()).name
    # La del dominio gratuito empieza con el nombre de usuario. Si no hay ninguna así,
    # puede ser un dominio propio: se acepta sólo si es la única, porque con varias
    # elegir "la primera" significaría pisarle la web app de otro proyecto.
    propias = [w for w in wsgis if w.name.startswith(f"{usuario}_")]
    if propias:
        elegido = propias[0]
    elif len(wsgis) == 1:
        elegido = wsgis[0]
    else:
        elegido = None
    if elegido is None:
        dominio = f"{usuario}.{entorno.get('PYTHONANYWHERE_DOMAIN') or 'pythonanywhere.com'}"
        return Sitio(usuario=usuario, dominio=dominio,
                     wsgi=var_www / f"{dominio.replace('.', '_')}_wsgi.py", existe=False)
    return Sitio(usuario=usuario, dominio=_dominio_de(elegido.name),
                 wsgi=elegido, existe=True)


def ya_configurado(sitio: Sitio, proyecto: Path | str) -> bool:
    """¿El archivo WSGI ya apunta a este proyecto?"""
    if not sitio.wsgi.exists():
        return False
    try:
        contenido = sitio.wsgi.read_text(encoding="utf-8")
    except OSError:
        return False
    return str(proyecto) in contenido and "from app import app" in contenido


def escribir_wsgi(sitio: Sitio, proyecto: Path | str) -> Path | None:
    """Deja el WSGI apuntando al proyecto. Devuelve la ruta del backup, si hizo uno."""
    backup = None
    if sitio.wsgi.exists() and sitio.wsgi.read_text(encoding="utf-8").strip():
        backup = sitio.wsgi.with_name(sitio.wsgi.name + ".bak")
        backup.write_text(sitio.wsgi.read_text(encoding="utf-8"), encoding="utf-8")
    sitio.wsgi.write_text(PLANTILLA_WSGI.format(proyecto=proyecto), encoding="utf-8")
    return backup


def recargar(sitio: Sitio) -> bool:
    """Equivalente al botón Reload: tocar el archivo WSGI reinicia la web app."""
    try:
        os.utime(sitio.wsgi, None)
        return True
    except OSError:
        return False
