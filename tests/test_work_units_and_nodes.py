from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.contracts.item_pipeline import PlannedIngredient
from app.agent.nodes.batch_router import route_work_item
from app.agent.nodes.reduce import reduce
from app.agent.state import EstimationState
from app.application.work_units import align_completed_items, build_menu_work_units, completed_item_keys
from tests.helpers import sample_menu_spec


def test_planned_ingredient_requires_quantity() -> None:
    with pytest.raises(ValidationError):
        PlannedIngredient.model_validate({"name": "bacon"})


def test_align_completed_items_handles_duplicate_names_in_order() -> None:
    menu_spec = sample_menu_spec()
    completed = [
        {"item_name": "Duplicate Dish", "category": "appetizers", "ingredients": [], "ingredient_cost_per_unit": 0.0},
        {"item_name": "Duplicate Dish", "category": "appetizers", "ingredients": [], "ingredient_cost_per_unit": 0.0},
    ]

    aligned = align_completed_items(menu_spec, completed)

    assert aligned[0]["item_key"] == "appetizers:0"
    assert aligned[1]["item_key"] == "appetizers:1"
    assert completed_item_keys(menu_spec, completed) == {"appetizers:0", "appetizers:1"}


def test_route_work_item_uses_runtime_item_keys_for_resume() -> None:
    menu_spec = sample_menu_spec()
    completed = align_completed_items(
        menu_spec,
        [
            {
                "item_name": "Duplicate Dish",
                "category": "appetizers",
                "ingredients": [],
                "ingredient_cost_per_unit": 0.0,
            }
        ],
    )
    state = EstimationState(menu_spec=menu_spec, completed_items=completed)

    assert route_work_item(state) == "item_worker"

    state = EstimationState(
        menu_spec=menu_spec,
        completed_items=align_completed_items(
            menu_spec,
            [
                {
                    "item_name": "Duplicate Dish",
                    "category": "appetizers",
                    "ingredients": [],
                    "ingredient_cost_per_unit": 0.0,
                },
                {
                    "item_name": "Duplicate Dish",
                    "category": "appetizers",
                    "ingredients": [],
                    "ingredient_cost_per_unit": 0.0,
                },
                {
                    "item_name": "Steak",
                    "category": "main_plates",
                    "ingredients": [],
                    "ingredient_cost_per_unit": 0.0,
                },
            ],
        ),
    )

    assert route_work_item(state) == "reduce"


def test_reduce_separates_failed_items() -> None:
    menu_spec = sample_menu_spec()
    state = EstimationState(
        menu_spec=menu_spec,
        completed_items=[
            {
                "item_name": "Duplicate Dish",
                "category": "appetizers",
                "ingredients": [{"name": "salt", "quantity": "1 tsp", "unit_cost": 0.05, "source": "estimated"}],
                "ingredient_cost_per_unit": 0.05,
            },
            {
                "item_name": "Steak",
                "category": "main_plates",
                "ingredients": [],
                "ingredient_cost_per_unit": 0.0,
                "status": "failed",
            },
        ],
    )

    result = reduce(state)

    assert result["status"] == "completed_with_failures"
    assert result["quote"]["line_items"][0]["item_name"] == "Duplicate Dish"
    assert result["quote"]["failed_items"] == [{"item_name": "Steak", "category": "main_plates"}]


def test_estimation_state_preserves_quote_payload() -> None:
    quote = {
        "quote_id": "q-1",
        "event": "Test Event",
        "generated_at": "2026-03-06T00:00:00Z",
        "line_items": [],
    }
    state = EstimationState(menu_spec=sample_menu_spec(), quote=quote)

    assert state.model_dump().get("quote") == quote


def test_build_menu_work_units_flattens_in_stable_order() -> None:
    units = build_menu_work_units(sample_menu_spec())

    assert [unit["item_key"] for unit in units] == [
        "appetizers:0",
        "appetizers:1",
        "main_plates:0",
    ]
