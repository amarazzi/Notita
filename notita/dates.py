"""Toda la aritmética de fechas de Notita.

Módulo puro: no toca red ni base de datos, así se puede testear a gusto.
La regla de oro es que el LLM nunca calcula fechas; sólo devuelve una
`DateSpec` (una intención) y acá la resolvemos contra el día de hoy.

Convención de semana: lunes = 0 ... domingo = 6 (como `date.weekday()`).
La semana va de lunes a domingo.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import TZ

DIAS_SEMANA = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
MESES_NOMBRE = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Tipos válidos de DateSpec (también es el enum que le pedimos al LLM).
KINDS = (
    "hoy",
    "manana",
    "pasado",
    "ayer",                 # «sacar la basura ayer»: se anota vencida
    "anteayer",
    "en_minutos",           # «en 2 horas», «en 10 minutos»
    "dia_semana",           # "el lunes" -> próximo lunes (nunca hoy)
    "dia_semana_prox",      # "el lunes de la semana que viene"
    "esta_semana",          # vence el domingo de esta semana
    "semana_que_viene",     # vence el domingo de la semana que viene
    "fin_de_semana",        # el sábado que viene
    "fecha_exacta",         # 3 de octubre
    "dia_del_mes",          # "el 10"
    "en_dias",
    "algun_dia",            # sin vencimiento
    "desconocida",          # hay que preguntar
)


@dataclass(frozen=True)
class DateSpec:
    kind: str
    weekday: int | None = None
    day: int | None = None
    month: int | None = None
    year: int | None = None
    days: int | None = None
    hora: int | None = None      # hora del día, si la dijeron («a las 18»)
    minuto: int | None = None
    minutos: int | None = None   # para «en_minutos»: cuánto falta desde ahora

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @property
    def tiene_hora(self) -> bool:
        return self.hora is not None or self.kind == "en_minutos"


def hoy() -> date:
    """Hoy en hora de Buenos Aires."""
    return datetime.now(TZ).date()


def ahora() -> datetime:
    return datetime.now(TZ)


# --------------------------------------------------------------------------
# Resolución
# --------------------------------------------------------------------------

def lunes_de_la_semana(ref: date) -> date:
    return ref - timedelta(days=ref.weekday())


def domingo_de_la_semana(ref: date) -> date:
    return lunes_de_la_semana(ref) + timedelta(days=6)


def proximo_dia_semana(ref: date, weekday: int) -> date:
    """Próxima ocurrencia estricta: si hoy es ese día, devuelve el de la semana siguiente."""
    delta = (weekday - ref.weekday()) % 7
    return ref + timedelta(days=delta or 7)


def dia_semana_de_la_semana_que_viene(ref: date, weekday: int) -> date:
    return lunes_de_la_semana(ref) + timedelta(days=7 + weekday)


def proximo_fin_de_semana(ref: date) -> date:
    """Sábado que viene. Si hoy es sábado, el domingo; si es domingo, el sábado próximo."""
    if ref.weekday() == 5:
        return ref + timedelta(days=1)
    return proximo_dia_semana(ref, 5)


def proximo_dia_del_mes(ref: date, day: int) -> date:
    """Próxima vez que el calendario marque ese número (hoy cuenta)."""
    candidato = _fecha_segura(ref.year, ref.month, day)
    if candidato >= ref:
        return candidato
    y, m = _sumar_meses(ref.year, ref.month, 1)
    return _fecha_segura(y, m, day)


def resolve(spec: DateSpec | None, ref: date | None = None) -> date | None:
    """Convierte una DateSpec en una fecha concreta. `None` = sin vencimiento."""
    if spec is None:
        return None
    ref = ref or hoy()
    k = spec.kind
    if k == "hoy":
        return ref
    if k == "manana":
        return ref + timedelta(days=1)
    if k == "pasado":
        return ref + timedelta(days=2)
    if k == "ayer":
        return ref - timedelta(days=1)
    if k == "anteayer":
        return ref - timedelta(days=2)
    if k == "en_minutos" and spec.minutos is not None:
        # Depende de la hora actual, así que se calcula con el reloj de la casa.
        return (momento() + timedelta(minutes=spec.minutos)).date()
    if k == "dia_semana" and spec.weekday is not None:
        return proximo_dia_semana(ref, spec.weekday)
    if k == "dia_semana_prox" and spec.weekday is not None:
        return dia_semana_de_la_semana_que_viene(ref, spec.weekday)
    if k == "esta_semana":
        return domingo_de_la_semana(ref)
    if k == "semana_que_viene":
        return domingo_de_la_semana(ref) + timedelta(days=7)
    if k == "fin_de_semana":
        return proximo_fin_de_semana(ref)
    if k == "en_dias" and spec.days is not None:
        return ref + timedelta(days=spec.days)
    if k == "dia_del_mes" and spec.day is not None:
        return proximo_dia_del_mes(ref, spec.day)
    if k == "fecha_exacta" and spec.day is not None:
        mes = spec.month or ref.month
        if spec.year:
            return _fecha_segura(spec.year, mes, spec.day)
        candidato = _fecha_segura(ref.year, mes, spec.day)
        if candidato < ref:  # si ya pasó, es del año que viene
            candidato = _fecha_segura(ref.year + 1, mes, spec.day)
        return candidato
    return None  # algun_dia / desconocida / spec incompleta


def momento() -> datetime:
    """El instante actual, pero tomando el día de `hoy()`.

    Importa para los tests y para el generador de la demo, que congelan `hoy()`: si
    la hora viniera del reloj real, las cuentas con fecha y hora no coincidirían.
    """
    reloj = ahora()
    return datetime.combine(hoy(), reloj.timetz())


def resolver_momento(spec: DateSpec | None,
                     instante: datetime | None = None) -> tuple[date | None, str | None]:
    """Fecha y hora («HH:MM») de una intención. La hora es None si no la dijeron."""
    instante = instante or momento()
    if spec is None:
        return None, None
    if spec.kind == "en_minutos" and spec.minutos is not None:
        objetivo = instante + timedelta(minutes=spec.minutos)
        return objetivo.date(), objetivo.strftime("%H:%M")
    fecha = resolve(spec, instante.date())
    if fecha is None or spec.hora is None:
        return fecha, None
    hora = min(23, max(0, spec.hora))
    minuto = min(59, max(0, spec.minuto or 0))
    return fecha, f"{hora:02d}:{minuto:02d}"


def cuando_se_entrega(fecha: date | None, hora: str | None,
                      ref: date | None = None) -> tuple[date | None, str | None]:
    """Cuándo va a salir de verdad algo pedido para (fecha, hora).

    Notita no puede avisar a las 22:07 si la rutina corre cada 5 minutos: va a salir
    22:10. Y si corre una sola vez por día, sale en la pasada principal. Mejor decir
    la hora real que prometer una que no se va a cumplir.
    """
    from . import config

    if fecha is None:
        return None, None
    ref = ref or hoy()
    if hora is None:
        return fecha, config.HORA_RUTINA

    h, m = (int(x) for x in hora.split(":"))
    if config.CRON_MINUTOS:
        paso = config.CRON_MINUTOS
        redondeado = -(-m // paso) * paso        # al siguiente múltiplo, hacia arriba
        if redondeado >= 60:
            h, redondeado = h + 1, 0
        if h >= 24:                               # pasa al día siguiente
            return fecha + timedelta(days=1), f"{h - 24:02d}:{redondeado:02d}"
        return fecha, f"{h:02d}:{redondeado:02d}"

    # Una sola corrida por día: si la hora ya pasó para esa corrida, sale en la del día
    # siguiente (los recados con hora esperan su hora, no se adelantan).
    if hora <= config.HORA_RUTINA:
        return fecha, config.HORA_RUTINA
    return fecha + timedelta(days=1), config.HORA_RUTINA


def fecha_imposible(spec: DateSpec | None) -> bool:
    """«el 31 de febrero» no existe: mejor preguntar que guardar el 28 en silencio."""
    if spec is None or spec.kind != "fecha_exacta" or spec.day is None:
        return False
    mes = spec.month or 1
    if not 1 <= mes <= 12 or not 1 <= spec.day <= 31:
        return True
    anio = spec.year or hoy().year
    return spec.day > calendar.monthrange(anio, mes)[1]


def es_este_finde(d: date | None, ref: date | None = None) -> bool:
    """Si la fecha es el sábado o domingo de la semana en curso."""
    if d is None:
        return False
    ref = ref or hoy()
    return d.weekday() >= 5 and lunes_de_la_semana(d) == lunes_de_la_semana(ref)


# --------------------------------------------------------------------------
# Parser en español (rioplatense). Es el camino rápido: si acá entendemos,
# no hace falta gastar una llamada al LLM.
# --------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t).strip(" .!¡?¿,")


_ACENTOS = str.maketrans("áàäâãéèëêíìïîóòöôõúùüûýñç", "aaaaaeeeeiiiiooooouuuuync")


def aplanar(texto: str) -> str:
    """Como `normalizar` pero SIN cambiar la longitud del texto.

    Sirve para buscar expresiones dentro de una frase y después poder recortar
    el original usando las mismas posiciones.
    """
    t = texto.lower().translate(_ACENTOS)
    return t if len(t) == len(texto) else texto.lower()


_NUMEROS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15,
}

_RE_DIA_SEMANA = "|".join(DIAS_SEMANA)
_RE_MES = "|".join(MESES)


def parse_natural(texto: str) -> DateSpec | None:
    """Intenta entender una expresión de fecha en español. `None` si no la reconoce."""
    t = normalizar(texto)
    if not t:
        return None

    if re.search(r"\b(algun dia|alguna vez|cuando se pueda|cuando pueda|no se|ni idea|"
                 r"sin fecha|en algun momento|cualquier dia)\b", t):
        return DateSpec("algun_dia")

    if re.search(r"\bpasado manana\b", t):
        return DateSpec("pasado")
    if re.fullmatch(r"(el )?pasado", t):
        return DateSpec("pasado")
    if re.search(r"\bmanana\b", t):
        return DateSpec("manana")
    if re.search(r"\b(hoy|esta noche|ahora|hoy mismo|ya)\b", t):
        return DateSpec("hoy")

    # "el lunes de la semana que viene" / "el lunes que viene"
    m = re.search(rf"\b({_RE_DIA_SEMANA})\b", t)
    if m:
        wd = DIAS_SEMANA[m.group(1)]
        resto = t[m.end():]
        if re.search(r"(de la|la)? ?(semana|finde) (que viene|proxima)", resto) or \
           re.search(r"(semana|finde) (que viene|proxima)", t[: m.start()]):
            return DateSpec("dia_semana_prox", weekday=wd)
        return DateSpec("dia_semana", weekday=wd)

    if re.search(r"\b(fin de semana|finde)\b", t):
        return DateSpec("fin_de_semana")
    if re.search(r"\besta semana\b", t):
        return DateSpec("esta_semana")
    if re.search(r"\b(la )?(semana que viene|proxima semana|semana proxima)\b", t):
        return DateSpec("semana_que_viene")

    m = re.search(r"\ben (\d+|" + "|".join(_NUMEROS) + r") (dias?|semanas?|meses|mes)\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMEROS[m.group(1)]
        unidad = m.group(2)
        if unidad.startswith("semana"):
            return DateSpec("en_dias", days=n * 7)
        if unidad.startswith("mes"):
            return DateSpec("en_dias", days=n * 30)
        return DateSpec("en_dias", days=n)

    # "3 de octubre (de 2026)"
    m = re.search(rf"\b(\d{{1,2}}) de ({_RE_MES})(?: de (\d{{4}}))?\b", t)
    if m:
        return DateSpec("fecha_exacta", day=int(m.group(1)), month=MESES[m.group(2)],
                        year=int(m.group(3)) if m.group(3) else None)

    # "3/10" o "3/10/2026" o "3-10"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        anio = m.group(3)
        if anio and len(anio) == 2:
            anio = "20" + anio
        return DateSpec("fecha_exacta", day=int(m.group(1)), month=int(m.group(2)),
                        year=int(anio) if anio else None)

    # "el 10" (día del mes)
    m = re.fullmatch(r"(el |los |todos los )?(\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 31:
        return DateSpec("dia_del_mes", day=int(m.group(2)))

    return None


def parse_y_resolver(texto: str, ref: date | None = None) -> tuple[DateSpec | None, date | None]:
    spec = parse_natural(texto)
    return spec, resolve(spec, ref)


# Palabras que pueden acompañar a una fecha sin cambiarla.
RELLENO_FECHA = frozenset(
    "para el la los las un una de del al a en este esta ese esa por favor porfa mejor "
    "creo que es seria dale ok bueno y o mas menos tarde temprano noche manana tardecita "
    "mediodia medio dia primera hora".split())


def parse_solo_fecha(texto: str) -> DateSpec | None:
    """La fecha sólo si el mensaje ES una fecha y nada más.

    «el lunes» sí; «el lunes voy al dentista» no. La diferencia importa: cuando
    Notita pregunta «¿para cuándo?», un mensaje cualquiera que mencione un día no
    tiene que robarse la respuesta (antes se la robaba y el mensaje se perdía).
    """
    if parse_natural(texto) is None:
        return None
    spec, resto = extraer_fecha(texto)
    if spec is None:
        # `parse_natural` conoce más formas de decir "algún día" que el extractor.
        suelta = parse_natural(texto)
        return suelta if suelta and suelta.kind == "algun_dia" else None
    if [p for p in normalizar(resto).split() if p not in RELLENO_FECHA]:
        return None
    return spec


# --------------------------------------------------------------------------
# Recurrencia
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Recurrencia:
    kind: str  # diaria | semanal | mensual | anual
    interval: int = 1
    weekday: int | None = None
    monthday: int | None = None


def _numero(txt: str) -> int:
    return int(txt) if txt.isdigit() else _NUMEROS.get(txt, 1)


def _recortar(texto: str, span: tuple[int, int]) -> str:
    """Saca un pedazo del texto y deja el resto prolijo."""
    resto = texto[: span[0]] + " " + texto[span[1]:]
    return re.sub(r"\s+", " ", resto).strip(" ,.;:-")


# Cada entrada: (regex, función que arma la DateSpec con el match).
# El orden importa: lo más específico primero, para consumir la frase completa.
_PATRONES_FECHA: list[tuple[str, object]] = [
    (r"\b(?:algun dia|alguna vez|cuando se pueda|cuando pueda|en algun momento)\b",
     lambda m: DateSpec("algun_dia")),
    (r"\bpasado manana\b", lambda m: DateSpec("pasado")),
    (r"\bmanana\b", lambda m: DateSpec("manana")),
    (r"\bhoy\b", lambda m: DateSpec("hoy")),
    (r"\banteayer\b", lambda m: DateSpec("anteayer")),
    (r"\bayer\b", lambda m: DateSpec("ayer")),
    # «en 2 horas», «en 10 minutos». Va antes que «en N días» porque comparten forma.
    (rf"\ben (\d+|{'|'.join(_NUMEROS)}) (horas?|minutos?|mins?)\b",
     lambda m: DateSpec("en_minutos",
                        minutos=_numero(m.group(1)) * (60 if m.group(2).startswith("hora") else 1))),
    # "el lunes", "el lunes que viene", "el lunes de la semana que viene"
    (rf"\b(?:el |los |este |para el )?({_RE_DIA_SEMANA})\b"
     r"(?:\s+(?:de\s+)?(?:la\s+)?semana\s+(?:que viene|proxima))?",
     lambda m: DateSpec("dia_semana_prox" if "semana" in m.group(0) else "dia_semana",
                        weekday=DIAS_SEMANA[m.group(1)])),
    (r"\b(?:el )?(?:fin de semana|finde)\b", lambda m: DateSpec("fin_de_semana")),
    (r"\besta semana\b", lambda m: DateSpec("esta_semana")),
    (r"\b(?:la )?(?:semana que viene|proxima semana|semana proxima)\b",
     lambda m: DateSpec("semana_que_viene")),
    (rf"\ben (\d+|{'|'.join(_NUMEROS)}) (dias?|semanas?|mes|meses)\b",
     lambda m: DateSpec("en_dias", days=_numero(m.group(1)) *
                        (7 if m.group(2).startswith("semana") else
                         30 if m.group(2).startswith("mes") else 1))),
    (rf"\b(?:el )?(\d{{1,2}}) de ({_RE_MES})(?: de (\d{{4}}))?\b",
     lambda m: DateSpec("fecha_exacta", day=int(m.group(1)), month=MESES[m.group(2)],
                        year=int(m.group(3)) if m.group(3) else None)),
    (r"\b(?:el )?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b",
     lambda m: DateSpec("fecha_exacta", day=int(m.group(1)), month=int(m.group(2)),
                        year=_anio(m.group(3)))),
    # "el 10" suelto: pedimos el "el" para no agarrar cualquier número de la frase.
    (r"\bel (\d{1,2})\b", lambda m: DateSpec("dia_del_mes", day=int(m.group(1)))),
]

_PATRONES_RECURRENCIA: list[tuple[str, object]] = [
    (r"\b(?:todos los dias|cada dia|a diario|diariamente)\b",
     lambda m: Recurrencia("diaria")),
    (rf"\bcada (\d+|{'|'.join(_NUMEROS)}) dias\b",
     lambda m: Recurrencia("diaria", interval=_numero(m.group(1)))),
    (rf"\b(?:todos los|cada) ({_RE_DIA_SEMANA})\b",
     lambda m: Recurrencia("semanal", weekday=DIAS_SEMANA[m.group(1)])),
    (r"\b(?:todas las semanas|cada semana|semanalmente)\b",
     lambda m: Recurrencia("semanal")),
    (rf"\bcada (\d+|{'|'.join(_NUMEROS)}) semanas\b",
     lambda m: Recurrencia("semanal", interval=_numero(m.group(1)))),
    (r"\b(?:todos los meses|cada mes|mensualmente)\b",
     lambda m: Recurrencia("mensual")),
    (rf"\bcada (\d+|{'|'.join(_NUMEROS)}) meses\b",
     lambda m: Recurrencia("mensual", interval=_numero(m.group(1)))),
    # "pagar expensas todos los 10"
    (r"\b(?:todos los|cada) (\d{1,2})\b",
     lambda m: Recurrencia("mensual", monthday=int(m.group(1)))),
    (r"\b(?:todos los anos|cada ano|anualmente)\b", lambda m: Recurrencia("anual")),
]


def _anio(txt: str | None) -> int | None:
    if not txt:
        return None
    return int("20" + txt) if len(txt) == 2 else int(txt)


def _extraer(texto: str, patrones: list) -> tuple[object | None, str]:
    """Busca la expresión más larga que matchee y devuelve (lo encontrado, texto sin eso).

    Gana la más larga, no la primera de la lista: en «el lunes a la mañana» hay dos
    candidatas («el lunes» y «mañana») y la que vale es la más específica.
    A igual largo, manda el orden de la lista.
    """
    plano = aplanar(texto)
    mejor = None
    for indice, (patron, armar) in enumerate(patrones):
        m = re.search(patron, plano)
        if m is None:
            continue
        largo = m.end() - m.start()
        if mejor is None or largo > mejor[0]:
            mejor = (largo, indice, armar, m)
    if mejor is None:
        return None, texto
    _, _, armar, m = mejor
    return armar(m), _recortar(texto, m.span())


# La hora del día se busca aparte de la fecha: «el jueves a las 18» tiene las dos.
_FRANJAS = {"mañana": 9, "manana": 9, "tarde": 16, "noche": 21, "mediodia": 12,
            "mediodía": 12, "madrugada": 3}

_PATRONES_HORA: list[tuple[str, object]] = [
    # «a las 18:30», «18:30», «a las 8 y media»
    (r"\b(?:a la |a las |)?([01]?\d|2[0-3]):([0-5]\d)\s*(?:hs?|horas)?\b",
     lambda m: (int(m.group(1)), int(m.group(2)))),
    (r"\b(?:a la|a las)\s+([01]?\d|2[0-3])\s+y\s+media\b",
     lambda m: (int(m.group(1)), 30)),
    # «a las 18», «a las 6 de la tarde», «18 hs».
    # Ojo con el `\s`: si se lo come el grupo de «hs», el «de la tarde» queda afuera
    # y «a las 6 de la tarde» termina siendo a las 6 de la mañana.
    (rf"\b(?:a la|a las)\s+([01]?\d|2[0-3])(?:\s*(?:hs?|horas))?"
     rf"(?:\s+(?:de|por)\s+la\s+({'|'.join(_FRANJAS)}))?\b",
     lambda m: (_ajustar_franja(int(m.group(1)), m.group(2)), 0)),
    (r"\b([01]?\d|2[0-3])\s*(?:hs|hrs)\b", lambda m: (int(m.group(1)), 0)),
    # «al mediodía», «a la noche», «de la tarde»
    (rf"\b(?:al|a la|de la|por la|de|por)\s+({'|'.join(_FRANJAS)})\b",
     lambda m: (_FRANJAS[m.group(1)], 0)),
]


def _ajustar_franja(hora: int, franja: str | None) -> int:
    """«a las 6 de la tarde» son las 18."""
    if franja in ("tarde", "noche") and hora < 12:
        return hora + 12
    if franja in ("mañana", "manana", "madrugada") and hora == 12:
        return 0
    return hora


def extraer_hora(texto: str) -> tuple[tuple[int, int] | None, str]:
    """Saca la hora del día: «el jueves a las 18» -> ((18, 0), 'el jueves')."""
    return _extraer(texto, _PATRONES_HORA)


def extraer_fecha(texto: str) -> tuple[DateSpec | None, str]:
    """Saca la expresión de fecha de una frase: «regar el lunes» -> (lunes, 'regar')."""
    spec, resto = _extraer(texto, _PATRONES_FECHA)
    return spec, resto


def extraer_fecha_y_hora(texto: str) -> tuple[DateSpec | None, str]:
    """Las dos cosas juntas: «llamar el jueves a las 18» -> (jueves 18:00, 'llamar')."""
    hm, resto = extraer_hora(texto)
    spec, resto = extraer_fecha(resto)
    if hm is None:
        return spec, resto
    if spec is None:
        spec = DateSpec("hoy")     # «a las 18» sin día es hoy
    return DateSpec(spec.kind, weekday=spec.weekday, day=spec.day, month=spec.month,
                    year=spec.year, days=spec.days, hora=hm[0], minuto=hm[1]), resto


def extraer_recurrencia(texto: str) -> tuple[Recurrencia | None, str]:
    """«cambiar las piedritas cada semana» -> (semanal, 'cambiar las piedritas')."""
    rec, resto = _extraer(texto, _PATRONES_RECURRENCIA)
    return rec, resto


def _sumar_meses(year: int, month: int, n: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + n
    return total // 12, total % 12 + 1


def _fecha_segura(year: int, month: int, day: int) -> date:
    """Construye una fecha recortando el día al último del mes (31 -> 28/29/30)."""
    ultimo = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, ultimo))


_VERBOS_DE_COMPRA = re.compile(
    r"^(?:hay que |habria que |tenemos que |tengo que |)"
    r"(?:comprar|comprame|compra|traer|traeme|trae|conseguir|consegui|llevar|"
    r"reponer|encargar|pedir)\s+", re.IGNORECASE)



def texto_recurrencia(rec: Recurrencia | None) -> str:
    """«cada 3 días», «todos los martes», «todos los 10». Vacío si no se repite."""
    if rec is None or rec.kind not in ("diaria", "semanal", "mensual", "anual"):
        return ""
    n = max(1, rec.interval or 1)
    if rec.kind == "diaria":
        return "todos los días" if n == 1 else f"cada {n} días"
    if rec.kind == "semanal":
        if rec.weekday is not None and 0 <= rec.weekday <= 6 and n == 1:
            dia = DIAS_NOMBRE[rec.weekday]
            return f"todos los {dia}" + ("s" if rec.weekday >= 5 else "")
        return "cada semana" if n == 1 else f"cada {n} semanas"
    if rec.kind == "mensual":
        if rec.monthday and n == 1:
            return f"todos los {rec.monthday}"
        return "cada mes" if n == 1 else f"cada {n} meses"
    return "todos los años" if n == 1 else f"cada {n} años"


def proxima_ocurrencia(rec: Recurrencia | None, desde: date) -> date | None:
    """Siguiente vencimiento de una tarea recurrente, partiendo de `desde`.

    Para las mensuales se usa `monthday` como ancla, así un "todos los 31"
    que en febrero cayó el 28 vuelve al 31 en marzo.
    """
    if rec is None or rec.kind in (None, "", "ninguna"):
        return None
    n = max(1, rec.interval or 1)
    if rec.kind == "diaria":
        return desde + timedelta(days=n)
    if rec.kind == "semanal":
        if rec.weekday is not None:
            base = proximo_dia_semana(desde, rec.weekday)
            return base + timedelta(days=7 * (n - 1))
        return desde + timedelta(days=7 * n)
    if rec.kind == "mensual":
        ancla = rec.monthday or desde.day
        y, m = _sumar_meses(desde.year, desde.month, n)
        return _fecha_segura(y, m, ancla)
    if rec.kind == "anual":
        ancla_dia = rec.monthday or desde.day
        mes = desde.month
        return _fecha_segura(desde.year + n, mes, ancla_dia)
    return None


# --------------------------------------------------------------------------
# Formato lindo para los mensajes
# --------------------------------------------------------------------------

def formato_humano(d: date | None, ref: date | None = None) -> str:
    if d is None:
        return "algún día"
    ref = ref or hoy()
    delta = (d - ref).days
    if delta == 0:
        return "hoy"
    if delta == 1:
        return "mañana"
    if delta == 2:
        return "pasado mañana"
    if delta == -1:
        return "ayer"
    if 3 <= delta <= 6:
        return f"el {DIAS_NOMBRE[d.weekday()]} {d.day}/{d.month}"
    if delta < 0:
        return f"venció el {d.day}/{d.month}"
    if delta <= 13:
        return f"el {DIAS_NOMBRE[d.weekday()]} {d.day}/{d.month}"
    return f"el {d.day} de {MESES_NOMBRE[d.month - 1]}"


def formato_dia(d: date) -> str:
    return f"{DIAS_NOMBRE[d.weekday()]} {d.day}/{d.month}"


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def de_iso(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None
