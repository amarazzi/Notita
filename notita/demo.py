"""Telegram y Gemini de mentira, para probar el instalador sin credenciales.

Se activa con `python3 install.py --demo`. No sale nada a internet: todas las
respuestas se inventan acá, con los mismos formatos que devuelve la Bot API.
Sirve para ver la experiencia completa antes de tener el bot creado, y para
que alguien pueda mirar cómo es sin pedirle nada a nadie.
"""
from __future__ import annotations

TOKEN = "123456789:AADemoTokenDeMentiraParaProbarElInstalador"
CHAT_ID = -1001234567890
GRUPO = "Casa de mentira"
GENTE = ((111111111, "Vos"), (222222222, "Tu pareja"))


class TelegramDeMentira:
    """Imita `install.tg` y recuerda lo que le fueron pidiendo."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, dict]] = []
        self.webhook = ""
        self.privacy_off = True
        self.mensajes: list[str] = []
        self.updates_consumidos = False

    def __call__(self, token: str, metodo: str, **payload):
        self.llamadas.append((metodo, payload))
        fn = getattr(self, f"_{metodo}", None)
        return fn(payload) if fn else (True, {})

    # -- métodos de la Bot API que usa el instalador -----------------------

    def _getMe(self, payload):
        return True, {"id": 999, "username": "notita_demo_bot", "first_name": "Notita (demo)",
                      "can_read_all_group_messages": self.privacy_off}

    def _getWebhookInfo(self, payload):
        return True, {"url": self.webhook, "pending_update_count": 0}

    def _deleteWebhook(self, payload):
        self.webhook = ""
        return True, True

    def _setWebhook(self, payload):
        self.webhook = payload.get("url", "")
        return True, True

    def _getUpdates(self, payload):
        """Como si las dos personas acabaran de escribir en el grupo.

        Igual que la API real, una vez confirmados con el offset no vuelven nunca
        más. Es justo el motivo por el que, en una instalación que ya venía
        andando con webhook, la primera pasada no ve nada: hay que escribir de nuevo.
        """
        if payload.get("offset"):
            self.updates_consumidos = True
        if self.updates_consumidos:
            return True, []
        return True, [
            {"update_id": i,
             "message": {"message_id": i,
                         "chat": {"id": CHAT_ID, "type": "group", "title": GRUPO},
                         "from": {"id": uid, "first_name": nombre, "is_bot": False},
                         "text": "hola"}}
            for i, (uid, nombre) in enumerate(GENTE, start=1)
        ]

    def _getChat(self, payload):
        return True, {"id": CHAT_ID, "type": "group", "title": GRUPO}

    def _sendMessage(self, payload):
        self.mensajes.append(payload.get("text", ""))
        return True, {"message_id": len(self.mensajes)}


class RespuestaHTTP:
    """Lo mínimo que mira el instalador de una respuesta de `requests.get`."""

    ok = True
    status_code = 200
    text = '{"ok": true, "bot": "notita"}'

    def json(self):
        return {"ok": True, "bot": "notita"}


def gemini_de_mentira():
    """Devuelve (probar_conexion, interpretar_mensaje) con respuestas creíbles."""
    def probar_conexion(key=None, model=None):
        return True, ""

    def interpretar_mensaje(texto, autor, ref=None, contexto_previo=None):
        return {
            "es_tarea": True,
            "comentario": "",
            "items": [
                {"texto": "limpiar la heladera", "tipo": "casa", "categoria": "limpieza",
                 "responsable": "ninguno", "fecha_kind": "dia_semana", "fecha_weekday": 0},
                {"texto": "leche", "tipo": "compras", "categoria": "compras",
                 "responsable": "ninguno", "fecha_kind": "algun_dia"},
            ],
        }

    return probar_conexion, interpretar_mensaje
