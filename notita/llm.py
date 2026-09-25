"""Interpretación de lenguaje natural con Gemini (capa gratuita).

Llamamos a la API REST con `requests` a propósito: es una dependencia mínima y
en PythonAnywhere gratuito sale por el proxy sin configuración extra.

El LLM NUNCA calcula fechas. Devuelve una intención (`fecha_kind` + campos) y
la aritmética la hace `dates.resolve`.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import requests

from . import config
from .dates import KINDS, DIAS_NOMBRE, DateSpec, Recurrencia, hoy

log = logging.getLogger("notita.llm")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = 25

NO_APLICA = -1

_ITEM_PROPS = {
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

SCHEMA_MENSAJE = {
    "type": "object",
    "properties": {
        "es_tarea": {"type": "boolean", "description": "false si el mensaje es charla y no hay nada para anotar."},
        "comentario": {"type": "string", "description": "Si es_tarea=false, una respuesta breve y cariñosa. Si no, ''."},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM_PROPS,
                "required": ["texto", "tipo", "categoria", "responsable", "fecha_kind",
                             "recur_kind", "necesita_aclaracion"],
                "propertyOrdering": list(_ITEM_PROPS),
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

SISTEMA = """Sos Notita, un bot que organiza las tareas de una casa donde viven Axel y Barbu.
Recibís mensajes en español rioplatense de un grupo de Telegram y los convertís en tareas.

Reglas:
- Un mensaje puede contener VARIAS tareas: separalas en items distintos.
  "hay que limpiar la heladera, llamar al plomero y comprar focos" son 3 items.
- tipo="compras" si es algo que se compra en el super o en un negocio
  ("falta leche", "comprar yerba", "se acabó el detergente"). Todo lo demás es tipo="casa".
  Los items de compras siempre llevan categoria="compras" y fecha_kind="algun_dia".
- responsable: "axel" o "barbu" si el mensaje dice quién lo hace
  ("Barbu tiene que llamar al veterinario"), "ambos" si es de los dos,
  "ninguno" si no se menciona. No adivines.
- categoria: limpieza, arreglos, tramites, pagos, mascotas, compras u otros.
  * pagos: SOLO si hay que pagar plata (facturas, expensas, alquiler, impuestos).
  * tramites: gestiones, papeles, turnos, renovar cuentas o servicios sin pagar.
  * arreglos: reparar o instalar cosas en la casa. limpieza: limpiar u ordenar.
  * mascotas: todo lo del gato Milo. otros: lo que no encaje en ninguna.
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


def _contexto_fecha(ref: date) -> str:
    return (f"Hoy es {DIAS_NOMBRE[ref.weekday()]} {ref.isoformat()} "
            f"(zona horaria America/Argentina/Buenos_Aires).")


def _call(prompt: str, schema: dict, sistema: str) -> dict | None:
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
    try:
        r = requests.post(
            url,
            params={"key": config.GEMINI_API_KEY},
            json=body,
            timeout=TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code >= 400:
            log.error("Gemini %s: %s", r.status_code, r.text[:500])
            return None
        data = r.json()
        texto = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(texto)
    except Exception:
        log.exception("Falló la llamada a Gemini")
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
    return _call("\n".join(partes), SCHEMA_MENSAJE, SISTEMA)


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
