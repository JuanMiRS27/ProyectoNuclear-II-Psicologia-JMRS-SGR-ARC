"""Crea los usuarios de prueba (administrador, docente y estudiante).

Uso:  docker compose exec backend python -m app.scripts.sembrar_usuarios

Es idempotente: si un correo ya existe, lo deja como está. Las claves se
leen de las variables SEMILLA_* del .env (ver .env.example).
"""

import os

from sqlalchemy import select

from app.core.db import SesionLocal
from app.core.enums import RolUsuario
from app.core.seguridad import hashear_clave
from app.usuarios.models import Usuario


def _usuarios_semilla() -> list[dict]:
    return [
        {
            "nombre_completo": "Administrador de Prueba",
            "correo": os.getenv("SEMILLA_ADMIN_CORREO", "admin@simulador.edu.co"),
            "clave": os.environ["SEMILLA_ADMIN_CLAVE"],
            "rol": RolUsuario.ADMINISTRADOR,
        },
        {
            "nombre_completo": "Docente de Prueba",
            "correo": os.getenv("SEMILLA_DOCENTE_CORREO", "docente@simulador.edu.co"),
            "clave": os.environ["SEMILLA_DOCENTE_CLAVE"],
            "rol": RolUsuario.DOCENTE,
            "puede_crear_casos": True,
        },
        {
            "nombre_completo": "Estudiante de Prueba",
            "correo": os.getenv("SEMILLA_ESTUDIANTE_CORREO", "estudiante@simulador.edu.co"),
            "clave": os.environ["SEMILLA_ESTUDIANTE_CLAVE"],
            "rol": RolUsuario.ESTUDIANTE,
            "codigo_institucional": "EST-0001",
        },
    ]


def sembrar() -> None:
    with SesionLocal() as sesion:
        for datos in _usuarios_semilla():
            correo = datos["correo"].lower()
            if sesion.scalar(select(Usuario).where(Usuario.correo == correo)):
                print(f"Ya existe: {correo}")
                continue
            sesion.add(
                Usuario(
                    nombre_completo=datos["nombre_completo"],
                    correo=correo,
                    clave_hash=hashear_clave(datos["clave"]),
                    rol=datos["rol"],
                    codigo_institucional=datos.get("codigo_institucional"),
                    puede_crear_casos=datos.get("puede_crear_casos", False),
                )
            )
            print(f"Creado: {correo} ({datos['rol'].value})")
        sesion.commit()


if __name__ == "__main__":
    sembrar()
