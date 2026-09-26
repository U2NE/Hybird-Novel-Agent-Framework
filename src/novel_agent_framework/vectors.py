from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from abc import ABC, abstractmethod
from typing import Sequence

from .errors import ProviderUnavailable


class VectorProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class DisabledVectorProvider(VectorProvider):
    name = "disabled"
    model = "none"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ProviderUnavailable(
            "vector retrieval is disabled; core retrieval continues with FTS + graph"
        )


class SentenceTransformersProvider(VectorProvider):
    name = "sentence-transformers"

    def __init__(self, model: str = "intfloat/multilingual-e5-base") -> None:
        self.model = model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise ProviderUnavailable(
                "sentence-transformers extra is not installed"
            ) from exc
        self._model = SentenceTransformer(model)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in vectors]


class OpenAIEmbeddingProvider(VectorProvider):
    name = "openai"

    def __init__(self, model: str) -> None:
        self.model = model
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise ProviderUnavailable("OpenAI Python package is not installed") from exc
        self._client = OpenAI()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        result = self._client.embeddings.create(model=self.model, input=list(texts))
        return [list(map(float, item.embedding)) for item in result.data]


class DeterministicHashProvider(VectorProvider):
    """Test-only semantic adapter; never selected by production config."""

    name = "deterministic-test"
    model = "sha256-32"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            raw = [(byte - 127.5) / 127.5 for byte in digest]
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            vectors.append([v / norm for v in raw])
        return vectors


def build_provider(name: str, model: str | None = None) -> VectorProvider | None:
    if name in {"", "disabled", "none"}:
        return None
    if name == "sentence-transformers":
        return SentenceTransformersProvider(model or "intfloat/multilingual-e5-base")
    if name == "openai":
        if not model:
            raise ProviderUnavailable("OpenAI embedding provider requires an explicit model")
        return OpenAIEmbeddingProvider(model)
    raise ProviderUnavailable(f"unsupported vector provider: {name}")


def rebuild_vector_embeddings(
    con: sqlite3.Connection,
    provider: VectorProvider,
    *,
    batch_size: int = 64,
) -> dict[str, object]:
    rows = list(
        con.execute(
            "SELECT doc_id,content,canonical_revision FROM search_docs ORDER BY doc_id"
        )
    )
    con.execute(
        "DELETE FROM vector_embeddings WHERE provider=? AND model=?",
        (provider.name, provider.model),
    )
    inserted = 0
    dimensions: int | None = None
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        vectors = provider.embed([row["content"] for row in batch])
        if len(vectors) != len(batch):
            raise ProviderUnavailable("embedding provider returned wrong vector count")
        for row, vector in zip(batch, vectors, strict=True):
            if dimensions is None:
                dimensions = len(vector)
            if len(vector) != dimensions:
                raise ProviderUnavailable("embedding provider returned inconsistent dimensions")
            con.execute(
                """
                INSERT INTO vector_embeddings(
                  doc_id,provider,model,dimensions,vector_json,canonical_revision
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    row["doc_id"],
                    provider.name,
                    provider.model,
                    dimensions,
                    json.dumps(vector, separators=(",", ":")),
                    int(row["canonical_revision"]),
                ),
            )
            inserted += 1
    current = con.execute(
        "SELECT canonical_revision FROM project WHERE id='default'"
    ).fetchone()
    con.execute(
        """
        INSERT INTO projection_meta(name,canonical_revision,status)
        VALUES(?,?,'ready')
        ON CONFLICT(name) DO UPDATE SET
          canonical_revision=excluded.canonical_revision,
          status='ready',
          updated_at=CURRENT_TIMESTAMP
        """,
        (f"vector:{provider.name}:{provider.model}", int(current["canonical_revision"])),
    )
    return {
        "provider": provider.name,
        "model": provider.model,
        "documents": inserted,
        "dimensions": dimensions or 0,
    }
