"""Few-shot examples for calibrating the LLM's behavior.

Provides one complete worked example (Bacon-Wrapped Scallops) showing
the expected tool call sequence and output format.
"""

from __future__ import annotations

FEW_SHOT_EXAMPLE = """## Worked Example: Bacon-Wrapped Scallops

Menu Item: "Bacon-Wrapped Scallops"
Description: "Pan-seared diver scallops wrapped in applewood-smoked bacon, finished with a maple bourbon glaze"
Category: appetizers | Service: passed | Dietary: GF

Tool calls:
1. search_catalog("diver scallops") → SCALLOP, SEA, DIVER, DRY PACK (Sysco #7067228)
2. get_item_price("7067228", "2 each") → $5.71
3. search_catalog("applewood smoked bacon") → BACON, SMOKED, APPLEWOOD (Sysco #4842788)
4. get_item_price("4842788", "0.5 oz") → $0.42
5. search_catalog("maple syrup pure") → MAPLE SYRUP, PURE (Sysco #7751158)
6. get_item_price("7751158", "0.25 oz") → $0.29
7. search_catalog("bourbon") → No matches found
8. search_catalog("butter unsalted") → BUTTER, UNSALTED (Sysco #8187926)
9. get_item_price("8187926", "0.5 tbsp") → $0.04

Final result:
  item_name: "Bacon-Wrapped Scallops"
  category: "appetizers"
  ingredients:
    - Sea scallops, diver: 2 each, $5.71, sysco_catalog, #7067228
    - Applewood smoked bacon: 0.5 oz (1 strip), $0.42, sysco_catalog, #4842788
    - Pure maple syrup: 0.25 oz, $0.29, sysco_catalog, #7751158
    - Bourbon: 0.25 oz, null, not_available
    - Unsalted butter: 0.5 tbsp, $0.04, sysco_catalog, #8187926
  ingredient_cost_per_unit: $6.46

Note: Bourbon is not available in the Sysco catalog (it's a specialty spirit).
Mark it as not_available with unit_cost: null."""
