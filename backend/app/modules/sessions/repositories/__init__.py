"""Acceso a datos del módulo 3. Los repositorios no deciden reglas académicas."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.modules.auth.models import Usuario
from app.modules.sessions.models import Convocatoria, EspacioAcademico, EstadoSesionEnum, Sesion


class EspacioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, espacio_id: uuid.UUID) -> EspacioAcademico | None:
        return self._session.get(EspacioAcademico, espacio_id)

    def exists_nombre_periodo(self, nombre: str, periodo: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(EspacioAcademico.id).where(
            func.lower(EspacioAcademico.nombre) == nombre.lower(),
            func.lower(EspacioAcademico.periodo) == periodo.lower(),
        )
        if exclude_id is not None:
            stmt = stmt.where(EspacioAcademico.id != exclude_id)
        return self._session.scalars(stmt.limit(1)).first() is not None

    def list(self, *, activo: bool | None, skip: int, limit: int) -> tuple[list[EspacioAcademico], int]:
        filtros = [] if activo is None else [EspacioAcademico.activo.is_(activo)]
        total = self._session.scalar(select(func.count()).select_from(EspacioAcademico).where(*filtros)) or 0
        stmt = (
            select(EspacioAcademico)
            .where(*filtros)
            .order_by(EspacioAcademico.periodo.desc(), EspacioAcademico.nombre, EspacioAcademico.id)
            .offset(skip)
            .limit(limit)
        )
        return list(self._session.scalars(stmt)), total

    def add(self, espacio: EspacioAcademico) -> None:
        self._session.add(espacio)
        self._session.flush()


class SesionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_owned(self, sesion_id: uuid.UUID, docente_id: uuid.UUID, *, for_update: bool = False) -> Sesion | None:
        """Sesión del docente responsable; una ajena es indistinguible de una inexistente.

        `for_update` bloquea la fila: todas las operaciones que cambian la sesión o sus
        convocatorias lo usan, así que se ejecutan en serie con las transiciones de estado.
        """
        stmt = select(Sesion).where(Sesion.id == sesion_id, Sesion.docente_id == docente_id)
        if for_update:
            stmt = stmt.with_for_update(of=Sesion)
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    def get_for_update_skip_locked(self, sesion_id: uuid.UUID) -> Sesion | None:
        """Para el cierre automático: si otro proceso ya la tiene bloqueada, la omite."""
        stmt = select(Sesion).where(Sesion.id == sesion_id).with_for_update(skip_locked=True)
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    def list_by_docente(
        self, docente_id: uuid.UUID, *, estado: EstadoSesionEnum | None, skip: int, limit: int
    ) -> tuple[list[Sesion], int]:
        filtros = [Sesion.docente_id == docente_id]
        if estado is not None:
            filtros.append(Sesion.estado == estado)
        total = self._session.scalar(select(func.count()).select_from(Sesion).where(*filtros)) or 0
        stmt = select(Sesion).where(*filtros).order_by(Sesion.inicio.desc(), Sesion.id).offset(skip).limit(limit)
        return list(self._session.scalars(stmt)), total

    def ids_vencidas(self, fin_hasta: datetime, limite: int) -> list[uuid.UUID]:
        """Sesiones no finalizadas cuya hora de fin es anterior o igual a `fin_hasta` (usa ix_sesion_estado_fin)."""
        stmt = (
            select(Sesion.id)
            .where(Sesion.estado.in_([EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.EN_CURSO]), Sesion.fin <= fin_hasta)
            .order_by(Sesion.fin)
            .limit(limite)
        )
        return list(self._session.scalars(stmt))

    def contar_convocatorias(self, sesion_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not sesion_ids:
            return {}
        stmt = (
            select(Convocatoria.sesion_id, func.count())
            .where(Convocatoria.sesion_id.in_(sesion_ids), Convocatoria.revocada_en.is_(None))  # solo activas
            .group_by(Convocatoria.sesion_id)
        )
        return {sesion_id: total for sesion_id, total in self._session.execute(stmt)}

    def add(self, sesion: Sesion) -> None:
        self._session.add(sesion)
        self._session.flush()


# Espacio de nombres (int4) de los bloqueos consultivos de generación de códigos.
_CLASE_BLOQUEO_CODIGOS = 30_003


def _clave_bloqueo(estudiante_id: uuid.UUID) -> int:
    return int.from_bytes(estudiante_id.bytes[:4], "big", signed=True)


class ConvocatoriaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def bloquear_codigos_de_estudiantes(self, estudiante_ids: list[uuid.UUID]) -> None:
        """Serializa la generación de códigos por estudiante hasta el fin de la transacción.

        `pg_advisory_xact_lock` por estudiante: dos transacciones que generan o regeneran
        códigos para el mismo estudiante (aunque sea en sesiones distintas) se ejecutan
        en serie, así que leer sus códigos existentes y escribir el nuevo es atómico.
        Se bloquea en orden de id para evitar interbloqueos entre lotes.
        """
        for estudiante_id in sorted(set(estudiante_ids)):
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(CAST(:clase AS integer), CAST(:clave AS integer))"),
                {"clase": _CLASE_BLOQUEO_CODIGOS, "clave": _clave_bloqueo(estudiante_id)},
            )

    def get(self, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> Convocatoria | None:
        stmt = (
            select(Convocatoria)
            .where(Convocatoria.id == convocatoria_id, Convocatoria.sesion_id == sesion_id)
            .options(selectinload(Convocatoria.estudiante))
        )
        return self._session.scalars(stmt).first()

    def get_by_id(self, convocatoria_id: uuid.UUID) -> Convocatoria | None:
        return self._session.get(Convocatoria, convocatoria_id)

    def list_by_sesion(self, sesion_id: uuid.UUID, *, incluir_revocadas: bool = False) -> list[Convocatoria]:
        stmt = (
            select(Convocatoria)
            .join(Usuario, Convocatoria.estudiante_id == Usuario.id)
            .where(Convocatoria.sesion_id == sesion_id)
            .order_by(Usuario.nombre_completo, Convocatoria.id)
            .options(selectinload(Convocatoria.estudiante))
        )
        if not incluir_revocadas:
            stmt = stmt.where(Convocatoria.revocada_en.is_(None))
        return list(self._session.scalars(stmt))

    def por_estudiante(self, sesion_id: uuid.UUID) -> dict[uuid.UUID, Convocatoria]:
        """Todas las convocatorias de la sesión (activas y revocadas), por estudiante."""
        stmt = select(Convocatoria).where(Convocatoria.sesion_id == sesion_id)
        return {c.estudiante_id: c for c in self._session.scalars(stmt)}

    def cifrados_de_estudiante(self, estudiante_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        """(id, código cifrado) de las convocatorias del estudiante, para evitar códigos repetidos."""
        stmt = select(Convocatoria.id, Convocatoria.codigo_autorizacion).where(Convocatoria.estudiante_id == estudiante_id)
        return [(fila.id, fila.codigo_autorizacion) for fila in self._session.execute(stmt)]

    def de_estudiante_por_correo(self, correo: str) -> list[Convocatoria]:
        """Convocatorias del usuario con ese correo (el código cifrado no admite búsqueda por igualdad)."""
        stmt = (
            select(Convocatoria)
            .join(Usuario, Convocatoria.estudiante_id == Usuario.id)
            .where(Usuario.correo == correo)
            .options(selectinload(Convocatoria.estudiante), selectinload(Convocatoria.sesion))
        )
        return list(self._session.scalars(stmt))

    def add(self, convocatoria: Convocatoria) -> None:
        self._session.add(convocatoria)
        self._session.flush()

    def flush(self) -> None:
        # Las convocatorias nunca se eliminan físicamente (revocación lógica, migración 0007).
        self._session.flush()


class EstudianteRepository:
    """Consulta (solo lectura) de cuentas ESTUDIANTE del módulo 1 para convocarlas."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def por_correos(self, correos: list[str]) -> dict[str, Usuario]:
        if not correos:
            return {}
        stmt = select(Usuario).where(Usuario.correo.in_(correos))
        return {u.correo: u for u in self._session.scalars(stmt)}


__all__ = ["ConvocatoriaRepository", "EspacioRepository", "EstudianteRepository", "SesionRepository"]
