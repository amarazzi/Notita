"""El `callback_data` de los botones: versionado, corto y con un solo lugar de verdad.

Telegram permite 64 bytes. Los botones viven para siempre en el chat, así que van
versionados: cuando aparece uno de v1 (otra forma), Notita lo puede reconocer y
contestar que es de la versión anterior en vez de hacer cualquier cosa.

Forma: `2|accion|arg1|arg2`
"""
from __future__ import annotations

VERSION = "2"
SEPARADOR = "|"
LIMITE = 64      # bytes, el máximo de Telegram


def armar(accion: str, *args) -> str:
    dato = SEPARADOR.join([VERSION, accion, *(str(a) for a in args)])
    if len(dato.encode("utf-8")) > LIMITE:
        raise ValueError(f"callback_data demasiado largo ({dato!r})")
    return dato


def leer(dato: str) -> tuple[str, list[str]] | None:
    """(accion, args) si es un botón de esta versión. None si es de otra."""
    partes = (dato or "").split(SEPARADOR)
    if len(partes) < 2 or partes[0] != VERSION:
        return None
    return partes[1], partes[2:]


def entero(args: list[str], i: int = 0) -> int | None:
    try:
        return int(args[i])
    except (IndexError, ValueError):
        return None
