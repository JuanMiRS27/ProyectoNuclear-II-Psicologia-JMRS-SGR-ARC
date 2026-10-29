import logging
import uuid
from datetime import datetime

from app.core.config import get_settings
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.cases.models import CasoEstudio
from app.modules.cases.repositories import CasoRepository
from app.modules.cases.states import CicloVidaCaso
from app.modules.sessions import eventos
from app.modules.sessions.models import Convocatoria, EspacioAcademico, EstadoSesionEnum, Sesion
from app.modules.sessions.schemas import CodigoConvocatoriaRead, SesionCreate, SesionUpdate
from app.modules.sessions.security.cifrado import CodigoCifradoInvalidoError, cifrar_codigo, descifrar_codigo
from app.modules.sessions.security.codigos import generar_codigo
from app.modules.sessions.services.base import ServicioSesionesBase
from app.modules.sessions.states import CicloVidaSesion

logger = logging.getLogger("iter_animi.sesiones")

INTENTOS_GENERACION_CODIGO = 5
CAMPOS_OBLIGATORIOS = ("caso_id", "espacio_academico_id", "inicio", "fin", "tiempo_max_min")


class _ConvocatoriasMixin(ServicioSesionesBase):
    def _convocar(self, sesion: Sesion, correos: list[str]) -> list[Convocatoria]:
        """Crea una convocatoria con código propio por cada correo. Valida todo antes de insertar."""
        if not correos:
            return []
        encontrados = self._estudiantes.por_correos(correos)
        no_estudiantes = [c for c in correos if c not in encontrados or encontrados[c].rol != RolUsuario.ESTUDIANTE]
        if no_estudiantes:
            raise BusinessRuleError(f"Estos correos no corresponden a estudiantes registrados: {no_estudiantes}.")
        inactivos = [c for c in correos if not encontrados[c].activo]
        if inactivos:
            raise BusinessRuleError(f"Estas cuentas de estudiante están desactivadas: {inactivos}.")
        existentes = self._convocatorias.por_estudiante(sesion.id)
        repetidos = [c for c in correos if encontrados[c].id in existentes and existentes[encontrados[c].id].activa]
        if repetidos:
            raise ConflictError(f"Estos estudiantes ya están convocados a la sesión: {repetidos}.")

        self._convocatorias.bloquear_codigos_de_estudiantes([encontrados[c].id for c in correos])
        creadas = []
        for correo in correos:
            estudiante_id = encontrados[correo].id
            revocada = existentes.get(estudiante_id)
            if revocada is not None:
                # Volver a convocar a un estudiante retirado reactiva SU convocatoria (la restricción
                # única sesión-estudiante y el historial se conservan) con un código nuevo.
                revocada.revocada_en = None
                revocada.codigo_autorizacion = cifrar_codigo(self._codigo_unico(estudiante_id), revocada.id)
                self._convocatorias.flush()
                creadas.append(revocada)
                self._emitir(eventos.evento_convocatoria(eventos.CONVOCATORIA_REACTIVADA, sesion.id, revocada.id))
                continue
            convocatoria_id = uuid.uuid4()  # se fija antes de cifrar: es el dato asociado (AAD) del cifrado
            convocatoria = Convocatoria(
                id=convocatoria_id,
                sesion_id=sesion.id,
                estudiante_id=estudiante_id,
                codigo_autorizacion=cifrar_codigo(self._codigo_unico(estudiante_id), convocatoria_id),
                intentos_autorizados=1,
            )
            self._convocatorias.add(convocatoria)
            creadas.append(convocatoria)
            self._emitir(eventos.evento_convocatoria(eventos.CONVOCATORIA_CREADA, sesion.id, convocatoria.id))
        return creadas

    def _codigo_unico(self, estudiante_id: uuid.UUID) -> str:
        """Código distinto de los demás códigos del mismo estudiante.

        El ingreso identifica la convocatoria por (correo, código), así que basta con que el
        código no se repita entre las convocatorias del estudiante. Con 60 bits de entropía
        una colisión es prácticamente imposible; se comprueba igualmente.

        Requiere haber llamado antes a `bloquear_codigos_de_estudiantes` en la misma
        transacción: así la lectura de los códigos existentes y la escritura del nuevo
        son atómicas frente a otras transacciones del mismo estudiante.
        """
        existentes = set()
        for convocatoria_id, cifrado in self._convocatorias.cifrados_de_estudiante(estudiante_id):
            try:
                existentes.add(descifrar_codigo(cifrado, convocatoria_id))
            except CodigoCifradoInvalidoError:
                continue
        for _ in range(INTENTOS_GENERACION_CODIGO):
            codigo = generar_codigo()
            if codigo not in existentes:
                return codigo
        raise ConflictError("No se pudo generar un código único; intente de nuevo.")

    @staticmethod
    def _codigo_en_claro(convocatoria: Convocatoria) -> str:
        try:
            return descifrar_codigo(convocatoria.codigo_autorizacion, convocatoria.id)
        except CodigoCifradoInvalidoError as exc:
            logger.error("codigo cifrado invalido convocatoria=%s (alterado o con otra clave)", convocatoria.id)
            raise ConflictError("El código almacenado no es válido; regenérelo.") from exc


class SesionService(_ConvocatoriasMixin):
    # --- Programación ----------------------------------------------------------------

    def programar(self, docente: Usuario, data: SesionCreate) -> Sesion:
        """Crea la sesión y sus convocatorias en una sola transacción."""
        ahora = self._reloj.ahora()
        with self._transaction():
            caso = self._caso_programable(docente, data.caso_id)
            espacio = self._espacio_activo(data.espacio_academico_id)
            self._validar_condiciones(caso, data.inicio, data.fin, data.tiempo_max_min, ahora, fechas_cambiadas=True)
            sesion = Sesion(
                caso_id=caso.id,
                espacio_academico_id=espacio.id,
                docente_id=docente.id,  # siempre el docente autenticado
                inicio=data.inicio,
                fin=data.fin,
                tiempo_max_min=data.tiempo_max_min,
                lugar=data.lugar,
                mensaje=data.mensaje,
                estado=EstadoSesionEnum.SIN_INICIAR,
            )
            self._sesiones.add(sesion)
            self._emitir(eventos.EventoSesion(eventos.SESION_PROGRAMADA, sesion.id))
            if data.estudiantes:
                CicloVidaSesion(sesion).asegurar_nuevas_convocatorias(ahora)
            self._convocar(sesion, data.estudiantes)
        return sesion

    def actualizar(self, docente: Usuario, sesion_id: uuid.UUID, data: SesionUpdate) -> Sesion:
        cambios = data.model_dump(exclude_unset=True)
        nulos = [campo for campo in CAMPOS_OBLIGATORIOS if campo in cambios and cambios[campo] is None]
        if nulos:
            raise BusinessRuleError(f"Estos campos no pueden quedar vacíos: {nulos}.")
        if not cambios:
            return self._sesion_lectura(sesion_id, docente)

        ahora = self._reloj.ahora()
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).asegurar_programacion_editable(cambios.keys())

            caso = self._caso_programable(docente, cambios["caso_id"]) if "caso_id" in cambios else sesion.caso
            if "espacio_academico_id" in cambios:
                self._espacio_activo(cambios["espacio_academico_id"])
            if {"caso_id", "inicio", "fin", "tiempo_max_min"} & cambios.keys():
                self._validar_condiciones(
                    caso,
                    cambios.get("inicio", sesion.inicio),
                    cambios.get("fin", sesion.fin),
                    cambios.get("tiempo_max_min", sesion.tiempo_max_min),
                    ahora,
                    fechas_cambiadas=bool({"inicio", "fin"} & cambios.keys()),
                )
            for campo, valor in cambios.items():
                setattr(sesion, campo, valor)
            self._emitir(eventos.EventoSesion(eventos.SESION_MODIFICADA, sesion.id, {"campos": ",".join(sorted(cambios))}))
        return sesion

    # --- Ciclo de vida (State) ---------------------------------------------------------

    def iniciar(self, docente: Usuario, sesion_id: uuid.UUID) -> Sesion:
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).iniciar(self._reloj.ahora())
            self._emitir(eventos.EventoSesion(eventos.SESION_INICIADA, sesion.id))
        return sesion

    def finalizar(self, docente: Usuario, sesion_id: uuid.UUID) -> Sesion:
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).finalizar(self._reloj.ahora())
            self._emitir(eventos.EventoSesion(eventos.SESION_FINALIZADA, sesion.id, {"origen": "docente"}))
        return sesion

    # --- Consulta ------------------------------------------------------------------------

    def listar(
        self, docente: Usuario, *, estado: EstadoSesionEnum | None, skip: int, limit: int
    ) -> tuple[list[Sesion], int, dict[uuid.UUID, int]]:
        sesiones, total = self._sesiones.list_by_docente(docente.id, estado=estado, skip=skip, limit=limit)
        return sesiones, total, self._sesiones.contar_convocatorias([s.id for s in sesiones])

    def obtener(self, docente: Usuario, sesion_id: uuid.UUID) -> Sesion:
        return self._sesion_lectura(sesion_id, docente)

    def total_convocatorias(self, sesion: Sesion) -> int:
        return self._sesiones.contar_convocatorias([sesion.id]).get(sesion.id, 0)

    # --- Validaciones ---------------------------------------------------------------------

    def _caso_programable(self, docente: Usuario, caso_id: uuid.UUID) -> CasoEstudio:
        """Reutiliza las reglas del módulo 2: autoría del caso y su estado (State de casos)."""
        caso = CasoRepository(self._session).get_owned(caso_id, docente.id)
        if caso is None:
            raise NotFoundError("Caso no encontrado.")
        if not CicloVidaCaso(caso).admite_programacion():
            raise ConflictError(f"Solo se pueden programar casos publicados; el caso está {caso.estado.value}.")
        return caso

    def _espacio_activo(self, espacio_id: uuid.UUID) -> EspacioAcademico:
        espacio = self._espacios.get(espacio_id)
        if espacio is None:
            raise NotFoundError("Espacio académico no encontrado.")
        if not espacio.activo:
            raise BusinessRuleError("El espacio académico está inactivo; no admite nuevas sesiones.")
        return espacio

    @staticmethod
    def _validar_condiciones(
        caso: CasoEstudio, inicio: datetime, fin: datetime, tiempo_max: int, ahora: datetime, *, fechas_cambiadas: bool
    ) -> None:
        if inicio >= fin:
            raise BusinessRuleError("La hora de inicio debe ser anterior a la de finalización.")
        if fechas_cambiadas and fin <= ahora:
            raise BusinessRuleError("La ventana programada ya terminó; programe una hora de finalización futura.")
        if caso.tiempo_estimado_min is None or tiempo_max > caso.tiempo_estimado_min:
            raise BusinessRuleError(
                f"El tiempo máximo ({tiempo_max} min) no puede superar el tiempo estimado del caso "
                f"({caso.tiempo_estimado_min} min)."
            )


class ConvocatoriaService(_ConvocatoriasMixin):
    def agregar(self, docente: Usuario, sesion_id: uuid.UUID, correos: list[str]) -> list[Convocatoria]:
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).asegurar_nuevas_convocatorias(self._reloj.ahora())
            creadas = self._convocar(sesion, correos)
        return [self._convocatoria(sesion_id, c.id) for c in creadas]

    def listar(self, docente: Usuario, sesion_id: uuid.UUID, *, incluir_revocadas: bool = False) -> list[Convocatoria]:
        self._sesion_lectura(sesion_id, docente)
        return self._convocatorias.list_by_sesion(sesion_id, incluir_revocadas=incluir_revocadas)

    def obtener(self, docente: Usuario, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> Convocatoria:
        self._sesion_lectura(sesion_id, docente)
        return self._convocatoria(sesion_id, convocatoria_id)

    def retirar(self, docente: Usuario, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> None:
        """Revocación LÓGICA: fija `revocada_en`; el estudiante pierde de inmediato el acceso.

        La fila se conserva: las participaciones del módulo 4 (FK con RESTRICT) y el
        historial académico siguen referenciándola.
        """
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).asegurar_convocatorias_editables()
            convocatoria = self._convocatoria_activa(sesion_id, convocatoria_id)
            convocatoria.revocada_en = self._reloj.ahora()
            self._emitir(eventos.evento_convocatoria(eventos.CONVOCATORIA_RETIRADA, sesion.id, convocatoria_id))

    def modificar_intentos(
        self, docente: Usuario, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID, intentos: int, motivo: str
    ) -> Convocatoria:
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).asegurar_convocatorias_editables()
            convocatoria = self._convocatoria_activa(sesion_id, convocatoria_id)
            if intentos <= convocatoria.intentos_autorizados:
                raise BusinessRuleError(
                    f"Solo se pueden aumentar los intentos autorizados (actualmente {convocatoria.intentos_autorizados})."
                )
            anterior, convocatoria.intentos_autorizados = convocatoria.intentos_autorizados, intentos
            logger.info("intentos convocatoria=%s %s->%s motivo=%r", convocatoria_id, anterior, intentos, motivo)
            self._emitir(
                eventos.evento_convocatoria(
                    eventos.INTENTOS_MODIFICADOS, sesion.id, convocatoria_id, intentos=str(intentos), motivo=motivo
                )
            )
        return convocatoria

    def regenerar_codigo(
        self, docente: Usuario, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID, motivo: str
    ) -> CodigoConvocatoriaRead:
        """Nuevo código: el anterior deja de servir para ingresar y revoca los tokens emitidos con él."""
        with self._transaction():
            sesion = self._sesion_bloqueada(sesion_id, docente)
            CicloVidaSesion(sesion).asegurar_convocatorias_editables()
            convocatoria = self._convocatoria_activa(sesion_id, convocatoria_id)
            self._convocatorias.bloquear_codigos_de_estudiantes([convocatoria.estudiante_id])
            nuevo = self._codigo_unico(convocatoria.estudiante_id)
            convocatoria.codigo_autorizacion = cifrar_codigo(nuevo, convocatoria.id)
            logger.info("codigo regenerado convocatoria=%s motivo=%r", convocatoria_id, motivo)  # nunca el código
            self._emitir(eventos.evento_convocatoria(eventos.CODIGO_REGENERADO, sesion.id, convocatoria_id, motivo=motivo))
        return CodigoConvocatoriaRead(convocatoria_id=convocatoria.id, codigo_autorizacion=nuevo)

    def obtener_codigo(self, docente: Usuario, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> CodigoConvocatoriaRead:
        """Entrega controlada del código al docente responsable (hasta que el módulo 6 lo envíe por correo)."""
        if not get_settings().CONVOCATION_CODE_RETRIEVAL_ENABLED:
            raise PermissionDeniedError("La consulta de códigos está deshabilitada en este entorno.")
        self._sesion_lectura(sesion_id, docente)
        convocatoria = self._convocatoria_activa(sesion_id, convocatoria_id)
        logger.info("codigo consultado convocatoria=%s docente=%s", convocatoria.id, docente.id)
        return CodigoConvocatoriaRead(convocatoria_id=convocatoria.id, codigo_autorizacion=self._codigo_en_claro(convocatoria))

    def _convocatoria(self, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> Convocatoria:
        convocatoria = self._convocatorias.get(sesion_id, convocatoria_id)
        if convocatoria is None:
            raise NotFoundError("Convocatoria no encontrada en esta sesión.")
        return convocatoria

    def _convocatoria_activa(self, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> Convocatoria:
        convocatoria = self._convocatoria(sesion_id, convocatoria_id)
        if not convocatoria.activa:
            raise ConflictError("La convocatoria está revocada; vuelva a convocar al estudiante para reactivarla.")
        return convocatoria
