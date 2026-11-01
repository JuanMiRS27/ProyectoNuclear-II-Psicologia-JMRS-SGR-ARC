"""Expiración automática de participaciones cuyo tiempo individual terminó.

Una pasada (cron / Programador de tareas):

    python -m app.workers.participation_expiration

Bucle propio cada N segundos:

    python -m app.workers.participation_expiration --intervalo 60

Es seguro ejecutar varias instancias a la vez (FOR UPDATE SKIP LOCKED). Complementa la
expiración "perezosa" que ocurre al consultar o modificar una participación.
"""

import argparse
import logging

from app.workers.participation_expiration.runner import ejecutar_periodicamente, ejecutar_una_vez


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expira las participaciones EN_PROGRESO cuyo tiempo terminó.")
    parser.add_argument("--intervalo", type=int, default=0, help="Segundos entre pasadas; 0 = una sola pasada (por defecto).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.intervalo > 0:
        ejecutar_periodicamente(args.intervalo)
    else:
        ejecutar_una_vez()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
