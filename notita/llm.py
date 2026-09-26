"""Interpretación de lenguaje natural con Gemini (capa gratuita).

Llamamos a la API REST con `requests` a propósito: es una dependencia mínima y
en PythonAnywhere gratuito sale por el proxy sin configuración extra.

El LLM NUNCA calcula fechas. Devuelve una intención (`fecha_kind` + campos) y
la aritmética la hace `dates.resolve`.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date

import requests

from . import config
from .dates import KINDS, DIAS_NOMBRE, DateSpec, Recurrencia, hoy

log = logging.getLogger("notita.llm")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = 25
INTENTOS = 3
ESPERA = 1  # segundos (1, después 2)
# Errores que valen la pena reintentar: cuota momentánea y modelo sobrecargado.
REINTENTABLES = (429, 500, 502, 503, 504)

NO_APLICA = -1

def _item_props() -> dict:
    """El enum de `responsable` depende de quién vive en la casa, así que se arma al vuelo."""
    return {
        "texto": {"type": "string", "description": "La tarea en infinitivo, corta y clara. Ej: 'limpiar la heladera'."},
        "tipo": {"type": "string", "enum": ["casa", "compras"]},
        "categoria": {"type": "string", "enum": list(config.CATEGORIAS)},
        "responsable": {"type": "string", "enum": list(config.PERSONAS)},
        "fecha_kind": {"type": "string", "enum": list(KINDS)},
        "fecha_weekday": {"type": "integer", "description": "0=lunes ... 6=domingo. -1 si no aplica."},
        "fecha_day": {"type": "integer", "description": "Día del mes. -1 si no aplica."},
        "fecha_month": {"type": "integer", "description": "1-12. -1 si no aplica."},
        "fecha_year": {"type": "integer", "description": "Año de 4 dígitos. -1 si no aplica."},
        "fecha_dias": {"type": "integer", "description": "Cantidad de días para fecha_kind='en_dias'. -1 si no aplica."},
        "recur_kind": {"type": "string", "enum": ["ninguna", "diaria", "semanal", "mensual", "anual"]},
        "recur_interval": {"type": "integer", "description": "Cada cuántos períodos se repite. 1 por defecto."},
        "recur_weekday": {"type": "integer", "description": "0=lunes ... 6=domingo para recurrencia semanal. -1 si no aplica."},
        "recur_monthday": {"type": "integer", "description": "Día fijo del mes para recurrencia mensual. -1 si no aplica."},
        "necesita_aclaracion": {"type": "boolean", "description": "true si no estás seguro de qué es esto."},
        "pregunta": {"type": "string", "description": "Si necesita_aclaracion, la pregunta corta a hacer. Si no, ''."},
    }


def schema_mensaje() -> dict:
    props = _item_props()
    return {
        "type": "object",
        "properties": {
            "es_tarea": {"type": "boolean", "description": "false si el mensaje es charla y no hay nada para anotar."},
            "comentario": {"type": "string", "description": "Si es_tarea=false, una respuesta breve y cariñosa. Si no, ''."},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": ["texto", "tipo", "categoria", "responsable", "fecha_kind",
                                 "recur_kind", "necesita_aclaracion"],
                    "propertyOrdering": list(props),
                },
            },
        },
        "required": ["es_tarea", "items"],
        "propertyOrdering": ["es_tarea", "comentario", "items"],
    }

SCHEMA_FECHA = {
    "type": "object",
    "properties": {
        "fecha_kind": {"type": "string", "enum": list(KINDS)},
        "fecha_weekday": {"type": "integer"},
        "fecha_day": {"type": "integer"},
        "fecha_month": {"type": "integer"},
        "fecha_year": {"type": "integer"},
        "fecha_dias": {"type": "integer"},
    },
    "required": ["fecha_kind"],
    "propertyOrdering": ["fecha_kind", "fecha_weekday", "fecha_day", "fecha_month",
                         "fecha_year", "fecha_dias"],
}

def _quienes_viven() -> str:
    """Los nombres y los slugs de la casa, explicados para el prompt."""
    if not config.PERSONAS_CASA:
        return '- responsable: siempre "ninguno" (no hay personas configuradas).'
    pares = ", ".join(f'"{p.slug}" para {p.nombre}' for p in config.PERSONAS_CASA)
    todos = "de los dos" if len(config.PERSONAS_CASA) == 2 else "de todos"
    return (f'- responsable: {pares} si el mensaje dice quién lo hace\n'
            f'  (ej. "{config.PERSONAS_CASA[0].nombre} tiene que llamar al veterinario"),\n'
            f'  "ambos" si es {todos}, "ninguno" si no se menciona. No adivines.')


def _sistema() -> str:
    return f"""Sos Notita, un bot que organiza las tareas de una casa donde viven {config.nombres_de_la_casa()}.
Recibís mensajes en español rioplatense de un grupo de Telegram y los convertís en tareas.
{f"Sobre la casa: {config.CONTEXTO_CASA}" if config.CONTEXTO_CASA else ""}
Reglas:
- Un mensaje puede contener VARIAS tareas: separalas en items distintos.
  "hay que limpiar la heladera, llamar al plomero y comprar focos" son 3 items.
- tipo="compras" si es algo que se compra en el super o en un negocio
  ("falta leche", "comprar yerba", "se acabó el detergente"). Todo lo demás es tipo="casa".
  Los items de compras siempre llevan categoria="compras" y fecha_kind="algun_dia".
{_quienes_viven()}
- categoria: limpieza, arreglos, tramites, pagos, mascotas, compras u otros.
  * pagos: SOLO si hay que pagar plata (facturas, expensas, alquiler, impuestos).
  * tramites: gestiones, papeles, turnos, renovar cuentas o servicios sin pagar.
  * arreglos: reparar o instalar cosas en la casa. limpieza: limpiar u ordenar.
  * mascotas: todo lo de los animales de la casa. otros: lo que no encaje en ninguna.
- fecha_kind: elegí la INTENCIÓN, no calcules la fecha. Nunca devuelvas una fecha calculada.
  * "el lunes" -> dia_semana con fecha_weekday=0
  * "el lunes de la semana que viene" -> dia_semana_prox
  * "esta semana" -> esta_semana ; "la semana que viene" -> semana_que_viene
  * "mañana" -> manana ; "pasado" -> pasado ; "hoy" -> hoy
  * "el 3 de octubre" -> fecha_exacta con fecha_day=3, fecha_month=10
  * "todos los 10" -> dia_del_mes con fecha_day=10
  * "algún día", "no sé", "cuando se pueda" -> algun_dia
  * si el mensaje NO dice nada de cuándo -> desconocida (así se lo preguntamos)
- recur_kind si se repite: "cada semana" -> semanal, "todos los 10" -> mensual con
  recur_monthday=10, "todos los martes" -> semanal con recur_weekday=1.
- Campos numéricos que no aplican: mandá -1.
- texto: corto, en infinitivo, sin la parte de la fecha ni el nombre del responsable.
- Si algo es muy ambiguo y no sabés si es una tarea o qué significa,
  poné necesita_aclaracion=true y escribí una pregunta corta y tierna.
- Si el mensaje es pura charla (un saludo, un chiste, una pregunta al bot),
  devolvé es_tarea=false, items=[] y un comentario breve.
"""


def disponible() -> bool:
    """Si no hay API key, Notita trabaja en modo local (ver `heuristica.py`)."""
    return bool(config.GEMINI_API_KEY)


def _contexto_fecha(ref: date) -> str:
    return (f"Hoy es {DIAS_NOMBRE[ref.weekday()]} {ref.isoformat()} "
            f"(zona horaria America/Argentina/Buenos_Aires).")


def probar_conexion(key: str | None = None, model: str | None = None) -> tuple[bool, str]:
    """Chequea que la API key y el modelo anden. Devuelve (ok, motivo en castellano).

    La usan install.py y doctor.py para poder decir qué pasa en vez de un 400 pelado.
    """
    key = key if key is not None else config.GEMINI_API_KEY
    model = model or config.GEMINI_MODEL
    if not key:
        return False, "falta la API key"
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Respondé solo: listo"}]}],
        "generationConfig": {"temperature": 0},
    }
    try:
        r = requests.post(ENDPOINT.format(model=model), params={"key": key},
                          json=body, timeout=TIMEOUT)
    except requests.exceptions.RequestException as e:
        return False, f"no se pudo conectar con Google ({type(e).__name__})"
    if r.status_code == 200:
        return True, ""
    try:
        detalle = r.json().get("error", {}).get("message", "")
    except ValueError:
        detalle = r.text[:200]
    if r.status_code in (400, 403) and "API key" in detalle:
        return False, "la API key no es válida"
    if r.status_code == 404:
        return False, f"el modelo «{model}» no existe o no está habilitado para tu key"
    if r.status_code == 429:
        return False, "te pasaste de la cuota gratuita, probá en un rato"
    if r.status_code in REINTENTABLES:
        return False, f"el modelo está sobrecargado ({r.status_code}), no es tu culpa: probá en un rato"
    return False, f"HTTP {r.status_code}: {detalle[:160] or 'sin detalle'}"


def _call(prompt: str, schema: dict, sistema: str) -> dict | None:
    """Una llamada a Gemini con la respuesta ya parseada. `None` si no se pudo."""
    if not config.GEMINI_API_KEY:
        log.warning("Sin GEMINI_API_KEY, no se puede interpretar con el LLM")
        return None
    body = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": schema,
            # Ojo: con "thinkingBudget": 0, gemini-2.5-flash rompe las tildes en JSON
            # ("cómoda" -> "c\\nmoda"). Por eso dejamos que piense lo que necesite.
        },
    }
    url = ENDPOINT.format(model=config.GEMINI_MODEL)
    for intento in range(1, INTENTOS + 1):
        try:
            r = requests.post(
                url,
                params={"key": config.GEMINI_API_KEY},
                json=body,
                timeout=TIMEOUT,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code in REINTENTABLES:
                # 429/503: cuota momentánea o modelo sobrecargado. Esperamos y probamos de nuevo,
                # así el grupo no ve un "se me trabó la cabeza" por algo que se arregla solo.
                log.warning("Gemini %s (intento %d/%d)", r.status_code, intento, INTENTOS)
                if intento < INTENTOS:
                    time.sleep(ESPERA * intento)
                    continue
            if r.status_code >= 400:
                log.error("Gemini %s: %s", r.status_code, r.text[:500])
                return None
            data = r.json()
            texto = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(texto)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            log.warning("Gemini: %s (intento %d/%d)", type(e).__name__, intento, INTENTOS)
            if intento < INTENTOS:
                time.sleep(ESPERA * intento)
        except Exception:
            log.exception("Falló la llamada a Gemini")
            return None
    log.error("Gemini no contestó después de %d intentos", INTENTOS)
    return None


def _int(item: dict, key: str) -> int | None:
    v = item.get(key, NO_APLICA)
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return None if v == NO_APLICA or v < 0 else v


def spec_de_item(item: dict) -> DateSpec:
    kind = item.get("fecha_kind") or "desconocida"
    if kind not in KINDS:
        kind = "desconocida"
    return DateSpec(
        kind=kind,
        weekday=_int(item, "fecha_weekday"),
        day=_int(item, "fecha_day"),
        month=_int(item, "fecha_month"),
        year=_int(item, "fecha_year"),
        days=_int(item, "fecha_dias"),
    )


def recurrencia_de_item(item: dict) -> Recurrencia | None:
    kind = item.get("recur_kind") or "ninguna"
    if kind in ("ninguna", "", None):
        return None
    try:
        interval = max(1, int(item.get("recur_interval") or 1))
    except (TypeError, ValueError):
        interval = 1
    return Recurrencia(
        kind=kind,
        interval=interval,
        weekday=_int(item, "recur_weekday"),
        monthday=_int(item, "recur_monthday"),
    )


def interpretar_mensaje(texto: str, autor: str, ref: date | None = None,
                        contexto_previo: str | None = None) -> dict | None:
    """Devuelve el dict crudo del LLM o None si falló."""
    ref = ref or hoy()
    partes = [_contexto_fecha(ref), f"Lo escribió: {config.NOMBRES.get(autor, 'alguien')}."]
    if contexto_previo:
        partes.append(f"Mensaje anterior de la misma persona: «{contexto_previo}»")
    partes.append(f"Mensaje: «{texto}»")
    return _call("\n".join(partes), schema_mensaje(), _sistema())


SISTEMA_FECHA = """Convertís expresiones de fecha en español rioplatense a una intención.
NO calcules fechas: elegí el tipo y los campos. Los campos que no aplican van en -1.
"el lunes" -> dia_semana (fecha_weekday=0). "el lunes de la semana que viene" -> dia_semana_prox.
"esta semana" -> esta_semana. "la semana que viene" -> semana_que_viene.
"el 3 de octubre" -> fecha_exacta (fecha_day=3, fecha_month=10).
"algún día", "no sé", "cuando se pueda", "ni idea" -> algun_dia.
Si no se entiende nada -> desconocida.
"""


def interpretar_fecha(texto: str, ref: date | None = None) -> DateSpec | None:
    ref = ref or hoy()
    data = _call(f"{_contexto_fecha(ref)}\nExpresión: «{texto}»", SCHEMA_FECHA, SISTEMA_FECHA)
    if not data:
        return None
    return spec_de_item(data)
