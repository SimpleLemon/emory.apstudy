from services.environment_config import runtime_environment_config


def admin_user_ids():
    configured = runtime_environment_config()
    raw = configured.admin_user_ids_raw or configured.admin_user_id_raw or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def user_can_access_admin(user_id):
    normalized = str(user_id or "").strip()
    return bool(normalized and normalized in admin_user_ids())
