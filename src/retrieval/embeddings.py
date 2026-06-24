"""Embedding generation using sentence-transformers.

Provides a lazy-loading EmbeddingGenerator singleton so the model
is only loaded when first needed, not at import time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingGenerator:
    """Lazy-loading wrapper around sentence-transformers embedding model."""

    _model: object | None = field(default=None, init=False, repr=False)
    _model_name: str = field(default_factory=lambda: settings.EMBEDDING_MODEL, init=False)

    def _ensure_model(self) -> None:
        """Load the model on first use."""
        if self._model is not None:
            return
        logger.info("Loading embedding model: %s", self._model_name)
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self._model_name)
        logger.info("Embedding model loaded (dim=%d)", self.get_dimension())

    def get_dimension(self) -> int:
        """Return the embedding dimension."""
        self._ensure_model()
        # type: ignore[union-attr]
        return self._model.get_sentence_embedding_dimension()

    def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding vector.
        """
        self._ensure_model()
        # type: ignore[union-attr]
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in a batch.

        Args:
            texts: List of input texts to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        if not texts:
            return []
        self._ensure_model()
        # type: ignore[union-attr]
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return [v.tolist() for v in vectors]


# Module-level singleton — lazy, only loads on first call
embedding_generator = EmbeddingGenerator()
