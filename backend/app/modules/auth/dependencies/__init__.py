from app.modules.auth.dependencies.auth import (
    AdminUser,
    CaseCreator,
    CurrentUser,
    DocenteUser,
    PasswordUser,
    get_auth_service,
    get_current_user,
    get_usuario_service,
    require_admin,
    require_case_creator,
    require_docente,
    require_roles,
)

__all__ = [
    "AdminUser",
    "CaseCreator",
    "CurrentUser",
    "DocenteUser",
    "PasswordUser",
    "get_auth_service",
    "get_current_user",
    "get_usuario_service",
    "require_admin",
    "require_case_creator",
    "require_docente",
    "require_roles",
]
