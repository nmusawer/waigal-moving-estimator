"""
POST /quotes

Validates the inquiry, recomputes the price server-side, writes one item to
DynamoDB, and returns the quote reference. Notifications are NOT sent here —
the DynamoDB stream triggers the notifier. If SES is down, the lead is still
captured, and the customer still gets a reference number.
"""

import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3

import pricing

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
PHONE_RE = re.compile(r"\D")

# Base32-ish alphabet with I/O/0/1 removed so references survive a phone call
REF_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class Invalid(Exception):
    pass


def _reference():
    return "WM-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))


def _respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def _clean_phone(raw):
    digits = PHONE_RE.sub("", raw or "")
    if not digits:
        return None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def _validate(body):
    customer = body.get("customer") or {}
    move = body.get("move") or {}

    name = (customer.get("name") or "").strip()[:120]
    email = (customer.get("email") or "").strip().lower()[:254]

    if len(name) < 2:
        raise Invalid("Enter the name the move should be booked under.")
    if not EMAIL_RE.match(email):
        raise Invalid("That email address doesn't look right.")
    if move.get("homeSize") not in pricing.HOME_SIZES:
        raise Invalid("Choose a home size before requesting an estimate.")

    # Honeypot: a real browser never fills this. Bots fill every field.
    if (body.get("website") or "").strip():
        raise Invalid("Submission rejected.")

    return {
        "name": name,
        "email": email,
        "phone": _clean_phone(customer.get("phone")),
        "smsOptIn": bool(customer.get("smsOptIn")),
        "moveDate": (customer.get("moveDate") or "")[:10] or None,
        "notes": (customer.get("notes") or "").strip()[:2000],
    }, move


def handler(event, _context):
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return _respond(204, {})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _respond(400, {"error": "Send a JSON body."})

    try:
        customer, move = _validate(body)
    except Invalid as e:
        return _respond(400, {"error": str(e)})

    try:
        estimate = pricing.calculate(move)
    except (ValueError, TypeError, KeyError):
        log.warning("pricing failed", exc_info=True)
        return _respond(400, {"error": "We couldn't price that combination."})

    # The browser sends what it showed the customer. We never trust it, but a
    # mismatch means the deployed frontend is stale — worth an alarm.
    shown = (body.get("estimate") or {}).get("low")
    if shown is not None:
        try:
            drift = abs(Decimal(str(shown)) - estimate["low"])
            if drift > 1:
                log.warning(
                    "PRICE_DRIFT client=%s server=%s ref_pending", shown, estimate["low"]
                )
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    ref = _reference()

    item = {
        "pk": f"QUOTE#{ref}",
        "sk": "META",
        "quoteId": ref,
        "status": "NEW",
        "createdAt": now.isoformat(),
        "gsi1pk": f"DATE#{now:%Y-%m-%d}",
        "gsi1sk": now.isoformat(),
        "customer": customer,
        "move": json.loads(json.dumps(move), parse_float=Decimal),
        "estimate": estimate,
        "sourceIp": event.get("requestContext", {}).get("http", {}).get("sourceIp"),
        # Housekeeping: unconverted leads age out after two years.
        "ttl": int(time.time()) + 60 * 60 * 24 * 730,
    }

    TABLE.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    log.info("quote stored ref=%s cuft=%s", ref, estimate["cubicFeet"])

    return _respond(201, {"quoteId": ref, "estimate": estimate})
