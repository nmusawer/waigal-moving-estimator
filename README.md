# Smart Moving & Logistics Cost Estimator

Customer-facing quote tool for Waigal Movers. A single-page estimator prices a
move in the browser, and a serverless backend captures the lead, re-prices it
authoritatively, and sends a written estimate.

Built as a portfolio project alongside SAA-C03 prep — the notes below explain
*why* each service was chosen, since that's the part an interviewer asks about.

---

## Architecture

```
                    ┌──────────────┐
  waigalmovers.com  │  CloudFront  │  static estimator page
        ─────────►  │   + S3 (OAC) │
                    └──────┬───────┘
                           │  GET /rates      (cached 1 hr)
                           │  POST /quotes
                    ┌──────▼───────┐
                    │  HTTP API    │  throttled 10 rps / 20 burst
                    │ (API Gateway)│
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
      ┌───────▼────────┐       ┌────────▼───────┐
      │ SubmitQuote λ  │       │  GetRates λ    │
      │  validate      │       │  serve rate    │
      │  re-price      │       │  card          │
      │  PutItem       │       └────────┬───────┘
      └───────┬────────┘                │
              │                    ┌────▼─────────┐
      ┌───────▼────────┐           │ PricingLayer │  ← one copy of the
      │  DynamoDB      │           └──────────────┘     pricing engine
      │  waigal-quotes │
      │  + byDate GSI  │
      └───────┬────────┘
              │ Streams (NEW_IMAGE)
      ┌───────▼────────┐
      │  Notifier λ    │──► SES      written estimate to the customer
      │  idempotent    │──► SNS      new-lead alert to the owner
      └───────┬────────┘
              │ after 3 retries
      ┌───────▼────────┐
      │  SQS DLQ       │──► CloudWatch alarm ──► SNS
      └────────────────┘
```

---

## The decisions worth defending

**The write is the event.** Nothing publishes a "quote created" message.
DynamoDB Streams turns the successful write itself into the trigger, which
removes the dual-write problem entirely — there is no state where the lead is
saved but the event was lost, or vice versa.

**Notifications are out of the request path.** `POST /quotes` returns as soon
as the item is durable. If SES is throttled or the customer's mail server is
slow, the customer still gets a reference number and you still have the lead.
The alternative — sending email inline — means an SES hiccup shows the
customer an error for a move you already captured.

**The client price is never trusted.** The browser sends what it displayed;
the server recomputes from the layer and stores its own number. A mismatch
over $1 logs `PRICE_DRIFT`, which is how you find out a stale page is cached
somewhere before a customer holds you to a number you didn't quote.

**One pricing engine, two consumers.** `GET /rates` serves the same module the
Lambda prices with. Change a rate in `layers/pricing/python/pricing.py`, deploy,
and both the browser preview and the stored quote move together.

**Idempotent notifier.** Stream records can be redelivered. The notifier claims
each quote with a conditional update on `attribute_not_exists(notifiedAt)`, so
a retry after a partial failure can't send the customer a second estimate.

**Partial batch failures.** `ReportBatchItemFailures` means one bad record in a
batch of ten retries alone instead of replaying the whole batch. Combined with
`BisectBatchOnFunctionError`, a single poison record can't stall the shard.

**On-demand billing.** Moving inquiries are spiky — a Saturday morning after an
ad runs looks nothing like a Tuesday night. Provisioned capacity would mean
paying for peak all week or throttling the peak.

**Graviton (arm64).** Same code, roughly 20% cheaper per millisecond.

**OIDC in CI.** The deploy role is assumed via GitHub's OIDC provider. No AWS
access keys sitting in repository secrets waiting to leak.

---

## Notifications: what to set up

You said you have nothing yet. Here's the order that gets you working fastest.

**Email — Amazon SES.** Verify `waigalmovers.com` as a domain identity, not
just one address, so you can send from `quotes@waigalmovers.com` and reply-to
`booking@waigalmovers.com`. SES gives you three DKIM CNAME records; add them in
Hostinger's DNS panel, plus an SPF TXT record. New accounts start in the SES
sandbox — you can only send to verified addresses until you request production
access, which is a short form and usually clears in a day. **Request it before
you launch**, not the morning you launch.

**Owner alerts — SNS with an email subscription.** Already in the template. You
confirm the subscription once and every new lead hits your inbox with the
volume, truck, and price band in the subject line. Zero setup cost.

**SMS — wait on this one.** US carriers require a registered origination
identity now, so you can't just call `sns:Publish` to a customer's phone and
have it arrive. Through AWS End User Messaging you'd register a toll-free
number, which is inexpensive but takes a couple of weeks to clear. Two options:

- *If SMS can wait:* start the toll-free registration now, ship with email
  only, and add SMS when the number clears. The notifier is already the right
  place to bolt it on.
- *If you want SMS this month:* Twilio's verification is faster in practice.
  You'd put the auth token in Secrets Manager and add one call to the notifier.

Either way, keep the `smsOptIn` checkbox and store the consent — carriers care,
and so does the TCPA. Don't text anyone who didn't tick it.

---

## Deploying

```bash
# one-time
sam build --use-container
sam deploy --guided \
  --parameter-overrides \
    Stage=dev \
    AllowedOrigin=https://waigalmovers.com \
    FromAddress=quotes@waigalmovers.com \
    OwnerEmail=you@example.com
```

Take `ApiEndpoint` from the stack outputs and set it in the estimator page:

```js
const CONFIG = {
  apiEndpoint: "https://xxxx.execute-api.us-east-1.amazonaws.com/dev/quotes",
  ...
```

Confirm the SNS subscription email, then submit a test quote.

### Hosting the page

S3 bucket with public access blocked, fronted by CloudFront with Origin Access
Control. Point `estimate.waigalmovers.com` at the distribution with an ACM
certificate in `us-east-1`. The page is static, so it costs pennies and
survives any traffic your ads can produce.

---

## Reading your leads

```bash
# every quote from a given day, newest first
aws dynamodb query \
  --table-name waigal-quotes-prod \
  --index-name byDate \
  --key-condition-expression "gsi1pk = :d" \
  --expression-attribute-values '{":d":{"S":"DATE#2026-08-16"}}' \
  --scan-index-forward
```

---

## Layout

```
template.yaml                       SAM stack
layers/pricing/python/pricing.py    the pricing engine (shared)
src/submit_quote/app.py             POST /quotes
src/get_rates/app.py                GET /rates
src/notifier/app.py                 stream consumer
src/notifier/templates.py           customer email bodies
web/index.html                      the estimator page
.github/workflows/deploy.yml        OIDC deploy
```

## What's not built yet

- Distance lookup from ZIP pair (currently a manual miles field)
- Admin view for the leads in the table
- Calendar availability so a date can actually be held
