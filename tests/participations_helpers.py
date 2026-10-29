"""Utilidades compartidas por las pruebas del módulo 4."""

import threading
from typing import Any

import pytest
from sqlalchemy import text

from app.core.database import engine
from tests.conftest import bearer
from tests.sessions_helpers import SESSIONS_URL, codigo_de, convocatorias, iniciar, ingresar, programar

PART_URL = "/api/v1/student/participations"


def iniciar_participacion(client, headers, sesion_id):
    return client.post(f"/api/v1/student/sessions/{sesion_id}/participations", headers=headers)


def guardar(client, headers, participacion_id, pregunta_id, opcion_id):
    return client.put(f"{PART_URL}/{participacion_id}/answers/{pregunta_id}", headers=headers, json={"opcion_id": str(opcion_id)})


def fila(sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).one()


def contar(sql: str, **params) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def en_paralelo(*tareas):
    resultados = [None] * len(tareas)

    def ejecutar(i, tarea):
        try:
            resultados[i] = tarea()
        except Exception as exc:  # noqa: BLE001
            resultados[i] = exc

    hilos = [threading.Thread(target=ejecutar, args=(i, t)) for i, t in enumerate(tareas)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
        assert not h.is_alive()
    return resultados


class Entorno:
    """Sesión 08:00-10:00 (límite de ingreso 09:00, tiempo_max_min 40) iniciada a las 08:00."""

    def __init__(self, client, docente_headers, reloj, sesion, caso, estudiantes, convs, codigos):
        self.client = client
        self.docente_headers = docente_headers
        self.reloj = reloj
        self.sesion = sesion
        self.caso = caso
        self.estudiantes = estudiantes
        self.convocatorias = convs
        self.codigos = codigos

    @property
    def sesion_id(self) -> str:
        return self.sesion["id"]

    @property
    def preguntas(self) -> list[dict[str, Any]]:
        return [p for e in self.caso["escenarios"] for p in e["preguntas"]]

    def headers(self, indice: int = 0) -> dict[str, str]:
        estudiante = self.estudiantes[indice]
        response = ingresar(self.client, estudiante.correo, self.codigos[estudiante.correo])
        assert response.status_code == 200, response.text
        return bearer(response.json()["access_token"])

    def conv_url(self, indice: int = 0, sufijo: str = "") -> str:
        conv = self.convocatorias[self.estudiantes[indice].correo]
        return f"{SESSIONS_URL}/{self.sesion_id}/convocations/{conv['id']}{sufijo}"

    def iniciar(self, headers) -> dict[str, Any]:
        response = iniciar_participacion(self.client, headers, self.sesion_id)
        assert response.status_code in (200, 201), response.text
        return response.json()


@pytest.fixture
def entorno(client, creador_headers, reloj, espacio, caso_publicado, estudiantes) -> Entorno:
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)
    convs = {c["estudiante"]["correo"]: c for c in convocatorias(client, creador_headers, sesion["id"])}
    codigos = {correo: codigo_de(client, creador_headers, sesion["id"], c["id"]) for correo, c in convs.items()}
    assert iniciar(client, creador_headers, sesion["id"], reloj).status_code == 200
    return Entorno(client, creador_headers, reloj, sesion, caso_publicado, estudiantes, convs, codigos)
