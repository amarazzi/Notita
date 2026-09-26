#!/usr/bin/env python3
"""Instalador guiado de Notita. Te deja el .env escrito y el webhook andando.

    python3 install.py
    python3 install.py --env /tmp/prueba.env   # ensayo: escribe en otro archivo

No hace falta saber nada de código: te va preguntando y validando todo contra
Telegram y Google a medida que avanza. Se puede cortar con Ctrl+C y volver a
empezar cuando quieras; no toca la base de datos.

Si ya había un .env, sus valores se ofrecen como respuesta por defecto y el
original queda copiado en .env.bak.
"""
from __future__ import annotations

import argparse
import logging
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from notita import deps

deps.exigir("requests")  # antes de importarlas, para poder avisar bien

import requests  # noqa: E402

from notita import config, llm, pythonanywhere  # noqa: E402
from notita.config import Persona, slugificar  # noqa: E402

TG = "https://api.telegram.org/bot{token}/{metodo}"
ENV = config.BASE_DIR / ".env"
# Lo que ya estaba configurado, para ofrecerlo como default y no perder nada.
PREVIO: dict[str, str] = {}
# Si desconectamos un webhook que ya andaba, lo recordamos para poder dejarlo
# como estaba (incluso si cortan el wizard a mitad de camino).
_A_RESTAURAR: dict[str, str] = {}
# En modo demo nada sale a internet (ver notita/demo.py).
DEMO = False
# Cuánto escuchar el grupo esperando que escriban, y cuántos segundos de silencio
# alcanzan para darlo por terminado.
ESPERA = 90
QUIETO = 6

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


SI = ("s", "si", "sí", "sip", "dale", "ok", "y", "yes")
NO = ("n", "no", "nop", "nah")


def confirmar(texto: str, default: bool = True) -> bool:
    """Sí o no. Si la respuesta no se entiende, vuelve a preguntar.

    Importante que NO asuma nada: antes cualquier tecla que no empezara con «s»
    contaba como no, y algunas de estas preguntas cortan el instalador.
    """
    d = "S/n" if default else "s/N"
    while True:
        r = preguntar(f"{texto} ({d})").strip().lower()
        if not r:
            return default
        if r in SI:
            return True
        if r in NO:
            return False
        mal("No te entendí. Contestá «s» o «n».")


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


def leer_env(ruta: Path) -> dict[str, str]:
    """Lee un .env a mano (sin dotenv) para no pisar lo que ya estaba configurado."""
    valores: dict[str, str] = {}
    if not ruta.exists():
        return valores
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valores[clave.strip()] = valor.strip()
    return valores


def activar_demo() -> None:
    """Reemplaza Telegram y Gemini por simuladores. No se toca la red."""
    global tg, DEMO
    from notita import demo, llm as _llm

    DEMO = True
    tg = demo.TelegramDeMentira()
    requests.get = lambda *a, **k: demo.RespuestaHTTP()  # el chequeo de la web app
    _llm.probar_conexion, _llm.interpretar_mensaje = demo.gemini_de_mentira()

    print("\n\033[33m\033[1m  MODO DEMO\033[0m — Telegram y Gemini son de mentira.")
    print("  No sale nada a internet y no se toca ninguna configuración real.")
    print(f"  Podés pegar este token falso: \033[1m{demo.TOKEN}\033[0m")
    print("  O escribir cualquier cosa: te va a ir avisando qué está mal.")


def paso_token() -> tuple[str, str]:
    titulo(1, "El token del bot")
    dato("Si todavía no lo tenés: abrí Telegram, hablale a @BotFather,")
    dato("mandale /newbot, seguí los pasos y copiá el token que te da.")
    while True:
        token = preguntar("Token de BotFather", PREVIO.get("TELEGRAM_TOKEN", ""))
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


def esperar_mensajes(token: str, espera: float | None = None, quieto: float | None = None
                     ) -> tuple[dict[int, str], dict[int, str]]:
    """Escucha el grupo hasta `espera` segundos y avisa a medida que ve gente.

    Corta cuando pasaron `quieto` segundos sin ver a nadie nuevo, así no hay que
    adivinar cuándo terminaron de escribir todos.
    """
    espera = ESPERA if espera is None else espera
    quieto = QUIETO if quieto is None else quieto
    grupos: dict[int, str] = {}
    gente: dict[int, str] = {}
    offset = None
    arranque = time.time()
    ultimo = None

    print("  \033[36m…\033[0m Escuchando el grupo. Escriban algo ahora (Ctrl+C para cortar).")
    while time.time() - arranque < espera:
        bien, updates = tg(token, "getUpdates", timeout=0, offset=offset,
                           allowed_updates=["message"])
        if not bien:
            mal(f"No pude leer los mensajes: {updates}")
            break
        for u in updates or []:
            offset = u.get("update_id", 0) + 1
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            autor_nombre = (msg.get("from") or {}).get("first_name") or "vos"
            if chat.get("type") == "private":
                # Notita también sirve para una sola persona, por privado.
                grupos[chat["id"]] = f"privado con {autor_nombre}"
            elif chat.get("type") in ("group", "supergroup"):
                grupos[chat["id"]] = chat.get("title", "sin título")
            else:
                continue
            autor = msg.get("from") or {}
            uid = autor.get("id")
            if uid and not autor.get("is_bot") and uid not in gente:
                gente[uid] = autor.get("first_name") or f"user{uid}"
                ok(f"Te escuché, {gente[uid]} ({uid})")
                ultimo = time.time()
        if ultimo and time.time() - ultimo >= quieto:
            break
        if DEMO:  # en demo no hay nada que esperar
            break
        time.sleep(2)

    # Confirmamos lo leído. Si no, Telegram guarda esos mensajes y se los entrega
    # al webhook cuando lo reconectamos: Notita intentaría anotar un «hola».
    if offset is not None:
        tg(token, "getUpdates", timeout=0, offset=offset)
    return grupos, gente


def paso_grupo_y_personas(token: str, usuario_bot: str) -> tuple[int, tuple[Persona, ...], str]:
    """Devuelve (chat_id, personas, webhook que había antes de empezar)."""
    titulo(2, "Dónde vive Notita y quién la usa")
    dato(f"Si son varios en la casa: creá un grupo, agregá a @{usuario_bot}, y que")
    dato("CADA persona escriba un mensaje cualquiera ahí.")
    dato(f"Si lo vas a usar solo: escribile por privado a @{usuario_bot}, alcanza con eso.")
    dato("(Así detecto el chat y el user_id de cada uno, sin que nadie busque nada.)")

    # getUpdates no anda si hay un webhook puesto, así que lo desconectamos un rato.
    # Guardamos cuál era para poder dejar todo como estaba si el wizard no lo reemplaza.
    bien, info = tg(token, "getWebhookInfo")
    webhook_previo = (info or {}).get("url", "") if bien else ""
    if webhook_previo:
        aviso("Ya había un webhook puesto: lo desconecto un minuto para poder leer el grupo.")
        dato(f"Era {webhook_previo}. Al final lo vuelvo a enchufar.")
        _A_RESTAURAR.update(token=token, url=webhook_previo)
    tg(token, "deleteWebhook")

    # Se acumulan entre vueltas: si en la segunda nadie escribe, no se pierde
    # el grupo que ya habíamos encontrado.
    chat_id, gente, grupos = 0, {}, {}
    while True:
        grupos_nuevos, gente_nueva = esperar_mensajes(token)
        grupos.update(grupos_nuevos)
        gente.update(gente_nueva)

        if not grupos:
            mal("No vi ningún mensaje.")
            dato(f"Si es un grupo: chequeá que @{usuario_bot} esté agregado ahí.")
            dato("Los mensajes tienen que ser NUEVOS: escribí algo mientras yo espero.")
            dato("Si el privacy mode quedó encendido, probá escribiendo /todo en el grupo.")
            if confirmar("¿Espero de nuevo?"):
                continue
            raise SystemExit("  Cortamos acá. Volvé cuando el bot esté en el chat.")

        if len(grupos) == 1:
            chat_id, nombre = next(iter(grupos.items()))
            ok(f"Chat detectado: «{nombre}» (chat_id {chat_id})")
        else:
            print("  Encontré varios chats:")
            opciones = list(grupos.items())
            for i, (cid, nombre) in enumerate(opciones, 1):
                dato(f"{i}) {nombre} ({cid})")
            elegido = preguntar("¿En cuál querés usar Notita? (número)", "1")
            try:
                chat_id, nombre = opciones[int(elegido) - 1]
            except (ValueError, IndexError):
                mal("Número inválido")
                continue
            ok(f"Va «{nombre}» ({chat_id})")

        if not gente:
            aviso("No pude sacar quién es quién: no vi mensajes de personas.")
        if confirmar("¿Está completa la lista?"):
            break
        aviso("Que escriba en el grupo quien falte, que sigo escuchando.")

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
    return chat_id, tuple(personas), webhook_previo


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
    model = PREVIO.get("GEMINI_MODEL") or llm.MODELO_RECOMENDADO
    if llm.cuota_chica(model):
        # No lo ofrecemos como default: en la capa gratuita da 20 mensajes por día.
        aviso(f"Tenías {model}, que gratis da muy pocos mensajes por día.")
        dato(f"Te propongo {llm.MODELO_RECOMENDADO}, que tiene mucha más cuota.")
        model = llm.MODELO_RECOMENDADO
    anterior = PREVIO.get("GEMINI_API_KEY", "")
    pregunta = ("API key de Google AI Studio (Enter = la que ya tenías)" if anterior
                else "API key de Google AI Studio (Enter = modo local)")
    while True:
        key = preguntar(pregunta, anterior)
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

def paso_url(webhook_previo: str = "", sitio=None) -> str:
    titulo(4, "La dirección pública")
    dato("Es la dirección donde Telegram le va a avisar de cada mensaje nuevo.")
    if sitio:
        ok(f"Estás en PythonAnywhere, así que ya la sé: {sitio.url}")
        sugerida = webhook_previo or sitio.url
    else:
        dato("Es la URL de tu web app, por ejemplo https://tuusuario.pythonanywhere.com")
        dato("Si todavía no la creaste, dejalo vacío y lo configurás después con set_webhook.py.")
        sugerida = webhook_previo
    url = preguntar("URL pública (Enter para saltear)", sugerida)
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
    return preguntar("Contexto (Enter para saltear)", PREVIO.get("NOTITA_CONTEXTO", ""))


def escribir_env(valores: dict[str, str]) -> None:
    titulo(6, "Guardar la configuración")
    if ENV.exists():
        backup = ENV.with_name(ENV.name + ".bak")
        shutil.copy(ENV, backup)
        aviso(f"Ya existía un {ENV.name}: lo guardé como {backup.name}")
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
        f"CRON_SECRET={PREVIO.get('CRON_SECRET', '')}",
    ]
    if valores.get("contexto"):
        lineas += ["", f"NOTITA_CONTEXTO={valores['contexto']}"]
    if PREVIO.get("NOTITA_DB"):  # no perder una ruta de base personalizada
        lineas += ["", f"NOTITA_DB={PREVIO['NOTITA_DB']}"]
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    ENV.chmod(0o600)  # tiene secretos: que no lo lea nadie más
    ok(f"Escrito en {ENV}")


def paso_web_app(sitio) -> bool:
    """Deja la web app apuntando a Notita y la recarga. Devuelve si quedó lista."""
    titulo(7, "La web app")
    if sitio is None:
        dato("No estás en PythonAnywhere, así que esto lo configurás vos:")
        dato("tu servidor tiene que servir `app.py` (mirá el README, sección Plan B).")
        return False

    if not sitio.existe:
        aviso("Todavía no creaste la web app, así que no puedo configurarla.")
        dato("Andá a la pestaña Web → Add a new web app → Manual configuration → Python 3.13,")
        dato("y después volvé a correr este instalador: el resto ya va a estar hecho.")
        return False

    proyecto = config.BASE_DIR
    if pythonanywhere.ya_configurado(sitio, proyecto):
        ok(f"{sitio.wsgi.name} ya apuntaba a Notita")
    else:
        dato(f"Voy a hacer que {sitio.wsgi.name} cargue Notita.")
        if not confirmar("¿Lo escribo?"):
            aviso("Lo dejo como está. Vas a tener que editarlo a mano (mirá el README).")
            return False
        try:
            backup = pythonanywhere.escribir_wsgi(sitio, proyecto)
        except OSError as e:
            mal(f"No pude escribirlo: {e}")
            dato("Editalo a mano desde la pestaña Web, o corré el instalador de nuevo.")
            return False
        ok(f"Escrito {sitio.wsgi}")
        if backup:
            dato(f"Lo que había quedó en {backup.name}")

    if pythonanywhere.recargar(sitio):
        ok("Web app recargada (así toma la configuración nueva)")
        return True
    aviso("No pude recargarla: hacelo con el botón Reload de la pestaña Web.")
    return False


def paso_webhook(token: str, url: str, secret: str, chat_id: int,
                 webhook_previo: str = "") -> None:
    titulo(8, "Enchufar el webhook")
    if not url:
        if webhook_previo:
            # Para leer el grupo hubo que desconectarlo: lo dejamos como estaba.
            restaurar_webhook()
            aviso("Quedó con el secreto viejo, no con el que acabo de generar.")
            dato(f"Si querés usar el nuevo: python3 set_webhook.py {webhook_previo}")
            return
        aviso("Sin URL no hay webhook. Cuando tengas la web app andando, corré:")
        dato("python3 set_webhook.py https://TU_USUARIO.pythonanywhere.com/telegram")
        return
    bien, res = tg(token, "setWebhook", url=url, secret_token=secret,
                   allowed_updates=["message", "edited_message", "callback_query"],
                   drop_pending_updates=True)
    if not bien:
        mal(f"Telegram rechazó el webhook: {res}")
        return
    _A_RESTAURAR.clear()  # ya quedó uno nuevo, no hay nada que restaurar
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
    global ENV, PREVIO

    p = argparse.ArgumentParser(description="Instalador guiado de Notita")
    p.add_argument("--env", help="escribir en otro archivo (para ensayar sin tocar el .env real)")
    p.add_argument("--demo", action="store_true",
                   help="recorrer el instalador con Telegram y Gemini simulados, sin credenciales")
    args = p.parse_args()

    print("\n\033[1m🧲 Notita — instalador\033[0m")
    print("  Se puede cortar con Ctrl+C. No toca la base de datos.")

    if args.demo:
        # En demo nunca se escribe el .env real, ni aunque no pasen --env.
        ENV = Path(args.env).expanduser().resolve() if args.env else \
            Path(tempfile.gettempdir()) / "notita-demo.env"
        activar_demo()
    elif args.env:
        ENV = Path(args.env).expanduser().resolve()
    PREVIO = leer_env(ENV)

    if PREVIO:
        print(f"  Ya hay configuración en {ENV.name}: la uso como respuesta por defecto.")
    if args.env or args.demo:
        print(f"  \033[33mModo ensayo:\033[0m voy a escribir en {ENV}")

    sitio = None if DEMO else pythonanywhere.detectar()
    # Pase lo que pase de acá en adelante (Ctrl+C, un paso que corta con SystemExit,
    # un error inesperado), el webhook que ya andaba tiene que quedar como estaba.
    # Si no, el bot se queda mudo y nadie entiende por qué.
    try:
        _pasos(sitio)
    finally:
        restaurar_webhook()


def _pasos(sitio) -> None:
    token, usuario_bot = paso_token()
    chat_id, personas, webhook_previo = paso_grupo_y_personas(token, usuario_bot)
    gemini_key, gemini_model = paso_gemini(personas)
    url = paso_url(webhook_previo, sitio)
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
    lista = paso_web_app(sitio)
    paso_webhook(token, url, secret, chat_id, webhook_previo)

    if DEMO:
        print("\n\033[33m\033[1m  Fin de la demo.\033[0m Nada de esto era real.")
        print(f"  Mirá lo que habría escrito: cat {ENV}")
        print("  Cuando tengas el bot de verdad: python3 install.py\n")
        return

    piton = f"python{sys.version_info.major}.{sys.version_info.minor}"
    print("\n\033[1m¡Listo!\033[0m", end=" ")
    if lista:
        print("Queda una sola cosa, y es en la web de PythonAnywhere:")
    else:
        print("Lo que falta:")
        if sitio:
            print("  · Pestaña \033[1mWeb\033[0m → botón \033[1mReload\033[0m")
    print("\n  \033[1mLos recordatorios de las 20:00\033[0m")
    print("  Pestaña \033[1mTasks\033[0m → Daily task → hora \033[1m23:00\033[0m (es UTC, "
          "equivale a las 20:00 acá)")
    print(f"  Comando: \033[1m{piton} {config.BASE_DIR}/run_reminders.py\033[0m")
    print(f"\n  Para chequear que todo esté en orden: {piton} doctor.py")
    print("  Y probalo ya: escribí en el grupo «hay que limpiar la heladera el lunes» 🤍\n")


def restaurar_webhook() -> None:
    """Deja el webhook como estaba si el wizard no llegó a poner uno nuevo."""
    if not _A_RESTAURAR:
        return
    bien, _ = tg(_A_RESTAURAR["token"], "setWebhook", url=_A_RESTAURAR["url"],
                 secret_token=PREVIO.get("TELEGRAM_WEBHOOK_SECRET") or None,
                 allowed_updates=["message", "edited_message", "callback_query"],
                 # Que no le lleguen los mensajes que usamos para detectar el grupo.
                 drop_pending_updates=True)
    if bien:
        ok(f"Dejé el webhook como estaba: {_A_RESTAURAR['url']}")
    else:
        mal("No pude restaurar el webhook. Corré: python3 set_webhook.py "
            + _A_RESTAURAR["url"])
    _A_RESTAURAR.clear()


if __name__ == "__main__":
    try:
        main()   # el webhook se restaura solo, en el finally de main()
    except KeyboardInterrupt:
        print("\n\n  Cortado. No se guardó nada nuevo.\n")
        sys.exit(1)
