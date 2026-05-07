"""Recall-target selection strategies for MementoPolicy.maybe_recall.

A strategy picks at most one tool-message index to restore, given:
  - the message list (some tool messages have `memento` set, indicating
    they're currently rendered as the short summary text)
  - the current step
  - a `recent_text` window (the trailing assistant/tool/user messages,
    concatenated) — used by content-aware strategies to score what the
    agent is currently focused on

Strategies return None if no candidate is worth recalling.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional


class RecallStrategy(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def pick(
        self,
        messages: List[Dict[str, Any]],
        *,
        step: int,
        recent_text: str,
    ) -> Optional[int]:
        """Return the index of the message to recall, or None."""
        ...

    @staticmethod
    def _candidate_indices(
        messages: List[Dict[str, Any]], step: int = -1,
    ) -> List[int]:
        """Tool messages that currently have a memento set.

        Phase 9: when step is provided, exclude msgs memento'd in the
        CURRENT step. The engine captures during the chat that follows
        the memento-set; recalling in the same chat would query the
        store before capture has run → 'unknown memento_id'. We require
        at least one chat between memento-set and recall.
        """
        out = []
        for i, m in enumerate(messages):
            if m.get("role") != "tool" or not m.get("memento"):
                continue
            if step >= 0 and m.get("memento_step", -1) >= step:
                continue
            out.append(i)
        return out


class LRURecall(RecallStrategy):
    """Restore the most-recently-evicted tool obs (highest message index).

    Content-blind floor — the value LRU adds is bounded by the chance that
    'most recent eviction' coincides with 'what the agent now wants.'
    """

    name = "lru"

    def pick(self, messages, *, step, recent_text):
        cands = self._candidate_indices(messages, step=step)
        # Phase 9 alignment: pick the OLDEST memento'd msg (cands[0]).
        # The engine's masking processor reliably re-compacts the FIRST
        # marker pair in the prompt every chat (it gets re-added to
        # pending_compactions on each re-prefill). So that's the obs the
        # engine has captured most reliably under its content hash. Using
        # cands[-1] (newest) instead picked an obs the engine may not have
        # gotten around to capturing yet — splice would fail.
        return cands[0] if cands else None


class EmbeddingRecall(RecallStrategy):
    """Restore the mementoed obs whose memento text is most similar to the
    agent's recent reasoning / current intent.

    Uses sentence-transformers MiniLM for the embedding. Embeddings on the
    memento text are computed lazily at recall time and cached on the
    message dict (`_memento_embedding`) so we never re-embed a stable
    memento. The query embedding (the trajectory tail) is computed fresh
    each call.

    Args:
        model_name: HF id for sentence-transformers.
        threshold: minimum cosine similarity to accept a candidate.
            Below threshold → no recall this step.
        device: passed to SentenceTransformer.
    """

    name = "embedding"

    def __init__(
        self,
        *,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        threshold: float = 0.40,
        device: Optional[str] = None,
    ):
        self._threshold = threshold
        self._device = device
        self._model_name = model_name
        self._model = None  # lazy load

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,  # None → auto
            )

    def _embed(self, text: str):
        self._ensure_model()
        # normalize_embeddings=True so dot product == cosine similarity
        return self._model.encode(text, normalize_embeddings=True, convert_to_numpy=True)

    def pick(self, messages, *, step, recent_text):
        cands = self._candidate_indices(messages, step=step)
        if not cands:
            return None
        if not recent_text.strip():
            # No query → fall back to LRU
            return cands[-1]

        import numpy as np
        q = self._embed(recent_text)
        best_idx = None
        best_score = -1.0
        for i in cands:
            m = messages[i]
            emb = m.get("_memento_embedding")
            if emb is None:
                emb = self._embed(m.get("memento") or "")
                m["_memento_embedding"] = emb.tolist()
            else:
                emb = np.asarray(emb, dtype=np.float32)
            score = float(np.dot(q, emb))
            if score > best_score:
                best_score = score
                best_idx = i
        if best_score < self._threshold:
            return None
        return best_idx


def build_recall_strategy(name: str, **kwargs) -> RecallStrategy:
    """Factory used by MementoPolicy."""
    name = (name or "lru").lower()
    if name == "lru":
        return LRURecall()
    if name == "embedding":
        return EmbeddingRecall(**kwargs)
    raise ValueError(f"unknown recall strategy: {name!r}")
