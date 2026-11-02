# Reporte de pruebas — Módulos 1 a 4

**Proyecto:** Proyecto Nuclear II — Inter Nos, simulador académico de casos de psicología social
**Ejecución registrada:** 1 de noviembre de 2026, rama `main`, sobre el commit `cb5c2c9` más los cambios de esta entrega, aún sin confirmar
**Evidencias:** carpeta [`docs/evidencias/`](evidencias/)
**Rutas:** el código está en `backend/`; las rutas `app/...` y `tests/...` de este reporte son relativas a esa carpeta.

| Resumen | Valor |
|---|---|
| Pruebas ejecutadas | **1343** |
| Aprobadas | **1343** |
| Falladas | **0** |
| Omitidas | **0** |
| Cobertura de `app/` (líneas y ramas) | **99,82 %** (Coverage la muestra redondeada como 99 %) |
| Defectos reales encontrados | **5** (todos corregidos) |

## A. Introducción

Estas pruebas verifican que los cuatro módulos desarrollados cumplen sus reglas de negocio, rechazan las entradas incorrectas y colaboran entre sí sobre la base de datos real. Son la evidencia técnica de la entrega que representa el 50 % del desarrollo del proyecto.

El repositorio ya tenía 458 pruebas sólidas, pero todas dependían de PostgreSQL y no estaban separadas por tipo. Esta entrega las conserva sin modificarlas y añade 885: 680 unitarias realmente aisladas y 205 de integración centradas en contratos HTTP, permisos y flujos entre módulos.

### Alcance real frente a lo planificado

Los nombres de los módulos del plan de pruebas eran orientativos. Su correspondencia con el código es la siguiente:

| Módulo | Paquete real | Observación |
|---|---|---|
| 1. Usuarios y espacios | `app/modules/auth` | Los espacios académicos están en `app/modules/sessions`; sus pruebas se ubicaron con el módulo 1 para respetar la organización académica. |
| 2. Casos y contenido | `app/modules/cases` | Completo. |
| 3. Sesiones y convocatorias | `app/modules/sessions` | Completo. |
| 4. Participaciones y respuestas | `app/modules/participations` | Cubre registro, respuestas y cierre. |

**Funcionalidades que aún no existen y, por tanto, no se probaron:**

- **Cálculo de notas y generación de retroalimentación.** Pertenecen al módulo 5. Las columnas `nota_final` y `retroalimentacion_general` están reservadas y no hay rutas de calificación. Las pruebas confirman que el módulo 4 no inventa esos valores y que entrega los datos que el módulo 5 necesitará (`ConsultaEvaluacionService`).
- **Patrones Observer y Strategy.** No están implementados (ver sección H).
- **Autorregistro de usuarios.** Las cuentas las crea el administrador; no hay registro público.

## B. Herramientas utilizadas

| Herramienta | Versión | Uso |
|---|---|---|
| Python | 3.14.8 | Entorno de ejecución |
| Pytest | 9.2.0 | Motor de pruebas, fixtures y `parametrize` |
| pytest-cov / Coverage | 7.1.0 / 7.17.0 | Cobertura de líneas y ramas |
| FastAPI TestClient (httpx 0.28.1) | FastAPI 0.162.0 | Peticiones HTTP a la aplicación real |
| SQLAlchemy / Alembic | 2.1.6 / 1.20.1 | Persistencia y creación del esquema por migraciones |
| PostgreSQL (Docker) | 17.11, imagen `postgres:17-alpine` | Base de pruebas desechable en memoria |
| `unittest.mock` | Biblioteca estándar | Dobles de repositorios, sesiones y puertos |
| pytest-randomly | Solo para una verificación puntual | Orden aleatorio; se desinstaló y no es dependencia del proyecto |

## C. Estrategia de pruebas

| Capa | Ubicación | Qué verifica | Dependencias | Pruebas |
|---|---|---|---|---:|
| Unitaria | `tests/unit/` | Una regla o clase aislada | Dobles de prueba, sin base de datos | 680 |
| Integración | `tests/integration/` | Endpoints, servicios y persistencia juntos | PostgreSQL real | 205 |
| Regresión preexistente | `tests/test_*.py` | API, migraciones, disparadores y concurrencia | PostgreSQL real | 458 |

**Aislamiento de las unitarias.** `tests/unit/conftest.py` sustituye las fixtures de migración y limpieza, y hace fallar cualquier intento de conexión. No es solo una convención: la suite unitaria se ejecutó apuntando a un puerto sin servidor y dio el mismo resultado (`07_unitarias_sin_postgresql.txt`).

**Preparación del entorno de integración.** Al iniciar, la suite ejecuta `alembic downgrade base` y `upgrade head`, de modo que el esquema real, con sus disparadores, se crea desde las migraciones. Después de cada prueba se vacían las tablas y el limitador de intentos. El reloj se sustituye por uno controlable, así que no hay esperas ni dependencia de la hora real.

**Seguridad de los datos.** Se usó únicamente la base `simulador_psicologia_test`, en un contenedor con almacenamiento en memoria y accesible solo desde `127.0.0.1`. `tests/db_guard.py` impide ejecutar la suite sobre bases que no sean de prueba. No se tocó ninguna base de desarrollo o producción.

**SQLite no se usó.** El proyecto lo prohíbe, con razón: disparadores, `FOR UPDATE SKIP LOCKED`, bloqueos consultivos, restricciones diferibles, JSONB e índices parciales no se reproducen fielmente en él.

## D. Pruebas por módulo

Selección representativa de 69 casos. **Las columnas «Resultado obtenido» y «Estado» se generaron automáticamente desde `docs/evidencias/resultados.xml`**, y la generación se detiene si un caso citado no se ejecutó. El detalle de cada prueba está en los archivos `03_*`, `04_*`, `05_*` y `06_*`.

| ID | Módulo | Funcionalidad | Tipo de prueba | Caso evaluado | Resultado esperado | Resultado obtenido | Estado |
|---|---|---|---|---|---|---|---|
| PR-001 | M1 | Registro | Unitaria | Clave de 8 caracteres (límite inferior) | Se acepta | Conforme a lo esperado | Aprobada |
| PR-002 | M1 | Registro | Unitaria | Intento de enviar `rol` en el alta | ValidationError | Conforme a lo esperado | Aprobada |
| PR-003 | M1 | Autenticación | Unitaria | Login de estudiante con contraseña | Mismo error genérico de credenciales | Conforme a lo esperado | Aprobada |
| PR-004 | M1 | Autenticación | Unitaria | Hash con parámetros antiguos | Se actualiza y confirma al iniciar sesión | Conforme a lo esperado | Aprobada |
| PR-005 | M1 | Tokens | Unitaria | Token con `ver` booleano | InvalidTokenError | Conforme a lo esperado | Aprobada |
| PR-006 | M1 | Tokens | Unitaria | Token de versión antigua | AuthenticationError | Conforme a lo esperado | Aprobada |
| PR-007 | M1 | Permisos | Unitaria | Desactivar al último administrador | BusinessRuleError y rollback | Conforme a lo esperado | Aprobada |
| PR-008 | M1 | Duplicidad | Unitaria | Violación de `uq_usuario_correo` en el flush | ConflictError y rollback | Conforme a lo esperado | Aprobada |
| PR-009 | M1 | Script admin | Unitaria | Claves distintas en modo interactivo | Termina sin crear la cuenta | Conforme a lo esperado | Aprobada |
| PR-010 | M1 | Espacios | Unitaria | Docente pide espacios inactivos | Solo recibe activos | Conforme a lo esperado | Aprobada |
| PR-011 | M1 | Roles por API | Integración | Docente en `GET /users` | HTTP 403 | Conforme a lo esperado | Aprobada |
| PR-012 | M1 | Sin token | Integración | Anónimo en `POST /users/teachers` | HTTP 401 | Conforme a lo esperado | Aprobada |
| PR-013 | M1 | Persistencia | Integración | Alta de docente | Fila con hash Argon2 y correo normalizado | Conforme a lo esperado | Aprobada |
| PR-014 | M1 | Contraseña | Integración | Cambio de clave de extremo a extremo | Clave vieja 401, nueva 200, versión 1 | Conforme a lo esperado | Aprobada |
| PR-015 | M1 | Integración M1-M2 | Integración | Conceder permiso de casos | Efecto inmediato en `POST /cases` | Conforme a lo esperado | Aprobada |
| PR-016 | M1 | Paginación | Integración | `skip=2^63` (DEF-02) | HTTP 422; `2^63-1` responde 200 | Conforme a lo esperado | Aprobada |
| PR-017 | M1 | Texto con NUL | Integración | Nombre de usuario con byte NUL (DEF-03) | HTTP 422 | Conforme a lo esperado | Aprobada |
| PR-018 | M2 | Casos | Unitaria | Tiempo estimado de 241 minutos | ValidationError | Conforme a lo esperado | Aprobada |
| PR-019 | M2 | Rúbrica | Unitaria | Peso 100.01 | ValidationError | Conforme a lo esperado | Aprobada |
| PR-020 | M2 | Opciones | Unitaria | Pregunta sin opción correcta | Código y ruta exactos del error | Conforme a lo esperado | Aprobada |
| PR-021 | M2 | Rúbrica | Unitaria | Pesos que suman 99.99 | `RUBRICA_PESOS_NO_SUMAN_100` | Conforme a lo esperado | Aprobada |
| PR-022 | M2 | Relaciones | Unitaria | Pregunta con criterio de otro caso | BusinessRuleError; no inserta | Conforme a lo esperado | Aprobada |
| PR-023 | M2 | Edición | Unitaria | Editar caso publicado | TransicionNoPermitidaError | Conforme a lo esperado | Aprobada |
| PR-024 | M2 | CaseDSL | Unitaria | Pregunta con criterio inexistente | ValidationError | Conforme a lo esperado | Aprobada |
| PR-025 | M2 | Decimales | Unitaria | Niveles en JSONB | `Decimal` exacto tras ida y vuelta | Conforme a lo esperado | Aprobada |
| PR-026 | M2 | Autoría | Integración | Construir por endpoints y publicar | Caso PUBLICADO | Conforme a lo esperado | Aprobada |
| PR-027 | M2 | Órdenes | Integración | Reordenar opciones | Orden invertido y persistido | Conforme a lo esperado | Aprobada |
| PR-028 | M2 | Órdenes | Integración | Orden ocupado | HTTP 409 por la restricción real | Conforme a lo esperado | Aprobada |
| PR-029 | M2 | Estados | Integración | PATCH de escenario en caso archivado | HTTP 409; filas sin cambios | Conforme a lo esperado | Aprobada |
| PR-030 | M2 | Aislamiento | Integración | Otro docente lee una pregunta | HTTP 404 | Conforme a lo esperado | Aprobada |
| PR-031 | M2 | CaseDSL | Integración | Exportar e importar para otro autor | Copia independiente en BORRADOR | Conforme a lo esperado | Aprobada |
| PR-032 | M3 | Fechas | Unitaria | Fecha sin zona horaria | ValidationError | Conforme a lo esperado | Aprobada |
| PR-033 | M3 | Fechas | Unitaria | Fecha no representable en UTC (DEF-04) | ValidationError | Conforme a lo esperado | Aprobada |
| PR-034 | M3 | Programación | Unitaria | `tiempo_max_min` mayor que el estimado | BusinessRuleError | Conforme a lo esperado | Aprobada |
| PR-035 | M3 | Convocatorias | Unitaria | Reconvocar a un estudiante retirado | Reactiva la fila con código nuevo | Conforme a lo esperado | Aprobada |
| PR-036 | M3 | Duplicados | Unitaria | Colisiones repetidas de código | ConflictError tras 5 intentos | Conforme a lo esperado | Aprobada |
| PR-037 | M3 | Seguridad | Unitaria | Cifrado de otra convocatoria | CodigoCifradoInvalidoError | Conforme a lo esperado | Aprobada |
| PR-038 | M3 | Limitador | Unitaria | Tres fallos con ventana de 60 s | Retry-After de 50 s a los 10 s | Conforme a lo esperado | Aprobada |
| PR-039 | M3 | Eventos | Unitaria | Fallo dentro de la transacción | No se publica ningún evento | Conforme a lo esperado | Aprobada |
| PR-040 | M3 | Acceso | Unitaria | Código con caracteres no ASCII (DEF-01) | AuthenticationError y fallo contabilizado | Conforme a lo esperado | Aprobada |
| PR-041 | M3 | Acceso | Unitaria | Token tras regenerar el código | AuthenticationError | Conforme a lo esperado | Aprobada |
| PR-042 | M3 | Limitador | Unitaria | Memoria con claves caducadas (DEF-05) | Se libera | Conforme a lo esperado | Aprobada |
| PR-043 | M3 | Sesiones | Integración | Cambiar la ventana | `limite_ingreso` recalculado | Conforme a lo esperado | Aprobada |
| PR-044 | M3 | Disparador | Integración | Cambiar tiempo con el caso archivado | HTTP 409 desde el disparador | Conforme a lo esperado | Aprobada |
| PR-045 | M3 | Lote | Integración | Lote con un estudiante ya convocado | HTTP 409; no inserta ninguno | Conforme a lo esperado | Aprobada |
| PR-046 | M3 | Código | Integración | Código dañado en la base | 409 y se recupera regenerando | Conforme a lo esperado | Aprobada |
| PR-047 | M3 | Acceso | Integración | Código no ASCII seis veces (DEF-01) | 401 cinco veces y luego 429 | Conforme a lo esperado | Aprobada |
| PR-048 | M3 | Acceso | Integración | Dos sesiones, dos códigos | Cada código abre su sesión | Conforme a lo esperado | Aprobada |
| PR-049 | M3 | Cierre | Integración | Worker tras el plazo de gracia | FINALIZADO e idempotente | Conforme a lo esperado | Aprobada |
| PR-050 | M4 | Temporizador | Unitaria | Instante exacto del límite | Vencida (`ahora >= límite`) | Conforme a lo esperado | Aprobada |
| PR-051 | M4 | Temporizador | Unitaria | Segundos restantes fraccionarios | Redondeo hacia arriba | Conforme a lo esperado | Aprobada |
| PR-052 | M4 | Cierre | Unitaria | Cierre administrativo tras agotar el tiempo | Prevalece TIEMPO_AGOTADO | Conforme a lo esperado | Aprobada |
| PR-053 | M4 | Registro | Unitaria | Intentos agotados (1 de 1) | ConflictError; no inserta | Conforme a lo esperado | Aprobada |
| PR-054 | M4 | Concurrencia | Unitaria | Orden de bloqueos y lectura del reloj | Sesión, convocatoria, participación y reloj | Conforme a lo esperado | Aprobada |
| PR-055 | M4 | Respuestas | Unitaria | Opción de otra pregunta | BusinessRuleError | Conforme a lo esperado | Aprobada |
| PR-056 | M4 | Respuestas | Unitaria | Guardar con tiempo agotado | Expira, confirma y rechaza | Conforme a lo esperado | Aprobada |
| PR-057 | M4 | Seguridad | Unitaria | Cuestionario sin datos del docente | Sin `es_correcta` ni criterios | Conforme a lo esperado | Aprobada |
| PR-058 | M4 | Evaluación | Unitaria | Cierre de un intento | `nota_final` sigue nula | Conforme a lo esperado | Aprobada |
| PR-059 | M4 | Inicio | Integración | Dos solicitudes de inicio | 201 y 200; un solo intento | Conforme a lo esperado | Aprobada |
| PR-060 | M4 | Respuestas | Integración | Cambiar de opción | Una fila; conserva `respondida_en` | Conforme a lo esperado | Aprobada |
| PR-061 | M4 | Respuestas | Integración | Reintento con la misma opción | `actualizada_en` no cambia | Conforme a lo esperado | Aprobada |
| PR-062 | M4 | Permisos | Integración | Finalizar intento de otro estudiante | HTTP 404; sigue EN_PROGRESO | Conforme a lo esperado | Aprobada |
| PR-063 | M4 | Cierre | Integración | Fin de sesión con intentos mixtos | Cada intento con su causa | Conforme a lo esperado | Aprobada |
| PR-064 | M4 | Evaluación | Integración | Rutas de calificación | No existen en la API | Conforme a lo esperado | Aprobada |
| PR-065 | M4 | Evaluación | Integración | Consulta para el módulo 5 | Respondidas, pendientes y ausencias correctas | Conforme a lo esperado | Aprobada |
| PR-066 | M1-4 | Flujo completo | Integración | Del alta de usuarios a los datos para evaluar | Coherencia entre los cuatro módulos | Conforme a lo esperado | Aprobada |
| PR-067 | M1-4 | Flujo completo | Integración | Desactivar cuenta durante un intento | Token 401 e ingreso 403 | Conforme a lo esperado | Aprobada |
| PR-068 | M1-4 | Flujo completo | Integración | Retirar y reconvocar | Conserva el conteo de intentos | Conforme a lo esperado | Aprobada |
| PR-069 | M1-4 | Flujo completo | Integración | Dos docentes | Aislamiento total | Conforme a lo esperado | Aprobada |

### Pruebas por archivo

| Archivo | Tipo | Pruebas |
|---|---|---:|
| `tests/unit/test_modulo_1.py` | Unitaria | 143 |
| `tests/unit/test_modulo_2.py` | Unitaria | 160 |
| `tests/unit/test_modulo_3.py` | Unitaria | 173 |
| `tests/unit/test_modulo_4.py` | Unitaria | 97 |
| `tests/unit/test_patrones.py` | Unitaria | 81 |
| `tests/unit/test_nucleo.py` | Unitaria | 26 |
| `tests/integration/test_modulo_1_api.py` | Integración | 65 |
| `tests/integration/test_modulo_2_api.py` | Integración | 60 |
| `tests/integration/test_modulo_3_api.py` | Integración | 41 |
| `tests/integration/test_modulo_4_api.py` | Integración | 33 |
| `tests/integration/test_flujo_completo.py` | Integración | 6 |
| `tests/test_*.py` (22 archivos preexistentes) | Regresión | 458 |

## E. Resultados

| Conjunto | Ejecutadas | Aprobadas | Falladas | Omitidas |
|---|---:|---:|---:|---:|
| Unitarias | 680 | 680 | 0 | 0 |
| Integración | 205 | 205 | 0 | 0 |
| Regresión preexistente | 458 | 458 | 0 | 0 |
| **Total** | **1343** | **1343** | **0** | **0** |

Duración total: 190,14 s con cobertura (`01_suite_completa_con_cobertura.txt`).

Durante el trabajo, DEF-03 y DEF-05 estuvieron registrados como `xfail` estricto: pruebas que describen el comportamiento correcto y fallan por un defecto conocido. Ambos se corrigieron, las marcas se retiraron y hoy no queda ningún `xfail`.

### Verificación de la calidad de la propia suite

La cobertura indica qué código se ejecutó, no si las pruebas detectarían un error. Por eso se hicieron tres comprobaciones adicionales:

| Comprobación | Método | Resultado | Evidencia |
|---|---|---|---|
| Mutación controlada | 22 reglas críticas alteradas una por una, ejecutando solo las pruebas unitarias | **22 de 22 detectadas** | `08_mutaciones_controladas.txt` |
| Independencia | Una ejecución de las pruebas nuevas en orden aleatorio | 885 aprobadas | `09_orden_aleatorio.txt` |
| Aislamiento | Unitarias sin servidor PostgreSQL | 680 aprobadas | `07_unitarias_sin_postgresql.txt` |

**Alcance de estas comprobaciones:** las mutaciones se eligieron manualmente, así que son una muestra dirigida y no un análisis exhaustivo. La ejecución aleatoria fue una sola, sobre las pruebas nuevas; no incluyó las 458 preexistentes.

## F. Cobertura

Medida con `--cov=app` y cobertura de ramas activada (`pyproject.toml`).

| Componente | Líneas | Ramas | Cobertura combinada |
|---|---:|---:|---:|
| Módulo 1 — `auth` | 368 / 370 | 54 / 54 | 99,53 % |
| Módulo 2 — `cases` | 1203 / 1204 | 184 / 184 | 99,93 % |
| Módulo 3 — `sessions` | 1102 / 1104 | 156 / 158 | 99,68 % |
| Módulo 4 — `participations` | 502 / 502 | 58 / 58 | 100 % |
| Núcleo y `main` | 184 / 184 | 8 / 8 | 100 % |
| Workers | 75 / 75 | 4 / 4 | 100 % |
| Script `create_admin` | 42 / 42 | 8 / 8 | 100 % |
| **Total** | **3476 / 3481** | **472 / 474** | **99,82 %** |

| Medición | Antes | Después |
|---|---:|---:|
| Cobertura de líneas | 96 % (130 líneas sin cubrir) | 99,86 % (5 líneas sin cubrir) |
| `app/scripts/create_admin.py` | 0 % | 100 % |

Solo con las pruebas unitarias, sin base de datos, la cobertura es del 90 % (`02_unitarias_con_cobertura.txt`). El resto corresponde sobre todo a repositorios, que deben ejercitarse con SQL real.

### Líneas sin cubrir

Son código sin uso o inalcanzable. No se escribieron pruebas artificiales para ellas; lo recomendable es eliminarlas o documentar su propósito.

| Archivo | Línea | Motivo |
|---|---:|---|
| `app/modules/auth/repositories/usuario_repository.py` | 20-21 | `get_by_id_for_update` no se usa en ningún lugar. |
| `app/modules/cases/repositories/contenido_repository.py` | 107 | `flush()` del repositorio sin llamadores. |
| `app/modules/sessions/repositories/__init__.py` | 187 | `por_correos` con lista vacía: `_convocar` ya retorna antes. |
| `app/modules/sessions/states/estados.py` | 72 | `autorizar_ingreso` base: los tres estados concretos la reemplazan. |
| `app/modules/sessions/integracion.py` | 50 (rama) | `configurar` nunca se invoca sin `fabrica_reingreso`. |

### Funcionalidades pendientes de verificar

- **Módulo 5:** notas y retroalimentación, cuando existan.
- **Módulo 6:** envío real de notificaciones; hoy solo existe el publicador nulo.
- **Despliegue con varios procesos:** el limitador vive en memoria y su conteo no se comparte entre workers. Es una limitación ya documentada en el código.
- **Carga y rendimiento:** fuera de este alcance.
- **Frontend:** este repositorio solo contiene el backend.

## G. Problemas detectados

Se encontraron cinco defectos reales que la suite anterior no detectaba. Los cuatro primeros permitían provocar HTTP 500 con datos enviados por el cliente. La salida previa a las correcciones está en `00_defectos_antes_de_corregir.txt` (9 fallas causadas por DEF-01, DEF-02 y DEF-04; DEF-03 y DEF-05 figuran ahí como `xfail`, y sus pruebas ampliadas también se vieron fallar antes de corregirlos).

| ID | Severidad | Defecto | Reproducción | Estado |
|---|---|---|---|---|
| DEF-01 | Media | `hmac.compare_digest` lanza `TypeError` con texto no ASCII. El ingreso responde 500 **y el intento no se cuenta**, de modo que esas peticiones no activan el limitador. No ayuda a adivinar códigos, porque un código válido siempre es ASCII. | `POST /auth/student/login` con el código `ÑÑÑÑÑÑÑÑÑÑÑÑ` y un correo convocado | **Corregido** |
| DEF-02 | Media | `skip` sin límite superior: PostgreSQL rechaza un `OFFSET` fuera de `bigint`. | `GET /users?skip=9223372036854775808` (también casos, sesiones y espacios) | **Corregido** |
| DEF-03 | Media | Un byte NUL en un texto llega a PostgreSQL, que lo rechaza con `sqlalchemy.exc.DataError`. | `POST /academic-spaces` con `{"nombre": "a\u0000b", "periodo": "2026-1"}`. Confirmado también en usuarios, casos y niveles JSONB. | **Corregido** |
| DEF-04 | Media | `astimezone(UTC)` desborda con fechas extremas (`OverflowError`). | `POST /sessions` con `inicio = 0001-01-01T00:00:00+05:00` | **Corregido** |
| DEF-05 | Baja | El limitador conserva una entrada por cada correo o IP aunque sus fallos hayan caducado, por lo que su memoria crece sin límite. | `test_limitador_no_acumula_memoria_por_claves_caducadas` | **Corregido** |

### Correcciones aplicadas en producción

Son 5 correcciones pequeñas: 52 líneas añadidas y 10 eliminadas en 9 archivos. Ninguna altera una respuesta válida: cuatro convierten un 500 en el error de cliente correcto y la quinta solo cambia la gestión interna de memoria.

| Defecto | Archivo | Cambio | Resultado |
|---|---|---|---|
| DEF-01 | `app/modules/sessions/services/acceso_service.py` | Comparar `claro.encode()` con `codigo.encode()` | 401 y fallo contabilizado (429 en el sexto intento) |
| DEF-02 | `app/core/database.py` y los 4 routers con paginación | Constante `OFFSET_MAXIMO` y `Query(le=OFFSET_MAXIMO)` | 422 |
| DEF-03 | `app/core/exceptions.py` | Manejador global de `sqlalchemy.exc.DataError` | 422 con mensaje genérico; el registro solo guarda el tipo de error, nunca la consulta ni sus parámetros |
| DEF-04 | `app/modules/sessions/schemas/__init__.py` | Capturar `OverflowError` en `_a_utc` | 422 |
| DEF-05 | `app/modules/sessions/security/limitador.py` | Eliminar claves vacías y limpiar las caducadas | La memoria deja de crecer indefinidamente; los bloqueos vigentes se conservan |

### Decisión sobre DEF-03

Se eligió un manejador global en lugar de validar cada esquema porque cubre todos los campos, incluidos los que van dentro de JSONB, sin modificar los módulos. Como contrapartida, cualquier `DataError` responde 422, aunque su origen fuera un error interno; por eso cada caso queda registrado como advertencia.

### Decisión sobre DEF-05

La limpieza completa corre como máximo una vez por ventana para no recorrer todas las claves en cada petición. Por tanto, la memoria queda acotada por los fallos recientes, no por todos los históricos. El límite sigue siendo por proceso; la limitación de varios workers no cambia.

### Limitaciones del entorno y advertencias

- El equipo no tenía PostgreSQL instalado, así que se usó un contenedor Docker. La versión (17.11) es posterior a la citada en el README (17.6).
- Se añadió `pytest-cov` a `requirements.txt`. El repositorio no incluye configuración Docker; el comando del contenedor quedó documentado en el README.
- Hay una advertencia sin efecto en los resultados: `StarletteDeprecationWarning`, porque `starlette.testclient` recomienda `httpx2`.

## H. Patrones de diseño

Solo se probaron los patrones presentes en el código. No se crearon implementaciones artificiales.

| Patrón | Implementación | Responsabilidad | Comportamiento verificado | Pruebas en |
|---|---|---|---|---|
| **State** (casos) | `cases/states/estados.py` | Ciclo BORRADOR, PUBLICADO, ARCHIVADO | Selección del estado concreto, transiciones y matriz de permisos; solo el borrador valida; un estado nuevo rechaza todo por defecto | `test_patrones.py`, `test_cases_state.py` |
| **State** (sesiones) | `sessions/states/estados.py` | Ciclo SIN_INICIAR, EN_CURSO, FINALIZADO | Transiciones válidas e inválidas, límites exactos de la ventana, plazo de cierre e idempotencia | `test_patrones.py`, `test_sessions_state.py` |
| **Builder + Director** | `cases/builders/` | Construir el caso desde CaseDSL | El Director funciona con un constructor alternativo, secuencia de pasos, numeración, resolución tardía de criterios, reinicio tras construir y reversibilidad con el exportador | `test_patrones.py`, `test_cases_builder.py` |
| **Chain of Responsibility** | `cases/validators/cadena.py` | Validación previa a publicar | Orden de los eslabones, delegación aunque haya errores, acumulación, ningún efecto sobre el caso y colaboración con State | `test_patrones.py`, `test_modulo_2.py` |
| **Repository** | `*/repositories/` | Aislar el acceso a datos | Encapsula la sesión, no confirma transacciones y no conoce reglas de negocio | `test_patrones.py` |
| **Puertos y adaptadores** | `sessions/integracion.py`, `app/bootstrap.py`, `participations/policies/integracion_sesiones.py` | Integrar los módulos 3 y 4 sin dependencia circular | La raíz de composición conecta las implementaciones reales y el módulo 3 no importa al 4 | `test_patrones.py`, `test_modulo_4.py` |
| **Null Object** | `PublicadorNulo`, `_SinParticipaciones`, `SinReingreso` | Comportamiento neutro por defecto | No actúan, registran sin datos sensibles y son sustituibles | `test_patrones.py`, `test_modulo_3.py` |
| **Policy** | `temporizador.py`, `PoliticaReingreso` | Reglas separadas del servicio | Regla de reingreso y cálculos de tiempo sin base de datos | `test_modulo_4.py`, `test_modulo_3.py` |

**Observer: no implementado.** `sessions/integracion.py` indica que es «inyección de dependencias con un único destinatario por puerto, no Observer», y `sessions/eventos.py` deja el Observer al módulo 6. `PublicadorEventosSesion` es un puerto con un solo destinatario; sí se verificó que los eventos se publican después del commit y se descartan si la transacción falla.

**Strategy: no implementada.** `participations/services/consulta_evaluacion.py` la menciona como futura para el módulo 5. Hoy solo existe la consulta de datos que esa Strategy usará.

Para el documento académico conviene presentarlo así: ambos patrones están planificados para los módulos 5 y 6, y su punto de extensión ya existe y está probado.

## I. Evidencias visuales sugeridas

Tome estas capturas después de ejecutar los comandos de reproducción. Las versiones en texto de cada resultado ya están en `docs/evidencias/`.

| # | Captura | Comando o ubicación | Qué debe verse |
|---:|---|---|---|
| 1 | Contenedor de pruebas | `docker ps` | `inter_nos_pg_test` con `postgres:17-alpine` |
| 2 | Suite completa | `pytest -q` | Línea final `1343 passed` |
| 3 | Regresión de un defecto | `pytest tests/integration/test_modulo_1_api.py -k byte_nul -v` | Las pruebas de DEF-03 aprobadas |
| 4 | Cobertura en terminal | `pytest --cov=app --cov-report=term-missing` | Tabla por archivo y línea `TOTAL` |
| 5 | Cobertura HTML | Abrir `htmlcov/index.html` | Portada con el porcentaje global |
| 6 | Archivo con líneas sin cubrir | Abrir `usuario_repository.py` desde el HTML | Líneas 20-21 marcadas |
| 7 | Unitarias por módulo | `pytest tests/unit/test_modulo_1.py -v` (repetir con 2, 3 y 4) | Nombres de prueba y conteo final |
| 8 | Integración por módulo | `pytest tests/integration/test_modulo_1_api.py -v` (repetir con 2, 3 y 4) | Pruebas HTTP y conteo final |
| 9 | Flujo de extremo a extremo | `pytest tests/integration/test_flujo_completo.py -v` | Los 6 flujos entre módulos |
| 10 | Patrones | `pytest tests/unit/test_patrones.py -v` | Pruebas de State, Builder y Chain |
| 11 | Aislamiento | Comando sin servidor indicado abajo | Unitarias aprobadas con PostgreSQL inaccesible |
| 12 | Defectos antes de corregir | Abrir `docs/evidencias/00_defectos_antes_de_corregir.txt` | `9 failed` |
| 13 | Mutaciones | Abrir `docs/evidencias/08_mutaciones_controladas.txt` | `detectadas 22; sobrevivientes 0` |
| 14 | Correcciones | `git diff -- app` | Los cambios de producción |

### Reproducción

En PowerShell, desde la carpeta `backend/`:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
docker desktop start
docker run -d --rm --name inter_nos_pg_test -e POSTGRES_USER=simulador_test -e POSTGRES_PASSWORD=solo_pruebas_local -e POSTGRES_DB=simulador_psicologia_test -p 127.0.0.1:55432:5432 --tmpfs /var/lib/postgresql/data postgres:17-alpine
$env:TEST_DATABASE_URL = "postgresql+psycopg://simulador_test:solo_pruebas_local@127.0.0.1:55432/simulador_psicologia_test"
$env:DATABASE_URL = "postgresql+psycopg://simulador_test:solo_pruebas_local@127.0.0.1:55432/no_usada_dev"
pytest --cov=app --cov-report=term-missing --cov-report=html
```

`DATABASE_URL` debe ser distinta de la de pruebas porque la salvaguarda las compara al iniciar. La suite no se conecta a `no_usada_dev`.

Unitarias sin PostgreSQL (en el puerto 1 no hay servidor, a propósito):

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://x:x@127.0.0.1:1/sin_servidor_test"
pytest tests/unit
```

Para detener el contenedor, que no conserva datos:

```powershell
docker stop inter_nos_pg_test
```

## J. Conclusiones

Los cuatro módulos presentan un estado de calidad alto. Las reglas de negocio, los permisos entre roles, la integridad de los datos y el flujo completo entre módulos funcionan como se espera. La suite terminó sin fallas, con una cobertura de 99,82 %, y detectó las 22 regresiones introducidas a propósito.

El mayor aporte no fue el porcentaje, que ya era alto, sino encontrar cinco defectos que la suite anterior no veía porque se concentraba en entradas plausibles. Los cinco se corrigieron y cada uno quedó cubierto por pruebas de regresión.

### Recomendaciones

1. **Reemplazar el limitador por un almacén compartido** antes de desplegar con varios procesos.
2. **Eliminar el código sin uso** indicado en la sección F.
3. **Integrar la suite en un flujo de integración continua:** las unitarias tardan unos 5 segundos y no necesitan base de datos.
4. **Agregar pruebas de la Strategy de evaluación** cuando exista el módulo 5, y retirar entonces las pruebas que confirman la ausencia de notas.
5. **Agregar pruebas de carga** antes de usar el sistema con grupos numerosos en simultáneo.
