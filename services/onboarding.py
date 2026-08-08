"""Step handlers for the settings onboarding flow."""


def save_onboarding_step_one(payload, user, user_id, dependencies):
    """Persist the user's display name and username."""
    AppwriteException = dependencies["AppwriteException"]
    collections = dependencies["collections"]
    jsonify = dependencies["jsonify"]
    logger = dependencies["logger"]
    update_row_safe = dependencies["update_row_safe"]
    username_is_taken = dependencies["username_is_taken"]
    validate_username = dependencies["validate_username"]

    display_name = (payload.get("display_name") or "").strip()
    if not display_name:
        return jsonify({"error": "Display name is required."}), 400

    try:
        username = validate_username(payload.get("username"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    if username_is_taken(username, user_id):
        return jsonify({"error": "That username is already taken."}), 409

    next_step = max(user.onboarding_step or 1, 2)
    try:
        update_row_safe(
            collections["users"],
            user_id,
            {
                "name": display_name,
                "username": username,
                "onboarding_step": next_step,
            },
        )
    except AppwriteException:
        logger.exception("Failed to update onboarding step")
        return jsonify({"error": "Unable to save onboarding."}), 500
    user.onboarding_step = next_step
    user.name = display_name
    user.username = username
    return jsonify({"status": "ok", "next_step": 2})


def save_onboarding_step_two(payload, user, user_id, dependencies):
    """Persist education and school details."""
    AppwriteException = dependencies["AppwriteException"]
    collections = dependencies["collections"]
    jsonify = dependencies["jsonify"]
    logger = dependencies["logger"]
    normalize_education_level = dependencies["normalize_education_level"]
    normalize_emory_email = dependencies["normalize_emory_email"]
    normalize_emory_student = dependencies["normalize_emory_student"]
    school_payload = dependencies["school_payload"]
    sync_chat_presence_labels_for_user = dependencies["sync_chat_presence_labels_for_user"]
    update_row_safe = dependencies["update_row_safe"]

    education_level = normalize_education_level(payload.get("education_level"))
    if not education_level:
        return jsonify({"error": "Select an education level before continuing."}), 400

    class_year = (payload.get("class_year") or "").strip() or None
    emory_student = normalize_emory_student(payload.get("emory_student"))
    emory_email = payload.get("emory_email")
    school_updates = school_payload(None)

    if education_level in {"High School", "Undergraduate"}:
        if not class_year or len(class_year) != 4 or not class_year.isdigit():
            return jsonify({"error": "Enter a valid 4-digit class year."}), 400
    else:
        class_year = None

    if education_level == "Undergraduate":
        if emory_student is None:
            return jsonify({"error": "Select whether you are an Emory University student."}), 400
        if emory_student:
            try:
                emory_email = normalize_emory_email(emory_email)
            except ValueError as error:
                return jsonify({"error": str(error)}), 400
            school_updates = school_payload("Emory University")
        else:
            emory_email = None
            school_updates = school_payload(payload.get("school"))
    else:
        emory_student = None
        emory_email = None

    next_step = 3 if education_level == "Undergraduate" and emory_student else 4

    try:
        update_row_safe(
            collections["users"],
            user_id,
            {
                "education_level": education_level,
                "class_year": class_year,
                "emory_student": emory_student,
                "emory_email": emory_email,
                **school_updates,
                "onboarding_step": next_step,
            },
        )
    except AppwriteException:
        logger.exception("Failed to update onboarding profile")
        return jsonify({"error": "Unable to save onboarding."}), 500
    user.education_level = education_level
    user.class_year = class_year
    user.emory_student = emory_student
    user.emory_email = emory_email
    user.school = school_updates.get("school")
    user.school_key = school_updates.get("school_key")
    user.school_source = school_updates.get("school_source")
    user.scorecard_id = school_updates.get("scorecard_id")
    user.onboarding_step = next_step
    sync_chat_presence_labels_for_user(user_id)
    return jsonify({"status": "ok", "next_step": next_step})


def save_onboarding_step_three(payload, action, user, user_id, dependencies):
    """Add an onboarding course or advance to the review step."""
    AppwriteException = dependencies["AppwriteException"]
    EntitlementError = dependencies["EntitlementError"]
    EntitlementLimitError = dependencies["EntitlementLimitError"]
    ID = dependencies["ID"]
    Query = dependencies["Query"]
    check_limit = dependencies["check_limit"]
    collections = dependencies["collections"]
    create_row_safe = dependencies["create_row_safe"]
    datetime = dependencies["datetime"]
    default_term = dependencies["default_term"]
    emit_creation_event = dependencies["emit_creation_event"]
    format_actor = dependencies["format_actor"]
    format_datetime = dependencies["format_datetime"]
    invites = dependencies["invites"]
    jsonify = dependencies["jsonify"]
    list_rows_all = dependencies["list_rows_all"]
    logger = dependencies["logger"]
    request_entitlements = dependencies["request_entitlements"]
    update_row_safe = dependencies["update_row_safe"]

    if action == "add_course":
        course_code = (payload.get("course_code") or "").strip().upper()
        course_name = (payload.get("course_name") or "").strip() or None
        section_number = (payload.get("section_number") or "").strip() or None
        instructor_name = (payload.get("instructor_name") or "").strip() or None
        term = (payload.get("term") or default_term).strip() or default_term

        subject = (payload.get("subject") or "").strip().upper()
        catalog = (payload.get("catalog") or "").strip()

        if course_code and (not subject or not catalog):
            parts = course_code.split()
            if len(parts) >= 2:
                subject = parts[0].upper()
                catalog = parts[1]

        if not subject or not catalog:
            return jsonify({"error": "Course code is required."}), 400

        try:
            candidates = list_rows_all(
                collections["user_courses"],
                [
                    Query.equal("user_id", [user_id]),
                    Query.equal("term", [term]),
                    Query.equal("subject", [subject]),
                    Query.equal("catalog", [catalog]),
                    Query.equal("source", ["onboarding"]),
                ],
            )
        except AppwriteException:
            logger.exception("Failed to check onboarding course")
            return jsonify({"error": "Unable to save course."}), 500

        existing = next((doc for doc in candidates if not doc.get("crn")), None)
        if existing:
            return jsonify({"error": "Course already added."}), 409

        try:
            entitlements = request_entitlements(user)
            check_limit(entitlements, "max_saved_courses", entitlements["usage"]["saved_courses"])
        except EntitlementLimitError as exc:
            return jsonify(exc.payload()), 403
        except EntitlementError:
            logger.exception("Failed to verify onboarding course limits")
            return jsonify({
                "error": "Unable to verify your course limits right now.",
                "code": "tier_check_unavailable",
            }), 503

        try:
            course = create_row_safe(
                collections["user_courses"],
                row_id=ID.unique(),
                data={
                    "user_id": user_id,
                    "term": term,
                    "subject": subject,
                    "catalog": catalog,
                    "course_name": course_name,
                    "section_number": section_number,
                    "instructor_name": instructor_name,
                    "source": "onboarding",
                    "added_at": format_datetime(datetime.utcnow()),
                },
            )
        except AppwriteException:
            logger.exception("Failed to add onboarding course")
            return jsonify({"error": "Unable to save course."}), 500

        try:
            invites.record_activation(user_id, "course")
        except Exception:
            logger.exception("Failed to record invite activation for onboarding course")

        emit_creation_event(
            "Onboarding Course Added",
            actor=format_actor(user),
            target=f"{subject} {catalog}",
            metadata={
                "page_context": "onboarding",
                "resource_type": "user_course",
                "resource_id": course.get("$id") or course.get("id"),
                "course_name": course_name,
                "section_number": section_number,
                "teacher": instructor_name,
                "term": term,
            },
            color="green",
        )
        return jsonify({
            "status": "ok",
            "course": {
                "id": course.get("$id"),
                "course_code": f"{subject} {catalog}",
                "course_name": course_name,
                "section_number": section_number,
                "instructor_name": instructor_name,
                "term": term,
            },
        }), 201

    if action in {"advance", "continue", "review"}:
        try:
            update_row_safe(
                collections["users"],
                user_id,
                {"onboarding_step": 4},
            )
        except AppwriteException:
            logger.exception("Failed to update onboarding step")
            return jsonify({"error": "Unable to save onboarding."}), 500
        user.onboarding_step = 4
        return jsonify({"status": "ok", "next_step": 4})

    if action == "complete":
        return jsonify({"error": "Complete onboarding from the confirm step."}), 400

    return None


def save_onboarding_step_four(user, user_id, dependencies):
    """Advance from review to the confirmation step."""
    AppwriteException = dependencies["AppwriteException"]
    collections = dependencies["collections"]
    jsonify = dependencies["jsonify"]
    logger = dependencies["logger"]
    update_row_safe = dependencies["update_row_safe"]

    try:
        update_row_safe(
            collections["users"],
            user_id,
            {"onboarding_step": 5},
        )
    except AppwriteException:
        logger.exception("Failed to update onboarding step")
        return jsonify({"error": "Unable to save onboarding."}), 500
    user.onboarding_step = 5
    return jsonify({"status": "ok", "next_step": 5})


def save_onboarding_step_five(user, user_id, dependencies):
    """Complete onboarding and run its post-completion side effects."""
    AppwriteException = dependencies["AppwriteException"]
    collections = dependencies["collections"]
    emit_user_event = dependencies["emit_user_event"]
    format_actor = dependencies["format_actor"]
    invites = dependencies["invites"]
    jsonify = dependencies["jsonify"]
    logger = dependencies["logger"]
    update_row_safe = dependencies["update_row_safe"]
    url_for = dependencies["url_for"]

    try:
        update_row_safe(
            collections["users"],
            user_id,
            {
                "onboarding_complete": True,
                "onboarding_step": 5,
            },
        )
    except AppwriteException:
        logger.exception("Failed to complete onboarding")
        return jsonify({"error": "Unable to save onboarding."}), 500
    user.onboarding_complete = True
    user.onboarding_step = 5

    from blueprints.chat_api import (
        create_welcome_dm_for_user,
        initialize_new_user_discord_read_states,
    )

    try:
        initialize_new_user_discord_read_states(user_id)
    except Exception:
        logger.exception(
            "Failed to initialize Discord read states after onboarding for user %s",
            user_id,
        )
    try:
        create_welcome_dm_for_user(user_id)
    except Exception:
        logger.exception("Failed to create welcome DM after onboarding for user %s", user_id)
    try:
        invites.promote_if_activated(user_id)
    except Exception:
        logger.exception("Failed to promote activated invite after onboarding for user %s", user_id)
    emit_user_event(
        "Onboarding Complete",
        actor=format_actor(user),
        target=str(user.id),
        metadata={
            "page_context": "onboarding",
            "resource_type": "user",
            "resource_id": user_id,
            "education_level": getattr(user, "education_level", None),
            "class_year": getattr(user, "class_year", None),
            "school": getattr(user, "school", None),
            "school_key": getattr(user, "school_key", None),
            "school_source": getattr(user, "school_source", None),
            "scorecard_id": getattr(user, "scorecard_id", None),
            "major": getattr(user, "major", None),
            "graduation_year": getattr(user, "graduation_year", None),
            "emory_student": getattr(user, "emory_student", None),
            "emory_email": getattr(user, "emory_email", None),
        },
        color="green",
    )
    return jsonify({"status": "ok", "redirect_url": url_for("dashboard.dashboard")})
