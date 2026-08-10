import sqlite3

from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from services import notifications

notifications_bp = Blueprint("notifications", __name__)

@notifications_bp.get("/notifications")
@login_required
def center_page():
    return redirect(f"{url_for('dashboard.dashboard')}?notifications=open")


@notifications_bp.get("/api/notifications/preferences")
@login_required
def get_preferences():
    push_config = notifications.push_configuration()
    return jsonify({
        "preferences": notifications.preferences(current_user.id),
        "devices": notifications.list_subscriptions(current_user.id),
        "push_configured": push_config["configured"],
        "vapid_public_key": push_config["public_key"],
    })


@notifications_bp.patch("/api/notifications/preferences")
@login_required
def patch_preferences():
    try: payload = notifications.update_preferences(current_user.id, request.get_json(silent=True) or {})
    except (ValueError, TypeError) as exc: return jsonify({"error": str(exc)}), 400
    return jsonify({"preferences": payload})


@notifications_bp.post("/api/notifications/subscriptions")
@login_required
def subscribe():
    payload = request.get_json(silent=True) or {}
    try: row_id = notifications.upsert_subscription(current_user.id, payload.get("subscription") or {}, payload.get("device_name"), request.user_agent.string)
    except PermissionError as exc: return jsonify({"error": str(exc)}), 409
    except ValueError as exc: return jsonify({"error": str(exc)}), 400
    return jsonify({"id": row_id, "devices": notifications.list_subscriptions(current_user.id)}), 201


@notifications_bp.delete("/api/notifications/subscriptions/<subscription_id>")
@login_required
def unsubscribe(subscription_id):
    notifications.delete_subscription(current_user.id, subscription_id=subscription_id)
    return jsonify({"devices": notifications.list_subscriptions(current_user.id)})

@notifications_bp.patch("/api/notifications/subscriptions/<subscription_id>")
@login_required
def rename_subscription(subscription_id):
    try: devices = notifications.rename_subscription(current_user.id, subscription_id, (request.get_json(silent=True) or {}).get("device_name"))
    except ValueError as exc: return jsonify({"error": str(exc)}), 400
    return jsonify({"devices": devices})


@notifications_bp.post("/api/notifications/subscriptions/current/delete")
@login_required
def unsubscribe_current():
    notifications.delete_subscription(current_user.id, endpoint=(request.get_json(silent=True) or {}).get("endpoint"))
    return jsonify({"ok": True})


@notifications_bp.post("/api/notifications/test")
@login_required
def test_notification():
    if not notifications.list_subscriptions(current_user.id):
        return jsonify({
            "error": "No browser is registered. Enable notifications on this browser before sending a test.",
            "code": "no_push_subscription",
        }), 409
    notification_id, result = notifications.notify(current_user.id, "test", "Nest notifications are working", "This browser can receive notifications even when Nest is closed.", "/settings#notifications", dedupe_key=None, force_push=True)
    if not result["accepted"]:
        return jsonify({
            "error": "The registered browser rejected the test. Repair its subscription, then try again.",
            "code": "push_delivery_failed",
            "notification_id": notification_id,
            **result,
        }), 502
    return jsonify({"notification_id": notification_id, **result})


@notifications_bp.get("/api/notifications")
@login_required
def feed():
    return jsonify(notifications.list_feed(
        current_user.id,
        category=request.args.get("category"),
        unread=request.args.get("unread") == "1",
        status=request.args.get("status"),
        search=request.args.get("search"),
        limit=request.args.get("limit", 50),
        before=request.args.get("before"),
    ))


@notifications_bp.post("/api/notifications/sync")
@login_required
def sync_foreground():
    payload = request.get_json(silent=True) or {}
    active = bool(payload.get("active"))
    device_class = str(payload.get("device_class") or "")
    try:
        result = notifications.sync_foreground_state(
            current_user.id,
            payload.get("tab_id"),
            active=active,
            device_class=device_class,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not result["active"]:
        return jsonify(result)
    prefs = result.pop("preferences")
    focus_active = False
    try:
        from services.focus_mode import is_focus_mode_active
        focus_active = is_focus_mode_active(current_user.id)
    except sqlite3.OperationalError:
        focus_active = False
    for item in result["notifications"]:
        urgent = notifications.is_urgent_during_focus(item.get("category"))
        item["urgent"] = urgent
        item["foreground_enabled"] = (
            notifications.category_delivery_enabled(item.get("category"), prefs)
            and (not focus_active or urgent)
        )
    result["focus_mode_active"] = focus_active
    return jsonify(result)


@notifications_bp.post("/api/notifications/foreground-ack")
@login_required
def acknowledge_foreground():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return jsonify({"error": "ids must be a list."}), 400
    return jsonify({"acknowledged": notifications.acknowledge_foreground(current_user.id, ids)})


@notifications_bp.post("/api/notifications/read")
@login_required
def read():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    if ids is not None and not isinstance(ids, list):
        return jsonify({"error": "ids must be a list."}), 400
    return jsonify({"unread_count": notifications.mutate_feed(current_user.id, ids[:100] if ids is not None else None, read=payload.get("read", True), category=payload.get("category"))})


@notifications_bp.delete("/api/notifications")
@login_required
def delete():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    if ids is not None and not isinstance(ids, list):
        return jsonify({"error": "ids must be a list."}), 400
    return jsonify({"unread_count": notifications.mutate_feed(current_user.id, ids[:100] if ids is not None else None, delete=True, category=payload.get("category"))})


@notifications_bp.get("/api/notifications/unread-count")
@login_required
def count():
    return jsonify({"unread_count": notifications.unread_count(current_user.id)})
