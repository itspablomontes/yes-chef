"""SQLAlchemy ORM models for persistence.

These are NOT domain entities — they are database-specific representations.
Conversion between domain entities and ORM models happens in the repository
implementations via _to_model() / _to_entity() mappers.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

json_type = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    metadata = MetaData(naming_convention=convention)


class EstimationJobModel(Base):
    """ORM model for estimation_jobs table."""

    __tablename__ = "estimation_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_name: Mapped[str] = mapped_column(String)
    total_items: Mapped[int] = mapped_column(Integer)
    items_completed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")
    menu_spec_json: Mapped[dict[str, object]] = mapped_column(json_type, default=dict)
    quote_json: Mapped[dict[str, object] | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ItemResultModel(Base):
    """ORM model for item_results table."""

    __tablename__ = "item_results"
    __table_args__ = (
        Index(
            "uq_item_results_estimation_item_key",
            "estimation_id",
            "item_key",
            unique=True,
            sqlite_where=text("item_key IS NOT NULL"),
            postgresql_where=text("item_key IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    estimation_id: Mapped[str] = mapped_column(
        ForeignKey("estimation_jobs.id")
    )
    item_name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    item_key: Mapped[str | None] = mapped_column(String, nullable=True)
    ingredients_json: Mapped[list[dict[str, object]]] = mapped_column(json_type)
    telemetry_json: Mapped[dict[str, object]] = mapped_column(json_type, default=dict)
    ingredient_cost_per_unit: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, default="completed")
    completed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
