"""Configuración de Notita, leída de variables de entorno."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent

# Carga opcional de un .env local (en PythonAnywhere se usan variables de entorno).
try:  # pragma: no cover - conveniencia de desarrollo
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover
    pass

# Zona horaria de la casa. Por defecto Buenos Aires, pero se puede cambiar sin
# tocar código: NOTITA_TZ=Europe/Madrid, por ejemplo.
TZ_POR_DEFECTO = "America/Argentina/Buenos_Aires"


def zona(nombre: str) -> tuple[str, ZoneInfo]:
    """El nombre y la zona. Si el nombre está mal escrito, cae en la de casa."""
    try:
        return nombre, ZoneInfo(nombre)
    except Exception:
        return TZ_POR_DEFECTO, ZoneInfo(TZ_POR_DEFECTO)


TZ_NOMBRE, TZ = zona(os.getenv("NOTITA_TZ", TZ_POR_DEFECTO))

# A qué hora corre la rutina diaria. Esto NO la programa (eso lo hace el cron o la
# tarea de PythonAnywhere): sirve para que los mensajes digan la hora correcta y
# para que el instalador calcule el horario en UTC.
HORA_RUTINA = os.getenv("NOTITA_HORA", "20:00")


def _leer_version() -> str:
    """El commit del árbol de trabajo, leído del .git. «desconocida» si no hay repo."""
    git = BASE_DIR / ".git"
    try:
        cabeza = (git / "HEAD").read_text().strip()
        if cabeza.startswith("ref: "):
            ref = git / cabeza[5:]
            sha = (ref.read_text().strip() if ref.exists()
                   else _version_de_packed_refs(git, cabeza[5:]))
        else:
            sha = cabeza
        return sha[:7] if sha else "desconocida"
    except OSError:
        return "desconocida"


def _version_de_packed_refs(git, ref: str) -> str:
    """Después de un clone las refs pueden estar empaquetadas en un solo archivo."""
    try:
        for linea in (git / "packed-refs").read_text().splitlines():
            if linea.endswith(" " + ref):
                return linea.split(" ", 1)[0]
    except OSError:
        pass
    return ""


# Se lee UNA vez, al importar: así es el commit del código que está corriendo y no el
# del archivo. Si alguien hace `git pull` y se olvida del reload, `/` sigue diciendo el
# viejo, que es justo lo que uno necesita saber.
VERSION = _leer_version()


def version() -> str:
    """El commit que está corriendo. Lo muestra la web app en `/`."""
    return VERSION


# Cada cuántos minutos corre la rutina. 0 = una sola vez por día (la tarea diaria de
# PythonAnywhere). Con un cron externo se puede poner 5, 15, 30... Notita lo usa para
# no prometer una hora que no va a poder cumplir.
try:
    CRON_MINUTOS = max(0, int(os.getenv("NOTITA_CRON_MINUTOS", "0")))
except ValueError:
    CRON_MINUTOS = 0


def hora_rutina_en_utc(hora: str | None = None) -> str:
    """La hora de la rutina pasada a UTC, que es como se programan los crons."""
    from datetime import datetime, timezone

    try:
        h, m = (int(x) for x in (hora or HORA_RUTINA).split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        h, m = 20, 0
    momento = datetime.now(TZ).replace(hour=h, minute=m, second=0, microsecond=0)
    return momento.astimezone(timezone.utc).strftime("%H:%M")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
# Secreto que Telegram manda en el header X-Telegram-Bot-Api-Secret-Token.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0") or 0)
# Clave para /cron/recordatorios (la llama un cron externo, ej. cron-job.org).
# Si esta vacia, esa ruta queda apagada.
CRON_SECRET = os.getenv("CRON_SECRET", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Ojo con el modelo: en la capa gratuita, gemini-2.5-flash da 20 requests POR DÍA
# (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier), que no alcanza para una
# casa. Los "lite" tienen mucha más. Los alias -latest no se dan de baja como los
# nombres con versión (gemini-2.0-flash ya devuelve 404).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

DB_PATH = os.getenv("NOTITA_DB", str(BASE_DIR / "notita.db"))

# Dato libre sobre la casa que se le pasa al LLM para que acierte mejor.
# Ej: "Tenemos un gato que se llama Milo. Vivimos en un PH con patio."
CONTEXTO_CASA = os.getenv("NOTITA_CONTEXTO", "").strip()

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


# --------------------------------------------------------------------------
# Quiénes viven en la casa
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Persona:
    slug: str      # identificador interno, lo que se guarda en la base
    nombre: str    # como se muestra en los mensajes
    user_id: int   # user_id de Telegram, para las menciones


def slugificar(nombre: str) -> str:
    """'Barbu' -> 'barbu', 'José Luis' -> 'jose_luis'."""
    t = unicodedata.normalize("NFD", nombre.strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t or "persona"


def parsear_personas(valor: str) -> tuple[Persona, ...]:
    """Lee NOTITA_PERSONAS con formato 'Axel:11111,Barbu:22222'.

    Tolera espacios y entradas sin user_id ('Axel'), que quedan sin mención.
    """
    personas: list[Persona] = []
    usados: set[str] = set()
    for parte in valor.split(","):
        parte = parte.strip()
        if not parte:
            continue
        nombre, _, uid = parte.partition(":")
        nombre = nombre.strip()
        if not nombre:
            continue
        slug = slugificar(nombre)
        while slug in usados:  # dos personas con el mismo nombre
            slug += "_2"
        usados.add(slug)
        try:
            user_id = int(uid.strip() or 0)
        except ValueError:
            user_id = 0
        personas.append(Persona(slug=slug, nombre=nombre, user_id=user_id))
    return tuple(personas)


def definir_personas(personas: tuple[Persona, ...]) -> None:
    """Fija quiénes viven en la casa y recalcula todo lo que depende de eso."""
    global PERSONAS_CASA, SLUGS, PERSONAS, NOMBRES
    PERSONAS_CASA = tuple(personas)
    SLUGS = tuple(p.slug for p in PERSONAS_CASA)
    # Valores válidos del campo `responsable` de una tarea.
    PERSONAS = SLUGS + ("ambos", "ninguno")
    NOMBRES = {p.slug: p.nombre for p in PERSONAS_CASA}
    NOMBRES["ambos"] = "los dos" if len(PERSONAS_CASA) == 2 else "todos"
    NOMBRES["ninguno"] = "quien pueda"


def _personas_del_entorno() -> tuple[Persona, ...]:
    """NOTITA_PERSONAS es la forma nueva; AXEL/BARBU_USER_ID siguen andando."""
    personas = parsear_personas(os.getenv("NOTITA_PERSONAS", ""))
    if personas:
        return personas
    viejas = []
    for nombre in ("Axel", "Barbu"):
        uid = os.getenv(f"{nombre.upper()}_USER_ID", "")
        try:
            uid = int(uid or 0)
        except ValueError:
            uid = 0
        if uid:
            viejas.append(Persona(slug=nombre.lower(), nombre=nombre, user_id=uid))
    return tuple(viejas)


definir_personas(_personas_del_entorno())


def persona(slug: str) -> Persona | None:
    return next((p for p in PERSONAS_CASA if p.slug == slug), None)


def persona_de_user_id(user_id: int) -> str:
    """Slug de quien escribió, o 'ninguno' si no lo tenemos configurado."""
    if user_id:
        for p in PERSONAS_CASA:
            if p.user_id == user_id:
                return p.slug
    return "ninguno"


def user_id_de_persona(slug: str) -> int | None:
    p = persona(slug)
    return p.user_id or None if p else None


def nombres_de_la_casa() -> str:
    """'Axel y Barbu' / 'Axel, Barbu y Cami' — para los prompts y los textos."""
    nombres = [p.nombre for p in PERSONAS_CASA]
    if not nombres:
        return "la gente de la casa"
    if len(nombres) == 1:
        return nombres[0]
    return f"{', '.join(nombres[:-1])} y {nombres[-1]}"
