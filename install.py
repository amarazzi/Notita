#!/usr/bin/env python3
"""Instalador guiado de Notita. Te deja el .env escrito y el webhook andando.

    python3 install.py

No hace falta saber nada de código: te va preguntando y validando todo contra
Telegram y Google a medida que avanza. Se puede cortar con Ctrl+C y volver a
empezar cuando quieras; no toca la base de datos.
"""
from __future__ import annotations

import logging
import re
import secrets
import shutil
import sys

import requests

from notita import config, llm
from notita.config import Persona, slugificar

TG = "https://api.telegram.org/bot{token}/{metodo}"
ENV = config.BASE_DIR / ".env"

# Los módulos logean los errores crudos; acá los contamos nosotros, con más contexto.
logging.getLogger("notita").setLevel(logging.CRITICAL)


# --------------------------------------------------------------------------
# Presentación
# --------------------------------------------------------------------------

def titulo(n: int, texto: str) -> None:
    print(f"\n\033[1m{n}. {texto}\033[0m")


def ok(texto: str) -> None:
    print(f"  \033[32m✓\033[0m {texto}")


def mal(texto: str) -> None:
    print(f"  \033[31m✗\033[0m {texto}")


def aviso(texto: str) -> None:
    print(f"  \033[33m!\033[0m {texto}")


def dato(texto: str) -> None:
    print(f"    {texto}")


def preguntar(texto: str, default: str = "") -> str:
    sufijo = f" [{default}]" if default else ""
    try:
        r = input(f"  \033[36m?\033[0m {texto}{sufijo}: ").strip()
    except EOFError:
        raise SystemExit("\nSe cortó la entrada. Volvé a correr el instalador.")
    return r or default


def confirmar(texto: str, default: bool = True) -> bool:
    d = "S/n" if default else "s/N"
    r = preguntar(f"{texto} ({d})").lower()
    return default if not r else r.startswith("s")


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def tg(token: str, metodo: str, **payload) -> tuple[bool, dict | str]:
    """Llama a la Bot API y devuelve (ok, resultado o mensaje de error)."""
    try:
        r = requests.post(TG.format(token=token, metodo=metodo), json=payload, timeout=20)
        data = r.json()
    except requests.exceptions.RequestException as e:
        return False, f"no se pudo conectar con api.telegram.org ({type(e).__name__})"
    except ValueError:
        return False, "Telegram contestó algo que no es JSON"
    if not data.get("ok"):
        return False, data.get("description", "error desconocido")
    return True, data.get("result")


def paso_token() -> tuple[str, str]:
    titulo(1, "El token del bot")
    dato("Si todavía no lo tenés: hablale a @BotFather → /newbot y copiá el token.")
    while True:
        token = preguntar("Token de BotFather")
        if not re.fullmatch(r"\d+:[\w-]{30,}", token or ""):
            mal("Eso no tiene forma de token. Es algo como 123456789:AAG...")
            continue
        bien, res = tg(token, "getMe")
        if not bien:
            mal(f"Telegram lo rechazó: {res}")
            continue
        ok(f"Hola, @{res['username']} ({res.get('first_name')})")

        if res.get("can_read_all_group_messages"):
            ok("El privacy mode está apagado: puede leer los mensajes del grupo")
        else:
            aviso("El privacy mode está ENCENDIDO. Así sólo va a leer los comandos (/todo),")
            dato("no los mensajes normales, que es casi todo lo que hace Notita.")
            dato("Arreglalo: @BotFather → /mybots → tu bot → Bot Settings →")
            dato("Group Privacy → Turn off. Y si ya estaba en el grupo, sacalo y volvé a agregarlo.")
            while not confirmar("¿Lo apagaste? Reviso de nuevo"):
                if not confirmar("¿Seguir igual, sabiendo que va a quedar a medias?", default=False):
                    raise SystemExit("  Dale, apagalo y volvé a correr el instalador.")
                return token, res["username"]
            bien, res2 = tg(token, "getMe")
            if bien and res2.get("can_read_all_group_messages"):
                ok("Ahora sí, privacy mode apagado")
            else:
                aviso("Sigue encendido. Podés seguir y arreglarlo después.")
        return token, res["username"]


def paso_grupo_y_personas(token: str, usuario_bot: str) -> tuple[int, tuple[Persona, ...]]:
    titulo(2, "El grupo y quiénes viven en la casa")
    dato(f"1) Creá un grupo de Telegram y agregá a @{usuario_bot}.")
    dato("2) Que CADA persona de la casa escriba un mensaje cualquiera en el grupo")
    dato("   (con eso detecto el grupo y el user_id de cada uno, sin que busquen nada).")

    # getUpdates no funciona si hay un webhook puesto.
    tg(token, "deleteWebhook")

    chat_id, gente = 0, {}
    while True:
        preguntar("Cuando todos hayan escrito, apretá Enter")
        bien, updates = tg(token, "getUpdates", timeout=0, allowed_updates=["message"])
        if not bien:
            mal(f"No pude leer los mensajes: {updates}")
            continue

        grupos: dict[int, str] = {}
        for u in updates or []:
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") not in ("group", "supergroup"):
                continue
            grupos[chat["id"]] = chat.get("title", "sin título")
            autor = msg.get("from") or {}
            if autor.get("id") and not autor.get("is_bot"):
                gente[autor["id"]] = autor.get("first_name") or f"user{autor['id']}"

        if not grupos:
            mal("No vi ningún mensaje de grupo.")
            dato("Chequeá que el bot esté EN el grupo y que alguien haya escrito después de agregarlo.")
            dato("Ojo: si el privacy mode está encendido, probá escribiendo /todo en el grupo.")
            if confirmar("¿Reintento?"):
                continue
            raise SystemExit("  Cortamos acá. Volvé cuando el bot esté en el grupo.")

        if len(grupos) == 1:
            chat_id, nombre = next(iter(grupos.items()))
            ok(f"Grupo detectado: «{nombre}» (chat_id {chat_id})")
        else:
            print("  Encontré varios grupos:")
            opciones = list(grupos.items())
            for i, (cid, nombre) in enumerate(opciones, 1):
                dato(f"{i}) {nombre} ({cid})")
            elegido = preguntar("¿Cuál es el de la casa? (número)", "1")
            try:
                chat_id, nombre = opciones[int(elegido) - 1]
            except (ValueError, IndexError):
                mal("Número inválido")
                continue
            ok(f"Va «{nombre}» ({chat_id})")

        if not gente:
            aviso("No pude sacar quién es quién: no vi mensajes de personas.")
        else:
            print("  Personas detectadas:")
            for uid, nombre in gente.items():
                dato(f"· {nombre} ({uid})")
        if confirmar("¿Está completa la lista?"):
            break
        aviso("Que escriba en el grupo quien falte y reintentamos.")

    personas: list[Persona] = []
    for uid, detectado in gente.items():
        nombre = preguntar(f"¿Cómo le pongo a {detectado}?", detectado)
        personas.append(Persona(slug=slugificar(nombre), nombre=nombre, user_id=uid))
    while confirmar("¿Falta alguien que no escribió? (lo agrego sin mención)", default=False):
        nombre = preguntar("Nombre")
        if nombre:
            personas.append(Persona(slug=slugificar(nombre), nombre=nombre, user_id=0))

    if not personas:
        aviso("Sin personas configuradas: las tareas van a quedar todas sin responsable.")
    else:
        ok("La casa: " + ", ".join(f"{p.nombre}" for p in personas))
    return chat_id, tuple(personas)


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def paso_gemini(personas: tuple[Persona, ...]) -> tuple[str, str]:
    titulo(3, "La API key de Gemini")
    dato("Sacala gratis en https://aistudio.google.com/apikey (no pide tarjeta).")
    dato("Es lo que le da la inteligencia: separa varias tareas de una frase,")
    dato("entiende fechas escritas de cualquier forma y elige categoría y responsable.")
    dato("Si preferís no mandarle nada a Google, dejalo vacío: Notita funciona igual,")
    dato("interpretando todo con reglas locales (más boba, pero 100% en tu servidor).")
    model = "gemini-2.5-flash"
    while True:
        key = preguntar("API key de Google AI Studio (Enter = modo local)")
        if not key:
            aviso("Modo local: sin separar tareas en lote ni categorías automáticas.")
            if confirmar("¿Seguro?", default=False):
                return "", model
            continue
        model = preguntar("Modelo", model)
        bien, detalle = llm.probar_conexion(key, model)
        if not bien:
            mal(f"No anduvo: {detalle}")
            continue
        ok("La key funciona")

        # Prueba de verdad: que interprete un mensaje con dos tareas y tildes.
        config.GEMINI_API_KEY, config.GEMINI_MODEL = key, model
        config.definir_personas(personas)
        data = llm.interpretar_mensaje("hay que limpiar la heladera el lunes y falta leche", "ninguno")
        items = (data or {}).get("items") or []
        if len(items) >= 2 and any(i.get("tipo") == "compras" for i in items):
            ok(f"Prueba real: entendió {len(items)} tareas y mandó la leche al súper")
        else:
            aviso("Contestó, pero no entendió la prueba como esperaba. Podés seguir igual.")
        return key, model


# --------------------------------------------------------------------------
# Webhook y .env
# --------------------------------------------------------------------------

def paso_url() -> str:
    titulo(4, "La dirección pública")
    dato("Es la URL de tu web app, por ejemplo https://tuusuario.pythonanywhere.com")
    dato("Si todavía no la creaste, dejalo vacío y lo configurás después con set_webhook.py.")
    url = preguntar("URL pública (Enter para saltear)")
    if not url:
        aviso("Salteado: el bot no va a recibir mensajes hasta que pongas el webhook.")
        return ""
    url = url.rstrip("/")
    if not url.startswith("https://"):
        aviso("Telegram sólo acepta https. Lo agrego.")
        url = "https://" + url.removeprefix("http://")
    if not url.endswith("/telegram"):
        url += "/telegram"
    base = url.removesuffix("/telegram")
    try:
        r = requests.get(base + "/", timeout=15)
        if r.ok and "notita" in r.text:
            ok("La web app está respondiendo")
        else:
            aviso(f"{base}/ contestó {r.status_code}. Revisá que la web app esté cargada.")
    except requests.exceptions.RequestException:
        aviso("No pude abrir esa URL desde acá. Si recién la creaste, hacé Reload y probá después.")
    return url


def paso_contexto() -> str:
    titulo(5, "Algo sobre la casa (opcional)")
    dato("Ayuda al LLM a acertar. Ej: «Tenemos un gato que se llama Milo».")
    return preguntar("Contexto (Enter para saltear)")


def escribir_env(valores: dict[str, str]) -> None:
    titulo(6, "Guardar la configuración")
    if ENV.exists():
        backup = ENV.with_suffix(".env.bak")
        shutil.copy(ENV, backup)
        aviso(f"Ya existía un .env: lo guardé como {backup.name}")
    lineas = [
        "# Generado por install.py. Podés editarlo a mano cuando quieras.",
        "",
        f"TELEGRAM_TOKEN={valores['token']}",
        f"TELEGRAM_WEBHOOK_SECRET={valores['secret']}",
        f"ALLOWED_CHAT_ID={valores['chat_id']}",
        f"NOTITA_PERSONAS={valores['personas']}",
        "",
        f"GEMINI_API_KEY={valores['gemini_key']}",
        f"GEMINI_MODEL={valores['gemini_model']}",
        "",
        "# Vacío = la ruta /cron/recordatorios queda apagada.",
        "CRON_SECRET=",
    ]
    if valores.get("contexto"):
        lineas += ["", f"NOTITA_CONTEXTO={valores['contexto']}"]
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    ENV.chmod(0o600)  # tiene secretos: que no lo lea nadie más
    ok(f"Escrito en {ENV}")


def paso_webhook(token: str, url: str, secret: str, chat_id: int) -> None:
    titulo(7, "Enchufar el webhook")
    if not url:
        aviso("Sin URL no hay webhook. Cuando tengas la web app andando, corré:")
        dato("python3 set_webhook.py https://TU_USUARIO.pythonanywhere.com/telegram")
        return
    bien, res = tg(token, "setWebhook", url=url, secret_token=secret,
                   allowed_updates=["message", "edited_message", "callback_query"],
                   drop_pending_updates=True)
    if not bien:
        mal(f"Telegram rechazó el webhook: {res}")
        return
    ok(f"Webhook apuntando a {url}")

    bien, info = tg(token, "getWebhookInfo")
    if bien and info.get("last_error_message"):
        aviso(f"Telegram reporta un error previo: {info['last_error_message']}")

    if confirmar("¿Le mando un mensaje de prueba al grupo?"):
        texto = ("Hola, soy Notita 🧲\nYa estoy funcionando. Escribime /ayuda para ver "
                 "cómo usarme, o contame qué hay que hacer en casa.")
        bien, res = tg(token, "sendMessage", chat_id=chat_id, text=texto)
        if bien:
            ok("Mensaje enviado, mirá el grupo")
        else:
            mal(f"No pude mandarlo: {res}")


# --------------------------------------------------------------------------

def main() -> None:
    print("\n\033[1m🧲 Notita — instalador\033[0m")
    print("  Se puede cortar con Ctrl+C. No toca la base de datos.")

    token, usuario_bot = paso_token()
    chat_id, personas = paso_grupo_y_personas(token, usuario_bot)
    gemini_key, gemini_model = paso_gemini(personas)
    url = paso_url()
    contexto = paso_contexto()

    secret = secrets.token_urlsafe(32)
    escribir_env({
        "token": token,
        "secret": secret,
        "chat_id": str(chat_id),
        "personas": ",".join(f"{p.nombre}:{p.user_id}" for p in personas),
        "gemini_key": gemini_key,
        "gemini_model": gemini_model,
        "contexto": contexto,
    })
    paso_webhook(token, url, secret, chat_id)

    print("\n\033[1m¡Listo!\033[0m Lo que falta:")
    print("  1. Si estás en PythonAnywhere: pestaña Web → botón \033[1mReload\033[0m")
    print("     (la web app lee el .env recién al arrancar).")
    print("  2. Programá la rutina de las 20:00: Tasks → Daily task → 23:00 UTC")
    print(f"     python3.13 {config.BASE_DIR}/run_reminders.py")
    print("  3. Chequeá que todo esté en orden:  python3 doctor.py")
    print("\n  Probalo escribiendo en el grupo: «hay que limpiar la heladera el lunes»\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cortado. No se guardó nada nuevo.\n")
        sys.exit(1)
