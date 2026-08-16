"""
Pricing engine for the Waigal Movers cost estimator.

This module is the single source of truth for rates. The browser gets the
same numbers from GET /rates, so the client-side preview and the stored
quote can never drift. The server always recomputes — a price arriving in
a request body is treated as untrusted input and only logged for
comparison.
"""

from decimal import Decimal, ROUND_HALF_UP

# --------------------------------------------------------------------------
# RATES — edit here, nowhere else
# --------------------------------------------------------------------------

CONFIG = {
    "localRadiusMiles": 50,
    "minHours": 3,
    "truckFee": 95,
    "crew": {
        "2": {"rate": 129, "throughput": 110},
        "3": {"rate": 169, "throughput": 150},
        "4": {"rate": 209, "throughput": 185},
    },
    "perMileBeyondRadius": 1.35,
    "fuelSurchargePct": 0.06,
    "longDistance": {
        "perCuFt": 0.55,
        "perCuFtPerMile": 0.0022,
        "minimum": 1400,
    },
    "packingHoursFactor": {"none": 0.0, "partial": 0.18, "full": 0.40},
    "supplyPerCuFt": 0.28,
    "disassemblyFlat": 145,
    "storagePerCuFtMonth": 0.42,
    "rangeSpread": 0.12,
}

HOME_SIZES = {
    "studio": 250, "1br": 380, "2br": 620,
    "3br": 960, "4br": 1350, "5br": 1800, "office": 500,
}

DENSITY = {"light": 0.80, "avg": 1.00, "packed": 1.28}

ITEMS = {
    "sectional": {"cuft": 65,  "fee": 0},
    "kingbed":   {"cuft": 70,  "fee": 0},
    "fridge":    {"cuft": 32,  "fee": 0},
    "washer":    {"cuft": 16,  "fee": 0},
    "dryer":     {"cuft": 16,  "fee": 0},
    "tv":        {"cuft": 12,  "fee": 25},
    "treadmill": {"cuft": 28,  "fee": 40},
    "piano":     {"cuft": 70,  "fee": 250},
    "grand":     {"cuft": 110, "fee": 475},
    "pooltable": {"cuft": 100, "fee": 300},
    "safe":      {"cuft": 22,  "fee": 200},
    "mower":     {"cuft": 60,  "fee": 75},
    "moto":      {"cuft": 90,  "fee": 150},
    "aquarium":  {"cuft": 20,  "fee": 120},
}

TRUCKS = [
    {"label": "10 ft", "cuft": 400},
    {"label": "15 ft", "cuft": 760},
    {"label": "20 ft", "cuft": 1015},
    {"label": "26 ft", "cuft": 1600},
]
MAX_CUFT = TRUCKS[-1]["cuft"]
UTILIZATION = 0.88          # never plan a truck past this
MAX_ITEM_COUNT = 12         # per line item, guards against junk input


def _money(value):
    """Round to whole dollars as Decimal so DynamoDB stores exact numbers."""
    return Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def volume(move):
    base = HOME_SIZES.get(move.get("homeSize"))
    if base is None:
        raise ValueError("unknown homeSize")
    density = DENSITY.get(move.get("density", "avg"), 1.0)

    extras = 0
    for item_id, count in (move.get("items") or {}).items():
        spec = ITEMS.get(item_id)
        if not spec:
            continue                       # silently drop unknown ids
        count = max(0, min(int(count), MAX_ITEM_COUNT))
        extras += spec["cuft"] * count

    return round(base * density + extras)


def specialty_fees(move):
    total = 0
    for item_id, count in (move.get("items") or {}).items():
        spec = ITEMS.get(item_id)
        if not spec:
            continue
        count = max(0, min(int(count), MAX_ITEM_COUNT))
        total += spec["fee"] * count
    return total


def access_multiplier(access):
    a = access or {}
    m = 1.0
    m += (int(a.get("stairsFrom", 0)) + int(a.get("stairsTo", 0))) * 0.07
    m += (bool(a.get("elevatorFrom")) + bool(a.get("elevatorTo"))) * 0.10
    m += (bool(a.get("longCarryFrom")) + bool(a.get("longCarryTo"))) * 0.09
    return m


def crew_for(cuft):
    if cuft <= 450:
        return 2
    if cuft <= 1000:
        return 3
    return 4


def truck_for(cuft):
    for t in TRUCKS:
        if cuft <= t["cuft"] * UTILIZATION:
            return {"label": t["label"], "trips": 1}
    trips = -(-cuft // int(MAX_CUFT * UTILIZATION))    # ceil division
    return {"label": "26 ft", "trips": int(trips)}


def calculate(move):
    """Take the `move` block from a request and return a priced estimate."""
    cuft = volume(move)
    miles = max(0, min(int(move.get("miles") or 0), 4000))
    is_local = miles <= CONFIG["localRadiusMiles"]

    crew_size = crew_for(cuft)
    crew = CONFIG["crew"][str(crew_size)]
    truck = truck_for(cuft)

    packing = move.get("packing", "none")
    hours = (cuft / crew["throughput"]) * access_multiplier(move.get("access"))
    hours *= 1 + CONFIG["packingHoursFactor"].get(packing, 0.0)
    if is_local:
        hours = max(hours, CONFIG["minHours"])
    if truck["trips"] > 1:
        hours *= 1 + 0.15 * (truck["trips"] - 1)
    hours = round(hours * 4) / 4

    lines = []
    total = 0.0

    if is_local:
        labor = hours * crew["rate"]
        lines.append((f"{crew_size} movers x {hours} hrs @ ${crew['rate']}/hr", labor))
        total += labor
        lines.append(("Truck & travel fee", CONFIG["truckFee"]))
        total += CONFIG["truckFee"]
        if miles > 0:
            drive = miles * CONFIG["perMileBeyondRadius"]
            lines.append((f"Mileage - {miles} mi", drive))
            total += drive
    else:
        ld = CONFIG["longDistance"]
        base = max(ld["minimum"],
                   cuft * ld["perCuFt"] + cuft * miles * ld["perCuFtPerMile"])
        lines.append((f"Line haul - {cuft} cu ft x {miles} mi", base))
        total += base
        fuel = base * CONFIG["fuelSurchargePct"]
        lines.append(("Fuel surcharge", fuel))
        total += fuel

    spec = specialty_fees(move)
    if spec:
        lines.append(("Specialty item handling", spec))
        total += spec

    add_ons = move.get("addOns") or {}
    if add_ons.get("supplies"):
        s = cuft * CONFIG["supplyPerCuFt"]
        lines.append(("Boxes & materials", s))
        total += s
    if add_ons.get("disassembly"):
        lines.append(("Disassembly & reassembly", CONFIG["disassemblyFlat"]))
        total += CONFIG["disassemblyFlat"]

    storage = cuft * CONFIG["storagePerCuFtMonth"] if add_ons.get("storage") else 0

    spread = CONFIG["rangeSpread"]
    return {
        "cubicFeet": cuft,
        "miles": miles,
        "rateType": "hourly" if is_local else "flat",
        "truck": truck["label"],
        "trips": truck["trips"],
        "crewSize": crew_size,
        "hours": _money(hours * 100) / 100 if is_local else None,
        "low": _money(total * (1 - spread)),
        "high": _money(total * (1 + spread)),
        "point": _money(total),
        "storagePerMonth": _money(storage) if storage else None,
        "lineItems": [{"label": l, "amount": _money(a)} for l, a in lines],
    }


def public_rates():
    """Payload for GET /rates so the browser prices with identical numbers."""
    return {
        "config": CONFIG,
        "homeSizes": HOME_SIZES,
        "density": DENSITY,
        "items": ITEMS,
        "trucks": TRUCKS,
        "utilization": UTILIZATION,
    }
