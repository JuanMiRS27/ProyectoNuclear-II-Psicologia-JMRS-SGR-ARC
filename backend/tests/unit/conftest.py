"""Aislamiento de las pruebas unitarias: ninguna toca PostgreSQL.

Sustituye las fixtures autouse del conftest raíz (migraciones y limpieza de tablas) y hace
fallar cualquier intento de abrir una conexión. Así, `pytest tests/unit` funciona sin un
servidor PostgreSQL en marcha; basta una TEST_DATABASE_URL sintácticamente válida.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import event

from app.core.database import engine


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def sin_base_de_datos() -> Iterator[None]:
    def rechazar(*_args, **_kwargs):
        raise AssertionError("Una prueba unitaria intentó conectarse a la base de datos.")

    event.listen(engine, "do_connect", rechazar)
    yield
    event.remove(engine, "do_connect", rechazar)
