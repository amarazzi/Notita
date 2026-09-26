"""Chequeo de dependencias con un mensaje entendible.

Sin esto, correr cualquier script sin haber hecho `pip install` escupe un
`ModuleNotFoundError` pelado, que no le dice nada a quien recién empieza.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

PARA_QUE = {
    "requests": "hablar con Telegram y con Gemini",
    "flask": "recibir los mensajes de Telegram",
    "dotenv": "leer el archivo .env",
}


def exigir(*modulos: str) -> None:
    """Corta con un mensaje claro si falta alguna dependencia."""
    faltan = []
    for modulo in modulos:
        try:
            importlib.import_module(modulo)
        except ImportError:
            faltan.append(modulo)
    if not faltan:
        return

    raiz = Path(__file__).resolve().parent.parent
    lista = ", ".join(f"«{m}» (para {PARA_QUE.get(m, 'funcionar')})" for m in faltan)
    mensaje = [f"\nFalta instalar: {lista}.\n", "Instalá las dependencias:",
               "    pip3 install --user -r requirements.txt"]

    # Caso típico: hay un entorno virtual pero se está corriendo el python del sistema.
    # Se compara por sys.prefix y no por sys.executable: el python del venv es un
    # symlink que resuelve al mismo binario que el del sistema.
    for carpeta in (".venv", "venv", "env"):
        venv = raiz / carpeta / "bin" / "python"
        if venv.exists() and Path(sys.prefix) != raiz / carpeta:
            mensaje += ["", f"Ojo: hay un entorno virtual en {raiz / carpeta}.",
                        "Seguramente quieras usar ese python:",
                        f"    {venv} {' '.join(sys.argv)}"]
            break
    raise SystemExit("\n".join(mensaje) + "\n")
