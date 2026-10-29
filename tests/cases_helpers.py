"""Datos y utilidades compartidas por las pruebas del módulo 2."""

import copy
from typing import Any

from sqlalchemy import text

from app.core.database import engine

CASES_URL = "/api/v1/cases"
TABLAS_MODULO_2 = ("caso_estudio", "escenario", "pregunta", "opcion", "rubrica", "criterio_rubrica")

NIVELES = [
    {"nombre": "Insuficiente", "minAciertosPct": 0, "valor": 1.00},
    {"nombre": "Aceptable", "minAciertosPct": 50, "valor": 3.00},
    {"nombre": "Sobresaliente", "minAciertosPct": 85, "valor": 5.00},
]
# Mismos niveles para la API REST, que usa snake_case (la DSL usa camelCase).
NIVELES_API = [
    {"nombre": n["nombre"], "min_aciertos_pct": n["minAciertosPct"], "valor": n["valor"]} for n in NIVELES
]


def _opciones(prefijo: str) -> list[dict[str, Any]]:
    return [
        {"texto": f"{prefijo}: respuesta adecuada", "esCorrecta": True, "justificacion": "Considera la presión grupal."},
        {"texto": f"{prefijo}: respuesta parcial", "esCorrecta": False, "justificacion": "Ignora el contexto social."},
        {"texto": f"{prefijo}: respuesta inadecuada", "esCorrecta": False, "justificacion": "Refuerza el sesgo."},
    ]


def dsl_valido() -> dict[str, Any]:
    """Caso completo que supera todas las validaciones de publicación."""
    return copy.deepcopy(
        {
            "dslVersion": "1.0",
            "caso": {
                "nombre": "Conformidad en el aula",
                "desarrolloSituacional": "Un grupo de estudiantes debe decidir frente a la presión de la mayoría.",
                "historia": "En una universidad, un experimento replica el paradigma de Asch.",
                "tiempoEstimadoMin": 45,
            },
            "escenarios": [
                {
                    "titulo": "La primera votación",
                    "contexto": "Todos responden antes que Laura.",
                    "preguntas": [
                        {"enunciado": "¿Qué fenómeno se observa?", "criterio": "ANALISIS", "opciones": _opciones("E1P1")},
                        {"enunciado": "¿Qué debería hacer Laura?", "criterio": "DECISION", "opciones": _opciones("E1P2")},
                    ],
                },
                {
                    "titulo": "La discusión posterior",
                    "contexto": "El grupo debate el resultado.",
                    "preguntas": [
                        {"enunciado": "¿Qué papel cumple el disidente?", "criterio": "ANALISIS", "opciones": _opciones("E2P1")},
                    ],
                },
            ],
            "rubrica": {
                "escalaMax": 5.00,
                "criterios": [
                    {"clave": "ANALISIS", "descripcion": "Análisis del fenómeno social", "peso": 60, "niveles": NIVELES},
                    {"clave": "DECISION", "descripcion": "Toma de decisiones éticas", "peso": 40, "niveles": NIVELES},
                ],
            },
        }
    )


def crear_caso_completo(client, headers, documento: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"{CASES_URL}/full", headers=headers, json=documento or dsl_valido())
    assert response.status_code == 201, response.text
    return response.json()


def publicar(client, headers, caso_id: str):
    return client.post(f"{CASES_URL}/{caso_id}/publish", headers=headers)


def contar_filas() -> dict[str, int]:
    with engine.connect() as conn:
        return {tabla: conn.execute(text(f"SELECT count(*) FROM {tabla}")).scalar_one() for tabla in TABLAS_MODULO_2}


def codigos(validacion: dict[str, Any]) -> list[str]:
    return [error["codigo"] for error in validacion["errores"]]
