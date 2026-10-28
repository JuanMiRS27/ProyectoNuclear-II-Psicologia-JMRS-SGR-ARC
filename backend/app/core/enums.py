import enum


class RolUsuario(str, enum.Enum):
    ADMINISTRADOR = "ADMINISTRADOR"
    DOCENTE = "DOCENTE"
    ESTUDIANTE = "ESTUDIANTE"


class EstadoCaso(str, enum.Enum):
    BORRADOR = "BORRADOR"
    PUBLICADO = "PUBLICADO"
    ARCHIVADO = "ARCHIVADO"


class EstadoSesion(str, enum.Enum):
    SIN_INICIAR = "SIN_INICIAR"
    EN_CURSO = "EN_CURSO"
    FINALIZADO = "FINALIZADO"


class EstadoParticipacion(str, enum.Enum):
    EN_PROGRESO = "EN_PROGRESO"
    FINALIZADA = "FINALIZADA"
    EXPIRADA = "EXPIRADA"
    AUSENTE = "AUSENTE"


class TipoNotificacion(str, enum.Enum):
    CONVOCATORIA = "CONVOCATORIA"
    CAMBIO_ESTUDIANTES = "CAMBIO_ESTUDIANTES"
    CALIFICACION = "CALIFICACION"
    RETROALIMENTACION = "RETROALIMENTACION"
