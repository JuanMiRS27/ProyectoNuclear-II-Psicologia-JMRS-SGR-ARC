"""Crea una cuenta de ADMINISTRADOR.

Uso interactivo (recomendado; la contraseña se solicita sin mostrarse):

    python -m app.scripts.create_admin --nombre "Nombre Apellido" --correo admin@universidad.edu

Uso no interactivo (despliegues): la contraseña se lee de la variable de entorno
INITIAL_ADMIN_PASSWORD. Nunca se acepta como argumento de línea de comandos
para que no quede en el historial del shell.
"""

import argparse
import getpass
import os
import sys

from pydantic import BaseModel, ValidationError

from app.core.database import SessionLocal
from app.core.exceptions import AppError
from app.modules.auth.schemas.usuario import Clave, CodigoInstitucional, Correo, NombreCompleto
from app.modules.auth.services import UsuarioService

PASSWORD_ENV_VAR = "INITIAL_ADMIN_PASSWORD"


class AdminInput(BaseModel):
    nombre_completo: NombreCompleto
    correo: Correo
    clave: Clave
    codigo_institucional: CodigoInstitucional | None = None


def _read_password() -> str:
    from_env = os.environ.get(PASSWORD_ENV_VAR)
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        sys.exit(f"Error: defina {PASSWORD_ENV_VAR} o ejecute el script en una terminal interactiva.")
    clave = getpass.getpass("Contraseña del administrador: ")
    if clave != getpass.getpass("Confirme la contraseña: "):
        sys.exit("Error: las contraseñas no coinciden.")
    return clave


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crea una cuenta de administrador.")
    parser.add_argument("--nombre", required=True, help="Nombre completo")
    parser.add_argument("--correo", required=True, help="Correo electrónico")
    parser.add_argument("--codigo", default=None, help="Código institucional (opcional)")
    args = parser.parse_args(argv)

    try:
        data = AdminInput(
            nombre_completo=args.nombre, correo=args.correo, clave=_read_password(), codigo_institucional=args.codigo
        )
    except ValidationError as exc:
        for error in exc.errors():
            print(f"Error en '{'.'.join(map(str, error['loc']))}': {error['msg']}", file=sys.stderr)
        return 1

    with SessionLocal() as session:
        try:
            admin = UsuarioService(session).create_admin(**data.model_dump())
        except AppError as exc:
            print(f"Error: {exc.detail}", file=sys.stderr)
            return 1

    print(f"Administrador creado: {admin.correo} (id {admin.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
