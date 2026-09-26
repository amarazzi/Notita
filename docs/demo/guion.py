"""La conversación del GIF, actuada por el código real de Notita.

No hay textos escritos a mano: se ejecutan los handlers de verdad con Telegram
simulado, y lo que el bot contesta es lo que termina en el GIF. Si mañana
cambia un texto o un emoji, el GIF se regenera y sigue siendo fiel.
"""
from __future__ import annotations


import tempfile
from dataclasses import dataclass, field

CHAT = -1001234567890
AXEL, BARBU = 111111111, 222222222


@dataclass
class Burbuja:
    lado: str                      # "in" (Notita) o "out" (nosotros)
    html: str
    # Cada botón es (texto, callback_data): el texto lo dibuja el renderer y el
    # callback_data lo usa el guion para "tocarlo" de verdad.
    botones: list[list[tuple[str, str]]] = field(default_factory=list)
    hora: str = ""

    def callback(self, contiene: str) -> str:
        """El callback_data del botón cuyo texto contenga eso."""
        for fila in self.botones:
            for texto, data in fila:
                if contiene.lower() in texto.lower():
                    return data
        raise LookupError(f"no hay un botón «{contiene}» en {self.botones}")


@dataclass
class Evento:
    """Un cambio en la pantalla: una burbuja nueva, una edición o un separador."""
    tipo: str                      # burbuja | edicion | separador
    burbuja: Burbuja | None = None
    message_id: int = 0
    texto: str = ""


MODULOS_CON_HOY = ("notita.dates", "notita.views", "notita.handlers", "notita.reminders")
_HOY: list = [None]


def fijar_hoy(fecha) -> None:
    """Congela el día, para que el GIF salga siempre igual.

    Hay que pisarlo en cada módulo: varios hacen `from .dates import hoy`, así que
    se quedan con su propia referencia y no alcanza con cambiar `dates.hoy`.
    """
    import sys

    _HOY[0] = fecha
    for nombre in MODULOS_CON_HOY:
        modulo = sys.modules.get(nombre)
        if modulo is not None and hasattr(modulo, "hoy"):
            modulo.hoy = lambda: fecha


def preparar():
    """Deja el módulo listo con una casa de mentira y sin salir a internet."""
    from notita import config, db, telegram
    from notita.config import Persona

    # Directo sobre config: la variable de entorno no sirve acá porque config ya
    # se importó (y entonces la demo escribiría en la base de verdad, sumando
    # tareas repetidas en cada corrida).
    config.DB_PATH = tempfile.mktemp(suffix=".db")
    config.ALLOWED_CHAT_ID = CHAT
    config.GEMINI_API_KEY = "demo"
    config.CONTEXTO_CASA = "Tenemos un gato que se llama Milo."
    config.definir_personas((Persona("axel", "Axel", AXEL),
                             Persona("barbu", "Barbu", BARBU)))
    db.init_db()

    eventos: list[Evento] = []
    burbujas: dict[int, Burbuja] = {}
    reloj = {"hora": "19:41"}

    def fake_llamar(metodo, **payload):
        if metodo == "sendMessage":
            mid = len(burbujas) + 1
            b = Burbuja("in", payload["text"], _botones(payload), reloj["hora"])
            burbujas[mid] = b
            eventos.append(Evento("burbuja", burbuja=b))
            return {"message_id": mid}
        if metodo == "editMessageText":
            mid = payload["message_id"]
            eventos.append(Evento("edicion", message_id=mid, texto=payload["text"]))
            return {"message_id": mid}
        return {"ok": True}

    telegram.llamar = fake_llamar
    return eventos, burbujas, reloj


def _botones(payload) -> list[list[tuple[str, str]]]:
    filas = (payload.get("reply_markup") or {}).get("inline_keyboard") or []
    return [[(b["text"], b.get("callback_data", "")) for b in fila] for fila in filas]


def _item(texto, tipo="casa", categoria="otros", responsable="ninguno",
          fecha_kind="desconocida", **extra):
    base = {"texto": texto, "tipo": tipo, "categoria": categoria,
            "responsable": responsable, "fecha_kind": fecha_kind,
            "fecha_weekday": -1, "fecha_day": -1, "fecha_month": -1,
            "fecha_year": -1, "fecha_dias": -1, "recur_kind": "ninguna",
            "recur_interval": 1, "recur_weekday": -1, "recur_monthday": -1,
            "necesita_aclaracion": False, "pregunta": ""}
    base.update(extra)
    return base


# Lo que contestaría Gemini para cada mensaje. Es lo único simulado: de acá en
# adelante trabaja el código de verdad.
RESPUESTAS = {
    "hay que comprar comida para Milo, limpiar la heladera el finde y llamar al plomero": {
        "intencion": "anotar", "es_tarea": True, "items": [
            _item("comprar comida para Milo", tipo="compras", categoria="compras",
                  fecha_kind="algun_dia"),
            _item("limpiar la heladera", categoria="limpieza", fecha_kind="fin_de_semana"),
            _item("llamar al plomero", categoria="arreglos"),
        ]},
    "falta leche y papel higiénico": {
        "intencion": "anotar", "es_tarea": True, "items": [
            _item("leche", tipo="compras", categoria="compras", fecha_kind="algun_dia"),
            _item("papel higiénico", tipo="compras", categoria="compras",
                  fecha_kind="algun_dia"),
        ]},
    "decile a Axel que compre pan cuando vuelva": {
        "intencion": "anotar", "es_tarea": True, "items": [
            _item("comprá pan cuando vuelvas", tipo="recado", responsable="axel",
                  fecha_kind="hoy"),
        ]},
    "¿qué hay que hacer?": {
        "intencion": "ver_pendientes", "categoria_filtro": "ninguna",
        "es_tarea": False, "items": []},
}


def actuar():
    """Corre la conversación y devuelve los eventos de pantalla, en orden."""
    eventos, burbujas, reloj = preparar()
    from notita import handlers, llm, reminders

    if _HOY[0] is not None:   # por si se importaron recién, en preparar()
        fijar_hoy(_HOY[0])
    llm.interpretar_mensaje = lambda texto, *a, **k: RESPUESTAS[texto]

    def escribe(quien, texto, hora):
        reloj["hora"] = hora
        eventos.append(Evento("burbuja", burbuja=Burbuja("out", texto, hora=hora)))
        handlers.handle_update({"message": {
            "chat": {"id": CHAT}, "from": {"id": quien}, "text": texto, "message_id": 99}})

    def toca(quien, data, message_id, hora):
        reloj["hora"] = hora
        handlers.handle_update({"callback_query": {
            "id": "cb", "data": data, "from": {"id": quien},
            "message": {"message_id": message_id, "chat": {"id": CHAT}}}})

    def ultima_con_botones(mid_desde=0) -> int:
        """El message_id de la última burbuja que tenga botones."""
        return max(m for m, b in burbujas.items() if b.botones and m > mid_desde)

    escribe(AXEL, "hay que comprar comida para Milo, limpiar la heladera el finde "
                  "y llamar al plomero", "19:41")
    # Notita preguntó para cuándo es lo del plomero: Axel toca «Hoy».
    pregunta = ultima_con_botones()
    toca(AXEL, burbujas[pregunta].callback("Hoy"), pregunta, "19:42")

    escribe(BARBU, "falta leche y papel higiénico", "19:52")
    escribe(BARBU, "decile a Axel que compre pan cuando vuelva", "19:53")
    escribe(AXEL, "¿qué hay que hacer?", "19:58")

    eventos.append(Evento("separador", texto="20:00"))
    reloj["hora"] = "20:00"
    antes = max(burbujas)
    reminders.correr_rutina_diaria()

    # Llegó el recordatorio del plomero: Axel lo marca hecho.
    recordatorio = ultima_con_botones(antes)
    toca(AXEL, burbujas[recordatorio].callback("Hecho"), recordatorio, "20:03")
    return eventos
