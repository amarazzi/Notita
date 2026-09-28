#!/usr/bin/env python3
"""Mudar Notita a un chat_id nuevo, cuando Telegram convierte el grupo.

Pasa cuando el grupo se vuelve supergrupo (por ejemplo, al hacer admin a alguien):
Telegram le cambia el número para siempre. Desde v2 Notita se muda sola al recibir
el aviso, pero si el aviso se perdió —o si ya cambiaste el .env a mano— este script
termina el trabajo.

    python3 migrar_chat.py -1003993284076

El número viejo lo saca del .env; se puede forzar con --viejo.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notita import config, db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Mudar Notita a otro chat_id")
    p.add_argument("nuevo", type=int, help="el chat_id nuevo (el del mensaje de Telegram)")
    p.add_argument("--viejo", type=int, default=config.ALLOWED_CHAT_ID,
                   help="el anterior (por defecto, el que está en el .env)")
    p.add_argument("--sin-backup", action="store_true", help="no copiar la base antes")
    args = p.parse_args()

    if not args.viejo:
        sys.exit("No sé cuál era el chat_id viejo: pasalo con --viejo")
    if args.viejo == args.nuevo:
        sys.exit("El viejo y el nuevo son el mismo número: ¿ya lo cambiaste en el .env?")
    print(f"\n  Mudando Notita: {args.viejo} → {args.nuevo}")

    base = Path(config.DB_PATH)
    if not base.exists():
        sys.exit(f"No encuentro la base en {base}")

    if not args.sin_backup:
        respaldo = base.with_name(base.name + ".antes-de-mudarse.bak")
        shutil.copy2(base, respaldo)
        print(f"  Respaldo en {respaldo}")

    db.init_db()
    with db.conn() as c:
        antes = c.execute("SELECT COUNT(*) n FROM tasks WHERE chat_id = ? "
                          "AND estado = 'pendiente'", (args.viejo,)).fetchone()["n"]
    print(f"  Pendientes en el chat viejo: {antes}")

    movidas = db.migrar_chat(args.viejo, args.nuevo)
    if not movidas:
        print("  No había nada con ese chat_id. ¿Está bien el número viejo?")
    for tabla, cuantas in movidas.items():
        print(f"  {tabla}: {cuantas} fila(s)")

    if config.guardar_chat_id(args.nuevo):
        print(f"  .env actualizado: ALLOWED_CHAT_ID={args.nuevo}")
    else:
        print(f"  ⚠️  Poné a mano ALLOWED_CHAT_ID={args.nuevo} en el .env")

    print("\n  Ahora recargá la web app (pestaña Web → Reload) y escribí «tablero».")


if __name__ == "__main__":
    main()
