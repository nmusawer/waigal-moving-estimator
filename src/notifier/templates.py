"""Email bodies for the quote confirmation. Table-based HTML so it survives
Outlook, with a plain-text twin for deliverability."""

BRAND = "Waigal Movers"
INK = "#131C2B"
HAUL = "#F0A831"


def _rows(line_items):
    out = []
    for li in line_items:
        out.append(
            f'<tr><td style="padding:7px 0;border-bottom:1px solid #E2E8F0;'
            f'font-size:14px;color:#334155">{li["label"]}</td>'
            f'<td style="padding:7px 0;border-bottom:1px solid #E2E8F0;'
            f'font-size:14px;text-align:right;color:{INK};'
            f'font-family:monospace">${li["amount"]}</td></tr>'
        )
    return "".join(out)


def customer_html(quote):
    c, e = quote["customer"], quote["estimate"]
    trips = f" (x{e['trips']} trips)" if e.get("trips", 1) > 1 else ""
    hours = (
        f'<tr><td style="padding:4px 0;color:#64748B;font-size:13px">Time on site</td>'
        f'<td style="padding:4px 0;text-align:right;font-size:13px">{e["hours"]} hours</td></tr>'
        if e.get("hours") else ""
    )
    storage = (
        f'<p style="font-size:13px;color:#64748B">Storage, if you use it, runs '
        f'${e["storagePerMonth"]} per month.</p>'
        if e.get("storagePerMonth") else ""
    )

    return f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#EEF1F5">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
       style="max-width:560px;background:#fff;margin:24px 0;font-family:Helvetica,Arial,sans-serif">

  <tr><td style="background:{INK};padding:18px 24px;border-bottom:3px solid {HAUL}">
    <span style="color:#fff;font-size:18px;font-weight:bold;letter-spacing:-0.5px">
      WAIGAL<span style="color:{HAUL}">/</span>MOVERS</span>
  </td></tr>

  <tr><td style="padding:28px 24px 8px">
    <p style="margin:0 0 14px;font-size:15px;color:{INK}">Hi {c['name'].split(' ')[0]},</p>
    <p style="margin:0 0 20px;font-size:15px;color:#334155;line-height:1.55">
      Here's the estimate for your move. Your reference is
      <b style="font-family:monospace">{quote['quoteId']}</b> — mention it when you call
      and we'll pull everything up.</p>

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border-left:3px solid {HAUL};padding-left:16px;margin-bottom:22px">
      <tr><td>
        <div style="font-size:11px;letter-spacing:1px;color:#64748B;text-transform:uppercase">
          Estimated total</div>
        <div style="font-size:30px;font-weight:bold;color:{INK};letter-spacing:-1px;padding-top:4px">
          ${e['low']} <span style="color:#CBD5E1">–</span> ${e['high']}</div>
      </td></tr>
    </table>

    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:22px">
      <tr><td style="padding:4px 0;color:#64748B;font-size:13px">Volume</td>
          <td style="padding:4px 0;text-align:right;font-size:13px">{e['cubicFeet']} cu ft</td></tr>
      <tr><td style="padding:4px 0;color:#64748B;font-size:13px">Truck</td>
          <td style="padding:4px 0;text-align:right;font-size:13px">{e['truck']}{trips}</td></tr>
      <tr><td style="padding:4px 0;color:#64748B;font-size:13px">Crew</td>
          <td style="padding:4px 0;text-align:right;font-size:13px">{e['crewSize']} movers</td></tr>
      {hours}
    </table>

    <div style="font-size:11px;letter-spacing:1px;color:#64748B;text-transform:uppercase;
                padding-bottom:6px">What's in it</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border-top:1px solid #E2E8F0;margin-bottom:18px">
      {_rows(e['lineItems'])}
    </table>
    {storage}

    <p style="font-size:14px;color:#334155;line-height:1.55">
      This is an estimate, not a contract. We confirm the final price after a quick
      walkthrough or video survey — that's also when we lock your date.</p>

    <p style="font-size:14px;color:#334155;line-height:1.55">
      Reply to this email or call us to get on the calendar.</p>
  </td></tr>

  <tr><td style="padding:18px 24px;background:#F8FAFC;border-top:1px solid #E2E8F0">
    <p style="margin:0;font-size:12px;color:#64748B;line-height:1.6">
      {BRAND} · Charlottesville, VA<br>booking@waigalmovers.com</p>
  </td></tr>

</table></td></tr></table></body></html>"""


def customer_text(quote):
    c, e = quote["customer"], quote["estimate"]
    lines = "\n".join(f"  {li['label']}: ${li['amount']}" for li in e["lineItems"])
    return f"""Hi {c['name'].split(' ')[0]},

Here's the estimate for your move. Reference: {quote['quoteId']}

ESTIMATED TOTAL: ${e['low']} - ${e['high']}

  Volume: {e['cubicFeet']} cu ft
  Truck:  {e['truck']}
  Crew:   {e['crewSize']} movers

What's in it:
{lines}

This is an estimate, not a contract. We confirm the final price after a
walkthrough or video survey, and that's when we lock your date.

Reply to this email or give us a call to get on the calendar.

{BRAND} - Charlottesville, VA
booking@waigalmovers.com
"""
