"""Catalog index — Lexical search over Sysco catalog using RapidFuzz."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from rapidfuzz import fuzz


class CatalogMatch(BaseModel):
    """A lexical match result from the catalog index."""

    item_number: str
    description: str
    brand: str
    unit_of_measure: str
    cost_per_case: float
    score: float


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Raw catalog entry from CSV, plus pre-computed normalized form."""

    item_number: str
    description: str
    brand: str
    unit_of_measure: str
    cost_per_case: float
    normalized: str  # Pre-computed at index build time


def normalize_catalog_entry(description: str) -> str:
    """Normalize Sysco catalog format for matching."""
    text = description.lower()
    text = text.replace(",", " ")
    text = re.sub(r"\s+", " ", text)
    text = " ".join(sorted(text.split()))
    return text.strip()


def normalize_query(ingredient: str) -> str:
    """Normalize menu ingredient name for matching."""
    text = ingredient.lower()
    text = text.replace("-", " ")
    text = re.sub(r"\b(the|a|an|of|with)\b", "", text)
    text = re.sub(r"\s+", " ", text)
    text = " ".join(sorted(text.split()))
    return text.strip()


def _parse_cost(cost_str: str) -> float:
    """Parse cost string like '$289.50' to float."""
    return float(cost_str.replace("$", "").replace(",", "").strip())


class CatalogIndex:
    """Lexical search index over the Sysco catalog."""

    def __init__(self, entries: list[CatalogEntry]) -> None:
        self._entries = entries

    @classmethod
    def from_csv(cls, csv_path: str | Path) -> CatalogIndex:
        """Load catalog from CSV and build normalized index."""
        entries: list[CatalogEntry] = []
        path = Path(csv_path)

        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                description = row.get("Product Description", "").strip()
                if not description:
                    continue

                entries.append(
                    CatalogEntry(
                        item_number=row.get("Sysco Item Number", "").strip(),
                        description=description,
                        brand=row.get("Brand", "").strip(),
                        unit_of_measure=row.get("Unit of Measure", "").strip(),
                        cost_per_case=_parse_cost(row.get("Cost", "$0").strip()),
                        normalized=normalize_catalog_entry(description),
                    )
                )

        return cls(entries=entries)

    def search(
        self, query: str, max_results: int = 5, threshold: float = 40.0
    ) -> list[CatalogMatch]:
        """Lexical search using RapidFuzz token-sort-ratio."""
        normalized_query = normalize_query(query)
        entry_map = {e.item_number: e for e in self._entries}

        lexical_scores: dict[str, float] = {}
        for entry in self._entries:
            score = fuzz.token_sort_ratio(normalized_query, entry.normalized)
            if score >= threshold:
                lexical_scores[entry.item_number] = score

        sorted_lexical = sorted(
            lexical_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        return self._build_matches(sorted_lexical[:max_results], entry_map)

    def _build_matches(
        self,
        scored_ids: list[tuple[str, float]],
        entry_map: dict[str, CatalogEntry],
    ) -> list[CatalogMatch]:
        """Map scored item ids back into display-ready catalog matches."""
        top_matches: list[CatalogMatch] = []
        for item_id, score in scored_ids:
            if item_id not in entry_map:
                continue

            entry = entry_map[item_id]
            top_matches.append(
                CatalogMatch(
                    item_number=entry.item_number,
                    description=entry.description,
                    brand=entry.brand,
                    unit_of_measure=entry.unit_of_measure,
                    cost_per_case=entry.cost_per_case,
                    score=round(score, 2),
                )
            )
        return top_matches

    def get_by_item_number(self, item_number: str) -> CatalogEntry | None:
        """Exact lookup by Sysco item number."""
        for entry in self._entries:
            if entry.item_number == item_number:
                return entry
        return None

    @property
    def size(self) -> int:
        """Number of entries in the index."""
        return len(self._entries)


@lru_cache
def build_catalog_index(csv_path: str = "data/sysco_catalog.csv") -> CatalogIndex:
    """Build and cache the catalog index singleton."""
    return CatalogIndex.from_csv(csv_path)
