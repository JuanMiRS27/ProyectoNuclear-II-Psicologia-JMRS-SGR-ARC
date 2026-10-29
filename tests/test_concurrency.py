"""Regresión del manejo transaccional y pruebas de concurrencia reales contra PostgreSQL.

Las pruebas concurrentes usan hilos con sesiones independientes (conexiones distintas)
y una barrera para forzar que ambas operaciones coincidan en el punto crítico.
"""

import threading
from collections.abc import Callable

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.exceptions import BusinessRuleError, ConflictError
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.auth.repositories import UsuarioRepository
from app.modules.auth.schemas import DocenteCreate
from app.modules.auth.services import UsuarioService

TEACHERS_URL = "/api/v1/users/teachers"
BARRIER_TIMEOUT = 10


def teacher(**overrides) -> DocenteCreate:
    data = {
        "nombre_completo": "Docente Concurrente",
        "correo": "concurrente@universidad.edu.co",
        "clave": "ClaveInicial#2026",
    } | overrides
    return DocenteCreate(**data)


def run_concurrently(*tasks: Callable[[], object]) -> list[object]:
    """Ejecuta las tareas en hilos y devuelve, por tarea, su resultado o la excepción lanzada."""
    results: list[object] = [None] * len(tasks)

    def runner(index: int, task: Callable[[], object]) -> None:
        try:
            results[index] = task()
        except Exception as exc:  # noqa: BLE001 - se inspecciona en la prueba
            results[index] = exc

    threads = [threading.Thread(target=runner, args=(i, t)) for i, t in enumerate(tasks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "un hilo quedó bloqueado"
    return results


def count_users(**filters) -> int:
    with SessionLocal() as session:
        stmt = select(func.count()).select_from(Usuario).filter_by(**filters)
        return session.scalar(stmt) or 0


# --- Regresión: IntegrityError durante flush() --------------------------------


@pytest.fixture
def skip_uniqueness_precheck(monkeypatch):
    """Simula que otra transacción insertó el registro justo después de la verificación previa."""
    monkeypatch.setattr(UsuarioRepository, "exists_by_correo", lambda self, *a, **k: False)
    monkeypatch.setattr(UsuarioRepository, "exists_by_codigo_institucional", lambda self, *a, **k: False)


def test_flush_unique_violation_on_email_returns_409(client, admin_headers, docente, skip_uniqueness_precheck):
    payload = {"nombre_completo": "Duplicado", "correo": docente.correo, "clave": "ClaveInicial#2026"}
    response = client.post(TEACHERS_URL, headers=admin_headers, json=payload)

    assert response.status_code == 409
    assert response.json() == {"detail": "Ya existe un usuario registrado con ese correo."}
    assert count_users(correo=docente.correo) == 1


def test_flush_unique_violation_on_code_returns_409(client, admin_headers, skip_uniqueness_precheck):
    base = {"nombre_completo": "Docente", "clave": "ClaveInicial#2026", "codigo_institucional": "DOC-77"}
    assert client.post(TEACHERS_URL, headers=admin_headers, json=base | {"correo": "a@universidad.edu.co"}).status_code == 201

    response = client.post(TEACHERS_URL, headers=admin_headers, json=base | {"correo": "b@universidad.edu.co"})
    assert response.status_code == 409
    assert response.json() == {"detail": "Ya existe un usuario registrado con ese código institucional."}


def test_session_is_rolled_back_and_reusable_after_flush_conflict(db, docente, skip_uniqueness_precheck):
    service = UsuarioService(db)

    with pytest.raises(ConflictError):
        service.create_docente(teacher(correo=docente.correo))

    # Sin rollback, la sesión quedaría inutilizable (PendingRollbackError).
    creado = service.create_docente(teacher(correo="otro.docente@universidad.edu.co"))
    assert creado.id is not None


# --- Concurrencia: dos altas simultáneas con el mismo correo ------------------


def test_concurrent_teacher_creation_with_same_email(monkeypatch):
    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    original_exists = UsuarioRepository.exists_by_correo

    def exists_then_wait(self, *args, **kwargs):
        result = original_exists(self, *args, **kwargs)
        barrier.wait()  # ambos hilos superan la verificación previa antes de insertar
        return result

    monkeypatch.setattr(UsuarioRepository, "exists_by_correo", exists_then_wait)

    def create() -> Usuario:
        with SessionLocal() as session:
            return UsuarioService(session).create_docente(teacher())

    results = run_concurrently(create, create)

    created = [r for r in results if isinstance(r, Usuario)]
    conflicts = [r for r in results if isinstance(r, ConflictError)]
    assert len(created) == 1 and len(conflicts) == 1, results
    assert conflicts[0].detail == "Ya existe un usuario registrado con ese correo."
    assert count_users(correo="concurrente@universidad.edu.co") == 1


# --- Concurrencia: desactivación cruzada de los dos únicos administradores ----


def _cross_deactivation(admin_a: Usuario, admin_b: Usuario) -> list[object]:
    def deactivate(actor_id, target_id):
        def task():
            with SessionLocal() as session:
                service = UsuarioService(session)
                return service.set_status(service.get(actor_id), target_id, False)

        return task

    return run_concurrently(deactivate(admin_a.id, admin_b.id), deactivate(admin_b.id, admin_a.id))


def test_concurrent_deactivation_cannot_remove_all_admins(monkeypatch, make_user):
    admin_a = make_user(RolUsuario.ADMINISTRADOR)
    admin_b = make_user(RolUsuario.ADMINISTRADOR)

    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    original_lock = UsuarioRepository.lock_active_admins

    def wait_then_lock(self):
        barrier.wait()  # ambas peticiones llegan a la vez a la sección crítica
        return original_lock(self)

    monkeypatch.setattr(UsuarioRepository, "lock_active_admins", wait_then_lock)

    results = _cross_deactivation(admin_a, admin_b)

    succeeded = [r for r in results if isinstance(r, Usuario)]
    rejected = [r for r in results if isinstance(r, BusinessRuleError)]
    assert len(succeeded) == 1 and len(rejected) == 1, results
    assert count_users(rol=RolUsuario.ADMINISTRADOR, activo=True) == 1


def test_control_without_lock_the_race_is_real(monkeypatch, make_user):
    """Control: sin el bloqueo, la misma carrera deja el sistema sin administradores.

    Demuestra que la prueba anterior detecta el problema y no pasa por casualidad.
    """
    admin_a = make_user(RolUsuario.ADMINISTRADOR)
    admin_b = make_user(RolUsuario.ADMINISTRADOR)

    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    original_count = UsuarioRepository.count_active_admins

    def count_then_wait(self):
        result = original_count(self)
        barrier.wait()
        return result

    monkeypatch.setattr(UsuarioRepository, "lock_active_admins", lambda self: None)
    monkeypatch.setattr(UsuarioRepository, "count_active_admins", count_then_wait)

    results = _cross_deactivation(admin_a, admin_b)

    assert all(isinstance(r, Usuario) for r in results), results
    assert count_users(rol=RolUsuario.ADMINISTRADOR, activo=True) == 0
