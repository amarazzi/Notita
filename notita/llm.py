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
# Vale la pena reintentar cuando el modelo está sobrecargado: se arregla en segundos.
# El 429 NO se reintenta: es cuota por minuto y Google pide esperar ~30s, más de lo que
# Telegram aguanta un webhook. Conviene caer al modo local, que guarda la tarea igual.
REINTENTABLES = (500, 502, 503, 504)

NO_APLICA = -1

# Qué quiere el mensaje. `anotar` es lo de siempre; el resto son cosas que Notita
# ya sabía hacer pero sólo por comando.
# v2: el texto sólo CREA. Las modificaciones se proponen y las confirma un botón.
INTENCIONES = (
    "crear",        # hay algo para anotar
    "modificar",    # piden cambiar algo que ya existe -> se PROPONE, no se ejecuta
    "recado",       # «decile a Barbu que…»: se dice al instante
    "ver",          # piden ver la lista
    "charla",       # cualquier otra cosa
)

ACCIONES_MODIFICAR = ("completar", "borrar", "mover", "renombrar", "reasignar",
                      "vaciar_super", "pausar")

# Para «todo lo de mañana»: el conjunto lo resuelve el servidor, no el modelo.
CONJUNTOS = ("ninguno", "hoy", "manana", "vencidas", "semana", "algun_dia", "super", "todo")


def _item_props() -> dict:
    """El enum de `responsable` depende de quién vive en la casa: se arma al vuelo."""
    return {
        "titulo": {"type": "string", "description": "La cosa, CON LAS PALABRAS DEL MENSAJE. Sacá sólo la fecha y el nombre de quién la hace. NO saques ni agregues verbos y no la reescribas: 'falta leche' -> 'Falta leche'; 'comprar detergente para los platos' -> 'Comprar detergente para los platos'; 'hay que llamar al plomero el lunes' -> 'Llamar al plomero'. Arreglá sólo los errores de tipeo y las abreviaturas ('pa' -> 'para')."},
        "compra": {"type": "boolean", "description": "true si es algo que se resuelve metiéndolo al carrito en una salida de compras normal: comida, limpieza, cosas chicas de ferretería o vivero (pilas, cemento, tierra, lamparitas). false para todo lo demás, incluso si dice 'comprar': muebles, electrodomésticos, regalos, pasajes, trámites. ANTE LA DUDA: false. Es sólo una etiqueta para filtrar; no cambia nada más."},
        "categoria": {"type": "string", "enum": list(config.CATEGORIAS)},
        "responsable": {"type": "string", "enum": list(config.PERSONAS)},
        "fecha_kind": {"type": "string", "enum": list(KINDS)},
        "fecha_weekday": {"type": "integer", "description": "0=lunes ... 6=domingo. -1 si no aplica."},
        "fecha_day": {"type": "integer", "description": "Día del mes. -1 si no aplica."},
        "fecha_month": {"type": "integer", "description": "1-12. -1 si no aplica."},
        "fecha_year": {"type": "integer", "description": "Año de 4 dígitos. -1 si no aplica."},
        "fecha_dias": {"type": "integer", "description": "Días para fecha_kind='en_dias'. -1 si no aplica."},
        "fecha_hora": {"type": "integer", "description": "Hora del día en 24h si la dijeron ('a las 6 de la tarde' -> 18). -1 si no."},
        "fecha_minuto": {"type": "integer", "description": "Minutos de la hora. -1 si no aplica."},
        "recur_kind": {"type": "string", "enum": ["ninguna", "diaria", "semanal", "mensual", "anual"]},
        "recur_interval": {"type": "integer", "description": "Cada cuántos períodos. 1 por defecto."},
        "recur_weekday": {"type": "integer", "description": "0=lunes ... 6=domingo para la semanal. -1 si no aplica."},
        "recur_monthday": {"type": "integer", "description": "Día fijo del mes para la mensual. -1 si no aplica."},
    }


def schema_mensaje() -> dict:
    props = _item_props()
    return {
        "type": "object",
        "properties": {
            "intencion": {"type": "string", "enum": list(INTENCIONES)},
            "comentario": {"type": "string", "description": "Si intencion=charla o ver, una respuesta breve y cálida. Si no, ''."},
            "items": {
                "type": "array",
                "description": "Si intencion=crear: uno por cada cosa mencionada.",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": ["titulo", "compra", "categoria", "responsable",
                                 "fecha_kind", "recur_kind"],
                    "propertyOrdering": list(props),
                },
            },
            "accion": {"type": "string", "enum": ["ninguna", *ACCIONES_MODIFICAR],
                       "description": "Si intencion=modificar: qué quieren hacer."},
            "referencias": {
                "type": "array",
                "description": "Si intencion=modificar: UNA ENTRADA POR CADA cosa mencionada, con las palabras del mensaje. 'ya compré la leche y la lavandina' -> ['leche', 'lavandina'].",
                "items": {"type": "string"},
            },
            "conjunto": {"type": "string", "enum": list(CONJUNTOS),
                         "description": "Si en vez de nombrar cosas hablan de un grupo entero ('todo lo de mañana' -> manana, 'todas las tareas' -> todo, 'toda la lista de compras' -> super). Si nombran cosas puntuales: 'ninguno'."},
            "destino_fecha_kind": {"type": "string", "enum": list(KINDS),
                                   "description": "Si accion=mover: a cuándo. Si no, 'desconocida'."},
            "destino_fecha_weekday": {"type": "integer", "description": "0=lunes ... 6=domingo. -1 si no aplica."},
            "destino_fecha_day": {"type": "integer", "description": "Día del mes. -1 si no aplica."},
            "destino_fecha_month": {"type": "integer", "description": "1-12. -1 si no aplica."},
            "destino_fecha_year": {"type": "integer", "description": "Año. -1 si no aplica."},
            "destino_fecha_dias": {"type": "integer", "description": "Días. -1 si no aplica."},
            "destino_fecha_hora": {"type": "integer", "description": "Hora nueva en 24h. -1 si no."},
            "destino_fecha_minuto": {"type": "integer", "description": "Minutos. -1 si no aplica."},
            "destino_responsable": {"type": "string", "enum": list(config.PERSONAS),
                                    "description": "Si accion=reasignar: quién queda a cargo."},
            "destino_texto": {"type": "string", "description": "Si accion=renombrar: el nombre nuevo. Si no, ''."},
            "recado_para": {"type": "string", "enum": list(config.PERSONAS),
                            "description": "Si intencion=recado: a quién. Si no, 'ninguno'."},
            "recado_mensaje": {"type": "string", "description": "Si intencion=recado: el mensaje, escrito como si se lo dijeran en la cara ('decile a Axel que lo amo' -> 'te amo'). Si no, ''."},
            "ver_que": {"type": "string", "enum": ["ninguno", "tablero", "super"],
                        "description": "Si intencion=ver: qué quieren ver."},
        },
        "required": ["intencion", "items"],
        "propertyOrdering": ["intencion", "comentario", "items", "accion", "referencias",
                             "conjunto", "destino_fecha_kind", "destino_fecha_weekday",
                             "destino_fecha_day", "destino_fecha_month",
                             "destino_fecha_year", "destino_fecha_dias",
                             "destino_fecha_hora", "destino_fecha_minuto",
                             "destino_responsable", "destino_texto",
                             "recado_para", "recado_mensaje", "ver_que"],
    }


def _quienes_viven() -> str:
    if not config.PERSONAS_CASA:
        return '- responsable: siempre "ninguno" (no hay personas configuradas).'
    pares = ", ".join(f'"{p.slug}" para {p.nombre}' for p in config.PERSONAS_CASA)
    todos = "de los dos" if len(config.PERSONAS_CASA) == 2 else "de todos"
    return (f'- responsable: {pares} si el mensaje dice quién lo hace,\n'
            f'  "ambos" si es {todos}, "ninguno" si no se menciona. No adivines.')


def _sistema() -> str:
    return f"""Sos Notita, un bot que organiza las tareas de una casa donde viven {config.nombres_de_la_casa()}.
Recibís mensajes de un grupo de Telegram que se usa SÓLO para vos.
{f"Sobre la casa: {config.CONTEXTO_CASA}" if config.CONTEXTO_CASA else ""}

Tu trabajo es clasificar el mensaje en UNA intención:

- "crear": hay algo para anotar. Un mensaje puede tener VARIAS cosas: una por item.
  "hay que limpiar la heladera, llamar al plomero y falta leche" son 3 items.
- "modificar": piden cambiar algo que YA existe: darlo por hecho ("ya compré la
  leche"), borrarlo ("borrá lo del plomero"), moverlo de día ("pasá lo del horno al
  domingo", "todo lo de mañana para hoy"), renombrarlo o cambiar quién lo hace.
  También "ya compramos todo" (accion=vaciar_super) y "pausá Notita hasta el 10"
  (accion=pausar).
  VOS NO EJECUTÁS NADA: sólo describís el pedido. Alguien va a confirmar con un botón.
  Poné en `referencias` una entrada por cada cosa nombrada, con las palabras del
  mensaje. Si en cambio hablan de un grupo entero, usá `conjunto`.
- "recado": le piden que le transmita algo a alguien de la casa, para AHORA
  ("decile a Barbu que ya salí"). `recado_mensaje` va en primera persona.
  OJO: si el recado es para más tarde o para otro día ("decile mañana que compre
  pan"), NO es un recado: es "crear" un item con esa fecha y ese responsable.
- "ver": piden ver la lista de tareas o la de compras.
- "charla": saludos, gracias, preguntas, cualquier otra cosa. items=[].

CUIDADO con el pasado: un infinitivo con fecha pasada es algo que quedó pendiente,
no algo hecho. "sacar la basura ayer" -> crear con fecha_kind="ayer".
"saqué la basura" (verbo en pasado) -> modificar/completar. La diferencia es el verbo.

Reglas de los items:
{_quienes_viven()}
- categoria: limpieza, arreglos, tramites, pagos, mascotas, compras u otros.
  * pagos: SOLO si hay que pagar plata. tramites: gestiones, papeles, turnos.
  * arreglos: reparar o instalar. limpieza: limpiar u ordenar.
  * mascotas: los animales de la casa. otros: lo que no encaje.
- titulo: LAS PALABRAS DEL MENSAJE. Saca la fecha y el nombre de quién la hace, y
  nada más. No saques verbos, no agregues verbos, no lo reescribas más lindo:
  "falta leche" -> "Falta leche"; "comprar leche" -> "Comprar leche". Sí corregí
  tipeos y abreviaturas.
- compra: es sólo una ETIQUETA (🛒) para poder filtrar. true si se resuelve
  metiéndolo al carrito en una salida de compras normal: comida, limpieza, cosas
  chicas de ferretería o vivero. false para lo que hay que decidir, comparar o ir a
  buscar a un lugar puntual: muebles, electrodomésticos, regalos, pasajes, trámites,
  aunque el mensaje diga "comprar". ANTE LA DUDA: false.
  La etiqueta NO tiene nada que ver con la fecha: "mañana compramos leche" es una
  compra con fecha de mañana.
- fecha_kind: elegí la INTENCIÓN, no calcules la fecha. Nunca devuelvas una fecha hecha.
  * "el lunes" -> dia_semana con fecha_weekday=0
  * "el lunes de la semana que viene" -> dia_semana_prox
  * "esta semana" -> esta_semana ; "la semana que viene" -> semana_que_viene
  * "mañana" -> manana ; "pasado" -> pasado ; "hoy" -> hoy ; "ayer" -> ayer
  * "el 3 de octubre" -> fecha_exacta con fecha_day=3, fecha_month=10
  * "todos los 10" -> dia_del_mes con fecha_day=10
  * "algún día", "no sé", "cuando se pueda" -> algun_dia
  * si el mensaje NO dice cuándo -> desconocida
  * NO INVENTES FECHAS. "hoy" SÓLO si dicen "hoy" (o "esta tarde", "esta noche").
    "comprar tierra para las macetas" no dice cuándo: es desconocida.
- La hora, si la dicen, va en fecha_hora (24h) y fecha_minuto.
  "a las 6 de la tarde" -> 18. "18:30" -> 18 y 30.
- recur_kind si se repite: "cada semana" -> semanal; "todos los 10" -> mensual con
  recur_monthday=10; "cada 3 días" -> diaria con recur_interval=3.
- Campos numéricos que no aplican: -1.

Cómo hablás: español rioplatense, cálido y breve, con algún emoji. NUNCA uses "che"
ni arranques con vocativos ("Ey", "Mirá vos"). No prometas nada que no hagas: no
avisás a horas exactas ni mandás recados para más tarde.
Los mensajes vienen con errores de tipeo y abreviaturas ("q", "xq", "pa", "elimna"):
entendelos igual.
"""


# Modelos cuya cuota gratuita es demasiado chica para usar Notita todos los días.
# gemini-2.5-flash da 20 requests POR DÍA (quotaId GenerateRequestsPerDay...-FreeTier).
MODELOS_CON_POCA_CUOTA = ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-3-pro")
MODELO_RECOMENDADO = "gemini-flash-lite-latest"


def cuota_chica(modelo: str) -> bool:
    return modelo.strip() in MODELOS_CON_POCA_CUOTA


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
        return False, ("se agotó la cuota gratuita de este modelo. Ojo que gemini-2.5-flash "
                       "da sólo 20 mensajes por día: probá con gemini-flash-lite-latest")
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
                # Modelo sobrecargado: se arregla en segundos, así que esperamos y probamos
                # de nuevo antes de caer al modo local.
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


def spec_de_item(item: dict, prefijo: str = "fecha") -> DateSpec:
    """La intención de fecha que devolvió el modelo.

    `prefijo` permite leer también los campos `cambio_fecha_*`, que son los mismos
    campos pero para reprogramar una tarea que ya existe.
    """
    kind = item.get(f"{prefijo}_kind") or "desconocida"
    if kind not in KINDS:
        kind = "desconocida"
    return DateSpec(
        kind=kind,
        weekday=_int(item, f"{prefijo}_weekday"),
        day=_int(item, f"{prefijo}_day"),
        month=_int(item, f"{prefijo}_month"),
        year=_int(item, f"{prefijo}_year"),
        days=_int(item, f"{prefijo}_dias"),
        hora=_int(item, f"{prefijo}_hora"),
        minuto=_int(item, f"{prefijo}_minuto"),
        minutos=_int(item, f"{prefijo}_minutos"),
    )


def objetivos_de(data: dict) -> list[str]:
    """Las tareas a las que apunta una acción, sin repetidas ni vacías.

    El modelo puede mandar `objetivos` (lo esperado) o `referencia` (una sola).
    """
    crudos = data.get("objetivos") or []
    if isinstance(crudos, str):
        crudos = [crudos]
    if not crudos and data.get("referencia"):
        crudos = [data["referencia"]]
    vistos, limpios = set(), []
    for o in crudos:
        o = " ".join(str(o).split())
        if o and o.lower() not in vistos:
            vistos.add(o.lower())
            limpios.append(o)
    return limpios


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
        # Redactado así a propósito: cuando esto decía sólo "mensaje anterior: «X»",
        # el modelo anotaba TAMBIÉN lo del mensaje anterior, y hasta le cambiaba la
        # intención al nuevo. El anterior es contexto, no contenido.
        partes.append(
            f"CONTEXTO: hace un rato escribieron «{contexto_previo}» y no quedó claro, "
            f"así que preguntaste qué querían decir. El mensaje de abajo es la respuesta "
            f"a esa pregunta.\n"
            f"Usá el contexto SÓLO para entender de qué hablan. NO devuelvas items por "
            f"lo del mensaje anterior: devolvé UN SOLO item, el de esa cosa ya aclarada.")
    partes.append(f"Mensaje: «{texto}»")
    return _call("\n".join(partes), schema_mensaje(), _sistema())


SCHEMA_FECHA = {
    "type": "object",
    "properties": {
        "fecha_kind": {"type": "string", "enum": list(KINDS)},
        "fecha_weekday": {"type": "integer"},
        "fecha_day": {"type": "integer"},
        "fecha_month": {"type": "integer"},
        "fecha_year": {"type": "integer"},
        "fecha_dias": {"type": "integer"},
        "fecha_hora": {"type": "integer"},
        "fecha_minuto": {"type": "integer"},
    },
    "required": ["fecha_kind"],
    "propertyOrdering": ["fecha_kind", "fecha_weekday", "fecha_day", "fecha_month",
                         "fecha_year", "fecha_dias", "fecha_hora", "fecha_minuto"],
}

SISTEMA_FECHA = """Convertís expresiones de fecha en español rioplatense a una intención.
NO calcules fechas: elegí el tipo y los campos. Los campos que no aplican van en -1.
"el lunes" -> dia_semana (fecha_weekday=0). "el lunes de la semana que viene" -> dia_semana_prox.
"esta semana" -> esta_semana. "la semana que viene" -> semana_que_viene.
"el 3 de octubre" -> fecha_exacta (fecha_day=3, fecha_month=10).
"algún día", "no sé", "cuando se pueda", "ni idea" -> algun_dia.
Si no se entiende nada -> desconocida.
"""


# --------------------------------------------------------------------------
# Transcribir notas de voz
# --------------------------------------------------------------------------

# Telegram manda las notas de voz en Ogg/Opus y Gemini las acepta tal cual: probado
# con un audio real de WhatsApp (48 kHz, mono) y con archivos generados con ffmpeg.
# No hace falta transcodificar, que era el riesgo que podía tumbar todo esto.
MIME_VOZ = "audio/ogg"

# Timeout propio: transcribir 14 segundos de audio tardó 20 en las pruebas, o sea
# más que el TIMEOUT normal de 25. Y un solo reintento, porque reintentar una
# llamada de 20 segundos es carísimo en espera.
TIMEOUT_AUDIO = 60
INTENTOS_AUDIO = 2

INDESCIFRABLE = "NO_SE_ENTIENDE"

SISTEMA_VOZ = (
    "Transcribí literalmente este audio en español rioplatense. "
    "Devolvé SOLO la transcripción, sin comillas ni comentarios ni explicaciones. "
    f"Si no se entiende nada, o es puro ruido o silencio, devolvé exactamente: {INDESCIFRABLE}"
)


def transcribir(audio: bytes, mime: str = MIME_VOZ) -> str | None:
    """El texto del audio, o None si no se pudo (que es distinto de no entenderlo).

    Devuelve `INDESCIFRABLE` cuando el audio es ruido o silencio: probado, el modelo
    no inventa palabras en ese caso.
    """
    import base64

    if not config.GEMINI_API_KEY:
        return None
    cuerpo = {
        "contents": [{"parts": [
            {"text": SISTEMA_VOZ},
            {"inline_data": {"mime_type": mime,
                             "data": base64.b64encode(audio).decode()}},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    for intento in range(1, INTENTOS_AUDIO + 1):
        try:
            r = requests.post(
                ENDPOINT.format(model=config.GEMINI_MODEL),
                params={"key": config.GEMINI_API_KEY},
                json=cuerpo, timeout=TIMEOUT_AUDIO,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.RequestException as e:
            log.warning("Transcribiendo: %s (intento %d)", type(e).__name__, intento)
            if intento < INTENTOS_AUDIO:
                time.sleep(ESPERA)
            continue
        if r.status_code == 200:
            texto = _texto_de(r.json())
            return " ".join(texto.split()) if texto else None
        # 503 («el modelo está sobrecargado») pasa seguido: vale reintentar una vez.
        log.error("Transcribiendo: HTTP %s %s", r.status_code, r.text[:200])
        if r.status_code in (429, 500, 503) and intento < INTENTOS_AUDIO:
            time.sleep(ESPERA * 2)
            continue
        return None
    return None


def _texto_de(data: dict) -> str:
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        log.error("Respuesta sin texto: %s", str(data)[:200])
        return ""


def interpretar_fecha(texto: str, ref: date | None = None) -> DateSpec | None:
    ref = ref or hoy()
    data = _call(f"{_contexto_fecha(ref)}\nExpresión: «{texto}»", SCHEMA_FECHA, SISTEMA_FECHA)
    if not data:
        return None
    return spec_de_item(data)
