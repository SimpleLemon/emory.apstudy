import json
import logging
from datetime import datetime, timezone

import requests as http_requests
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.query import Query

from appwrite_client import COLLECTIONS
from appwrite_helpers import create_row_safe, first_row, format_datetime, update_row_safe
from services.row_utils import row_id as _row_id


logger = logging.getLogger(__name__)

ZENQUOTES_TODAY_URL = "https://zenquotes.io/api/today"
ZENQUOTES_TIMEOUT_SECONDS = 30
FALLBACK_QUOTE = {
    "text": "Small steps every day become the work you are proud of.",
    "author": "APStudy Nest",
}


def utc_quote_date(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.date().isoformat()


def _quote_table():
    return COLLECTIONS["daily_quotes"]

def normalize_zenquotes_payload(data):
    item = data[0] if isinstance(data, list) and data else data
    if isinstance(item, dict) and isinstance(item.get("quote"), dict):
        item = item["quote"]
    if not isinstance(item, dict):
        raise ValueError("ZenQuotes response did not include a quote object.")

    text = str(item.get("q") or item.get("text") or "").strip()
    author = str(item.get("a") or item.get("author") or "").strip()
    if not text:
        raise ValueError("ZenQuotes response did not include quote text.")
    return {
        "text": text,
        "author": author or FALLBACK_QUOTE["author"],
    }


def fetch_zenquotes_quote(quote_date=None, timeout=ZENQUOTES_TIMEOUT_SECONDS):
    response = http_requests.get(ZENQUOTES_TODAY_URL, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    quote = normalize_zenquotes_payload(data)
    return {
        **quote,
        "date": quote_date or utc_quote_date(),
        "source": "zenquotes",
        "source_url": ZENQUOTES_TODAY_URL,
        "raw_payload": json.dumps(data, separators=(",", ":"), default=str),
    }


def upsert_daily_quote(quote_date, quote):
    now = format_datetime(datetime.now(timezone.utc))
    payload = {
        "quote_date": quote_date,
        "quote_text": str(quote.get("text") or "").strip(),
        "author": str(quote.get("author") or FALLBACK_QUOTE["author"]).strip(),
        "source": str(quote.get("source") or "zenquotes").strip(),
        "source_url": str(quote.get("source_url") or ZENQUOTES_TODAY_URL).strip(),
        "raw_payload": str(quote.get("raw_payload") or ""),
        "fetched_at": now,
        "updated_at": now,
    }
    if not payload["quote_text"]:
        raise ValueError("Daily quote text is required.")

    existing = first_row(_quote_table(), [Query.equal("quote_date", [quote_date])])
    if existing:
        return update_row_safe(_quote_table(), _row_id(existing), payload)
    return create_row_safe(
        _quote_table(),
        row_id=ID.unique(),
        data={**payload, "created_at": now},
    )


def fetch_and_store_daily_quote(quote_date=None):
    target_date = quote_date or utc_quote_date()
    quote = fetch_zenquotes_quote(quote_date=target_date)
    return upsert_daily_quote(target_date, quote)


def _row_to_quote(row):
    if not row:
        return None
    text = str(row.get("quote_text") or "").strip()
    if not text:
        return None
    return {
        "text": text,
        "author": str(row.get("author") or FALLBACK_QUOTE["author"]).strip(),
        "date": str(row.get("quote_date") or "").strip(),
        "source": str(row.get("source") or "zenquotes").strip(),
        "fetched_at": row.get("fetched_at"),
        "fallback": False,
    }


def fallback_quote_payload(quote_date=None):
    return {
        **FALLBACK_QUOTE,
        "date": quote_date or utc_quote_date(),
        "source": "fallback",
        "fallback": True,
    }


def get_daily_quote_payload(quote_date=None):
    target_date = quote_date or utc_quote_date()
    try:
        row = first_row(_quote_table(), [Query.equal("quote_date", [target_date])])
    except AppwriteException:
        logger.exception("Failed to load daily quote from Appwrite")
        return fallback_quote_payload(target_date)

    quote = _row_to_quote(row)
    return quote or fallback_quote_payload(target_date)
