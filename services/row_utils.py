"""Shared helpers for Appwrite-shaped row mappings."""


def row_id(row):
    return (row or {}).get("$id") or (row or {}).get("id")
