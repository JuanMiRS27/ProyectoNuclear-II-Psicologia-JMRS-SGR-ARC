"""Registro, modificación y eliminación de respuestas de un intento EN_PROGRESO."""

import uuid

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.participations.policies import temporizador
from app.modules.participations.schemas import RespuestaRead
from app.modules.participations.services.base import Contexto, ServicioParticipacionesBase
from app.modules.sessions.services import AccesoEstudiantil

TIEMPO_AGOTADO = "El tiempo de la participación terminó; ya no se aceptan cambios."


class RespuestaService(ServicioParticipacionesBase):
    def guardar(
        self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID, pregunta_id: uuid.UUID, opcion_id: uuid.UUID
    ) -> RespuestaRead:
        """Crea o actualiza la respuesta de la pregunta (una sola fila; reintentos sin duplicados)."""

        def operar(ctx: Contexto, ahora) -> RespuestaRead:
            if self._respuestas.pregunta_del_caso(pregunta_id, ctx.sesion.caso_id) is None:
                raise NotFoundError("La pregunta no pertenece a este cuestionario.")
            if self._respuestas.opcion_de_pregunta(opcion_id, pregunta_id) is None:
                raise BusinessRuleError("La opción no pertenece a la pregunta.")
            self._respuestas.guardar(ctx.participacion.id, pregunta_id, opcion_id, ahora)
            guardada = self._respuestas.get(ctx.participacion.id, pregunta_id)
            return RespuestaRead(
                pregunta_id=guardada.pregunta_id,
                opcion_id=guardada.opcion_id,
                respondida_en=guardada.respondida_en,
                actualizada_en=guardada.actualizada_en,
            )

        return self._en_intento_abierto(acceso, participacion_id, operar)

    def eliminar(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID, pregunta_id: uuid.UUID) -> None:
        """Deja la pregunta sin responder. Idempotente: si no había respuesta, no hace nada."""

        def operar(ctx: Contexto, ahora) -> None:
            if self._respuestas.pregunta_del_caso(pregunta_id, ctx.sesion.caso_id) is None:
                raise NotFoundError("La pregunta no pertenece a este cuestionario.")
            self._respuestas.eliminar(ctx.participacion.id, pregunta_id)

        self._en_intento_abierto(acceso, participacion_id, operar)

    def listar(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID) -> list[RespuestaRead]:
        with self._transaction():
            ctx = self._bloquear(acceso, participacion_id)
            if ctx.participacion.activa and temporizador.vencida(ctx.participacion, ctx.sesion.tiempo_max_min, ctx.ahora):
                temporizador.expirar_por_tiempo(ctx.participacion, ctx.sesion.tiempo_max_min)
            respuestas = self._respuestas.de_participacion(ctx.participacion.id)
            return [
                RespuestaRead(pregunta_id=r.pregunta_id, opcion_id=r.opcion_id, respondida_en=r.respondida_en, actualizada_en=r.actualizada_en)
                for r in respuestas
            ]

    def _en_intento_abierto(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID, operar):
        """Ejecuta `operar` solo si el intento sigue EN_PROGRESO y con tiempo.

        Si `ahora >= fecha_limite`, primero se expira el intento (y se confirma) y después se
        rechaza la operación: así el intento queda EXPIRADA aunque nadie más lo detecte.
        El vencimiento se comprueba con la hora leída tras los bloqueos (`ctx.ahora`).
        """
        expirada = False
        with self._transaction():
            ctx = self._bloquear(acceso, participacion_id)
            participacion = ctx.participacion
            if not participacion.activa:
                raise ConflictError(f"La participación está {participacion.estado.value} y no admite cambios.")
            if temporizador.vencida(participacion, ctx.sesion.tiempo_max_min, ctx.ahora):
                temporizador.expirar_por_tiempo(participacion, ctx.sesion.tiempo_max_min)
                expirada = True
                resultado = None
            else:
                resultado = operar(ctx, ctx.ahora)
        if expirada:
            raise ConflictError(TIEMPO_AGOTADO)
        return resultado
