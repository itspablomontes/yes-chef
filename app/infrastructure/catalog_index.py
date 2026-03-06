"""Catalog index — Hybrid Search Pipeline (Lexical + Semantic).

Uses rapidfuzz for token-sort-ratio matching and Chroma DB for vector semantics,
combining results via Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions
from pydantic import BaseModel
from rapidfuzz import fuzz

from app.infrastructure.settings import get_settings

logger = logging.getLogger(__name__)

class CatalogMatch(BaseModel):
    """A hybrid match result from the catalog index."""

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
    """Hybrid search index over the Sysco catalog."""

    _LEXICAL_ONLY_SCORE = 85.0

    def __init__(self, entries: list[CatalogEntry], collection: chromadb.Collection) -> None:
        self._entries = entries
        self._collection = collection

    @classmethod
    def from_csv(cls, csv_path: str | Path) -> CatalogIndex:
        """Load catalog from CSV, build normalized index, and populate Chroma."""
        settings = get_settings()
        entries: list[CatalogEntry] = []
        path = Path(csv_path)
        
        # Parse CSV
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

        # Initialize Chroma DB (Docker First Approach)
        client = chromadb.PersistentClient(
            path=settings.chroma_path
        )
        
        from typing import cast
        
        openai_ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name="text-embedding-3-small"
        )
        
        collection = client.get_or_create_collection(
            name="sysco_catalog", 
            embedding_function=cast(embedding_functions.EmbeddingFunction, openai_ef)
        )

        # Populate if empty
        if collection.count() == 0:
            logger.info(f"Populating Chroma DB with {len(entries)} catalog items...")
            batch_size = 100
            for i in range(0, len(entries), batch_size):
                batch = entries[i:i+batch_size]
                collection.add(
                    documents=[e.description for e in batch],
                    metadatas=[{"item_number": e.item_number} for e in batch],
                    ids=[e.item_number for e in batch]
                )
            logger.info("Chroma DB population complete.")

        return cls(entries=entries, collection=collection)

    def search(
        self, query: str, max_results: int = 5, threshold: float = 40.0
    ) -> list[CatalogMatch]:
        """Hybrid search combining RapidFuzz lexical match and Chroma vector match."""
        normalized_query = normalize_query(query)
        entry_map = {e.item_number: e for e in self._entries}
        settings = get_settings()

        # 1. Lexical Search (Fuzzy)
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

        # For this catalog size, a strong lexical match is both faster and cheaper
        # than issuing an embedding request for every single tool call.
        if sorted_lexical and sorted_lexical[0][1] >= self._LEXICAL_ONLY_SCORE:
            return self._build_matches(sorted_lexical[:max_results], entry_map)

        if not settings.enable_vector_search:
            return self._build_matches(sorted_lexical[:max_results], entry_map)

        # 2. Semantic Search (Vector)
        vector_scores: dict[str, float] = {}
        try:
            vector_results = self._collection.query(
                query_texts=[query],
                n_results=10,
                include=["distances"]
            )
            
            if vector_results["ids"] and vector_results["distances"]:
                ids = vector_results["ids"][0]
                distances = vector_results["distances"][0]
                # Convert distance to similarity score (1 - normalized distance)
                # Chroma uses L2 distance by default.
                for vid, dist in zip(ids, distances, strict=False):
                    sim_score = max(0.0, 100.0 - (dist * 50.0)) # mapping heuristic
                    vector_scores[vid] = sim_score
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to pure lexical: {e}")

        # 3. Reciprocal Rank Fusion (RRF)
        # RRF formula: 1 / (k + rank)
        k = 60
        rrf_scores: dict[str, float] = {}

        # Rank Lexical
        for rank, (item_id, _) in enumerate(sorted_lexical):
            rrf_scores[item_id] = rrf_scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))
            
        # Rank Vector
        sorted_vector = sorted(vector_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (item_id, _) in enumerate(sorted_vector):
            rrf_scores[item_id] = rrf_scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))

        # Sort combined results
        sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        return self._build_matches(sorted_rrf[:max_results], entry_map, scale_rrf=True)

    def _build_matches(
        self,
        scored_ids: list[tuple[str, float]],
        entry_map: dict[str, CatalogEntry],
        *,
        scale_rrf: bool = False,
    ) -> list[CatalogMatch]:
        """Map scored item ids back into display-ready catalog matches."""
        top_matches: list[CatalogMatch] = []
        for item_id, score in scored_ids:
            if item_id not in entry_map:
                continue

            entry = entry_map[item_id]
            display_score = min(100.0, score * 3000.0) if scale_rrf else score
            top_matches.append(
                CatalogMatch(
                    item_number=entry.item_number,
                    description=entry.description,
                    brand=entry.brand,
                    unit_of_measure=entry.unit_of_measure,
                    cost_per_case=entry.cost_per_case,
                    score=round(display_score, 2),
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
