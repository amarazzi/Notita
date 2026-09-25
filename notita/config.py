"""Configuración de Notita, leída de variables de entorno."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent

# Carga opcional de un .env local (en PythonAnywhere se usan variables de entorno).
try:  # pragma: no cover - conveniencia de desarrollo
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover
    pass

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
# Secreto que Telegram manda en el header X-Telegram-Bot-Api-Secret-Token.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0") or 0)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

DB_PATH = os.getenv("NOTITA_DB", str(BASE_DIR / "notita.db"))

AXEL_USER_ID = int(os.getenv("AXEL_USER_ID", "0") or 0)
BARBU_USER_ID = int(os.getenv("BARBU_USER_ID", "0") or 0)

PERSONAS = ("axel", "barbu", "ambos", "ninguno")
NOMBRES = {"axel": "Axel", "barbu": "Barbu", "ambos": "los dos", "ninguno": "quien pueda"}

CATEGORIAS = ("limpieza", "arreglos", "tramites", "pagos", "mascotas", "compras", "otros")
CATEGORIA_EMOJI = {
    "limpieza": "🧽",
    "arreglos": "🔧",
    "tramites": "📄",
    "pagos": "💸",
    "mascotas": "🐾",
    "compras": "🛒",
    "otros": "📌",
}


def persona_de_user_id(user_id: int) -> str:
    """Devuelve 'axel' / 'barbu' / 'ninguno' según el user_id de Telegram."""
    if user_id and user_id == AXEL_USER_ID:
        return "axel"
    if user_id and user_id == BARBU_USER_ID:
        return "barbu"
    return "ninguno"


def user_id_de_persona(persona: str) -> int | None:
    return {"axel": AXEL_USER_ID or None, "barbu": BARBU_USER_ID or None}.get(persona)
