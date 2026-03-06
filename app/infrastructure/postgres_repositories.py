"""PostgreSQL repository implementations (Adapters).

Implement the domain Protocol interfaces using SQLAlchemy async sessions.
Uses Entity-Model mapper pattern: domain entities ↔ SQLAlchemy models.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import EstimationJob, ItemResult
from app.domain.value_objects import EstimationStatus, IngredientCost, IngredientSource
from app.infrastructure.models import EstimationJobModel, ItemResultModel


class PostgresEstimationRepository:
    """Implements EstimationRepository Protocol with PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, job: EstimationJob) -> EstimationJob:
        model = self._to_model(job)
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get(self, estimation_id: str) -> EstimationJob | None:
        result = await self._session.execute(
            select(EstimationJobModel).where(
                EstimationJobModel.id == estimation_id
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def update_progress(
        self, estimation_id: str, items_completed: int
    ) -> None:
        await self._session.execute(
            update(EstimationJobModel)
            .where(EstimationJobModel.id == estimation_id)
            .values(items_completed=items_completed)
        )
        await self._session.commit()

    async def update_status(
        self, estimation_id: str, status: EstimationStatus
    ) -> None:
        await self._session.execute(
            update(EstimationJobModel)
            .where(EstimationJobModel.id == estimation_id)
            .values(status=status.value)
        )
        await self._session.commit()

    async def update_quote(
        self, estimation_id: str, quote_json: dict[str, object]
    ) -> None:
        await self._session.execute(
            update(EstimationJobModel)
            .where(EstimationJobModel.id == estimation_id)
            .values(quote_json=quote_json)
        )
        await self._session.commit()

    @staticmethod
    def _to_model(entity: EstimationJob) -> EstimationJobModel:
        return EstimationJobModel(
            id=entity.id,
            event_name=entity.event_name,
            total_items=entity.total_items,
            items_completed=entity.items_completed,
            status=entity.status.value,
            menu_spec_json=entity.menu_spec_json,
            quote_json=entity.quote_json,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    @staticmethod
    def _to_entity(model: EstimationJobModel) -> EstimationJob:
        return EstimationJob(
            id=model.id,
            event_name=model.event_name,
            total_items=model.total_items,
            items_completed=model.items_completed,
            status=EstimationStatus(model.status),
            created_at=model.created_at,
            updated_at=model.updated_at,
            menu_spec_json=model.menu_spec_json,
            quote_json=model.quote_json,
        )


class PostgresItemResultRepository:
    """Implements ItemResultRepository Protocol with PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, result: ItemResult) -> ItemResult:
        if result.item_key:
            existing_result = await self._session.execute(
                select(ItemResultModel).where(
                    ItemResultModel.estimation_id == result.estimation_id,
                    ItemResultModel.item_key == result.item_key,
                )
            )
            existing_model = existing_result.scalar_one_or_none()
            if existing_model is not None:
                return self._to_entity(existing_model)

        model = self._to_model(result)
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_estimation(
        self, estimation_id: str
    ) -> list[ItemResult]:
        query_result = await self._session.execute(
            select(ItemResultModel)
            .where(ItemResultModel.estimation_id == estimation_id)
            .order_by(ItemResultModel.completed_at.asc(), ItemResultModel.id.asc())
        )
        models = query_result.scalars().all()
        return [self._to_entity(m) for m in models]

    @staticmethod
    def _to_model(entity: ItemResult) -> ItemResultModel:
        ingredients_json: list[dict[str, object]] = [
            {
                "name": ic.name,
                "quantity": ic.quantity,
                "unit_cost": ic.unit_cost,
                "source": ic.source.value,
                "sysco_item_number": ic.sysco_item_number,
            }
            for ic in entity.ingredients
        ]
        return ItemResultModel(
            id=entity.id,
            estimation_id=entity.estimation_id,
            item_name=entity.item_name,
            category=entity.category,
            item_key=entity.item_key,
            ingredients_json=ingredients_json,
            ingredient_cost_per_unit=entity.ingredient_cost_per_unit,
            status=entity.status,
            completed_at=entity.completed_at,
        )

    @staticmethod
    def _to_entity(model: ItemResultModel) -> ItemResult:
        ingredients = [
            IngredientCost(
                name=str(ic_dict.get("name", "")),
                quantity=str(ic_dict.get("quantity", "")),
                unit_cost=float(str(ic_dict["unit_cost"])) if ic_dict.get("unit_cost") is not None else None,
                source=IngredientSource(str(ic_dict.get("source", "not_available"))),
                sysco_item_number=(
                    str(ic_dict["sysco_item_number"])
                    if ic_dict.get("sysco_item_number") is not None
                    else None
                ),
            )
            for ic_dict in model.ingredients_json
        ]
        return ItemResult(
            id=model.id,
            estimation_id=model.estimation_id,
            item_name=model.item_name,
            category=model.category,
            ingredients=ingredients,
            ingredient_cost_per_unit=model.ingredient_cost_per_unit,
            item_key=model.item_key,
            status=model.status,
            completed_at=model.completed_at,
        )
