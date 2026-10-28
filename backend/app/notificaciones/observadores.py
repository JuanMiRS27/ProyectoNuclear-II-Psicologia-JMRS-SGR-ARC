"""Observadores concretos (patrón Observer).

Cada correo enviado con éxito queda registrado en la tabla notificacion como
evidencia del despacho. Si el envío a un estudiante falla, se registra en el
log y se sigue con los demás; esa notificación no se guarda.
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.casos.models import CasoEstudio, Rubrica
from app.core.enums import TipoNotificacion
from app.notificaciones import plantillas
from app.notificaciones.correo import ErrorEnvioCorreo, ServicioCorreo
from app.notificaciones.eventos import EventoAcademico, ObservadorEvento, TipoEvento
from app.notificaciones.models import Notificacion
from app.participacion.models import Participacion
from app.sesiones.models import Convocatoria, Sesion
from app.usuarios.models import EspacioAcademico, Usuario

logger = logging.getLogger(__name__)

FabricaSesion = Callable[[], Session]


class _ObservadorConCorreo(ObservadorEvento):
    def __init__(self, fabrica_sesion: FabricaSesion, correo: ServicioCorreo) -> None:
        self._fabrica_sesion = fabrica_sesion
        self._correo = correo

    def _enviar_y_registrar(
        self,
        sesion: Session,
        *,
        destinatario: str,
        asunto: str,
        cuerpo: str,
        tipo: TipoNotificacion,
        convocatoria_id: uuid.UUID | None = None,
        participacion_id: uuid.UUID | None = None,
    ) -> bool:
        try:
            self._correo.enviar(destinatario, asunto, cuerpo)
        except ErrorEnvioCorreo:
            logger.exception("No se pudo notificar a %s (%s)", destinatario, tipo.value)
            return False
        sesion.add(
            Notificacion(
                tipo=tipo,
                convocatoria_id=convocatoria_id,
                participacion_id=participacion_id,
                asunto=asunto,
                cuerpo=cuerpo,
            )
        )
        sesion.commit()
        return True


class ObservadorConvocatorias(_ObservadorConCorreo):
    """Avisa a los estudiantes cuando se programa una sesión o cambia la lista."""

    def actualizar(self, evento: EventoAcademico) -> None:
        es_cambio = evento.tipo == TipoEvento.ESTUDIANTES_MODIFICADOS
        with self._fabrica_sesion() as sesion:
            fila = sesion.execute(
                select(Sesion, CasoEstudio.nombre, EspacioAcademico.nombre)
                .join(CasoEstudio, Sesion.caso_id == CasoEstudio.id)
                .join(EspacioAcademico, Sesion.espacio_academico_id == EspacioAcademico.id)
                .where(Sesion.id == evento.entidad_id)
            ).first()
            if fila is None:
                logger.warning("Sesión %s no encontrada", evento.entidad_id)
                return
            sesion_academica, nombre_caso, nombre_espacio = fila

            consulta = (
                select(Convocatoria, Usuario)
                .join(Usuario, Convocatoria.estudiante_id == Usuario.id)
                .where(Convocatoria.sesion_id == sesion_academica.id)
            )
            if es_cambio:
                ids = [uuid.UUID(str(i)) for i in evento.datos.get("convocatoria_ids", [])]
                consulta = consulta.where(Convocatoria.id.in_(ids))

            for convocatoria, estudiante in sesion.execute(consulta).all():
                asunto, cuerpo = plantillas.convocatoria(
                    estudiante=estudiante.nombre_completo,
                    caso=nombre_caso,
                    espacio=nombre_espacio,
                    inicio=sesion_academica.inicio,
                    fin=sesion_academica.fin,
                    tiempo_max_min=sesion_academica.tiempo_max_min,
                    lugar=sesion_academica.lugar,
                    mensaje=sesion_academica.mensaje,
                    codigo=convocatoria.codigo_autorizacion,
                    intentos=convocatoria.intentos_autorizados,
                    es_cambio=es_cambio,
                )
                self._enviar_y_registrar(
                    sesion,
                    destinatario=estudiante.correo,
                    asunto=asunto,
                    cuerpo=cuerpo,
                    tipo=TipoNotificacion.CAMBIO_ESTUDIANTES
                    if es_cambio
                    else TipoNotificacion.CONVOCATORIA,
                    convocatoria_id=convocatoria.id,
                )


class ObservadorAvisosAcademicos(_ObservadorConCorreo):
    """Avisa al estudiante cuando se registra su calificación o retroalimentación."""

    def actualizar(self, evento: EventoAcademico) -> None:
        with self._fabrica_sesion() as sesion:
            fila = sesion.execute(
                select(Participacion, Usuario, CasoEstudio.nombre, Rubrica.escala_max)
                .join(Convocatoria, Participacion.convocatoria_id == Convocatoria.id)
                .join(Usuario, Convocatoria.estudiante_id == Usuario.id)
                .join(Sesion, Convocatoria.sesion_id == Sesion.id)
                .join(CasoEstudio, Sesion.caso_id == CasoEstudio.id)
                .outerjoin(Rubrica, Rubrica.caso_id == CasoEstudio.id)
                .where(Participacion.id == evento.entidad_id)
            ).first()
            if fila is None:
                logger.warning("Participación %s no encontrada", evento.entidad_id)
                return
            participacion, estudiante, nombre_caso, escala_max = fila

            if evento.tipo == TipoEvento.CALIFICACION_REGISTRADA:
                if participacion.nota_final is None:
                    logger.warning("Participación %s sin nota", participacion.id)
                    return
                asunto, cuerpo = plantillas.calificacion(
                    estudiante=estudiante.nombre_completo,
                    caso=nombre_caso,
                    nota=participacion.nota_final,
                    escala_max=escala_max,
                )
                tipo = TipoNotificacion.CALIFICACION
            else:
                if not participacion.retroalimentacion_general:
                    logger.warning("Participación %s sin retroalimentación", participacion.id)
                    return
                asunto, cuerpo = plantillas.retroalimentacion(
                    estudiante=estudiante.nombre_completo,
                    caso=nombre_caso,
                    texto=participacion.retroalimentacion_general,
                )
                tipo = TipoNotificacion.RETROALIMENTACION

            self._enviar_y_registrar(
                sesion,
                destinatario=estudiante.correo,
                asunto=asunto,
                cuerpo=cuerpo,
                tipo=tipo,
                participacion_id=participacion.id,
            )


def registrar_observadores(
    publicador, fabrica_sesion: FabricaSesion, correo: ServicioCorreo
) -> None:
    """Conecta los observadores con los eventos. Se llama al arrancar la app."""
    convocatorias = ObservadorConvocatorias(fabrica_sesion, correo)
    avisos = ObservadorAvisosAcademicos(fabrica_sesion, correo)
    publicador.suscribir(TipoEvento.SESION_PROGRAMADA, convocatorias)
    publicador.suscribir(TipoEvento.ESTUDIANTES_MODIFICADOS, convocatorias)
    publicador.suscribir(TipoEvento.CALIFICACION_REGISTRADA, avisos)
    publicador.suscribir(TipoEvento.RETROALIMENTACION_REGISTRADA, avisos)
