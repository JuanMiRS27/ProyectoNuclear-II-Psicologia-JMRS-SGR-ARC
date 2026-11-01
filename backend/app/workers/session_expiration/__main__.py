"""Cierre automático de sesiones vencidas.

Una pasada (para cron, el Programador de tareas de Windows o un CronJob):

    python -m app.workers.session_expiration

Bucle propio cada N segundos (si no hay planificador externo):

    python -m app.workers.session_expiration --intervalo 300

Es seguro ejecutar varias instancias a la vez (FOR UPDATE SKIP LOCKED).
"""

import argparse
import logging

from app.workers.session_expiration.runner import ejecutar_periodicamente, ejecutar_una_vez


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finaliza las sesiones vencidas (SESSION_AUTO_CLOSE_HOURS tras su fin).")
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
