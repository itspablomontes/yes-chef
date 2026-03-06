"""Tool: get_item_price — calculate per-serving cost from case pricing.

Handles UoM parsing and deterministic cost calculation.
Architecture principle: tools do math, LLMs do reasoning (ADR-006).
"""

from __future__ import annotations

import re

from langchain_core.tools import tool

from app.infrastructure.catalog_index import build_catalog_index

# Unit conversion factors to ounces (base unit)
_TO_OZ: dict[str, float] = {
    "OZ": 1.0,
    "LB": 16.0,
    "GAL": 128.0,
    "QT": 32.0,
    "PT": 16.0,
    "CT": 1.0,     # count-based, special handling
    "DZ": 12.0,    # dozen → count
    "EACH": 1.0,
    "EA": 1.0,
    "SHT": 1.0,    # sheet = 1 each
    "IN": 1.0,     # inches, count-based
    "FT": 1.0,     # feet, count-based
    "ML": 0.033814, # ml to oz
    "LT": 33.814,   # liter to oz
}

# These units are count-based, not weight/volume
_COUNT_UNITS = {"CT", "DZ", "EACH", "EA", "SHT", "IN", "FT"}


def parse_uom(uom: str) -> tuple[int, float, str]:
    """Parse Sysco UoM format into (packs, quantity_per_pack, unit).

    Examples:
        '2/5 LB'   → (2, 5.0, 'LB')    → 10 LB per case
        '20/8 OZ'  → (20, 8.0, 'OZ')   → 160 OZ per case
        '1/15 LB'  → (1, 15.0, 'LB')   → 15 LB per case
        '36/1 LB'  → (36, 1.0, 'LB')   → 36 LB per case
        '1/15 DZ'  → (1, 15.0, 'DZ')   → 15 DZ per case (180 count)
        '12/1 QT'  → (12, 1.0, 'QT')   → 12 QT per case
    """
    uom = uom.strip().upper()

    # Pattern: "N/Q UNIT" (e.g., "2/5 LB", "20/8 OZ")
    match = re.match(r"(\d+)/(\d+\.?\d*)\s*([A-Z]+)", uom)
    if match:
        packs = int(match.group(1))
        qty = float(match.group(2))
        unit = match.group(3)
        return packs, qty, unit

    # Pattern: "N/Q.Q UNIT" with decimal
    match = re.match(r"(\d+)/(\d+\.?\d*)\s*([A-Z]+)", uom)
    if match:
        return int(match.group(1)), float(match.group(2)), match.group(3)

    # Fallback: try to extract any numbers and a unit
    nums = re.findall(r"\d+\.?\d*", uom)
    unit_match = re.search(r"[A-Z]{2,}", uom)
    unit = unit_match.group(0) if unit_match else "EACH"

    if len(nums) >= 2:
        return int(float(nums[0])), float(nums[1]), unit
    if len(nums) == 1:
        return 1, float(nums[0]), unit

    return 1, 1.0, unit


def calculate_unit_cost(
    case_cost: float, uom: str, quantity_needed: str
) -> dict[str, object]:
    """Calculate per-serving cost from case pricing.

    Args:
        case_cost: Total cost per case
        uom: Unit of measure string from catalog (e.g., "2/5 LB")
        quantity_needed: Amount needed per serving (e.g., "8 oz", "2 each")

    Returns:
        Dict with cost breakdown for the LLM.
    """
    packs, qty_per_pack, case_unit = parse_uom(uom)
    total_case_qty = packs * qty_per_pack

    # Parse the quantity needed
    qty_match = re.match(r"([\d.]+)\s*(.+)", quantity_needed.strip())
    if not qty_match:
        return {
            "error": f"Could not parse quantity '{quantity_needed}'",
            "unit_cost": None,
        }

    needed_amount = float(qty_match.group(1))
    needed_unit = qty_match.group(2).strip().upper()

    # Normalize to common units
    if case_unit in _COUNT_UNITS or needed_unit in {"EACH", "EA", "CT", "PIECE", "PIECES", "STRIP", "STRIPS"}:
        # Count-based: cost per item
        if case_unit == "DZ":
            total_items = total_case_qty * 12.0
        else:
            total_items = total_case_qty
        cost_per_item = case_cost / total_items
        unit_cost = round(cost_per_item * needed_amount, 2)
    else:
        # Weight/volume: convert to OZ
        case_oz = total_case_qty * _TO_OZ.get(case_unit, 1.0)
        cost_per_oz = case_cost / case_oz if case_oz > 0 else 0

        # Map common ingredient units
        needed_unit_map: dict[str, float] = {
            "OZ": 1.0, "LB": 16.0, "TBSP": 0.5, "TSP": 0.1667,
            "CUP": 8.0, "GAL": 128.0, "QT": 32.0, "PT": 16.0,
            "ML": 0.033814, "FL": 1.0, "FLOZ": 1.0,
        }
        # Find best match for needed_unit
        needed_oz_factor = 1.0
        for key, factor in needed_unit_map.items():
            if key in needed_unit:
                needed_oz_factor = factor
                break

        needed_oz = needed_amount * needed_oz_factor
        unit_cost = round(cost_per_oz * needed_oz, 2)

    return {
        "unit_cost": unit_cost,
        "case_cost": case_cost,
        "case_uom": uom,
        "total_case_quantity": f"{total_case_qty} {case_unit}",
        "cost_per_unit": round(case_cost / (total_case_qty if total_case_qty > 0 else 1), 4),
        "quantity_needed": quantity_needed,
    }


@tool
def get_item_price(sysco_item_number: str, quantity_needed: str) -> str:
    """Calculate the per-serving cost for a Sysco catalog item.

    The math is done deterministically in Python — do NOT attempt to
    calculate costs yourself. Provide the Sysco item number from
    search_catalog results and the quantity needed per serving.

    Args:
        sysco_item_number: Sysco Item Number from search_catalog results
        quantity_needed: Amount needed per serving (e.g., "8 oz", "2 each", "0.5 tbsp")

    Returns:
        Cost breakdown including unit cost, case info, and calculation details.
    """
    index = build_catalog_index()
    entry = index.get_by_item_number(sysco_item_number)

    if entry is None:
        return f"Sysco item #{sysco_item_number} not found in catalog."

    result = calculate_unit_cost(
        case_cost=entry.cost_per_case,
        uom=entry.unit_of_measure,
        quantity_needed=quantity_needed,
    )

    if "error" in result:
        return f"Error calculating price: {result['error']}"

    return (
        f"Price for {entry.description}:\n"
        f"  Quantity needed: {quantity_needed}\n"
        f"  Case: {entry.unit_of_measure} at ${entry.cost_per_case:.2f}\n"
        f"  Total case quantity: {result['total_case_quantity']}\n"
        f"  Unit cost for {quantity_needed}: ${result['unit_cost']}\n"
    )
