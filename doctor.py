#!/usr/bin/env python3
"""Revisa que Notita esté bien configurada y te dice qué falta.

    python3 doctor.py              # diagnóstico completo
    python3 doctor.py --mensaje    # además manda un mensaje de prueba al grupo

Devuelve código de salida 1 si encontró algo roto, así se puede usar en scripts.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone

from notita import deps

deps.exigir("requests")  # antes de importarlas, para poder avisar bien

import requests  # noqa: E402

from notita import config, db, llm  # noqa: E402

TG = "https://api.telegram.org/bot{token}/{metodo}"

# Los módulos logean los errores crudos; acá los traducimos nosotros.
logging.getLogger("notita").setLevel(logging.CRITICAL)

problemas: list[str] = []
avisos: list[str] = []


def titulo(texto: str) -> None:
    print(f"\n\033[1m{texto}\033[0m")


def ok(texto: str) -> None:
    print(f"  \033[32m✓\033[0m {texto}")


def mal(texto: str, arreglo: str = "") -> None:
    print(f"  \033[31m✗\033[0m {texto}")
    if arreglo:
        print(f"      \033[2m→ {arreglo}\033[0m")
    problemas.append(texto)


def aviso(texto: str, arreglo: str = "") -> None:
    print(f"  \033[33m!\033[0m {texto}")
    if arreglo:
        print(f"      \033[2m→ {arreglo}\033[0m")
    avisos.append(texto)


def tg(metodo: str, **payload) -> tuple[bool, dict | str]:
    if not config.TELEGRAM_TOKEN:
        return False, "falta TELEGRAM_TOKEN"
    try:
        r = requests.post(TG.format(token=config.TELEGRAM_TOKEN, metodo=metodo),
                          json=payload, timeout=20)
        data = r.json()
    except requests.exceptions.RequestException as e:
        return False, f"no se pudo conectar con api.telegram.org ({type(e).__name__})"
    except ValueError:
        return False, "Telegram contestó algo que no es JSON"
    if not data.get("ok"):
        return False, data.get("description", "error desconocido")
    return True, data.get("result")


# --------------------------------------------------------------------------

def revisar_entorno() -> None:
    titulo("Entorno")
    v = sys.version_info
    if v < (3, 10):
        mal(f"Python {v.major}.{v.minor}: el código usa sintaxis de 3.10+",
            "usá python3.13 (o al menos 3.10)")
    else:
        ok(f"Python {v.major}.{v.minor}.{v.micro}")

    for modulo, para_que in (("flask", "el webhook"), ("requests", "las llamadas HTTP"),
                             ("dotenv", "leer el .env")):
        try:
            __import__(modulo)
            ok(f"{modulo} instalado")
        except ImportError:
            mal(f"falta {modulo} (lo necesita {para_que})",
                "pip3.13 install --user -r requirements.txt")

    if not (config.BASE_DIR / ".env").exists():
        aviso("no hay archivo .env",
              "corré python3 install.py, o copiá .env.example y completalo")
    else:
        ok(".env presente")

    ahora = datetime.now(config.TZ)
    offset = ahora.utcoffset().total_seconds() / 3600
    ok(f"Hora en {config.TZ_NOMBRE}: {ahora:%H:%M} "
       f"(UTC{offset:+.0f}, son {datetime.now(timezone.utc):%H:%M} UTC)")
    ok(f"La rutina va a las {config.HORA_RUTINA} = {config.hora_rutina_en_utc()} UTC")


def revisar_bot() -> dict | None:
    titulo("El bot en Telegram")
    if not config.TELEGRAM_TOKEN:
        mal("falta TELEGRAM_TOKEN", "corré python3 install.py")
        return None
    bien, yo = tg("getMe")
    if not bien:
        # No es lo mismo "el token está mal" que "no se pudo salir a internet": el
        # proxy de las cuentas gratuitas de PythonAnywhere falla cada tanto, y decir
        # "revisá el token" manda a buscar el problema donde no está.
        if "conectar" in str(yo) or "Proxy" in str(yo) or "timeout" in str(yo).lower():
            aviso(f"no pude hablar con Telegram desde acá: {yo}",
                  "suele ser el proxy de PythonAnywhere, que falla cada tanto. "
                  "Volvé a correr el doctor; si el bot contesta en el grupo, está todo bien")
        else:
            mal(f"el token no sirve: {yo}", "revisá TELEGRAM_TOKEN en el .env")
        return None
    ok(f"@{yo['username']} responde")
    if yo.get("can_read_all_group_messages"):
        ok("privacy mode apagado (lee los mensajes del grupo)")
    else:
        mal("privacy mode ENCENDIDO: sólo va a leer los comandos",
            "@BotFather → /mybots → Bot Settings → Group Privacy → Turn off, "
            "y después sacá y volvé a agregar el bot al grupo")
    return yo


def revisar_webhook() -> None:
    titulo("Webhook")
    bien, info = tg("getWebhookInfo")
    if not bien:
        mal(f"no pude consultarlo: {info}")
        return
    url = info.get("url") or ""
    if not url:
        mal("no hay webhook configurado: el bot no recibe nada",
            "python3 set_webhook.py https://TU_USUARIO.pythonanywhere.com/telegram")
        return
    ok(f"apuntando a {url}")
    if not url.endswith("/telegram"):
        aviso("la URL no termina en /telegram, puede estar mal armada")
    if info.get("has_custom_certificate"):
        aviso("usa certificado propio")

    if config.TELEGRAM_WEBHOOK_SECRET:
        ok("hay TELEGRAM_WEBHOOK_SECRET configurado")
    else:
        aviso("sin TELEGRAM_WEBHOOK_SECRET: cualquiera que sepa la URL puede postear",
              "poné uno en el .env y volvé a correr set_webhook.py")

    pendientes = info.get("pending_update_count") or 0
    if pendientes > 10:
        mal(f"{pendientes} mensajes encolados sin procesar: la web app no está respondiendo",
            "mirá el Error log de la pestaña Web y hacé Reload")
    elif pendientes:
        aviso(f"{pendientes} mensajes en cola")
    else:
        ok("sin mensajes encolados")

    if info.get("last_error_message"):
        cuando = info.get("last_error_date")
        cuando = f" ({datetime.fromtimestamp(cuando, config.TZ):%d/%m %H:%M})" if cuando else ""
        aviso(f"último error de Telegram{cuando}: {info['last_error_message']}",
              "si es viejo, ignoralo; si es de ahora, revisá el Error log")


def revisar_grupo(yo: dict | None) -> None:
    titulo("El chat")
    if not config.ALLOWED_CHAT_ID:
        mal("falta ALLOWED_CHAT_ID: el bot ignora todos los chats",
            "escribí /chatid en el chat y poné ese número en el .env")
        return
    bien, chat = tg("getChat", chat_id=config.ALLOWED_CHAT_ID)
    if not bien:
        mal(f"no puedo ver el chat {config.ALLOWED_CHAT_ID}: {chat}",
            "¿está bien el ALLOWED_CHAT_ID? ¿el bot sigue en el chat?")
        return
    if chat.get("type") == "private":
        ok(f"chat privado con {chat.get('first_name', 'vos')} (uso individual)")
        return  # en un privado no hay membresía que revisar
    ok(f"«{chat.get('title', 'sin título')}» ({chat.get('type')})")

    if yo:
        bien, miembro = tg("getChatMember", chat_id=config.ALLOWED_CHAT_ID, user_id=yo["id"])
        if bien:
            estado = (miembro.get("status") or "").lower()
            if estado in ("member", "administrator", "creator"):
                ok(f"el bot está en el grupo ({estado})")
            else:
                mal(f"el bot figura como «{estado}» en el grupo", "volvé a agregarlo")


def revisar_personas() -> None:
    titulo("Quiénes viven en la casa")
    if not config.PERSONAS_CASA:
        mal("no hay personas configuradas: todo va a quedar sin responsable",
            "poné NOTITA_PERSONAS=Nombre:user_id,Otro:user_id en el .env")
        return
    for p in config.PERSONAS_CASA:
        if p.user_id:
            ok(f"{p.nombre} (slug «{p.slug}», id {p.user_id})")
        else:
            aviso(f"{p.nombre} no tiene user_id: no se lo puede mencionar en los recordatorios",
                  "que le escriba a @userinfobot y agregá el número al .env")
    if config.CONTEXTO_CASA:
        ok(f"contexto: «{config.CONTEXTO_CASA[:60]}»")


def revisar_gemini() -> None:
    titulo("Gemini")
    if not config.GEMINI_API_KEY:
        ok("sin GEMINI_API_KEY: Notita anda en modo local, no le manda nada a Google")
        print("      \033[2m→ en modo local no separa varias tareas de una frase sin comas,\033[0m")
        print("      \033[2m  ni elige categoría/responsable tan bien. Las fechas sí funcionan.\033[0m")
        print("      \033[2m→ si querés el modo completo: https://aistudio.google.com/apikey\033[0m")
        return
    bien, detalle = llm.probar_conexion()
    if not bien:
        mal(f"Gemini no responde: {detalle}",
            "revisá GEMINI_API_KEY y GEMINI_MODEL en el .env")
        return
    ok(f"{config.GEMINI_MODEL} responde")
    if llm.cuota_chica(config.GEMINI_MODEL):
        aviso(f"{config.GEMINI_MODEL} da muy pocos mensajes por día en la capa gratuita",
              f"cambiá GEMINI_MODEL a {llm.MODELO_RECOMENDADO} en el .env y recargá")

    data = llm.interpretar_mensaje("comprar una cómoda y limpiar el baño el lunes", "ninguno")
    if data is None:
        # Puede ser algo transitorio de Google: preguntamos de nuevo para saber qué decir.
        _, detalle = llm.probar_conexion()
        if "sobrecargado" in detalle or "cuota" in detalle:
            aviso(f"la interpretación falló: {detalle}",
                  "el bot reintenta 3 veces solo; si pasa seguido, probá gemini-2.5-flash-lite")
        else:
            mal("la key anda pero la interpretación falló",
                "mirá el Error log de la web app")
        return
    items = data.get("items") or []
    if len(items) >= 2:
        ok(f"interpretó {len(items)} tareas de un mensaje con dos")
    else:
        aviso(f"interpretó {len(items)} tarea(s) donde había 2; puede fallar con mensajes largos")

    textos = " ".join(i.get("texto", "") for i in items)
    if "cómoda" in textos or "ó" in textos:
        ok("las tildes vienen bien")
    elif "comoda" in textos.lower().replace("ó", "o"):
        aviso("devolvió el texto sin tildes (no es grave)")
    else:
        aviso(f"el texto vino raro, revisá si se rompió la codificación: «{textos[:60]}»")


def revisar_base() -> None:
    titulo("Base de datos")
    try:
        db.init_db()
        with db.conn() as c:
            filas = c.execute(
                "SELECT estado, tipo, COUNT(*) n FROM tasks GROUP BY estado, tipo"
            ).fetchall()
    except sqlite3.Error as e:
        mal(f"no se pudo abrir {config.DB_PATH}: {e}",
            "revisá permisos de escritura en esa carpeta")
        return
    ok(f"{config.DB_PATH} escribible")
    resumen = {f"{f['estado']}/{f['tipo']}": f["n"] for f in filas}
    if resumen:
        ok("tareas: " + ", ".join(f"{k} {v}" for k, v in sorted(resumen.items())))
    else:
        ok("base vacía, recién empezando")

    if config.ALLOWED_CHAT_ID:
        from notita.dates import hoy

        hoy_ = hoy()
        vencen = db.vencen_hasta(config.ALLOWED_CHAT_ID, hoy_)
        if vencen:
            ok(f"{len(vencen)} tarea(s) se van a recordar hoy a las 20:00")


def revisar_recordatorios() -> None:
    titulo(f"El parte de las {config.HORA_RUTINA}")
    _revisar_si_la_rutina_corre()
    if config.CRON_SECRET:
        ok("CRON_SECRET configurado: /cron/recordatorios está activa (opción B)")
        print("      \033[2m→ probala con: curl -H \"X-Cron-Secret: ...\" "
              "https://TU_USUARIO.pythonanywhere.com/cron/recordatorios\033[0m")
    else:
        ok("/cron/recordatorios apagada: usás la tarea diaria de PythonAnywhere (opción A)")
        print(f"      \033[2m→ Tasks → Daily task → {config.hora_rutina_en_utc()} UTC → "
              f"python3.13 {config.BASE_DIR}/run_parte.py\033[0m")
    print("      \033[2m→ para probar ahora: python3 run_parte.py --forzar\033[0m")


def _revisar_si_la_rutina_corre() -> None:
    """Que la ruta esté activa no significa que alguien la esté llamando.

    Es el modo de falla más silencioso que tiene Notita: si el cron no existe, no
    sale el parte y nadie se entera. Cada parte enviado queda anotado en la base.
    """
    from notita.dates import ahora, hoy

    try:
        ultimo = db.ultimo_parte()
    except sqlite3.Error:
        return
    if ultimo is None:
        aviso("todavía no salió ningún parte",
              "si ya pasaron las " + config.HORA_RUTINA + ", revisá que el cron exista")
        return
    dias = (hoy() - ultimo).days
    if dias <= 1:
        ok(f"el último parte salió el {ultimo.strftime('%d/%m')}")
    else:
        mal(f"el último parte salió el {ultimo.strftime('%d/%m')} ({dias} días)",
            "revisá que el cronjob siga vivo")
    del ahora


def revisar_tablero() -> None:
    """El tablero fijado es la cara de v2: si no está, hay que saberlo."""
    titulo("El tablero")
    if not config.ALLOWED_CHAT_ID:
        return
    guardado = db.tablero_actual(config.ALLOWED_CHAT_ID)
    if not guardado or not guardado.get("message_id"):
        aviso("todavía no hay tablero publicado",
              "escribí «tablero» en el grupo (o /tablero)")
        return
    bien, chat = tg("getChat", chat_id=config.ALLOWED_CHAT_ID)
    fijado = (chat or {}).get("pinned_message") or {} if bien else {}
    if fijado.get("message_id") == guardado["message_id"]:
        ok(f"el tablero está fijado (mensaje {guardado['message_id']})")
    elif fijado:
        aviso("el mensaje fijado no es el tablero",
              "escribí «tablero» para volver a publicarlo y fijarlo")
    else:
        aviso("no hay ningún mensaje fijado en el grupo",
              "hacé admin al bot con permiso de fijar, y escribí «tablero»")

    bien, yo = tg("getChatMember", chat_id=config.ALLOWED_CHAT_ID,
                  user_id=(_mi_user_id() or 0))
    if bien and isinstance(yo, dict):
        if yo.get("status") == "administrator" and yo.get("can_pin_messages"):
            ok("tiene permiso para fijar mensajes")
        else:
            aviso("no es admin con permiso de fijar",
                  "el tablero funciona igual, pero no queda fijado arriba")


def _mi_user_id() -> int | None:
    bien, yo = tg("getMe")
    return (yo or {}).get("id") if bien else None


def mandar_mensaje_de_prueba() -> None:
    titulo("Mensaje de prueba")
    if not config.ALLOWED_CHAT_ID:
        mal("sin ALLOWED_CHAT_ID no puedo mandar nada")
        return
    bien, res = tg("sendMessage", chat_id=config.ALLOWED_CHAT_ID,
                   text="Prueba de Notita 🧲 Si ves esto, la conexión está bien.")
    if bien:
        ok("mandado, mirá el grupo")
    else:
        mal(f"no pude mandarlo: {res}")


def main() -> None:
    p = argparse.ArgumentParser(description="Diagnóstico de Notita")
    p.add_argument("--mensaje", action="store_true", help="manda un mensaje de prueba al grupo")
    args = p.parse_args()

    print("\n\033[1m🧲 Notita — diagnóstico\033[0m")
    revisar_entorno()
    yo = revisar_bot()
    if yo:
        revisar_webhook()
        revisar_grupo(yo)
    revisar_personas()
    revisar_gemini()
    revisar_base()
    if yo:
        revisar_tablero()
    revisar_recordatorios()
    if args.mensaje:
        mandar_mensaje_de_prueba()

    print()
    if problemas:
        print(f"\033[31m\033[1m{len(problemas)} cosa(s) para arreglar:\033[0m")
        for x in problemas:
            print(f"  · {x}")
    if avisos:
        print(f"\033[33m{len(avisos)} aviso(s), no urgentes:\033[0m")
        for x in avisos:
            print(f"  · {x}")
    if not problemas and not avisos:
        print("\033[32m\033[1mTodo en orden 🤍\033[0m")
    elif not problemas:
        print("\033[32mNada roto 🤍\033[0m")
    print()
    sys.exit(1 if problemas else 0)


if __name__ == "__main__":
    main()
