"""
DynamoDB Streams -> notifications.

Triggered by INSERT events on the quotes table. Sends the customer their
written estimate by SES and pushes an owner alert to SNS. Marks the item
notified with a conditional update so a stream retry can't double-send.

Failures fall through to the SQS DLQ configured on the event source, so a
bad SES call never blocks the shard or loses a lead.
"""

import json
import logging
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

import templates

log = logging.getLogger()
log.setLevel(logging.INFO)

ses = boto3.client("sesv2")
sns = boto3.client("sns")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

FROM_ADDRESS = os.environ["FROM_ADDRESS"]
REPLY_TO = os.environ.get("REPLY_TO", FROM_ADDRESS)
OWNER_TOPIC = os.environ["OWNER_TOPIC_ARN"]
CONFIG_SET = os.environ.get("SES_CONFIG_SET")


def _plain(value):
    """DynamoDB deserialiser output -> plain Python for templating."""
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _claim(quote_id):
    """Return True if this invocation owns the notification for this quote."""
    try:
        table.update_item(
            Key={"pk": f"QUOTE#{quote_id}", "sk": "META"},
            UpdateExpression="SET notifiedAt = :now, #s = :sent",
            ConditionExpression="attribute_not_exists(notifiedAt)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":now": _now(), ":sent": "QUOTED"},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            log.info("quote %s already notified, skipping", quote_id)
            return False
        raise


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _send_customer_email(quote):
    customer = quote["customer"]
    args = {
        "FromEmailAddress": FROM_ADDRESS,
        "ReplyToAddresses": [REPLY_TO],
        "Destination": {"ToAddresses": [customer["email"]]},
        "Content": {
            "Simple": {
                "Subject": {"Data": f"Your moving estimate — {quote['quoteId']}"},
                "Body": {
                    "Html": {"Data": templates.customer_html(quote)},
                    "Text": {"Data": templates.customer_text(quote)},
                },
            }
        },
    }
    if CONFIG_SET:
        args["ConfigurationSetName"] = CONFIG_SET
    ses.send_email(**args)
    log.info("customer email sent ref=%s", quote["quoteId"])


def _alert_owner(quote):
    est = quote["estimate"]
    customer = quote["customer"]
    sns.publish(
        TopicArn=OWNER_TOPIC,
        Subject=f"New quote {quote['quoteId']} — ${est['low']}-${est['high']}",
        Message=(
            f"{customer['name']}\n"
            f"{customer['email']}  {customer.get('phone') or 'no phone'}\n"
            f"Move date: {customer.get('moveDate') or 'not set'}\n\n"
            f"{est['cubicFeet']} cu ft | {est['truck']} truck"
            f"{' x' + str(est['trips']) if est.get('trips', 1) > 1 else ''}"
            f" | {est['crewSize']} movers\n"
            f"{est['rateType']} | {est['miles']} mi\n"
            f"Estimate: ${est['low']} - ${est['high']}\n\n"
            f"Notes: {customer.get('notes') or '—'}\n"
        ),
        MessageAttributes={
            "quoteId": {"DataType": "String", "StringValue": quote["quoteId"]},
        },
    )


def handler(event, _context):
    from boto3.dynamodb.types import TypeDeserializer

    deser = TypeDeserializer()
    failures = []

    for record in event.get("Records", []):
        seq = record["dynamodb"]["SequenceNumber"]
        try:
            if record["eventName"] != "INSERT":
                continue

            image = record["dynamodb"]["NewImage"]
            quote = _plain({k: deser.deserialize(v) for k, v in image.items()})

            if quote.get("sk") != "META":
                continue

            if not _claim(quote["quoteId"]):
                continue

            _send_customer_email(quote)
            _alert_owner(quote)

        except Exception:
            log.exception("notification failed seq=%s", seq)
            failures.append({"itemIdentifier": seq})

    # Partial batch response: only the failed records are retried.
    return {"batchItemFailures": failures}
