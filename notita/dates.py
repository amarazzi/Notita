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

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


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


# --------------------------------------------------------------------------
# Parser en español (rioplatense). Es el camino rápido: si acá entendemos,
# no hace falta gastar una llamada al LLM.
# --------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t).strip(" .!¡?¿,")


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


# --------------------------------------------------------------------------
# Recurrencia
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Recurrencia:
    kind: str  # diaria | semanal | mensual | anual
    interval: int = 1
    weekday: int | None = None
    monthday: int | None = None


def _sumar_meses(year: int, month: int, n: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + n
    return total // 12, total % 12 + 1


def _fecha_segura(year: int, month: int, day: int) -> date:
    """Construye una fecha recortando el día al último del mes (31 -> 28/29/30)."""
    ultimo = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, ultimo))


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
