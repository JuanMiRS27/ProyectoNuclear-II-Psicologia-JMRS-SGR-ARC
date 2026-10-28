import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import TipoNotificacion
from app.core.tipos import UUIDPk, enum_pg


class Notificacion(Base):
    __tablename__ = "notificacion"
    __table_args__ = (
        # Se origina en una convocatoria o en una participación, nunca en ambas
        CheckConstraint(
            "(convocatoria_id IS NOT NULL) <> (participacion_id IS NOT NULL)",
            name="ck_notif_origen",
        ),
        CheckConstraint(
            "(tipo IN ('CONVOCATORIA', 'CAMBIO_ESTUDIANTES') AND convocatoria_id IS NOT NULL)"
            " OR (tipo IN ('CALIFICACION', 'RETROALIMENTACION') AND participacion_id IS NOT NULL)",
            name="ck_notif_tipo",
        ),
    )

    id: Mapped[UUIDPk]
    tipo: Mapped[TipoNotificacion] = mapped_column(
        enum_pg(TipoNotificacion, "tipo_notificacion")
    )
    convocatoria_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("convocatoria.id")
    )
    participacion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participacion.id")
    )
    asunto: Mapped[str] = mapped_column(String(200))
    cuerpo: Mapped[str] = mapped_column(Text)
    enviada_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
