"""Verifica que la migración inicial crea el esquema esperado y sus restricciones en PostgreSQL."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import engine
from tests.conftest import alembic_config


def test_database_is_at_head_revision():
    config = alembic_config()
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert current == head


def test_usuario_table_structure():
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("usuario")}
    assert set(columns) == {
        "id",
        "nombre_completo",
        "correo",
        "clave_hash",
        "rol",
        "codigo_institucional",
        "puede_crear_casos",
        "activo",
        "creado_en",
        "version_token",
    }
    assert columns["clave_hash"]["nullable"] is True
    assert all(not columns[c]["nullable"] for c in ("nombre_completo", "correo", "rol", "activo", "creado_en"))

    unique = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("usuario")}
    assert ("correo",) in unique
    assert ("codigo_institucional",) in unique

    checks = {c["name"] for c in inspector.get_check_constraints("usuario")}
    assert {
        "ck_usuario_correo_normalizado",
        "ck_usuario_nombre_no_vacio",
        "ck_usuario_clave_requerida_por_rol",
        "ck_usuario_permiso_casos_solo_docente",
        "ck_usuario_version_token_no_negativa",
    } <= checks


def test_rol_usuario_enum_values():
    with engine.connect() as conn:
        values = conn.execute(text("SELECT unnest(enum_range(NULL::rol_usuario))::text")).scalars().all()
    assert values == ["ADMINISTRADOR", "DOCENTE", "ESTUDIANTE"]


def _insert(**overrides):
    row = {
        "nombre_completo": "Prueba",
        "correo": "prueba@universidad.edu.co",
        "clave_hash": "hash",
        "rol": "DOCENTE",
    } | overrides
    columns = ", ".join(row)
    params = ", ".join(f":{k}" for k in row)
    with engine.begin() as conn:
        return conn.execute(
            text(f"INSERT INTO usuario ({columns}) VALUES ({params}) RETURNING id, activo, puede_crear_casos, creado_en, version_token"),
            row,
        ).one()


def test_server_defaults():
    inserted = _insert()
    assert inserted.id is not None
    assert inserted.activo is True
    assert inserted.puede_crear_casos is False
    assert inserted.creado_en is not None
    assert inserted.version_token == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"rol": "ADMINISTRADOR", "puede_crear_casos": True},
        {"rol": "ESTUDIANTE", "clave_hash": None, "puede_crear_casos": True},
        {"correo": "Mayusculas@Universidad.edu.co"},
        {"rol": "DOCENTE", "clave_hash": None},
        {"rol": "ADMINISTRADOR", "clave_hash": None},
        {"nombre_completo": "   "},
        {"version_token": -1},
    ],
    ids=["permiso-admin", "permiso-estudiante", "correo-no-normalizado", "docente-sin-clave", "admin-sin-clave", "nombre-vacio", "version-negativa"],
)
def test_check_constraints_reject_invalid_rows(overrides):
    with pytest.raises(IntegrityError):
        _insert(**overrides)


def test_student_without_password_is_allowed():
    _insert(rol="ESTUDIANTE", clave_hash=None, correo="estudiante@universidad.edu.co")


def test_duplicate_email_rejected_by_database():
    _insert()
    with pytest.raises(IntegrityError):
        _insert()


def test_migration_0002_backfills_existing_rows():
    """Las filas creadas con la revisión 0001 reciben version_token = 0 al aplicar 0002."""
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0001_usuario")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO usuario (nombre_completo, correo, clave_hash, rol) "
                    "VALUES ('Previo', 'previo@universidad.edu.co', 'hash', 'DOCENTE')"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as conn:
            version = conn.execute(
                text("SELECT version_token FROM usuario WHERE correo = 'previo@universidad.edu.co'")
            ).scalar_one()
        assert version == 0
    finally:
        command.upgrade(config, "head")


def test_downgrade_and_upgrade_roundtrip():
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "base")
    assert not inspect(engine).has_table("usuario")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regtype('rol_usuario')")).scalar() is None
    command.upgrade(config, "head")
    assert inspect(engine).has_table("usuario")
