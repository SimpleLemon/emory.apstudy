#!/usr/bin/env python3
"""Offline structural validation for the Nest calendar ICS Nginx includes.

This intentionally does not invoke Nginx or access the network. It catches the
high-risk mistakes that can be made while installing the three split-context
files; ``nginx -t`` remains the authoritative syntax check on the target host.
"""

from __future__ import annotations

import ipaddress
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REAL_IP = ROOT / "nginx-calendar-ics-cloudflare-real-ip.conf"
HTTP = ROOT / "nginx-calendar-ics-http.conf"
SERVER = ROOT / "nginx-calendar-ics-feed.snippet.conf"

EXPECTED_CLOUDFLARE_CIDRS = {
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        fail(f"{label} is missing: {needle}")


def without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def extract_block(text: str, marker: str) -> str:
    start = text.find(marker)
    if start < 0:
        fail(f"missing block: {marker}")
    brace = text.find("{", start)
    if brace < 0:
        fail(f"missing opening brace for {marker}")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    fail(f"unterminated block: {marker}")


def main() -> int:
    real_ip = REAL_IP.read_text(encoding="utf-8")
    http = HTTP.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")
    real_ip_directives = without_comments(real_ip)
    http_directives = without_comments(http)
    server_directives = without_comments(server)

    cidrs = set(re.findall(r"^\s*set_real_ip_from\s+([^;]+);", real_ip, re.MULTILINE))
    if cidrs != EXPECTED_CLOUDFLARE_CIDRS:
        fail(
            "Cloudflare CIDR set differs from the checked official lists "
            f"(missing={sorted(EXPECTED_CLOUDFLARE_CIDRS - cidrs)}, "
            f"extra={sorted(cidrs - EXPECTED_CLOUDFLARE_CIDRS)})"
        )
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr, strict=True)
        except ValueError as exc:
            fail(f"invalid Cloudflare CIDR {cidr}: {exc}")
    require(real_ip, "real_ip_header CF-Connecting-IP;", "Cloudflare real-IP header")
    require(real_ip, "real_ip_recursive on;", "Cloudflare recursive real-IP setting")
    if any(token in real_ip_directives for token in ("0.0.0.0/0", "::/0", "unix:")):
        fail("Cloudflare real-IP trust list contains an unrestricted trust entry")
    if re.search(r"^\s*(?:location|server)\b", real_ip_directives, re.MULTILINE):
        fail("Cloudflare real-IP file contains server/location context")

    require(http, "log_format nest_calendar_ics_redacted", "ICS log format")
    require(http, "limit_req_zone $binary_remote_addr zone=nest_calendar_ics_per_ip:10m rate=300r/m;", "ICS rate zone")
    log_format = http[http.index("log_format nest_calendar_ics_redacted") :]
    log_format = log_format[: log_format.index(";") + 1]
    if any(token in log_format for token in ("$request ", "$request\"", "$request_uri", "$args", "$query_string", "$is_args")):
        fail("ICS log format may emit query arguments or the full tokenized request line")
    require(log_format, "$uri", "query-redacted URI in ICS log format")
    if re.search(r"^\s*(?:location|server)\b", http_directives, re.MULTILINE):
        fail("ICS HTTP file contains server/location context")

    feed = extract_block(server, "location = /api/calendar/share-feed.ics")
    handler = extract_block(server, "location @nest_calendar_ics_rate_limited")
    if any(re.search(rf"^\s*{directive}\b", server_directives, re.MULTILINE) for directive in ("log_format", "limit_req_zone", "set_real_ip_from")):
        fail("ICS server file contains HTTP-context directives")
    require(feed, "error_log /var/log/nginx/nest-calendar-ics-error.log crit;", "feed error log")
    require(handler, "error_log /var/log/nginx/nest-calendar-ics-error.log crit;", "429 error log")
    for block, label in ((feed, "feed"), (handler, "429 handler")):
        require(block, "access_log /var/log/nginx/nest-calendar-ics-access.log nest_calendar_ics_redacted;", f"{label} redacted access log")
    for directive in (
        "limit_req zone=nest_calendar_ics_per_ip burst=30 nodelay;",
        "limit_req_status 429;",
        "proxy_cache off;",
        "proxy_cache_bypass 1;",
        "proxy_no_cache 1;",
        "proxy_hide_header Content-Disposition;",
        'add_header Cache-Control "private, no-store, no-transform" always;',
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_pass http://127.0.0.1:8000;",
    ):
        require(feed, directive, "feed directive")
    require(feed, "error_page 429 = @nest_calendar_ics_rate_limited;", "feed-scoped 429 error page")
    require(handler, "add_header Retry-After 60 always;", "429 retry hint")
    require(handler, "return 429;", "429 handler response")
    if re.search(r"\b(?:return|rewrite)\s+30[1278]\b", handler):
        fail("429 handler redirects")
    if re.search(r"\b(?:proxy_pass|proxy_redirect)\b", handler):
        fail("429 handler proxies or redirects")

    print("PASS: Cloudflare CIDRs, split contexts, redacted logs, rate limiting, proxying, caching, and 429 handling validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
