"""Interpretación sin LLM: el modo de emergencia (y el modo sin Google).

Se usa en dos situaciones:

1. **Sin `GEMINI_API_KEY`.** Notita funciona igual, sólo que más boba: no separa
   varias tareas de una frase sin comas y adivina la categoría por palabras
   clave. Todo queda en tu servidor, no sale nada para afuera.
2. **Cuando Gemini falla** (503, cuota, sin internet). Antes en ese caso la tarea
   se perdía con un «se me trabó la cabeza»; ahora se guarda lo mejor posible.

Devuelve exactamente la misma estructura que `llm.interpretar_mensaje`, así el
resto del código no se enteran de la diferencia.
"""
from __future__ import annotations

import re

from . import config
from .dates import DateSpec, aplanar, extraer_fecha, extraer_recurrencia

# Verbos que indican que algo se HACE (no que se compra).
ACCIONES = (
    "pagar", "pagale", "llamar", "llama", "avisar", "arreglar", "reparar", "limpiar",
    "lavar", "ordenar", "acomodar", "sacar", "tirar", "barrer", "aspirar", "planchar",
    "regar", "renovar", "turno", "tramite", "instalar", "colgar", "pintar", "cocinar",
    "hablar", "mandar", "enviar", "imprimir", "firmar", "reservar", "cambiar", "revisar",
    "devolver", "buscar", "anotar", "sacarle", "cortar", "podar", "bañar", "vacunar",
)

# Señales de que va a la lista del súper.
COMPRAS = (
    "comprar", "compra", "compremos", "comprame", "falta", "faltan", "falto", "faltó",
    "se acabo", "se acabó", "se termino", "se terminó", "traer", "reponer", "encargar",
    "necesitamos", "hace falta",
)

# Palabras por categoría, en orden de prioridad (la primera que matchea gana).
CATEGORIAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pagos", ("pagar", "pago", "expensas", "alquiler", "factura", "boleta", "impuesto",
               "abl", "monotributo", "tarjeta", "cuota", "seguro", "prepaga", "luz",
               "gas", "internet", "cable")),
    ("tramites", ("tramite", "turno", "dni", "pasaporte", "renovar", "anses", "afip",
                  "migraciones", "banco", "formulario", "papeles", "medico", "dentista",
                  "escribano", "abogado", "gestoria")),
    ("mascotas", ("gato", "gata", "perro", "perra", "veterinario", "veterinaria",
                  "vacuna", "piedritas", "arena", "pipeta", "balanceado", "correa")),
    ("arreglos", ("arreglar", "reparar", "plomero", "electricista", "gasista", "cerradura",
                  "canilla", "perdida", "gotera", "instalar", "colgar", "pintar", "pintura",
                  "humedad", "persiana", "cortina", "mueble")),
    ("limpieza", ("limpiar", "limpieza", "lavar", "ordenar", "acomodar", "barrer", "aspirar",
                  "baldear", "trapear", "basura", "platos", "ropa", "planchar", "tender",
                  "placard", "heladera", "baño", "cocina")),
)

# Arranques que no aportan nada a la tarea. Se sacan en cadena: de
# «los dos tenemos que ordenar» queda «ordenar».
PREFIJOS = (
    "hay que", "habria que", "habría que", "tenemos que", "tengo que", "tenes que",
    "tenés que", "hay q", "acordate de", "acordarse de", "no te olvides de",
    "no me olvido de", "recordar", "recordame", "me acordas de", "porfa", "por favor",
    "los dos", "las dos", "ambos", "juntos", "juntas", "che", "y", "tambien", "también",
)

# La coma es la única señal de "lista" en la que confiamos sin LLM. El « y » se
# parte sólo en el último pedazo, que es donde el castellano lo usa para cerrar
# una enumeración («a, b y c»). Así «hablar con el plomero y el electricista»
# queda entero.
MAX_FRAGMENTOS = 6

# Mensajes que claramente son charla. Sin LLM no se puede distinguir mucho más,
# así que la lista es corta a propósito: ante la duda, se anota.
SALUDOS = re.compile(r"^(?:hola+|holis|buenas|buen dia|buenos dias|buenas tardes|"
                     r"buenas noches|hey|ey|notita)(?: notita| bot)?$")
AGRADECIMIENTOS = re.compile(r"^(?:gracias+|mil gracias|te quiero|sos la mejor|"
                             r"sos lo mas|genia|genial)$")
RUIDO = re.compile(r"^(?:(?:ja|je|ji|ha|sh)+|ok|oka|okey|dale|listo|si|sip|no|nop|nada|"
                   r"bien|todo bien|chau|xd|uh|ah|eh|m+|bueno|obvio|claro)$")


# Intenciones que se reconocen sin LLM. Se prueban en orden.
INTENCIONES = (
    ("ver_super", r"(mostrame|pasame|dame|ver|cual es|leeme|que hay en).{0,20}"
                  r"(super|supermercado|compras|mandados)"
                  r"|^(super|compras|lista del super)\??$"
                  r"|que (falta|hay que) comprar"),
    ("ver_algun_dia", r"(algun dia|sin fecha)\b.{0,15}(que|cuales|mostrame|tengo|hay)"
                      r"|(mostrame|pasame|dame|ver).{0,20}(algun dia|sin fecha)"),
    ("ver_pendientes", r"(que|cuales|cuanto).{0,25}(hay que hacer|tenemos (que hacer|pendiente)"
                       r"|queda|falta hacer|hay pendiente|esta pendiente|pendientes)"
                       r"|(mostrame|pasame|dame|ver|leeme|listame).{0,20}"
                       r"(tareas|pendientes|lista|todo|que hacer)"
                       r"|^(tareas|pendientes|la lista)\??$"),
    ("ver_ayuda", r"(como (funciona|te uso|se usa|andas)|que (sabes|podes) hacer"
                  r"|para que servis|ayuda|help)"),
    ("completar", r"^(ya |listo,? ?)(esta|estan)? ?(hecho|hecha|hice|hicimos|termine|terminamos|"
                  r"limpie|limpiamos|pague|pagamos|llame|llamamos|saque|sacamos|compre|compramos|"
                  r"arregle|arreglamos|ordene|ordenamos|regue|regamos)"
                  r"|^(hecho|listo|ya esta|ya fue)\b"),
    # Con las variantes mal escritas más comunes: se escribe rápido desde el celular.
    # (`aplanar` ya saca las tildes, así que "borrá" llega como "borra".)
    ("borrar", r"^(borra|borralo|borrala|borrame|borrar|elimina|elimna|eliminar|"
               r"saca|sacalo|sacala|sacame|cancela|cancelar|olvidate|olvidalo|"
               r"no importa|ya no)\b"),
)

# Palabras a sacar de la referencia («borrá la de la heladera» -> «heladera»).
RUIDO_REFERENCIA = re.compile(
    r"^(ya|listo|borra|borrame|elimina|saca|sacame|cancela|olvidate de|olvidate|"
    r"esta|estan|hecho|hecha|hice|hicimos|termine|terminamos|limpie|limpiamos|pague|"
    r"pagamos|llame|llamamos|saque|sacamos|compre|compramos|arregle|arreglamos|ordene|"
    r"ordenamos|regue|regamos|lo de|la de|el de|lo del|la del|de|del|el|la|los|las|que|"
    r"tarea|tema)\b\s*")

# «decile a Axel que lo amo», «avisale a Barbu que llego tarde»
RECADO = re.compile(r"^(?:decile|decil|dile|avisale|avisa|contale|mandale|deciles)\s+"
                    r"(?:a\s+)?(?P<quien>[\wáéíóúñ]+)\s*(?:,|\s)?\s*"
                    r"(?:que\s+)?(?P<mensaje>.+)$", re.IGNORECASE)


def _intencion(texto: str) -> tuple[str, str] | None:
    """Devuelve (intencion, referencia) si reconoce un pedido que no sea anotar."""
    t = aplanar(texto).strip(" .!¡?¿,")
    for intencion, patron in INTENCIONES:
        m = re.search(patron, t)
        if not m:
            continue
        if intencion not in ("completar", "borrar"):
            return intencion, ""
        # La referencia es lo que queda después del verbo.
        resto = t[m.end():].strip() or t[m.start():].strip()
        anterior = None
        while resto != anterior:  # sacar el ruido en cadena
            anterior = resto
            resto = RUIDO_REFERENCIA.sub("", resto, count=1).strip()
        return intencion, resto
    return None


def _recado(texto: str, autor: str) -> dict | None:
    """«decile a Axel que lo amo» -> item de tipo recado."""
    m = RECADO.match(texto.strip())
    if not m:
        return None
    quien = aplanar(m.group("quien"))
    destinatario = next((p.slug for p in config.PERSONAS_CASA
                         if aplanar(p.nombre) == quien or p.slug == quien), None)
    if destinatario is None or destinatario == autor:
        return None
    spec, mensaje = extraer_fecha(m.group("mensaje"))
    # El "que" puede quedar en el medio: «decile a Axel mañana que lo amo».
    mensaje = re.sub(r"^\s*que\s+", "", _limpiar(mensaje), flags=re.IGNORECASE)
    if len(mensaje) < 2:
        return None
    item = _base(mensaje)
    item.update({"tipo": "recado", "categoria": "otros", "responsable": destinatario,
                 "fecha_kind": spec.kind if spec and spec.kind != "algun_dia" else "hoy"})
    if spec:
        for campo, valor in (("fecha_weekday", spec.weekday), ("fecha_day", spec.day),
                             ("fecha_month", spec.month), ("fecha_year", spec.year),
                             ("fecha_dias", spec.days)):
            if valor is not None:
                item[campo] = valor
    return item


def _charla(texto: str) -> str | None:
    """Si el mensaje es charla, devuelve la respuesta (puede ser ''). Si no, None."""
    t = aplanar(texto).strip(" .!¡?¿,")
    if not re.search(r"[a-z0-9]", t):  # sólo emojis o signos
        return ""
    if SALUDOS.match(t):
        return "¡Hola! Acá estoy 🤍"
    if AGRADECIMIENTOS.match(t):
        return "De nada 🤍"
    if RUIDO.match(t):
        return ""
    return None

NO_APLICA = -1


def _limpiar(texto: str) -> str:
    """Saca prefijos, conectores sueltos y espacios de más."""
    t = re.sub(r"\s+", " ", texto).strip(" ,.;:-¡!¿?")
    seguir = True
    while seguir:  # en cadena: "los dos" + "tenemos que"
        seguir = False
        plano = aplanar(t)
        for p in PREFIJOS:
            if plano.startswith(p + " ") or plano == p:
                t = t[len(p):].strip(" ,.;:-")
                seguir = True
                break
    # Preposiciones que quedaron colgando después de sacar la fecha.
    t = re.sub(r"\s+(el|la|los|las|de|del|para|en|a|al|que)$", "", t, flags=re.IGNORECASE)
    return t.strip(" ,.;:-")


def _responsable(plano: str) -> tuple[str, str | None]:
    """Devuelve (slug, nombre encontrado) mirando si se menciona a alguien."""
    if re.search(r"\b(los dos|ambos|las dos|juntos|juntas)\b", plano):
        return "ambos", None
    encontrados = [p for p in config.PERSONAS_CASA
                   if re.search(rf"\b{re.escape(aplanar(p.nombre))}\b", plano)]
    if len(encontrados) > 1:
        return "ambos", None
    if encontrados:
        return encontrados[0].slug, encontrados[0].nombre
    return "ninguno", None


def _sacar_nombre(texto: str, nombre: str) -> str:
    """«Barbu tiene que llamar al plomero» -> «llamar al plomero»."""
    patron = (rf"^\s*{re.escape(nombre)}\s*"
              r"(?:tiene que|tenes que|tenés que|podes|podés|va a|que)?\s*")
    return re.sub(patron, "", texto, count=1, flags=re.IGNORECASE)


def _es_compra(plano: str) -> bool:
    if any(re.search(rf"\b{a}\b", plano) for a in ACCIONES):
        return False
    return any(s in plano for s in COMPRAS)


def _categoria(plano: str) -> str:
    for categoria, palabras in CATEGORIAS:
        if any(re.search(rf"\b{p}\b", plano) for p in palabras):
            return categoria
    return "otros"


def _base(texto: str) -> dict:
    """Un item con la misma forma que devuelve el LLM, todo en valores neutros."""
    return {
        "texto": texto,
        "tipo": "casa",
        "categoria": "otros",
        "responsable": "ninguno",
        "fecha_kind": "desconocida",
        "fecha_weekday": NO_APLICA, "fecha_day": NO_APLICA, "fecha_month": NO_APLICA,
        "fecha_year": NO_APLICA, "fecha_dias": NO_APLICA,
        "recur_kind": "ninguna",
        "recur_interval": 1,
        "recur_weekday": NO_APLICA, "recur_monthday": NO_APLICA,
        "necesita_aclaracion": False,
        "pregunta": "",
    }


def item_crudo(texto: str) -> dict:
    """Último recurso: guardar el mensaje tal cual y después preguntar la fecha."""
    return _base(_limpiar(texto)[:200] or texto[:200])


def _item(fragmento: str) -> dict | None:
    """Convierte un pedazo de mensaje en un item con la forma que devuelve el LLM."""
    rec, resto = extraer_recurrencia(fragmento)
    spec, resto = extraer_fecha(resto)
    plano_completo = aplanar(fragmento)

    responsable, nombre = _responsable(plano_completo)
    if nombre:
        resto = _sacar_nombre(resto, nombre)
    texto = _limpiar(resto)
    if len(texto) < 2:
        return None

    plano = aplanar(texto)
    if _es_compra(plano_completo):
        tipo, categoria, spec = "compras", "compras", DateSpec("algun_dia")
    else:
        tipo, categoria = "casa", _categoria(plano)

    item = _base(texto)
    item.update({
        "tipo": tipo,
        "categoria": categoria,
        "responsable": responsable,
        "fecha_kind": spec.kind if spec else "desconocida",
        "recur_kind": rec.kind if rec else "ninguna",
        "recur_interval": rec.interval if rec else 1,
    })
    if spec:
        for campo, valor in (("fecha_weekday", spec.weekday), ("fecha_day", spec.day),
                             ("fecha_month", spec.month), ("fecha_year", spec.year),
                             ("fecha_dias", spec.days)):
            if valor is not None:
                item[campo] = valor
    if rec:
        if rec.weekday is not None:
            item["recur_weekday"] = rec.weekday
        if rec.monthday is not None:
            item["recur_monthday"] = rec.monthday
    return item


def _contagiar_compras(items: list[dict]) -> None:
    """«comprar yerba, pan y dulce de leche»: el verbo está sólo en el primer pedazo.

    Si la lista arranca en el súper, los fragmentos que quedaron sin clasificar
    (sin verbo propio ni fecha) también son del súper.
    """
    if not items or items[0]["tipo"] != "compras":
        return
    for item in items[1:]:
        sin_pistas = (item["categoria"] == "otros"
                      and item["fecha_kind"] == "desconocida"
                      and item["recur_kind"] == "ninguna"
                      and not any(re.search(rf"\b{a}\b", aplanar(item["texto"]))
                                  for a in ACCIONES))
        if sin_pistas:
            item.update({"tipo": "compras", "categoria": "compras",
                         "fecha_kind": "algun_dia"})


def _fragmentar(texto: str) -> list[str]:
    """Parte «a, b y c» en tres. Sin comas, no parte nada."""
    if "," not in texto:
        return [texto]
    pedazos = [p for p in texto.split(",") if p.strip()]
    if pedazos:  # el « y » final sólo cuenta en el último pedazo
        ultimos = re.split(r"\s+y\s+(?=\w)", pedazos[-1])
        pedazos = pedazos[:-1] + [u for u in ultimos if u.strip()]
    if len(pedazos) > MAX_FRAGMENTOS:  # enumeración larguísima: mejor no picarla mal
        return [texto]
    return pedazos


def interpretar(texto: str, autor: str = "ninguno") -> dict:
    """Misma forma que `llm.interpretar_mensaje`, pero sin salir a internet."""
    charla = _charla(texto)
    if charla is not None:
        return {"intencion": "charla", "es_tarea": False, "comentario": charla,
                "items": [], "local": True}

    pedido = _intencion(texto)
    if pedido:
        intencion, referencia = pedido
        return {"intencion": intencion, "referencia": referencia, "categoria_filtro": "",
                "es_tarea": False, "comentario": "", "items": [], "local": True}

    recado = _recado(texto, autor)
    if recado:
        return {"intencion": "anotar", "es_tarea": True, "comentario": "",
                "items": [recado], "local": True}

    items = [i for i in (_item(f) for f in _fragmentar(texto)) if i]
    _contagiar_compras(items)
    if not items:  # no pudimos sacar nada en limpio: lo guardamos tal cual
        items = [item_crudo(texto)]
    return {"intencion": "anotar", "es_tarea": True, "comentario": "",
            "items": items, "local": True}
