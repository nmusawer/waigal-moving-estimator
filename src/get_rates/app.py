"""
GET /rates

Serves the rate card the browser uses to preview prices. Because both this
and the pricing calculation import the same layer module, the number the
customer sees and the number we store can't drift apart.

Cached at the edge — rates change rarely, and every visitor hits this.
"""

import json
import os

import pricing

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")


def handler(_event, _context):
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
        },
        "body": json.dumps(pricing.public_rates()),
    }
