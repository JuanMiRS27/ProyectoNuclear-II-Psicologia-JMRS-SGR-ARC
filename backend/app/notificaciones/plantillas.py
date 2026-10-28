"""Textos de los correos. Cada función devuelve (asunto, cuerpo)."""

from datetime import datetime
from decimal import Decimal


def _fecha(valor: datetime) -> str:
    return valor.strftime("%d/%m/%Y %H:%M")


def convocatoria(
    *,
    estudiante: str,
    caso: str,
    espacio: str,
    inicio: datetime,
    fin: datetime,
    tiempo_max_min: int,
    lugar: str | None,
    mensaje: str | None,
    codigo: str,
    intentos: int,
    es_cambio: bool = False,
) -> tuple[str, str]:
    asunto = (
        f"Cambios en tu convocatoria: {caso}" if es_cambio else f"Convocatoria: {caso}"
    )
    lineas = [
        f"Hola, {estudiante}:",
        "",
        "Se actualizó la programación de la que haces parte."
        if es_cambio
        else "Has sido convocado(a) a resolver un caso de estudio en el simulador.",
        "",
        f"Caso: {caso}",
        f"Espacio académico: {espacio}",
        f"Ventana de inicio: {_fecha(inicio)} a {_fecha(fin)}",
        f"Tiempo máximo de resolución: {tiempo_max_min} minutos",
    ]
    if lugar:
        lineas.append(f"Lugar: {lugar}")
    lineas += [
        "",
        f"Tu código de autorización: {codigo}",
        f"Intentos autorizados: {intentos}",
        "",
        "Ingresa con tu correo institucional y este código. Si llegas después de la",
        "mitad de la ventana de inicio no podrás ingresar.",
    ]
    if mensaje:
        lineas += ["", "Mensaje del docente:", mensaje]
    return asunto, "\n".join(lineas)


def calificacion(
    *, estudiante: str, caso: str, nota: Decimal, escala_max: Decimal | None
) -> tuple[str, str]:
    nota_texto = f"{nota} / {escala_max}" if escala_max is not None else f"{nota}"
    cuerpo = "\n".join(
        [
            f"Hola, {estudiante}:",
            "",
            f"Ya está disponible tu calificación del caso «{caso}».",
            "",
            f"Nota: {nota_texto}",
            "",
            "Ingresa al simulador para ver tus respuestas y la retroalimentación por pregunta.",
        ]
    )
    return f"Calificación disponible: {caso}", cuerpo


def retroalimentacion(*, estudiante: str, caso: str, texto: str) -> tuple[str, str]:
    cuerpo = "\n".join(
        [
            f"Hola, {estudiante}:",
            "",
            f"Tu docente registró retroalimentación sobre tu participación en «{caso}»:",
            "",
            texto,
        ]
    )
    return f"Nueva retroalimentación: {caso}", cuerpo
