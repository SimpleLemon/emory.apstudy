"""Admin analytics aggregation and optional GA4 reporting."""

import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from services import database

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback is not used in CI.
    ZoneInfo = None


RANGE_OPTIONS = {
    "24h": {"label": "Last 24 hours", "hours": 24, "granularity": "hour"},
    "7d": {"label": "Last 7 days", "days": 7, "granularity": "day"},
    "14d": {"label": "Last 14 days", "days": 14, "granularity": "day"},
    "30d": {"label": "Last 30 days", "days": 30, "granularity": "day"},
    "60d": {"label": "Last 60 days", "days": 60, "granularity": "day"},
    "all": {"label": "All time", "granularity": "day"},
}

DEFAULT_GA4_PROPERTY_ID = "446578226"
GA4_ACCOUNT_ID = "318602205"
GA4_HOSTNAME = "nest.apstudy.org"


def utcnow():
    return datetime.now(timezone.utc)


def parse_utc(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_timezone(tz_name):
    if ZoneInfo is None:
        return timezone.utc, "UTC"
    try:
        tz = ZoneInfo((tz_name or "").strip() or "UTC")
        return tz, getattr(tz, "key", None) or (tz_name or "UTC")
    except Exception:
        return timezone.utc, "UTC"


def normalize_range(range_key):
    return range_key if range_key in RANGE_OPTIONS else "30d"


def _floor_hour(value):
    return value.replace(minute=0, second=0, microsecond=0)


def _floor_day(value):
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _floor_month(value):
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_bucket(value, granularity, amount=1):
    if granularity == "hour":
        return value + timedelta(hours=amount)
    if granularity == "month":
        month_index = value.month - 1 + amount
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        return value.replace(year=year, month=month, day=1)
    return value + timedelta(days=amount)


def _bucket_key(value, granularity):
    if granularity == "hour":
        return _floor_hour(value).strftime("%Y-%m-%dT%H:00")
    if granularity == "month":
        return _floor_month(value).strftime("%Y-%m")
    return _floor_day(value).strftime("%Y-%m-%d")


def _bucket_label(value, granularity):
    if granularity == "hour":
        return value.strftime("%-I %p") if os.name != "nt" else value.strftime("%I %p").lstrip("0")
    if granularity == "month":
        return value.strftime("%b %Y")
    return value.strftime("%b %-d") if os.name != "nt" else value.strftime("%b %d").replace(" 0", " ")


def _first_user_created_at(users):
    values = [parse_utc(user.get("created_at")) for user in users]
    values = [value for value in values if value]
    return min(values) if values else utcnow()


def _range_window(range_key, tz, users, now=None):
    range_key = normalize_range(range_key)
    now_utc = now or utcnow()
    local_now = now_utc.astimezone(tz)
    config = RANGE_OPTIONS[range_key]
    granularity = config["granularity"]
    if range_key == "24h":
        end_local = _floor_hour(local_now)
        start_local = end_local - timedelta(hours=config["hours"] - 1)
    elif range_key == "all":
        first_local = _first_user_created_at(users).astimezone(tz)
        start_local = _floor_day(first_local)
        end_local = _floor_day(local_now)
        if (end_local.date() - start_local.date()).days > 180:
            granularity = "month"
            start_local = _floor_month(start_local)
            end_local = _floor_month(end_local)
    else:
        end_local = _floor_day(local_now)
        start_local = end_local - timedelta(days=config["days"] - 1)
    return {
        "range": range_key,
        "granularity": granularity,
        "start_local": start_local,
        "end_local": end_local,
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc": _add_bucket(end_local, granularity).astimezone(timezone.utc),
        "now_utc": now_utc,
    }


def _previous_window(range_key, window):
    """Return the immediately preceding window with the same bucket count."""
    config = RANGE_OPTIONS.get(range_key) or {}
    if range_key == "all":
        return None
    granularity = window["granularity"]
    bucket_count = config.get("hours") if granularity == "hour" else config.get("days")
    if not bucket_count:
        return None
    end_local = _add_bucket(window["start_local"], granularity, -1)
    start_local = _add_bucket(end_local, granularity, -(bucket_count - 1))
    return {
        "range": range_key,
        "granularity": granularity,
        "start_local": start_local,
        "end_local": end_local,
        "start_utc": start_local.astimezone(timezone.utc),
        "end_utc": _add_bucket(end_local, granularity).astimezone(timezone.utc),
        "now_utc": window["now_utc"],
    }


def _bucket_series(window):
    buckets = []
    current = window["start_local"]
    final = window["end_local"]
    granularity = window["granularity"]
    while current <= final:
        buckets.append({
            "key": _bucket_key(current, granularity),
            "label": _bucket_label(current, granularity),
            "start": iso_utc(current.astimezone(timezone.utc)),
        })
        current = _add_bucket(current, granularity)
    return buckets


def _rows(table_id, *, path=None):
    try:
        with database.db_connection(path) as conn:
            database.table_columns(conn, table_id)
            return [dict(row) for row in conn.execute(f'SELECT * FROM "{table_id}"').fetchall()]
    except Exception:
        return []


def record_authenticated_activity(user_id, *, at=None, path=None):
    """Record signed-in product activity without creating one row per request."""
    user_id = str(user_id or "").strip()
    if not user_id:
        return
    seen_at = parse_utc(at) if at is not None else utcnow()
    if seen_at is None:
        seen_at = utcnow()
    hour_start = _floor_hour(seen_at)
    hour_key = iso_utc(hour_start)
    row_id = f"{user_id}:hour:{hour_key}"
    with database.db_connection(path) as conn:
        conn.execute(
            """
            INSERT INTO user_activity_buckets (
                id, user_id, bucket_granularity, bucket_start, last_seen_at
            ) VALUES (?, ?, 'hour', ?, ?)
            ON CONFLICT(user_id, bucket_granularity, bucket_start)
            DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            [row_id, user_id, hour_key, iso_utc(seen_at)],
        )
        conn.execute(
            "INSERT OR IGNORE INTO daily_active_users (user_id, active_date) VALUES (?, ?)",
            [user_id, seen_at.date().isoformat()],
        )


def _activity_rows(table_id, timestamp_column, start_utc, end_utc, *, filter_column=None, path=None):
    try:
        with database.db_connection(path) as conn:
            columns = database.table_columns(conn, table_id)
            range_column = filter_column or timestamp_column
            if "user_id" not in columns or timestamp_column not in columns or range_column not in columns:
                return []
            return [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT user_id, "{timestamp_column}"
                    FROM "{table_id}"
                    WHERE "{range_column}" >= ? AND "{range_column}" < ?
                    """,
                    [iso_utc(start_utc), iso_utc(end_utc)],
                ).fetchall()
            ]
    except Exception:
        return []


def _activity_observations(users, start_utc, end_utc, *, path=None):
    observations = []
    for user in users:
        user_id = str(user.get("id") or user.get("$id") or "").strip()
        seen_at = parse_utc(user.get("last_login"))
        if user_id and seen_at and start_utc <= seen_at < end_utc:
            observations.append((user_id, seen_at))
    for row in _activity_rows(
        "user_activity_buckets",
        "last_seen_at",
        start_utc,
        end_utc,
        filter_column="bucket_start",
        path=path,
    ):
        user_id = str(row.get("user_id") or "").strip()
        seen_at = parse_utc(row.get("last_seen_at"))
        if user_id and seen_at:
            observations.append((user_id, seen_at))
    for row in _activity_rows("analytics_events", "created_at", start_utc, end_utc, path=path):
        user_id = str(row.get("user_id") or "").strip()
        seen_at = parse_utc(row.get("created_at"))
        if user_id and seen_at:
            observations.append((user_id, seen_at))
    return observations


def _authenticated_activity_series(observations, buckets, window, tz):
    users_by_bucket = {bucket["key"]: set() for bucket in buckets}
    active_users = set()
    for user_id, seen_at in observations:
        if not (window["start_utc"] <= seen_at < window["end_utc"]):
            continue
        bucket_key = _bucket_key(seen_at.astimezone(tz), window["granularity"])
        if bucket_key not in users_by_bucket:
            continue
        users_by_bucket[bucket_key].add(user_id)
        active_users.add(user_id)
    return (
        [
            {
                "key": bucket["key"],
                "label": bucket["label"],
                "value": len(users_by_bucket[bucket["key"]]),
            }
            for bucket in buckets
        ],
        len(active_users),
    )


def _count_table(table_id, *, start_utc=None, end_utc=None, column="created_at", path=None):
    try:
        with database.db_connection(path) as conn:
            columns = database.table_columns(conn, table_id)
            if column not in columns:
                return 0
            sql = f'SELECT COUNT(*) AS total FROM "{table_id}"'
            params = []
            where = []
            if start_utc:
                where.append(f'"{column}" >= ?')
                params.append(iso_utc(start_utc))
            if end_utc:
                where.append(f'"{column}" < ?')
                params.append(iso_utc(end_utc))
            if where:
                sql += " WHERE " + " AND ".join(where)
            return int(conn.execute(sql, params).fetchone()["total"] or 0)
    except Exception:
        return 0


def _filter_created(rows, window, *, all_time=False, key="created_at"):
    if all_time:
        return list(rows)
    start = window["start_utc"]
    end = window["end_utc"]
    selected = []
    for row in rows:
        created_at = parse_utc(row.get(key))
        if created_at and start <= created_at < end:
            selected.append(row)
    return selected


def _normalize_provider(user):
    provider = str((user or {}).get("provider") or "").strip().lower()
    if provider in {"google", "discord", "github"}:
        return provider
    if (user or {}).get("google_id"):
        return "google"
    return "other"


def _uni_type(user):
    school = f"{user.get('school') or ''} {user.get('school_key') or ''}".lower()
    level = str(user.get("education_level") or "").strip().lower()
    if user.get("emory_student") or "emory" in school or "oxford" in school:
        return "emory"
    if "high" in level or "secondary" in level:
        return "high_school"
    if "graduate" in level or "master" in level or "phd" in level or "doctor" in level:
        return "graduate"
    if "university" in level or "college" in level or "undergraduate" in level:
        return "university"
    return "other"


def _total_user_growth(users, buckets, window, tz):
    granularity = window["granularity"]
    created_values = [parse_utc(user.get("created_at")) for user in users]
    created_values = sorted(value for value in created_values if value)
    index = 0
    values = []
    for bucket in buckets:
        local_start = parse_utc(bucket["start"]).astimezone(tz)
        bucket_end = _add_bucket(local_start, granularity).astimezone(timezone.utc)
        while index < len(created_values) and created_values[index] < bucket_end:
            index += 1
        values.append({"key": bucket["key"], "label": bucket["label"], "value": index})
    return values


def _breakdown(items, labels):
    counter = Counter(items)
    return [{"key": key, "label": label, "value": counter.get(key, 0)} for key, label in labels]


def _breakdown_growth_series(users, buckets, window, tz, classify_fn, labels):
    granularity = window["granularity"]
    by_category = {key: [] for key, _ in labels}
    for user in users:
        created_at = parse_utc(user.get("created_at"))
        if not created_at:
            continue
        category = classify_fn(user)
        if category in by_category:
            by_category[category].append(created_at)
    for key in by_category:
        by_category[key].sort()

    series = []
    for key, label in labels:
        created_values = by_category[key]
        index = 0
        points = []
        for bucket in buckets:
            local_start = parse_utc(bucket["start"]).astimezone(tz)
            bucket_end = _add_bucket(local_start, granularity).astimezone(timezone.utc)
            while index < len(created_values) and created_values[index] < bucket_end:
                index += 1
            points.append({"key": bucket["key"], "label": bucket["label"], "value": index})
        series.append({"key": key, "label": label, "points": points})
    return series


def _empty_bucket_points(buckets):
    return [
        {"key": bucket["key"], "label": bucket["label"], "value": 0}
        for bucket in buckets
    ]


def _metric_int(metric):
    try:
        return int(float(getattr(metric, "value", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _ga_date_range(window):
    return window["start_local"].date().isoformat(), window["end_local"].date().isoformat()


def _ga_comparison_date_range(range_key, window):
    config = RANGE_OPTIONS.get(range_key) or {}
    days = config.get("days")
    if not days:
        return None
    previous_end = window["start_local"] - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return previous_start.date().isoformat(), previous_end.date().isoformat()


def _ga_bucket_dimension(window):
    return "dateHour" if window["granularity"] == "hour" else "date"


def _ga_dimension_bucket_key(value, dimension_name, granularity, tz):
    text = str(value or "").strip()
    try:
        if dimension_name == "dateHour":
            parsed = datetime.strptime(text, "%Y%m%d%H").replace(tzinfo=tz)
        elif dimension_name == "date":
            parsed = datetime.strptime(text, "%Y%m%d").replace(tzinfo=tz)
        else:
            return ""
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text).replace(tzinfo=tz)
        except ValueError:
            return ""
    return _bucket_key(parsed, granularity)


def _ga_series_from_rows(rows, buckets, dimension_name, window, tz):
    active_counts = Counter()
    page_view_counts = Counter()
    bucket_keys = {bucket["key"] for bucket in buckets}
    for row in rows or []:
        bucket_key = _ga_dimension_bucket_key(
            row.dimension_values[0].value if row.dimension_values else "",
            dimension_name,
            window["granularity"],
            tz,
        )
        if bucket_key not in bucket_keys:
            continue
        metrics = [_metric_int(metric) for metric in row.metric_values]
        active_counts[bucket_key] += metrics[0] if len(metrics) > 0 else 0
        page_view_counts[bucket_key] += metrics[1] if len(metrics) > 1 else 0
    return {
        "activeUsers": [
            {"key": bucket["key"], "label": bucket["label"], "value": active_counts[bucket["key"]]}
            for bucket in buckets
        ],
        "pageViews": [
            {"key": bucket["key"], "label": bucket["label"], "value": page_view_counts[bucket["key"]]}
            for bucket in buckets
        ],
    }


def _ga_totals_from_rows(rows):
    row = next(iter(rows or []), None)
    if not row:
        return {"activeUsers": 0, "screenPageViews": 0, "eventCount": 0}
    metrics = [_metric_int(metric) for metric in row.metric_values]
    return {
        "activeUsers": metrics[0] if len(metrics) > 0 else 0,
        "screenPageViews": metrics[1] if len(metrics) > 1 else 0,
        "eventCount": metrics[2] if len(metrics) > 2 else 0,
    }


def _comparison_delta(current, previous):
    current = int(current or 0)
    previous = int(previous or 0)
    if previous <= 0:
        return {
            "current": current,
            "previous": previous,
            "percentChange": None,
            "direction": "up" if current > 0 else "neutral",
            "available": True,
            "label": "New" if current > 0 else "0.0%",
        }
    percent = round(((current - previous) / previous) * 100, 1)
    direction = "up" if percent > 0 else "down" if percent < 0 else "neutral"
    return {
        "current": current,
        "previous": previous,
        "percentChange": percent,
        "direction": direction,
        "available": True,
    }


def _dimension_value(row, index):
    try:
        return str(row.dimension_values[index].value or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _metric_value(row, index=0):
    try:
        return _metric_int(row.metric_values[index])
    except (AttributeError, IndexError, TypeError):
        return 0


def _clean_ga_page_title(title, path):
    page_path = str(path or "").strip()
    if page_path == "/":
        return "Landing Page"
    text = str(title or "").strip()
    for suffix in (" - APStudy Nest", " | APStudy Nest", " - APStudy", " | APStudy"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text or page_path or "Untitled Page"


def _ga_country_details_from_rows(current_rows, previous_rows):
    previous = {}
    for row in previous_rows or []:
        country_id = _dimension_value(row, 0)
        label = _dimension_value(row, 1) or country_id
        key = country_id or label
        if not key:
            continue
        previous[key] = previous.get(key, 0) + _metric_value(row)

    countries = []
    for row in current_rows or []:
        country_id = _dimension_value(row, 0)
        label = _dimension_value(row, 1) or country_id
        key = country_id or label
        value = _metric_value(row)
        if not key or value <= 0:
            continue
        previous_value = previous.get(key, 0)
        countries.append({
            "key": key,
            "countryId": country_id,
            "label": label,
            "value": value,
            "previous": previous_value,
            "comparison": _comparison_delta(value, previous_value),
        })
    countries.sort(key=lambda item: item["value"], reverse=True)
    return countries[:8]


def _ga_page_details_from_rows(current_rows, previous_rows):
    previous = {}
    for row in previous_rows or []:
        title = _dimension_value(row, 0)
        path = _dimension_value(row, 1)
        label = _clean_ga_page_title(title, path)
        key = path or label
        if not key:
            continue
        previous[key] = previous.get(key, 0) + _metric_value(row)

    pages = []
    for row in current_rows or []:
        title = _dimension_value(row, 0)
        path = _dimension_value(row, 1)
        label = _clean_ga_page_title(title, path)
        key = path or label
        value = _metric_value(row)
        if not key or value <= 0:
            continue
        previous_value = previous.get(key, 0)
        pages.append({
            "key": key,
            "title": label,
            "path": path,
            "label": label,
            "value": value,
            "previous": previous_value,
            "comparison": _comparison_delta(value, previous_value),
        })
    pages.sort(key=lambda item: item["value"], reverse=True)
    return pages[:8]


def _ga_top_pages_from_details(pages):
    return [
        {"label": page.get("path") or page.get("label") or page.get("title") or "", "value": page.get("value", 0)}
        for page in (pages or [])[:6]
        if page.get("value", 0) > 0
    ]


def _ga4_payload(range_key, window, buckets, tz):
    property_id = (os.environ.get("GA4_PROPERTY_ID") or DEFAULT_GA4_PROPERTY_ID).strip()
    credentials = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if not credentials:
        return {"configured": False, "status": "not_configured", "message": "GOOGLE_APPLICATION_CREDENTIALS is not configured."}

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest
    except Exception:
        return {"configured": False, "status": "missing_dependency", "message": "google-analytics-data is not installed."}

    try:
        client = BetaAnalyticsDataClient()
        start_date, end_date = _ga_date_range(window)
        bucket_dimension = _ga_bucket_dimension(window)
        hostname_filter = FilterExpression(
            filter=Filter(
                field_name="hostName",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.EXACT,
                    value=GA4_HOSTNAME,
                ),
            )
        )
        previous_date_range = _ga_comparison_date_range(range_key, window)
        totals_report = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                metrics=[
                    Metric(name="activeUsers"),
                    Metric(name="screenPageViews"),
                    Metric(name="eventCount"),
                ],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                dimension_filter=hostname_filter,
            )
        )
        bucket_report = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                dimensions=[Dimension(name=bucket_dimension)],
                metrics=[
                    Metric(name="activeUsers"),
                    Metric(name="screenPageViews"),
                ],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                dimension_filter=hostname_filter,
            )
        )
        page_details_report = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                dimensions=[Dimension(name="pageTitle"), Dimension(name="pagePath")],
                metrics=[Metric(name="screenPageViews")],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                dimension_filter=hostname_filter,
                limit=100,
            )
        )
        country_details_report = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                dimensions=[Dimension(name="countryId"), Dimension(name="country")],
                metrics=[Metric(name="activeUsers")],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                dimension_filter=hostname_filter,
                limit=100,
            )
        )
        previous_totals_report = None
        previous_page_details_report = None
        previous_country_details_report = None
        if previous_date_range:
            previous_start_date, previous_end_date = previous_date_range
            previous_totals_report = client.run_report(
                RunReportRequest(
                    property=f"properties/{property_id}",
                    metrics=[
                        Metric(name="activeUsers"),
                        Metric(name="screenPageViews"),
                        Metric(name="eventCount"),
                    ],
                    date_ranges=[DateRange(start_date=previous_start_date, end_date=previous_end_date)],
                    dimension_filter=hostname_filter,
                )
            )
            previous_page_details_report = client.run_report(
                RunReportRequest(
                    property=f"properties/{property_id}",
                    dimensions=[Dimension(name="pageTitle"), Dimension(name="pagePath")],
                    metrics=[Metric(name="screenPageViews")],
                    date_ranges=[DateRange(start_date=previous_start_date, end_date=previous_end_date)],
                    dimension_filter=hostname_filter,
                    limit=100,
                )
            )
            previous_country_details_report = client.run_report(
                RunReportRequest(
                    property=f"properties/{property_id}",
                    dimensions=[Dimension(name="countryId"), Dimension(name="country")],
                    metrics=[Metric(name="activeUsers")],
                    date_ranges=[DateRange(start_date=previous_start_date, end_date=previous_end_date)],
                    dimension_filter=hostname_filter,
                    limit=100,
                )
            )
    except Exception as exc:
        return {"configured": True, "status": "error", "message": str(exc)[:300]}

    totals = _ga_totals_from_rows(totals_report.rows)
    previous_totals = _ga_totals_from_rows(previous_totals_report.rows) if previous_totals_report else {}
    page_details = _ga_page_details_from_rows(
        page_details_report.rows,
        previous_page_details_report.rows if previous_page_details_report else [],
    )
    country_details = _ga_country_details_from_rows(
        country_details_report.rows,
        previous_country_details_report.rows if previous_country_details_report else [],
    )
    return {
        "configured": True,
        "status": "ok",
        "range": range_key,
        "propertyId": property_id,
        "hostName": GA4_HOSTNAME,
        "totals": totals,
        "previousTotals": previous_totals,
        "comparison": {
            "enabled": bool(previous_date_range),
            "range": {
                "start": previous_date_range[0] if previous_date_range else None,
                "end": previous_date_range[1] if previous_date_range else None,
            },
            "metrics": {
                "activeUsers": _comparison_delta(
                    totals.get("activeUsers", 0),
                    previous_totals.get("activeUsers", 0),
                ) if previous_date_range else None,
                "pageViews": _comparison_delta(
                    totals.get("screenPageViews", 0),
                    previous_totals.get("screenPageViews", 0),
                ) if previous_date_range else None,
            },
        },
        "series": _ga_series_from_rows(bucket_report.rows, buckets, bucket_dimension, window, tz),
        "topPages": _ga_top_pages_from_details(page_details),
        "details": {
            "countries": country_details,
            "pages": page_details,
        },
    }


def analytics_payload(range_key="30d", tz_name="UTC", *, include_ga=True, now=None, path=None):
    tz, resolved_tz = resolve_timezone(tz_name)
    range_key = normalize_range(range_key)
    users = _rows("users", path=path)
    window = _range_window(range_key, tz, users, now=now)
    buckets = _bucket_series(window)
    previous_window = _previous_window(range_key, window)
    previous_buckets = _bucket_series(previous_window) if previous_window else []
    all_time = range_key == "all"
    selected_users = _filter_created(users, window, all_time=all_time)
    previous_users = _filter_created(users, previous_window) if previous_window else []
    activity_start = previous_window["start_utc"] if previous_window else window["start_utc"]
    activity_observations = _activity_observations(
        users,
        activity_start,
        window["end_utc"],
        path=path,
    )
    active_user_series, active_user_total = _authenticated_activity_series(
        activity_observations,
        buckets,
        window,
        tz,
    )
    previous_active_total = 0
    if previous_window:
        _, previous_active_total = _authenticated_activity_series(
            activity_observations,
            previous_buckets,
            previous_window,
            tz,
        )
    ga4 = _ga4_payload(range_key, window, buckets, tz) if include_ga else {"configured": False, "status": "disabled"}
    ga4_ok = ga4.get("status") == "ok"
    empty_traffic_series = _empty_bucket_points(buckets)
    page_view_series = ga4.get("series", {}).get("pageViews", empty_traffic_series) if ga4_ok else empty_traffic_series
    top_pages = ga4.get("topPages", []) if ga4_ok else []
    ga4_totals = ga4.get("totals", {}) if ga4_ok else {}
    ga4_comparison = ga4.get("comparison", {}) if ga4_ok else {}
    ga4_details = ga4.get("details", {}) if ga4_ok else {}

    provider_labels = [
        ("google", "Google"),
        ("discord", "Discord"),
        ("github", "GitHub"),
        ("other", "Other"),
    ]
    uni_labels = [
        ("emory", "Emory (Main/Oxford)"),
        ("university", "University (All)"),
        ("high_school", "High School"),
        ("graduate", "Graduate"),
        ("other", "Other"),
    ]

    onboarding_total = len(selected_users)
    onboarding_complete = sum(1 for user in selected_users if user.get("onboarding_complete"))
    engagement_counts = {
        "tasks": _count_table("tasks", start_utc=None if all_time else window["start_utc"], end_utc=None if all_time else window["end_utc"], path=path),
        "courses": _count_table("user_courses", start_utc=None if all_time else window["start_utc"], end_utc=None if all_time else window["end_utc"], column="added_at", path=path),
        "notes": _count_table("notes", start_utc=None if all_time else window["start_utc"], end_utc=None if all_time else window["end_utc"], path=path),
        "files": _count_table("shared_files", start_utc=None if all_time else window["start_utc"], end_utc=None if all_time else window["end_utc"], path=path),
        "chat_messages": _count_table("chat_messages", start_utc=None if all_time else window["start_utc"], end_utc=None if all_time else window["end_utc"], path=path),
    }
    comparison_metrics = dict(ga4_comparison.get("metrics", {})) if ga4_ok else {}
    if previous_window:
        signup_comparison = _comparison_delta(len(selected_users), len(previous_users))
        comparison_metrics.update({
            "totalUsers": signup_comparison,
            "activeUsers": _comparison_delta(active_user_total, previous_active_total),
            "oauth": signup_comparison,
            "uniType": signup_comparison,
        })

    return {
        "range": range_key,
        "rangeLabel": RANGE_OPTIONS[range_key]["label"],
        "timezone": resolved_tz,
        "granularity": window["granularity"],
        "start": iso_utc(window["start_utc"]),
        "end": iso_utc(window["end_utc"]),
        "buckets": buckets,
        "cards": {
            "totalUsers": len(users),
            "activeUsers": active_user_total,
            "pageViews": ga4_totals.get("screenPageViews", 0),
            "oauth": len(selected_users),
            "uniType": len(selected_users),
            "onboardingRate": round((onboarding_complete / onboarding_total) * 100, 1) if onboarding_total else 0,
        },
        "series": {
            "totalUsers": _total_user_growth(users, buckets, window, tz),
            "activeUsers": active_user_series,
            "pageViews": page_view_series,
            "oauth": _breakdown_growth_series(users, buckets, window, tz, _normalize_provider, provider_labels),
            "uniType": _breakdown_growth_series(users, buckets, window, tz, _uni_type, uni_labels),
        },
        "breakdowns": {
            "oauth": _breakdown((_normalize_provider(user) for user in selected_users), provider_labels),
            "uniType": _breakdown((_uni_type(user) for user in selected_users), uni_labels),
        },
        "engagement": {
            "topPages": top_pages,
            "onboarding": {
                "complete": onboarding_complete,
                "incomplete": max(0, onboarding_total - onboarding_complete),
                "total": onboarding_total,
            },
            "featureUsage": [{"label": key.replace("_", " ").title(), "value": value} for key, value in engagement_counts.items()],
        },
        "comparison": {
            "enabled": bool(previous_window),
            "range": {
                "start": iso_utc(previous_window["start_utc"]) if previous_window else None,
                "end": iso_utc(previous_window["end_utc"]) if previous_window else None,
            },
            "metrics": comparison_metrics,
        },
        "gaDetails": {
            "countries": ga4_details.get("countries", []) if ga4_ok else [],
            "pages": ga4_details.get("pages", []) if ga4_ok else [],
        },
        "sources": {
            "traffic": {
                "label": "Google Analytics" if ga4_ok else "Google Analytics unavailable",
                "status": ga4.get("status"),
            },
            "featureUsage": {
                "label": "Nest database",
                "status": "ok",
            },
        },
        "ga4": ga4,
    }
