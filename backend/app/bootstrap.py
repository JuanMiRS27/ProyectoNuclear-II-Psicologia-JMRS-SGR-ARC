"""Raíz de composición: conecta los puertos entre módulos sin dependencias circulares.

Se invoca al crear la aplicación (`app.main.create_app`) y al arrancar cada worker, de
modo que las reglas transversales (cerrar participaciones al revocar una convocatoria o
finalizar una sesión) se cumplen tanto en peticiones HTTP como en procesos programados.
"""

from app.modules.participations.policies.integracion_sesiones import CierreParticipacionesSQL, ReingresoConParticipacionActiva
from app.modules.sessions import integracion as integracion_sesiones


def configurar_integraciones() -> None:
    integracion_sesiones.configurar(
        cierre=CierreParticipacionesSQL(),
        fabrica_reingreso=ReingresoConParticipacionActiva,
    )
