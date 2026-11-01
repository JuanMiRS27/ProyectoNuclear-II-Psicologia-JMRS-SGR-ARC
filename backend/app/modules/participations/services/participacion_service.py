"""Inicio, recuperación, consulta, cuestionario y finalización de participaciones."""

import uuid
from datetime import datetime

from app.core.exceptions import ConflictError
from app.modules.participations.models import EstadoParticipacionEnum, Participacion
from app.modules.participations.policies import temporizador
from app.modules.participations.schemas import (
    CasoCuestionario,
    CuestionarioRead,
    EscenarioCuestionario,
    OpcionCuestionario,
    ParticipacionRead,
    PreguntaCuestionario,
)
from app.modules.participations.services.base import Contexto, ServicioParticipacionesBase
from app.modules.sessions.services import AccesoEstudiantil


class ParticipacionService(ServicioParticipacionesBase):
    # --- Inicio o recuperación ---------------------------------------------------------------

    def iniciar_o_recuperar(self, acceso: AccesoEstudiantil) -> tuple[ParticipacionRead, bool]:
        """Devuelve (participación, creada).

        - Si hay un intento EN_PROGRESO con tiempo, se devuelve ese mismo (creada=False):
          recuperarlo no consume otro intento ni reinicia el temporizador, y se permite
          aunque la ventana de ingreso ya haya cerrado.
        - Si no lo hay, se crea uno nuevo (creada=True) solo si la ventana de ingreso está
          abierta (`asegurar_inicio_participacion`, módulo 3) y quedan intentos.

        La convocatoria se bloquea en exclusiva: dos inicios simultáneos se ejecutan en
        serie y el segundo recupera el intento creado por el primero.
        """
        # Fase 1: un intento activo pero vencido se cierra y se confirma, aunque después
        # no se pueda iniciar otro (p. ej., ventana cerrada).
        self._expirar_activa_vencida(acceso)

        with self._transaction():
            ctx = self._bloquear(acceso, convocatoria_exclusiva=True)
            ahora = ctx.ahora
            activa = self._participaciones.bloquear_activa(ctx.convocatoria.id)
            if activa is not None:
                return self._leer(activa, ctx.sesion, ctx.convocatoria, ahora), False

            # Mismas reglas que un ingreso nuevo: sesión EN_CURSO e inicio <= ahora <= límite de ingreso.
            acceso.asegurar_inicio_participacion(ahora)
            usados = self._participaciones.intentos_utilizados(ctx.convocatoria.id)
            if usados >= ctx.convocatoria.intentos_autorizados:
                raise ConflictError(
                    f"Se agotaron los intentos autorizados ({usados} de {ctx.convocatoria.intentos_autorizados})."
                )
            participacion = Participacion(
                convocatoria_id=ctx.convocatoria.id,
                numero_intento=usados + 1,
                inicio=ahora,  # reloj del servidor; nunca del cliente
                estado=EstadoParticipacionEnum.EN_PROGRESO,
            )
            self._participaciones.add(participacion)
            vista = self._leer(participacion, ctx.sesion, ctx.convocatoria, ahora)
        return vista, True

    def _expirar_activa_vencida(self, acceso: AccesoEstudiantil) -> None:
        with self._transaction():
            ctx = self._bloquear(acceso, convocatoria_exclusiva=True)
            activa = self._participaciones.bloquear_activa(ctx.convocatoria.id)
            if activa is not None and temporizador.vencida(activa, ctx.sesion.tiempo_max_min, ctx.ahora):
                temporizador.expirar_por_tiempo(activa, ctx.sesion.tiempo_max_min)

    # --- Consulta -------------------------------------------------------------------------------

    def listar(self, acceso: AccesoEstudiantil) -> list[ParticipacionRead]:
        """Intentos de la convocatoria del token (el alcance del token es una convocatoria)."""
        self._expirar_activa_vencida(acceso)
        ahora = self._reloj.ahora()
        return [
            self._leer(p, acceso.sesion, acceso.convocatoria, ahora)
            for p in self._participaciones.de_convocatoria(acceso.convocatoria.id)
        ]

    def obtener(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID) -> ParticipacionRead:
        ctx, ahora = self._vigente(acceso, participacion_id)
        return self._leer(ctx.participacion, ctx.sesion, ctx.convocatoria, ahora)

    def cuestionario(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID) -> CuestionarioRead:
        """Proyección explícita del caso para el estudiante: sin respuestas correctas ni justificaciones."""
        ctx, ahora = self._vigente(acceso, participacion_id)
        participacion, sesion = ctx.participacion, ctx.sesion
        caso = self._participaciones.caso_con_contenido(sesion.caso_id)
        seleccion = {r.pregunta_id: r.opcion_id for r in self._respuestas.de_participacion(participacion.id)}
        return CuestionarioRead(
            participacion_id=participacion.id,
            estado=participacion.estado,
            fecha_limite=temporizador.fecha_limite(participacion, sesion.tiempo_max_min),
            tiempo_restante_segundos=temporizador.tiempo_restante_segundos(participacion, sesion.tiempo_max_min, ahora),
            caso=CasoCuestionario(
                nombre=caso.nombre, desarrollo_situacional=caso.desarrollo_situacional, historia=caso.historia
            ),
            escenarios=[
                EscenarioCuestionario(
                    id=escenario.id,
                    orden=escenario.orden,
                    titulo=escenario.titulo,
                    contexto=escenario.contexto,
                    preguntas=[
                        PreguntaCuestionario(
                            id=pregunta.id,
                            orden=pregunta.orden,
                            enunciado=pregunta.enunciado,
                            opcion_seleccionada_id=seleccion.get(pregunta.id),
                            opciones=[OpcionCuestionario(id=o.id, orden=o.orden, texto=o.texto) for o in pregunta.opciones],
                        )
                        for pregunta in escenario.preguntas
                    ],
                )
                for escenario in caso.escenarios
            ],
            progreso=self._progreso(participacion, sesion.caso_id),
        )

    # --- Finalización ----------------------------------------------------------------------------

    def finalizar(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID) -> ParticipacionRead:
        """Idempotente: si ya está cerrada, devuelve su estado sin cambiarlo.

        Si el tiempo venció antes de procesar la solicitud, queda EXPIRADA (TIEMPO_AGOTADO).
        No exige haber respondido todas las preguntas.
        """
        with self._transaction():
            ctx = self._bloquear(acceso, participacion_id)
            temporizador.finalizar_voluntariamente(ctx.participacion, ctx.sesion.tiempo_max_min, ctx.ahora)
            self._participaciones.flush()
            vista = self._leer(ctx.participacion, ctx.sesion, ctx.convocatoria, ctx.ahora)
        return vista

    def _vigente(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID) -> tuple[Contexto, datetime]:
        """Bloquea la participación y, si su tiempo terminó, la cierra (y lo confirma) antes de responder."""
        with self._transaction():
            ctx = self._bloquear(acceso, participacion_id)
            if ctx.participacion.activa and temporizador.vencida(ctx.participacion, ctx.sesion.tiempo_max_min, ctx.ahora):
                temporizador.expirar_por_tiempo(ctx.participacion, ctx.sesion.tiempo_max_min)
        return ctx, ctx.ahora
