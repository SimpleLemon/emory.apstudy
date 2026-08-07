"""Appwrite-backed OAuth login session completion."""


def _complete_appwrite_login(
    remote_user,
    provider="appwrite",
    email=None,
    provider_access_token=None,
    provider_uid=None,
    page_context="auth/session",
    *,
    dependencies,
):
    """Complete an Appwrite login using route-provided compatibility bindings."""
    collections = dependencies["collections"]
    get_row_safe = dependencies["get_row_safe"]
    find_user_by_email = dependencies["find_user_by_email"]
    provider_access_token_from_identities = dependencies[
        "provider_access_token_from_identities"
    ]
    fetch_provider_profile = dependencies["fetch_provider_profile"]
    provider_avatar_url = dependencies["provider_avatar_url"]
    log_avatar_collection = dependencies["log_avatar_collection"]
    resolve_discord_link_identity = dependencies["resolve_discord_link_identity"]
    format_datetime = dependencies["format_datetime"]
    datetime = dependencies["datetime"]
    store_provider_avatar = dependencies["store_provider_avatar"]
    create_row_safe = dependencies["create_row_safe"]
    secrets = dependencies["secrets"]
    invites = dependencies["invites"]
    request = dependencies["request"]
    invite_cookie = dependencies["invite_cookie"]
    logger = dependencies["logger"]
    avatar_can_use_provider = dependencies["avatar_can_use_provider"]
    delete_avatar_file = dependencies["delete_avatar_file"]
    update_row_safe = dependencies["update_row_safe"]
    sync_chat_presence_labels_for_user = dependencies[
        "sync_chat_presence_labels_for_user"
    ]
    session = dependencies["session"]
    login_user = dependencies["login_user"]
    user_from_doc = dependencies["user_from_doc"]
    current_app = dependencies["current_app"]
    auth_session_duration = dependencies["auth_session_duration"]
    set_oauth_session = dependencies["set_oauth_session"]
    notes_collaboration = dependencies["notes_collaboration"]
    discord_bridge = dependencies["discord_bridge"]
    emit_user_event = dependencies["emit_user_event"]
    format_actor = dependencies["format_actor"]
    format_user_target = dependencies["format_user_target"]
    redirect_for_user_doc = dependencies["redirect_for_user_doc"]

    remote_user = remote_user or {}
    remote_user_id = remote_user.get("$id") or remote_user.get("id")
    remote_email = remote_user.get("email") or ""
    if not remote_user_id:
        raise ValueError("Invalid Appwrite user.")
    if not email:
        email = remote_email

    appwrite_user_id = str(remote_user_id)
    user_doc = get_row_safe(collections["users"], appwrite_user_id, allow_missing=True)
    if not user_doc and email:
        user_doc = find_user_by_email(email)
    created_user = False

    if not provider_access_token:
        identity_token = provider_access_token_from_identities(
            appwrite_user_id,
            provider=provider,
        )
        provider_access_token = (
            identity_token.get("provider_access_token") or provider_access_token
        )
        if not provider_uid:
            provider_uid = identity_token.get("provider_uid")
        if identity_token.get("provider"):
            provider = identity_token["provider"]

    provider_profile = fetch_provider_profile(provider, provider_access_token)
    provider_name = provider_profile.get("name")
    resolved_provider_avatar_url = provider_avatar_url(
        provider_profile,
        remote_user,
        provider=provider,
    )
    remote_avatar_candidate = provider_avatar_url({}, remote_user, provider=provider)
    log_avatar_collection(
        user_id=appwrite_user_id,
        provider=provider,
        page_context=page_context,
        created_user=not bool(user_doc),
        has_provider_token=bool(provider_access_token),
        provider_profile_avatar=provider_profile.get("avatar_url"),
        remote_avatar_candidate=remote_avatar_candidate,
        resolved_avatar_url=resolved_provider_avatar_url,
        storage_result="pending",
    )

    discord_id_value = None
    discord_username_value = None
    if provider == "discord":
        discord_identity = resolve_discord_link_identity(
            provider_uid=provider_uid,
            provider_access_token=provider_access_token,
            appwrite_user_ids=[appwrite_user_id],
        )
        discord_id_value = discord_identity.get("id")
        discord_username_value = discord_identity.get("username")

    name = provider_name or remote_user.get("name") or remote_user.get("displayName")
    picture_url = resolved_provider_avatar_url

    if not user_doc:
        created_at = format_datetime(datetime.utcnow())
        avatar_file_id = None
        avatar_file_size_bytes = 0
        storage_result = "none"
        if picture_url:
            picture_url, avatar_file_id, storage_result, avatar_file_size_bytes = (
                store_provider_avatar(
                    appwrite_user_id,
                    picture_url,
                    page_context=page_context,
                )
            )
        row_data = {
            "google_id": appwrite_user_id,
            "email": email,
            "name": name or remote_user.get("name"),
            "picture_url": picture_url,
            "avatar_file_id": avatar_file_id,
            "avatar_file_size_bytes": avatar_file_size_bytes,
            "tier": "free",
            "banner_color": "#fecae1",
            "avatar_source": "provider" if picture_url else None,
            "school": None,
            "major": None,
            "graduation_year": None,
            "onboarding_complete": False,
            "onboarding_step": 1,
            "created_at": created_at,
            "last_login": created_at,
        }
        if provider and provider != "appwrite":
            row_data["provider"] = provider
        if discord_id_value:
            row_data["discord_id"] = discord_id_value
            row_data["discord_username"] = discord_username_value
            row_data["discord_linked_at"] = created_at
        user_doc = create_row_safe(
            collections["users"],
            row_id=appwrite_user_id,
            data=row_data,
        )
        created_user = True

        create_row_safe(
            collections["user_settings"],
            row_id=appwrite_user_id,
            data={
                "user_id": appwrite_user_id,
                "ics_secret_token": secrets.token_urlsafe(32),
                "feed_refresh_minutes": 15,
                "preferred_calendar_view": "week",
                "interface_theme": "obsidian-dark",
                "theme": "dark",
                "sidebar_default": "expanded",
                "email_notifications": True,
                "product_updates": True,
                "task_sound_enabled": True,
                "chat_sound_enabled": True,
                "language": "en",
                "timezone": "",
                "created_at": created_at,
            },
        )
        try:
            invites.attribute_signup(
                request.cookies.get(invite_cookie),
                appwrite_user_id,
            )
        except Exception:
            logger.exception("Failed to attribute new user signup to invite")
    else:
        updates = {"last_login": format_datetime(datetime.utcnow())}
        if name:
            updates["name"] = name
        if picture_url and avatar_can_use_provider(user_doc):
            (
                stored_picture_url,
                stored_file_id,
                storage_result,
                stored_file_size_bytes,
            ) = store_provider_avatar(
                appwrite_user_id,
                picture_url,
                page_context=page_context,
            )
            previous_file_id = user_doc.get("avatar_file_id")
            updates["picture_url"] = stored_picture_url
            updates["avatar_source"] = "provider"
            updates["avatar_file_size_bytes"] = stored_file_size_bytes
            avatar_file_id = None
            if stored_file_id:
                avatar_file_id = stored_file_id
                if previous_file_id and previous_file_id != stored_file_id:
                    delete_avatar_file(previous_file_id)
            elif previous_file_id and storage_result == "provider_url_fallback":
                delete_avatar_file(previous_file_id)
            if stored_file_id or (
                previous_file_id and storage_result == "provider_url_fallback"
            ):
                updates["avatar_file_id"] = avatar_file_id
        if email:
            updates["email"] = email
        if provider and provider != "appwrite":
            updates["provider"] = provider
        if discord_id_value:
            updates["discord_id"] = discord_id_value
            updates["discord_username"] = discord_username_value
            if not user_doc.get("discord_id"):
                updates["discord_linked_at"] = format_datetime(datetime.utcnow())

        row_id = user_doc.get("$id") or user_doc.get("id")
        if not row_id:
            raise ValueError("User lookup failed.")
        user_doc = update_row_safe(
            collections["users"],
            row_id,
            updates,
        )

    sync_chat_presence_labels_for_user(
        user_doc.get("$id") or user_doc.get("id"),
        user_doc,
    )
    session.permanent = True
    login_user(
        user_from_doc(user_doc),
        remember=True,
        duration=current_app.config.get(
            "AUTH_SESSION_DURATION",
            auth_session_duration,
        ),
    )
    session["user_id"] = user_doc.get("$id") or user_doc.get("id")
    session["email"] = email or remote_email
    set_oauth_session(
        provider,
        appwrite_user_id,
        email,
        name=name,
        picture_url=picture_url,
    )
    if email or remote_email:
        try:
            notes_collaboration.claim_pending_invitations(
                session["user_id"],
                email or remote_email,
            )
        except Exception:
            logger.exception(
                "Failed to claim pending note invitations for %s",
                session["user_id"],
            )

    if discord_id_value:
        try:
            discord_bridge.add_guild_member_role(discord_id_value)
        except Exception:
            logger.exception(
                "Failed to grant Discord role on login for %s",
                discord_id_value,
            )

    if created_user:
        emit_user_event(
            "New User Created",
            actor=format_actor(
                user_id=user_doc.get("$id") or user_doc.get("id"),
                username=user_doc.get("username") or user_doc.get("name"),
            ),
            target=format_user_target(user_doc),
            metadata={
                "page_context": page_context,
                "resource_type": "user",
                "resource_id": user_doc.get("$id") or user_doc.get("id"),
                "provider": provider,
                "email": email or remote_email,
                "default_settings_created": True,
            },
            color="green",
        )

    emit_user_event(
        "User Login",
        actor=format_actor(
            user_id=user_doc.get("$id") or user_doc.get("id"),
            username=user_doc.get("username") or user_doc.get("name"),
        ),
        target=format_user_target(user_doc),
        metadata={
            "page_context": page_context,
            "resource_type": "user",
            "resource_id": user_doc.get("$id") or user_doc.get("id"),
            "provider": provider,
            "created_user": created_user,
        },
        color="green",
    )

    return {
        "created_user": created_user,
        "email": email or remote_email,
        "redirect": redirect_for_user_doc(user_doc),
        "user_doc": user_doc,
        "user_id": session["user_id"],
    }
