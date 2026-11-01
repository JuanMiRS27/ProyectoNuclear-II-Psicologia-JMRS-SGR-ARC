import uuid

from app.core.exceptions import BusinessRuleError
from app.modules.auth.models import Usuario
from app.modules.cases.builders import ConstructorCasoAcademico, ServicioConstruccionCaso
from app.modules.cases.dsl import CaseDSLDocument, exportar_caso
from app.modules.cases.models import CasoEstudio, EstadoCasoEnum
from app.modules.cases.schemas import CasoCreate, CasoUpdate
from app.modules.cases.services.base import ServicioCasoBase
from app.modules.cases.states import CicloVidaCaso
from app.modules.cases.validators import ResultadoValidacion, validar_caso


class CasoService(ServicioCasoBase):
    # --- Creación -----------------------------------------------------------

    def crear_borrador(self, autor: Usuario, data: CasoCreate) -> CasoEstudio:
        # autor_id siempre sale del usuario autenticado, nunca del cuerpo de la petición.
        caso = CasoEstudio(autor_id=autor.id, estado=EstadoCasoEnum.BORRADOR, **data.model_dump())
        with self._transaction():
            self._casos.add(caso)
        return self._caso_lectura(caso.id, autor, cargar_contenido=True)

    def crear_completo(self, autor: Usuario, documento: CaseDSLDocument) -> CasoEstudio:
        """Construye el caso con el Builder y lo persiste en una única transacción.

        Si cualquier INSERT falla, el rollback descarta el caso y todo su contenido.
        """
        director = ServicioConstruccionCaso(ConstructorCasoAcademico())
        agregado = director.construir_desde_dsl(documento, autor.id)
        with self._transaction():
            self._casos.add(agregado.caso)
        return self._caso_lectura(agregado.caso.id, autor, cargar_contenido=True)

    # --- Consulta -----------------------------------------------------------

    def listar(
        self, autor: Usuario, *, estado: EstadoCasoEnum | None, skip: int, limit: int
    ) -> tuple[list[CasoEstudio], int]:
        return self._casos.list_by_author(autor.id, estado=estado, skip=skip, limit=limit)

    def obtener(self, autor: Usuario, caso_id: uuid.UUID) -> CasoEstudio:
        return self._caso_lectura(caso_id, autor, cargar_contenido=True)

    def exportar_dsl(self, autor: Usuario, caso_id: uuid.UUID) -> CaseDSLDocument:
        return exportar_caso(self._caso_lectura(caso_id, autor, cargar_contenido=True))

    def validar(self, autor: Usuario, caso_id: uuid.UUID) -> ResultadoValidacion:
        """Ejecuta la cadena de validación sin cambiar el estado."""
        return validar_caso(self._caso_lectura(caso_id, autor, cargar_contenido=True))

    # --- Modificación -------------------------------------------------------

    def actualizar(self, autor: Usuario, caso_id: uuid.UUID, data: CasoUpdate) -> CasoEstudio:
        cambios = data.model_dump(exclude_unset=True)
        if "nombre" in cambios and cambios["nombre"] is None:
            raise BusinessRuleError("El nombre del caso no puede quedar vacío.")
        with self._transaction():
            caso = self._caso_editable(caso_id, autor)
            for campo, valor in cambios.items():
                setattr(caso, campo, valor)
        return self._caso_lectura(caso_id, autor, cargar_contenido=True)

    def eliminar(self, autor: Usuario, caso_id: uuid.UUID) -> None:
        """Eliminación física, solo de borradores (aún no pueden estar vinculados a sesiones)."""
        with self._transaction():
            caso = self._caso_bloqueado(caso_id, autor)
            CicloVidaCaso(caso).asegurar_eliminable()
            self._casos.delete(caso)

    # --- Ciclo de vida (State + Chain of Responsibility) --------------------

    def publicar(self, autor: Usuario, caso_id: uuid.UUID) -> CasoEstudio:
        """1) se bloquea el caso; 2) el estado decide; 3) en BORRADOR se ejecuta la cadena;
        4) si hay errores se lanza 422 y el caso sigue en BORRADOR; 5) si no, pasa a PUBLICADO y se confirma.
        """
        with self._transaction():
            caso = self._caso_bloqueado(caso_id, autor, cargar_contenido=True)
            CicloVidaCaso(caso).publicar(lambda: validar_caso(caso))
        return self._caso_lectura(caso_id, autor, cargar_contenido=True)

    def archivar(self, autor: Usuario, caso_id: uuid.UUID) -> CasoEstudio:
        with self._transaction():
            caso = self._caso_bloqueado(caso_id, autor)
            CicloVidaCaso(caso).archivar()
        return self._caso_lectura(caso_id, autor, cargar_contenido=True)
