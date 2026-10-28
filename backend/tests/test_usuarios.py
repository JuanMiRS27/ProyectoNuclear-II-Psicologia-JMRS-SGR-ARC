from tests.conftest import CLAVE_PRUEBA, cabecera


# ---------- Autenticación ----------


def test_login_correcto_y_me(cliente, docente):
    r = cliente.post(
        "/auth/login", data={"username": "DOCENTE@PRUEBA.edu.co", "password": CLAVE_PRUEBA}
    )
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = cliente.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["correo"] == "docente@prueba.edu.co"
    assert "clave_hash" not in r.json()


def test_login_clave_incorrecta(cliente, docente):
    r = cliente.post(
        "/auth/login", data={"username": "docente@prueba.edu.co", "password": "otra-clave"}
    )
    assert r.status_code == 401


def test_usuario_inactivo_no_entra(cliente, sesion, docente):
    docente.activo = False
    sesion.commit()
    r = cliente.post(
        "/auth/login", data={"username": "docente@prueba.edu.co", "password": CLAVE_PRUEBA}
    )
    assert r.status_code == 401
    assert cliente.get("/auth/me", headers=cabecera(docente)).status_code == 401


def test_token_invalido(cliente):
    r = cliente.get("/auth/me", headers={"Authorization": "Bearer basura"})
    assert r.status_code == 401


# ---------- Usuarios ----------


def test_admin_crea_docente_con_permiso(cliente, admin):
    r = cliente.post(
        "/usuarios",
        headers=cabecera(admin),
        json={
            "nombre_completo": "Laura Gómez",
            "correo": "Laura@Uni.edu.co",
            "clave": "ClaveLarga1",
            "rol": "DOCENTE",
            "puede_crear_casos": True,
        },
    )
    assert r.status_code == 201
    assert r.json()["correo"] == "laura@uni.edu.co"
    assert r.json()["puede_crear_casos"] is True


def test_permiso_crear_casos_solo_docentes(cliente, admin):
    r = cliente.post(
        "/usuarios",
        headers=cabecera(admin),
        json={
            "nombre_completo": "Ana Ruiz",
            "correo": "ana@uni.edu.co",
            "clave": "ClaveLarga1",
            "rol": "ESTUDIANTE",
            "codigo_institucional": "EST-1",
            "puede_crear_casos": True,
        },
    )
    assert r.status_code == 422


def test_estudiante_requiere_codigo(cliente, admin):
    r = cliente.post(
        "/usuarios",
        headers=cabecera(admin),
        json={
            "nombre_completo": "Ana Ruiz",
            "correo": "ana@uni.edu.co",
            "clave": "ClaveLarga1",
            "rol": "ESTUDIANTE",
        },
    )
    assert r.status_code == 422


def test_correo_duplicado(cliente, admin, docente):
    r = cliente.post(
        "/usuarios",
        headers=cabecera(admin),
        json={
            "nombre_completo": "Otro Docente",
            "correo": "docente@prueba.edu.co",
            "clave": "ClaveLarga1",
            "rol": "DOCENTE",
        },
    )
    assert r.status_code == 409


def test_solo_admin_gestiona_usuarios(cliente, docente):
    assert cliente.get("/usuarios", headers=cabecera(docente)).status_code == 403
    assert cliente.get("/usuarios").status_code == 401


def test_cambiar_rol_quita_permiso(cliente, admin, docente):
    r = cliente.patch(
        f"/usuarios/{docente.id}",
        headers=cabecera(admin),
        json={"rol": "ADMINISTRADOR"},
    )
    assert r.status_code == 200
    assert r.json()["rol"] == "ADMINISTRADOR"
    assert r.json()["puede_crear_casos"] is False


def test_admin_no_se_desactiva_a_si_mismo(cliente, admin):
    r = cliente.delete(f"/usuarios/{admin.id}", headers=cabecera(admin))
    assert r.status_code == 422


def test_desactivar_usuario(cliente, admin, estudiante):
    r = cliente.delete(f"/usuarios/{estudiante.id}", headers=cabecera(admin))
    assert r.status_code == 204
    r = cliente.get(f"/usuarios/{estudiante.id}", headers=cabecera(admin))
    assert r.json()["activo"] is False


def test_filtrar_por_rol(cliente, admin, docente, estudiante):
    r = cliente.get("/usuarios?rol=ESTUDIANTE", headers=cabecera(admin))
    assert [u["correo"] for u in r.json()] == ["estudiante@prueba.edu.co"]


# ---------- Espacios académicos ----------


def test_crud_espacio(cliente, admin, docente, estudiante):
    r = cliente.post(
        "/espacios-academicos",
        headers=cabecera(docente),
        json={"nombre": "Psicología Social - Grupo 01", "periodo": "2026-2"},
    )
    assert r.status_code == 201
    espacio_id = r.json()["id"]

    # Todos los usuarios autenticados pueden consultar
    r = cliente.get("/espacios-academicos", headers=cabecera(estudiante))
    assert len(r.json()) == 1

    r = cliente.patch(
        f"/espacios-academicos/{espacio_id}",
        headers=cabecera(docente),
        json={"nombre": "Psicología Social - Grupo 02"},
    )
    assert r.json()["nombre"] == "Psicología Social - Grupo 02"

    # Solo el administrador desactiva
    r = cliente.delete(f"/espacios-academicos/{espacio_id}", headers=cabecera(docente))
    assert r.status_code == 403
    r = cliente.delete(f"/espacios-academicos/{espacio_id}", headers=cabecera(admin))
    assert r.status_code == 204


def test_espacio_unico_por_nombre_y_periodo(cliente, admin):
    datos = {"nombre": "Psicología Social - Grupo 01", "periodo": "2026-2"}
    assert cliente.post("/espacios-academicos", headers=cabecera(admin), json=datos).status_code == 201
    assert cliente.post("/espacios-academicos", headers=cabecera(admin), json=datos).status_code == 409

    datos["periodo"] = "2027-1"
    assert cliente.post("/espacios-academicos", headers=cabecera(admin), json=datos).status_code == 201


def test_periodo_con_formato_valido(cliente, admin):
    r = cliente.post(
        "/espacios-academicos",
        headers=cabecera(admin),
        json={"nombre": "Psicología Social", "periodo": "segundo"},
    )
    assert r.status_code == 422


def test_estudiante_no_crea_espacios(cliente, estudiante):
    r = cliente.post(
        "/espacios-academicos",
        headers=cabecera(estudiante),
        json={"nombre": "Psicología Social", "periodo": "2026-2"},
    )
    assert r.status_code == 403
