import uuid

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.auth.models import Usuario
from app.modules.sessions.models import EspacioAcademico
from app.modules.sessions.schemas import EspacioCreate, EspacioUpdate
from app.modules.sessions.services.base import ServicioSesionesBase

DUPLICADO = "Ya existe un espacio académico con ese nombre y periodo."


class EspacioService(ServicioSesionesBase):
    """Catálogo de espacios académicos: lo gestiona el administrador; los docentes consultan los activos."""

    def crear(self, data: EspacioCreate) -> EspacioAcademico:
        if self._espacios.exists_nombre_periodo(data.nombre, data.periodo):
            raise ConflictError(DUPLICADO)
        espacio = EspacioAcademico(nombre=data.nombre, periodo=data.periodo, activo=True)
        with self._transaction():
            self._espacios.add(espacio)
        return espacio

    def listar(self, usuario: Usuario, *, activo: bool | None, skip: int, limit: int) -> tuple[list[EspacioAcademico], int]:
        if usuario.es_docente:
            activo = True  # los docentes solo ven los espacios disponibles para programar
        return self._espacios.list(activo=activo, skip=skip, limit=limit)

    def obtener(self, usuario: Usuario, espacio_id: uuid.UUID) -> EspacioAcademico:
        espacio = self._espacios.get(espacio_id)
        if espacio is None or (usuario.es_docente and not espacio.activo):
            raise NotFoundError("Espacio académico no encontrado.")
        return espacio

    def actualizar(self, espacio_id: uuid.UUID, data: EspacioUpdate) -> EspacioAcademico:
        cambios = data.model_dump(exclude_unset=True)
        if any(cambios.get(campo, "") is None for campo in ("nombre", "periodo")):
            raise BusinessRuleError("El nombre y el periodo no pueden quedar vacíos.")
        espacio = self._obtener(espacio_id)
        nombre = cambios.get("nombre", espacio.nombre)
        periodo = cambios.get("periodo", espacio.periodo)
        if self._espacios.exists_nombre_periodo(nombre, periodo, exclude_id=espacio.id):
            raise ConflictError(DUPLICADO)
        with self._transaction():
            espacio.nombre, espacio.periodo = nombre, periodo
        return espacio

    def cambiar_estado(self, espacio_id: uuid.UUID, activo: bool) -> EspacioAcademico:
        """Desactivar impide programar nuevas sesiones en el espacio; las existentes no cambian."""
        espacio = self._obtener(espacio_id)
        with self._transaction():
            espacio.activo = activo
        return espacio

    def _obtener(self, espacio_id: uuid.UUID) -> EspacioAcademico:
        espacio = self._espacios.get(espacio_id)
        if espacio is None:
            raise NotFoundError("Espacio académico no encontrado.")
        return espacio
