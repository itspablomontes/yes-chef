# Technical Assessment: Yes Chef, AI Agent for Catering Estimation

## The Project
Below is a realistic business challenge that mimics real world problems we're trying to solve with ABLE. Your goal is to review the business information and technical considerations to prepare a live, agentic solution solves a real problem for the client. This is not meant to be a fully hardened & tested project and there is no right answer, but an opportunity to see how you think about architecting solutions. **Good luck & have fun.** 

## Company Overview

**Elegant Foods** is a US-based food catering company specializing in elegant, large-scale West Coast events including high-end weddings, corporate gatherings, product launches, and public celebrations.

Elegant Foods works with premier Event Planning firms who act as Project Managers on behalf of their clients. When an Event Planner sub-contracts Elegant Foods, they are responsible for all Food & Beverage activities for the event, including menu planning, ingredient sourcing, preparation, service, and bar operations.

---

## The Business Problem

Unlike restaurants with predictable daily operations, catering companies operate on a **project-based model**. Every event must be won through competitive bidding.

- Elegant Foods receives **15-25 bid requests per week** during peak season
- Industry conversion rates hover around **5-10%** — most bids don't result in won business
- Despite the low win rate, **every bid must be treated seriously**
- Event Planners expect quick turnaround on quotes (often 24-48 hours)
- Elegant Foods employs **2 full-time Estimators**, and when one is out, bids get missed

The estimation process involves taking menu specifications from Event Planners, breaking each dish into its component ingredients, looking up ingredient costs from their supplier (Sysco), and producing a per-unit ingredient cost estimate. The business then applies category-specific markups before submitting the final bid.

Elegant Foods believes an AI-powered estimation agent could handle the ingredient costing work, letting their Estimators focus on review, markup decisions, relationship management, and edge cases.

---

## Your Assignment

Build a working prototype of an AI agent system that takes a catering menu specification and produces a per-unit ingredient cost estimate for each menu item — using an orchestration pattern designed for production reliability.

### The Core Task

Given:
- A **menu specification** (`menu_spec.json`) containing 32 items across appetizers, main plates, desserts, and cocktails
- A **Sysco price list** (`sysco_catalog.csv`) with ~565 ingredient items

Your agent must process each menu item by:
1. Breaking the dish into its component ingredients (the agent reasons about what goes into each dish)
2. Estimating the quantity of each ingredient needed per serving
3. Looking up ingredient costs from the Sysco price list
4. Calculating the total ingredient cost per unit (per piece, per plate, per serving, or per drink)
5. Producing a structured quote conforming to the provided schema

### The Architectural Considerations: Long Horizon Tasks

In production, estimation requests can contain 50-100+ menu items. A single LLM session processing all items sequentially leads to:

1. **Context degradation** — model quality drops as conversation history grows
2. **No recoverability** — if the process fails on item 40 of 50, all work is lost
3. **No observability** — there's no way to report progress or intermediate results

**You should take the following into consideration**

- **Persisting progress** so processing can resume if interrupted
- **Context Management** - ensuring the agent both has and has access to the right context at the right time
- **Carry forward learnings** — if the agent discovers that wagyu beef isn't available from Sysco, it shouldn't re-discover this when it encounters another wagyu dish

**We will test resumability by interrupting your system mid-run and restarting it.**

---

## What You're Working With

### Menu Specification (`menu_spec.json`)

A JSON file describing the event menu. Items include a name, description, dietary notes, and category. Your agent must reason about the ingredients — they are not listed explicitly.

```json
{
  "event": "Anderson-Lee Wedding Reception",
  "guest_count_estimate": 175,
  "categories": {
    "appetizers": [
      {
        "name": "Bacon-Wrapped Scallops",
        "description": "Pan-seared diver scallops wrapped in applewood-smoked bacon, finished with a maple bourbon glaze",
        "dietary_notes": "GF",
        "service_style": "passed"
      }
    ],
    "main_plates": [
      {
        "name": "Filet Mignon",
        "description": "8oz center-cut filet mignon with roasted garlic compound butter, truffle mashed potatoes, and grilled asparagus with hollandaise",
        "dietary_notes": "GF"
      }
    ],
    "desserts": [...],
    "cocktails": [...]
  }
}
```

### Sysco Price List (`sysco_catalog.csv`)

A CSV with ~565 items from Elegant Foods' primary food distributor. Columns:

| Column | Description |
|--------|-------------|
| Contract Item # | Sequential row number |
| AASIS Item # | Internal reference number |
| Sysco Item Number | Supplier catalog number |
| Brand | Supplier brand code (e.g., `SYS CLS`, `SYS IMP`, `IMPFRSH`) |
| Product Description | Item name in ALL CAPS, comma-separated format |
| Unit of Measure | Pack size (e.g., `2/5 LB`, `12/1 QT`, `36/1 LB`) |
| Cost | Price per case |

**Important**: Not every ingredient your agent needs will be in this catalog. Specialty items (wagyu beef, high-end spirits, imported truffles, saffron, etc.) are not available through Sysco. Your system should handle missing items gracefully — the output schema supports marking ingredients as `"estimated"` or `"not_available"` via the `source` field.

**Also note**: Prices in the catalog are **per case**, not per unit. Your agent will need to calculate the per-serving cost from the case price and pack size (e.g., a case of 36 x 1LB butter at $198.60 means butter costs ~$5.52/lb).

### Quote Output Schema (`quote_schema.json`)

Your output must conform to this schema. The key structure is:

```json
{
  "quote_id": "...",
  "event": "Anderson-Lee Wedding Reception",
  "generated_at": "2025-09-01T12:00:00Z",
  "line_items": [
    {
      "item_name": "Bacon-Wrapped Scallops",
      "category": "appetizers",
      "ingredients": [
        {
          "name": "Sea scallops, diver, dry pack",
          "quantity": "2 each",
          "unit_cost": 5.71,
          "source": "sysco_catalog",
          "sysco_item_number": "7067228"
        },
        {
          "name": "Applewood smoked bacon",
          "quantity": "1 strip (0.5 oz)",
          "unit_cost": 0.42,
          "source": "sysco_catalog",
          "sysco_item_number": "4842788"
        },
        {
          "name": "Bourbon",
          "quantity": "0.25 oz",
          "unit_cost": null,
          "source": "not_available",
          "sysco_item_number": null
        }
      ],
      "ingredient_cost_per_unit": 7.85
    }
  ]
}
```

See the provided schema file for the full specification.

---

## Technical Requirements

- **LLM**: Use any LLM you prefer (Claude, GPT, Gemini, etc.). The orchestration pattern matters, not the specific model.
- **Framework**: Use any agent framework (or none — raw API calls are fine). We care about your design decisions, not your framework choice.
- **Language**: Python.
- **Deployment**: Your solution must be deployable to a cloud environment (e.g., AWS, GCP, Azure, Railway, Render, Fly.io, or similar). Include clear deployment instructions so we can stand up the backend/API with no ambiguity. Local runability is fine for development, but cloud deployability is a core requirement.
- **Source Code**: Provide a public GitHub repository.

### Deliverables

1. Working system that processes the provided menu specification into a priced quote
2. GitHub repository with a README explaining:
   - How to deploy the backend/API to a cloud environment (step-by-step)
   - Your orchestration architecture and why you designed it that way
   - How you designed for performance (eg state, persistence, summarization, etc)
   - What you would improve with more time

### Submission

Reply to the original email with a link to your public GitHub repository. Your repo must include clear setup and run instructions — we should be able to clone it and deploy it to a cloud environment with no ambiguity.

---

## Evaluation Criteria

We're evaluating how you think about building reliable agent systems, not just whether the output is correct.

### What We're NOT Looking For

- A UI and polish
- Comprehensive test coverage — we are not evaluating test completeness. Basic sanity checks are fine, but effort is better spent on architecture, reliability, and deployability.
- Over-engineered solutions that anticipate every edge case

---

## A Note on the Data

The Sysco catalog is intentionally imperfect. Some ingredients will match exactly, some will require interpreting the catalog's naming conventions (e.g., "applewood-smoked bacon" in the menu vs. `BACON, SMOKED, APPLEWOOD, THICK CUT` in the catalog), and some won't be there at all. How your system handles these cases is something to condsider.

Prices in the catalog are **per case** in food service quantities. 
---

## Timeline & Budget

- **Timeline**: 1 week from receipt of this brief
- **Budget**: $50 USD will be provided via a virtual credit card for any API or hosting costs

---

## Questions?

If you have questions about the requirements or need clarification, you can [book 15 minutes with me](https://calendar.app.google/4xsrJviL2jJ3ZmrB8). 
