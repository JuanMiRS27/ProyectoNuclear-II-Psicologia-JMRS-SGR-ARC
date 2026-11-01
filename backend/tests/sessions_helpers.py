"""Utilidades compartidas por las pruebas del módulo 3."""

from datetime import timedelta
from typing import Any

from sqlalchemy import text

from app.core.database import engine

SESSIONS_URL = "/api/v1/sessions"
SPACES_URL = "/api/v1/academic-spaces"
STUDENT_LOGIN_URL = "/api/v1/auth/student/login"


def ventana(reloj, inicio_h: float = 1, fin_h: float = 3) -> dict[str, str]:
    """Por defecto, con el reloj en 07:00: inicio 08:00, fin 10:00, límite de ingreso 09:00."""
    return {
        "inicio": (reloj.actual + timedelta(hours=inicio_h)).isoformat(),
        "fin": (reloj.actual + timedelta(hours=fin_h)).isoformat(),
    }


def datos_sesion(reloj, caso_id: str, espacio_id, estudiantes=(), **extra: Any) -> dict[str, Any]:
    return {
        "caso_id": caso_id,
        "espacio_academico_id": str(espacio_id),
        **ventana(reloj),
        "tiempo_max_min": 40,
        "lugar": "Aula 301",
        "mensaje": "Traigan audífonos.",
        "estudiantes": [e.correo for e in estudiantes],
    } | extra


def programar(client, headers, reloj, caso_id, espacio_id, estudiantes=(), **extra) -> dict[str, Any]:
    response = client.post(SESSIONS_URL, headers=headers, json=datos_sesion(reloj, caso_id, espacio_id, estudiantes, **extra))
    assert response.status_code == 201, response.text
    return response.json()


def convocatorias(client, headers, sesion_id) -> list[dict[str, Any]]:
    response = client.get(f"{SESSIONS_URL}/{sesion_id}/convocations", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def codigo_de(client, headers, sesion_id, convocatoria_id) -> str:
    response = client.get(f"{SESSIONS_URL}/{sesion_id}/convocations/{convocatoria_id}/code", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["codigo_autorizacion"]


def iniciar(client, headers, sesion_id, reloj=None):
    if reloj is not None and reloj.actual.hour < 8:
        reloj.avanzar(hours=8 - reloj.actual.hour)  # a la hora de inicio de la ventana por defecto
    return client.post(f"{SESSIONS_URL}/{sesion_id}/start", headers=headers)


def ingresar(client, correo: str, codigo: str):
    return client.post(STUDENT_LOGIN_URL, json={"correo": correo, "codigo": codigo})


def contar(tabla: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one()


def estado_bd(sesion_id) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT estado::text FROM sesion WHERE id = :id"), {"id": sesion_id}).scalar_one()
