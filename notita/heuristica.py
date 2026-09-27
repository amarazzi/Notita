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
from .dates import DateSpec, aplanar, extraer_fecha, extraer_fecha_y_hora, extraer_recurrencia

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
    # Acciones masivas. Van antes que completar/borrar porque son más específicas.
    ("vaciar_super", r"(borra|borrar|vacia|vaciar|limpia|saca|tacha|tachar)\s+"
                     r"((todo|todas?)\s*)?(lo\s+)?(el\s+|la\s+|del?\s+)?"
                     r"(super|supermercado|lista del super)\b"
                     r"|^(ya\s+)?(compramos|compre|compré)\s+todo\b"
                     r"|^listo,?\s+(compramos|compre)\s+todo\b"),
    ("borrar_todo", r"^(borra|borrar|elimina|eliminar|borrame)\s+"
                    r"(todo|todas las tareas|todas las cosas|la lista)\b"
                    r"|^empecemos de cero\b"),
    # «ya» + cualquier verbo en pasado. Antes era una lista cerrada y «ya lavé los
    # platos» o «ya colgué el cuadro» se anotaban como tarea nueva.
    ("completar", r"^(?:ya|listo,? ?ya?)\s+(?:lo|la|los|las|le)?\s*"
                  r"(?!que\b|no\b|se\b|es\b|esta\b|estan\b|casi\b|falta\b)"
                  r"\w{2,}(?:e|i|o|amos|imos|ado|ido|ada|ida)\b"
                  r"|^(?:ya |listo,? ?)(?:esta|estan) ?(?:hecho|hecha|hechas|hechos)\b"
                  r"|^(?:hecho|hecha|listo|lista|ya esta|ya fue|ya estan)\b"),
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


# Un verbo en primera persona del pasado arranca el mensaje: «saqué la basura».
# Se mira el texto SIN aplanar, porque la tilde final es justo la pista que lo
# distingue del infinitivo ("saqué" vs "sacar", "pagué" vs "pagar").
PASADO_SUELTO = re.compile(r"^\s*(?!café|puré|bebé|mamá|papá)([a-záéíóúñü]{3,}[éí])\b",
                           re.IGNORECASE)
PASADO_IRREGULAR = re.compile(r"^\s*(hice|hicimos|puse|pusimos|fui|fuimos|dije|dijimos|"
                              r"traje|trajimos|tuve|tuvimos|vine|vinimos)\b", re.IGNORECASE)


def _intencion(texto: str) -> tuple[str, str] | None:
    """Devuelve (intencion, referencia) si reconoce un pedido que no sea anotar."""
    t = aplanar(texto).strip(" .!¡?¿,")
    pasado = PASADO_SUELTO.match(texto) or PASADO_IRREGULAR.match(texto)
    if pasado:
        return "completar", _limpiar_referencia(aplanar(texto[pasado.end():]).strip())
    for intencion, patron in INTENCIONES:
        m = re.search(patron, t)
        if not m:
            continue
        if intencion not in ("completar", "borrar"):
            return intencion, ""
        if re.search(r"\btodo\b|\btodas\b", t):
            # «borrá todo» sin más: es masivo, no una tarea que se llama "todo".
            return ("vaciar_super" if re.search(r"super|compras", t) else "borrar_todo"), ""
        # La referencia es lo que queda después del verbo. Si el verbo se comió todo
        # («hecho», «ya fue»), no hay referencia y Notita pregunta cuál era.
        return intencion, _limpiar_referencia(t[m.end():].strip())
    return None


def _limpiar_referencia(resto: str) -> str:
    anterior = None
    while resto != anterior:  # sacar el ruido en cadena
        anterior = resto
        resto = RUIDO_REFERENCIA.sub("", resto, count=1).strip()
    return resto


def objetivos(referencia: str) -> list[str]:
    """«la yerba y el papel higiénico del super» -> ['yerba', 'papel higiénico'].

    Un mensaje puede nombrar varias tareas; antes sólo se atendía la primera.
    """
    if not referencia.strip():
        return []
    sin_cola = re.sub(r"\s+(del?|en el)\s+(super|supermercado|lista|listado)\s*$", "",
                      referencia.strip(), flags=re.IGNORECASE)
    partes = [p for p in re.split(r"\s*,\s*|\s+y\s+|\s+e\s+|\s*;\s*", sin_cola) if p.strip()]
    limpios = []
    for parte in partes:
        limpio = _limpiar_referencia(parte.strip())
        # Un pedazo de una o dos letras no alcanza para buscar nada.
        if len(limpio) >= 3 and limpio not in limpios:
            limpios.append(limpio)
    return limpios or ([sin_cola.strip()] if len(sin_cola.strip()) >= 3 else [])


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


def _parece_teclado_aporreado(t: str) -> bool:
    """«asdfghjk» no es una tarea.

    Cinco consonantes seguidas no pasan en castellano («construir» llega a cuatro),
    y una palabra larga sin vocales tampoco.
    """
    for palabra in re.findall(r"[a-z]+", t):
        if len(palabra) >= 4 and not re.search(r"[aeiou]", palabra):
            return True
        if re.search(r"[bcdfghjklmnpqrstvwxyz]{5,}", palabra):
            return True
    return False


def _charla(texto: str) -> str | None:
    """Si el mensaje es charla, devuelve la respuesta (puede ser ''). Si no, None."""
    t = aplanar(texto).strip(" .!¡?¿,")
    if not re.search(r"[a-z0-9]", t):  # sólo emojis o signos
        return ""
    if len(t.split()) <= 3 and _parece_teclado_aporreado(t):
        return "No te entendí 🤔 ¿me lo decís de otra forma?"
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
        "fecha_hora": NO_APLICA, "fecha_minuto": NO_APLICA, "fecha_minutos": NO_APLICA,
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
    spec, resto = extraer_fecha_y_hora(resto)
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
                             ("fecha_dias", spec.days), ("fecha_hora", spec.hora),
                             ("fecha_minuto", spec.minuto), ("fecha_minutos", spec.minutos)):
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
        return {"intencion": intencion, "referencia": referencia,
                "objetivos": objetivos(referencia), "categoria_filtro": "",
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
