from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, TypeVar
from uuid import uuid4

from memory.advanced_memory import (
    MemoryScoringSignals,
    MemoryScoringWeights,
    advanced_relevance_boost,
    extract_memory_graph,
)
from memory.backend.dry_run import DryRunMetadataStore, DryRunVectorStore
from memory.backend.errors import SchemaVersionError, VectorDimensionError
from memory.backend.log_safety import redact_secrets
from memory.backend.metadata_store import (
    LOCAL_DEFAULT_PROJECT_ID,
    PROJECT_ROLE_READER,
    PROJECT_ROLE_WRITER,
    SQLiteMetadataStore,
    _default_project_id,
    _validate_owner_id,
    _validate_project_id,
)
from memory.backend.mongodb_metadata_store import MongoDBMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import (
    ChromaDBVectorStore,
    ElasticsearchVectorStore,
    InMemoryVectorStore,
    LanceDBVectorStore,
    MilvusVectorStore,
    MongoDBAtlasVectorStore,
    OpenSearchVectorStore,
    PGVectorStore,
    PineconeVectorStore,
    QdrantVectorStore,
    RedisVectorStore,
    TurbopufferVectorStore,
    TypesenseVectorStore,
    VespaVectorStore,
    WeaviateVectorStore,
)
from memory.config import HubConfig, ensure_token_hash_secret, load_config, parse_config
from memory.ingestion.fact_timeline import (
    FactField,
    FactTimelineProjector,
    MemoryField,
    temporal_fact_source_metadata,
)
from memory.ingestion.summary_models import (
    GeneratedSummary,
    GeneratedSummaryProvenance,
    SummaryBasis,
    SummaryProvenanceStatus,
    SummaryType,
)
from memory.ingestion.thread_models import (
    SearchResultMode,
    ThreadMetadataKey,
    ThreadResultKey,
    result_mode_error_message,
    result_mode_values,
    thread_metadata_from_mapping,
)
from memory.ingestion.tokenizer import (
    count_tokens,
    split_token_windows,
    tokenizer_used,
    truncate_to_tokens,
)
from memory.ingestion.validate import (
    SCHEMA_PATH,
    load_schema,
    validate_conversation,
    validate_schema_compatibility,
)
from memory.observability.metrics import metrics
from memory.observability.tracing import start_observability_span
from memory.provider_models import VectorProviderAlias, VectorProviderName

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbeddingProvider:
    """Deterministic local embedding provider for offline/local-first mode."""

    def __init__(self, dimensions: int = 32):
        self.dimension = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_to_vector(text, self.dimension) for text in texts]


class HttpEmbeddingProvider:
    """HTTP embeddings endpoint client using the OpenAI-compatible schema."""

    def __init__(
        self,
        embedding_model: str = "text-embedding-3-small",
        dimension: int = 1536,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
    ):
        key = api_key or os.getenv("EMBEDDING_ENDPOINT_API_KEY")
        if not key:
            logger.warning(
                "EMBEDDING_ENDPOINT_API_KEY is required when providers.embeddings=http"
            )

        self.base_url = base_url.rstrip("/")
        self.api_key = key or ""
        self.embedding_model = embedding_model
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.embedding_model,
            "input": texts,
            "dimensions": self.dimension,
        }
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Embedding endpoint returned HTTP {exc.code}: {redact_secrets(detail)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Embedding endpoint request failed: {redact_secrets(str(exc.reason))}"
            ) from exc
        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Embedding endpoint returned invalid JSON") from exc
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Embedding endpoint response must include a data array")
        embeddings: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise RuntimeError("Embedding endpoint response item is missing embedding")
            vector = item["embedding"]
            if len(vector) != self.dimension:
                raise RuntimeError(
                    "Embedding endpoint returned vector dimensionality "
                    f"{len(vector)}; expected {self.dimension}"
                )
            embeddings.append([float(value) for value in vector])
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Embedding endpoint returned {len(embeddings)} vectors for {len(texts)} inputs"
            )
        return embeddings


@dataclass
class RuntimeDependencies:
    embedding_provider: EmbeddingProvider
    metadata_store: Any
    vector_store: Any
    health_state: dict[str, Any]
    schema_path: Path = SCHEMA_PATH
    conversation_schema: dict[str, Any] | None = None
    allow_trusted_appends: bool = False
    tokenizer_enabled: bool = False
    tokenizer_encoding: str = "cl100k_base"
    ask_max_context_tokens: int = 2000
    chunking_strategy: str = "message"
    chunking_max_tokens: int = 800
    chunking_overlap_tokens: int = 80
    retrieval_vector_score_threshold: float = 7.5
    retrieval_keyword_enabled: bool = True
    retrieval_keyword_candidate_limit: int = 50
    retrieval_keyword_weight: float = 0.25
    retrieval_metadata_weight: float = 0.15
    retrieval_candidate_multiplier: int = 3
    fact_extractor: Any | None = None
    graph_enabled: bool = False
    retrieval_graph_enabled: bool = False
    retrieval_graph_quality_gate_passed: bool = False
    retrieval_graph_weight: float = 0.2
    retrieval_advanced_scoring_enabled: bool = False
    retrieval_recency_weight: float = 0.05
    retrieval_importance_weight: float = 0.15
    retrieval_pin_weight: float = 0.3
    retrieval_access_weight: float = 0.05


@dataclass(frozen=True)
class AuditContext:
    source_surface: str = "service"
    request_id: str | None = None


_VECTOR_COMPATIBILITY_METADATA_PREFIX = "vector_index_compatibility:"
_FALLBACK_POLICY_WARNED: set[str] = set()
_PRODUCTION_FALLBACK_POLICY_WARNED: set[str] = set()
_MEMORY_STATUS_ACTIVE = "active"
_MEMORY_STATUS_PENDING_REVIEW = "pending_review"
_MEMORY_STATUS_QUARANTINED = "quarantined"
_MEMORY_STATUS_REJECTED = "rejected"
_MEMORY_STATUS_ALL = "all"
_MEMORY_STATUS_VALUES = {
    _MEMORY_STATUS_ACTIVE,
    _MEMORY_STATUS_PENDING_REVIEW,
    _MEMORY_STATUS_QUARANTINED,
    _MEMORY_STATUS_REJECTED,
    _MEMORY_STATUS_ALL,
}
_REVIEWABLE_MEMORY_STATUSES = {
    _MEMORY_STATUS_PENDING_REVIEW,
    _MEMORY_STATUS_QUARANTINED,
}


@dataclass(frozen=True)
class ConversationFilters:
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tags: tuple[str, ...] = ()
    thread_id: str | None = None

    @classmethod
    def from_options(
        cls,
        *,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: Sequence[str] | None = None,
        thread_id: str | None = None,
    ) -> "ConversationFilters":
        return cls(
            source=str(source) if source else None,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            tags=tuple(str(tag) for tag in tags or () if str(tag)),
            thread_id=str(thread_id) if thread_id else None,
        )

    @property
    def has_filters(self) -> bool:
        return bool(
            self.source or self.date_from or self.date_to or self.tags or self.thread_id
        )


@dataclass(frozen=True)
class FactFilters:
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    confidence: str | None = None
    status: str = "active"
    source_quality: str | None = None
    save_intent: str | None = None
    save_intent_source: str | None = None
    freshness_from: str | None = None
    freshness_to: str | None = None

    @classmethod
    def from_options(
        cls,
        *,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        source_quality: str | None = None,
        save_intent: str | None = None,
        save_intent_source: str | None = None,
        freshness_from: str | None = None,
        freshness_to: str | None = None,
    ) -> "FactFilters":
        normalized_status = str(status or "active").lower()
        if normalized_status not in {"active", "superseded", "all"}:
            raise ValueError("status must be one of: active, superseded, all")
        return cls(
            source=str(source) if source else None,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            confidence=str(confidence) if confidence else None,
            status=normalized_status,
            source_quality=str(source_quality) if source_quality else None,
            save_intent=str(save_intent) if save_intent else None,
            save_intent_source=str(save_intent_source) if save_intent_source else None,
            freshness_from=str(freshness_from) if freshness_from else None,
            freshness_to=str(freshness_to) if freshness_to else None,
        )

    @property
    def include_superseded(self) -> bool:
        return self.status in {"superseded", "all"}


class _AskResponseKey(StrEnum):
    STATUS = "status"
    RESULTS = "results"
    ANSWER = "answer"
    CITATIONS = "citations"
    CONFIDENCE = "confidence"
    CONFIDENCE_REASON = "confidence_reason"
    ANSWER_BASIS = "answer_basis"
    PROVENANCE = "provenance"
    EVIDENCE = "evidence"
    STRUCTURED_EVIDENCE = "structured_evidence"
    FACTS = "facts"
    LATEST = "latest"
    FACT_TIMELINE = "fact_timeline"
    CONTEXT_TOKENS_USED = "context_tokens_used"
    CHUNKS_SELECTED = "chunks_selected"
    CHUNKS_DROPPED = "chunks_dropped"
    CONTEXT_TRUNCATED = "context_truncated"
    TOKENIZER_USED = "tokenizer_used"


class _AskResponseStatus(StrEnum):
    OK = "ok"


class _AskAnswerBasis(StrEnum):
    DIRECT_MEMORY = "direct_memory"
    FACT_LAYER = "fact_layer"
    MIXED = "mixed"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"


class _FactQualifierKey(StrEnum):
    CORRECTS = "corrects"
    ITEM = "item"
    NORMALIZATION = "normalization"
    PREFERENCE = "preference"
    SAVE_INTENT = "save_intent"
    SAVE_INTENT_EVIDENCE = "save_intent_evidence"
    SAVE_INTENT_SOURCE = "save_intent_source"
    SOURCE_ROLE = "source_role"
    TOPIC = "topic"


class _FactSourceRole(StrEnum):
    ASSISTANT = "assistant"
    USER = "user"


class _FactEvidenceType(StrEnum):
    FACT = "fact"


class _FactSubject(StrEnum):
    USER = "user"


class _FactPredicate(StrEnum):
    COMMAND_NAME = "command_name"
    CREATOR = "creator"
    DESCRIPTION = "description"
    INDEXING_STRATEGY = "indexing_strategy"
    LIKES = "likes"
    OWNS_GUITAR = "owns_guitar"
    OWNS_ITEM = "owns_item"
    PROFILE_IDENTITY = "profile_identity"
    PROFILE_LOCATION = "profile_location"
    PROFILE_NAME = "profile_name"
    PROFILE_ROLE = "profile_role"
    RECURRING_TOPIC = "recurring_topic"


class _FactPredicatePrefix(StrEnum):
    FAVORITE = "favorite_"


class _FactItemPrefix(StrEnum):
    FAVORITE = "favorite "


class _StructuredEvidenceKey(StrEnum):
    FACTS = "facts"
    RESULTS = "results"


class _FactCorrectionGroup(StrEnum):
    ITEM = "item"
    NEW_VALUE = "new"
    OLD_VALUE = "old"


class _FactRuleName(StrEnum):
    OWN = "own"
    FAVORITE = "favorite"
    LIKES = "likes"
    CREATOR = "creator"
    SUBJECT_CREATOR = "subject_creator"
    COMMAND_NAME = "command_name"
    INDEXING_STRATEGY = "indexing_strategy"
    PROJECT_ATTRIBUTE = "project_attribute"
    PROJECT_ATTRIBUTE_CHANGE = "project_attribute_change"
    PROFILE_NAME = "profile_name"
    PROFILE_IDENTITY = "profile_identity"
    PROFILE_ROLE = "profile_role"
    PROFILE_LOCATION = "profile_location"


@dataclass(frozen=True)
class _FactCorrectionMatch:
    span: tuple[int, int]
    item: str
    new_value: str
    old_value: str


_PayloadKey = TypeVar("_PayloadKey", bound=StrEnum)


_RUNTIME: RuntimeDependencies | None = None
_RUNTIME_OVERRIDE: ContextVar[RuntimeDependencies | None] = ContextVar(
    "amh_mvp_runtime_override", default=None
)
_AUDIT_CONTEXT: ContextVar[AuditContext] = ContextVar(
    "amh_audit_context", default=AuditContext()
)


def set_audit_context(
    *, source_surface: str = "service", request_id: str | None = None
) -> Token[AuditContext]:
    return _AUDIT_CONTEXT.set(
        AuditContext(source_surface=source_surface, request_id=request_id)
    )


def reset_audit_context(token: Token[AuditContext]) -> None:
    _AUDIT_CONTEXT.reset(token)


_SEARCH_CANDIDATE_MULTIPLIER = 3
_CONVERSATION_GROUP_SCORE_WINDOW = 0.25
_MAX_MESSAGES = 10_000
_MAX_MESSAGE_BYTES = 1_000_000
_MAX_PAYLOAD_BYTES = 25_000_000
_MAX_RAW_TRANSCRIPT_BYTES = 5_000_000
_MAX_METADATA_BYTES = 1_000_000
_MAX_METADATA_SUMMARY_CHARS = 2_000
_MAX_AUTO_TAGS = 24
_MAX_SENSITIVE_FINDINGS = 16
_SENSITIVE_SCAN_EXCLUDED_KEYS = {
    "id",
    "conversation_id",
    "chunk_id",
    "conversation_hash",
    "created_at",
    "embedding_index",
    "hash",
    "imported_at",
    "index_chunks",
    "message_hashes",
    "message_index",
    "owner_id",
    "parent_conversation_id",
    "project_id",
    "related_conversation_ids",
    "timestamp",
    "updated_at",
    "upstream_thread_id",
}
_ROLE_ALIASES = {
    "human": "user",
    "user_message": "user",
    "ai": "assistant",
    "bot": "assistant",
    "assistant_message": "assistant",
}
_TRANSCRIPT_LINE_RE = re.compile(r"^(User|Assistant):\s?(.*)$", re.IGNORECASE)
_SENSITIVE_CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "secret.private_key",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    ),
    (
        "secret.openai_api_key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    ),
    (
        "secret.github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "secret.slack_token",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b"),
    ),
    (
        "secret.aws_access_key_id",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    (
        "secret.aws_secret_access_key",
        re.compile(
            r"\baws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "secret.bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE),
    ),
    (
        "secret.credential_url",
        re.compile(
            r"\b[A-Za-z][A-Za-z0-9+.-]{2,}://[^/\s:@]+:[^@\s/]{8,}@[^ \t\r\n]+",
            re.IGNORECASE,
        ),
    ),
    (
        "secret.named_credential",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|token)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9][A-Za-z0-9._~+/=-]{15,}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pii.ssn",
        re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    ),
    (
        "pii.passport_number",
        re.compile(
            r"\bpassport(?:[_ -]?(?:number|no)|\s*#)?\s*[:=]\s*[A-Z0-9]{6,9}\b",
            re.IGNORECASE,
        ),
    ),
]
_PAYMENT_CARD_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:\d[ -]?){13,19}(?![A-Za-z0-9_-])")
_TOPIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python", re.compile(r"\bpython\b", re.IGNORECASE)),
    (
        "javascript",
        re.compile(r"\b(javascript|js|node\.?js|typescript|ts)\b", re.IGNORECASE),
    ),
    ("sql", re.compile(r"\b(sql|sqlite|postgres|mysql|database|db)\b", re.IGNORECASE)),
    ("api", re.compile(r"\b(api|rest|endpoint|http|json-rpc)\b", re.IGNORECASE)),
    ("mcp", re.compile(r"\b(mcp|model context protocol)\b", re.IGNORECASE)),
    ("docker", re.compile(r"\b(docker|container|kubernetes|k8s)\b", re.IGNORECASE)),
    (
        "testing",
        re.compile(
            r"\b(test|tests|pytest|unit test|integration test)\b", re.IGNORECASE
        ),
    ),
    (
        "machine-learning",
        re.compile(r"\b(ai|llm|rag|embedding|vector)\b", re.IGNORECASE),
    ),
    (
        "frontend",
        re.compile(r"\b(frontend|ui|css|html|react|vue|angular)\b", re.IGNORECASE),
    ),
    ("backend", re.compile(r"\b(backend|fastapi|flask|server)\b", re.IGNORECASE)),
]
_RESULT_MODES = result_mode_values()


@contextmanager
def _ingestion_stage(stage: str, **attributes: Any) -> Iterator[None]:
    started = time.perf_counter()
    with start_observability_span(
        f"ingestion.{stage}",
        attributes={
            "ingestion.stage": stage,
            **attributes,
        },
    ) as span:
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if span is not None:
                span.set_attribute("memory.duration_ms", elapsed_ms)
            metrics.observe(
                "memory_ingestion_stage_duration_ms",
                elapsed_ms,
                stage=stage,
            )
_FACT_CORRECTION_ITEM = (
    rf"(?P<{_FactCorrectionGroup.ITEM.value}>[A-Za-z0-9][A-Za-z0-9 _-]{{1,80}})"
)
_FACT_CORRECTION_NEW = rf"(?P<{_FactCorrectionGroup.NEW_VALUE.value}>[^.?!\n,]+?)"
_FACT_CORRECTION_OLD = rf"(?P<{_FactCorrectionGroup.OLD_VALUE.value}>[^.?!\n]+)"
_FACT_CORRECTION_RE = re.compile(
    rf"\b(?:(?:actually|no),?\s+|correction:\s+)?my\s+{_FACT_CORRECTION_ITEM}"
    rf"\s+is\s+{_FACT_CORRECTION_NEW},\s+not\s+{_FACT_CORRECTION_OLD}",
    re.IGNORECASE,
)
_FACT_REPLACES_CORRECTION_RE = re.compile(
    rf"\bcorrection:\s+{_FACT_CORRECTION_NEW}\s+replaces\s+{_FACT_CORRECTION_OLD}"
    rf"\s+for\s+my\s+{_FACT_CORRECTION_ITEM}",
    re.IGNORECASE,
)
_GENERIC_ATTRIBUTE_QUESTION_RE = re.compile(
    rf"\bwhat\s+is\s+(?:the\s+)?(?P<{FactField.SUBJECT.value}>[A-Za-z0-9][A-Za-z0-9 _-]{{1,120}})\??\s*$",
    re.IGNORECASE,
)
_GENERIC_ATTRIBUTE_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_FACT_RULES: list[tuple[_FactRuleName, re.Pattern[str]]] = [
    (
        _FactRuleName.OWN,
        re.compile(r"\bI\s+(?:have|own)\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.FAVORITE,
        re.compile(
            r"\bMy\s+favorite\s+(?P<name>[A-Za-z0-9 _-]+?)\s+is\s+(?P<object>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    (
        _FactRuleName.LIKES,
        re.compile(
            r"\bI\s+(?:really\s+)?(?:like|enjoy|prefer)\s+(?P<object>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    (
        _FactRuleName.CREATOR,
        re.compile(r"\bThe\s+creator\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.SUBJECT_CREATOR,
        re.compile(
            r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})\s+creator\s+is\s+(?P<object>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    (
        _FactRuleName.COMMAND_NAME,
        re.compile(r"\bThe\s+command\s+name\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.INDEXING_STRATEGY,
        re.compile(r"\bThe\s+indexing\s+strategy\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.PROJECT_ATTRIBUTE,
        re.compile(
            rf"\b(?P<{FactField.SUBJECT.value}>[A-Z][A-Za-z0-9 _-]{{1,80}})\s+is\s+"
            rf"(?P<{FactField.OBJECT.value}>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    (
        _FactRuleName.PROJECT_ATTRIBUTE_CHANGE,
        re.compile(
            rf"\b(?P<{FactField.SUBJECT.value}>[A-Z][A-Za-z0-9 _-]{{1,80}})\s+"
            rf"(?:changes|changed)\s+to\s+(?P<{FactField.OBJECT.value}>[^.?!\n]+?)"
            r"(?:\s+on\s+\d{4}-\d{2}-\d{2})?(?=$|[.?!\n])",
            re.IGNORECASE,
        ),
    ),
    (
        _FactRuleName.PROFILE_NAME,
        re.compile(r"\bMy\s+name\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.PROFILE_IDENTITY,
        re.compile(r"\bI\s+am\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.PROFILE_IDENTITY,
        re.compile(r"\bI'm\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.PROFILE_ROLE,
        re.compile(r"\bI\s+work\s+as\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        _FactRuleName.PROFILE_LOCATION,
        re.compile(r"\bI\s+live\s+in\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
]
_FACT_INLINE_CORRECTION_RE = re.compile(r",\s+not\s+[^.?!\n]+", re.IGNORECASE)
_NOISY_PROJECT_ATTRIBUTE_SUBJECT_TOKENS = {
    "favorite",
    "favourite",
    "question",
    "remember",
    "remeber",
}
_FACT_QUESTION_STOPWORDS = {
    "about",
    "answer",
    "did",
    "discuss",
    "do",
    "does",
    "favorite",
    "favourite",
    "for",
    "from",
    "handle",
    "have",
    "how",
    "is",
    "like",
    "likes",
    "me",
    "memory",
    "my",
    "own",
    "prefer",
    "preference",
    "please",
    "source",
    "the",
    "this",
    "typo",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
}
_COMMON_FACT_SPELLING_CORRECTIONS = {
    "aniversary": "anniversary",
    "anniversery": "anniversary",
}
_MONTH_NAMES = {
    "january": "January",
    "february": "February",
    "march": "March",
    "april": "April",
    "may": "May",
    "june": "June",
    "july": "July",
    "august": "August",
    "september": "September",
    "october": "October",
    "november": "November",
    "december": "December",
}
_NAME_LIKE_PREDICATES = {
    _FactPredicate.CREATOR.value,
    _FactPredicate.PROFILE_NAME.value,
}


def _redacted_startup_error(component: str, exc: Exception) -> RuntimeError:
    message = redact_secrets(f"{component} initialization failed: {type(exc).__name__}: {exc}")
    return RuntimeError(message)


def build_runtime(
    config: HubConfig | dict[str, Any] | None = None,
) -> RuntimeDependencies:
    cfg = (
        parse_config(config) if isinstance(config, dict) else (config or load_config())
    )
    ensure_token_hash_secret(cfg)
    schema_path = Path(cfg.schema_config.file) if cfg.schema_config.file else SCHEMA_PATH
    conversation_schema = load_schema(schema_path)
    validate_schema_compatibility(conversation_schema)
    data_dir = Path(cfg.paths.data_dir)
    try:
        if cfg.providers.metadata_db == "postgres":
            metadata_store = PostgresMetadataStore(
                cfg.storage.metadata_providers.postgres.url
            )
        elif cfg.providers.metadata_db == "mongodb":
            mongodb_config = cfg.storage.metadata_providers.mongodb
            metadata_store = MongoDBMetadataStore(
                uri=mongodb_config.uri,
                database=mongodb_config.database,
                conversations_collection=mongodb_config.conversations_collection,
                facts_collection=mongodb_config.facts_collection,
                generated_summaries_collection=mongodb_config.generated_summaries_collection,
                schema_collection=mongodb_config.schema_collection,
            )
        else:
            metadata_store = SQLiteMetadataStore(data_dir / "metadata.sqlite3")
    except Exception as exc:
        raise _redacted_startup_error("Metadata provider", exc) from exc
    _validate_metadata_schema(
        metadata_store=metadata_store,
        supported_versions=cfg.storage.metadata_schema_versions,
    )

    if cfg.providers.embeddings == "http":
        embedding_provider: EmbeddingProvider = HttpEmbeddingProvider(
            cfg.providers.embedding_model,
            cfg.providers.embedding_dimension,
            cfg.embedding_endpoint.base_url,
            cfg.embedding_endpoint.api_key,
        )
    else:
        embedding_provider = LocalEmbeddingProvider()

    expected_dimension = embedding_provider.dimension
    requested_vector_provider = (
        VectorProviderName.MEMORY.value
        if cfg.providers.vector_db == VectorProviderAlias.IN_MEMORY.value
        else cfg.providers.vector_db
    )
    _log_startup_policy(cfg=cfg, requested_vector_provider=requested_vector_provider)
    vector_fallback_active = False
    fallback_reasons: list[str] = []
    if requested_vector_provider == VectorProviderName.MEMORY.value:
        vector_store = InMemoryVectorStore(dimension=expected_dimension)
    else:
        try:
            vector_store = _build_vector_store(
                provider=requested_vector_provider,
                cfg=cfg,
                data_dir=data_dir,
                expected_dimension=expected_dimension,
            )
        except Exception as exc:
            if not cfg.storage.vector.allow_fallback:
                raise _redacted_startup_error("Vector provider", exc) from exc
            logger.warning(
                "Vector store initialization failed (%s); falling back to in-memory provider",
                redact_secrets(f"{type(exc).__name__}: {exc}"),
                extra={
                    "event": "vector_fallback_activated",
                    "requested_vector_provider": requested_vector_provider,
                    "effective_vector_provider": VectorProviderName.MEMORY.value,
                    "error_type": type(exc).__name__,
                },
            )
            vector_store = InMemoryVectorStore(dimension=expected_dimension)
            vector_fallback_active = True
            fallback_reasons.append(type(exc).__name__)

    _validate_vector_dimension(
        embedding_dimension=expected_dimension, vector_store=vector_store
    )
    actual_vector_provider = (
        VectorProviderName.MEMORY.value if vector_fallback_active else requested_vector_provider
    )
    vector_health = vector_store.health() if hasattr(vector_store, "health") else {}
    embedding_index_metadata = _embedding_index_metadata(
        cfg=cfg,
        embedding_provider=embedding_provider,
        vector_provider=actual_vector_provider,
        vector_store=vector_store,
        vector_health=vector_health,
    )
    embedding_index_mismatch = _validate_embedding_index_metadata(
        metadata_store=metadata_store,
        vector_provider=actual_vector_provider,
        vector_store=vector_store,
        vector_health=vector_health,
        embedding_index_metadata=embedding_index_metadata,
    )

    if cfg.storage.dry_run:
        logger.warning("DRY-RUN enabled: write operations will be skipped")
        metadata_store = DryRunMetadataStore(metadata_store)
        vector_store = DryRunVectorStore(vector_store)

    mode = "ok"
    if vector_fallback_active:
        mode = "degraded"
    if cfg.storage.dry_run:
        mode = "dry_run"
    health_state = {
        "mode": mode,
        "metadata_provider": cfg.providers.metadata_db,
        "vector_provider": actual_vector_provider,
        "requested_vector_provider": requested_vector_provider,
        "vector_fallback_active": vector_fallback_active,
        "reasons": fallback_reasons,
        "embedding": embedding_index_metadata["embedding"],
        "embedding_health": _embedding_readiness(
            cfg=cfg,
            embedding_provider=embedding_provider,
            live_probe=cfg.observability.embedding_readiness_probe,
        ),
        "tokenizer_enabled": cfg.tokenizer.enabled,
        "vector_index": embedding_index_metadata["vector_index"],
        "embedding_index_mismatch": embedding_index_mismatch,
    }

    return RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=vector_store,
        health_state=health_state,
        schema_path=schema_path,
        conversation_schema=conversation_schema,
        allow_trusted_appends=cfg.storage.allow_trusted_appends,
        tokenizer_enabled=cfg.tokenizer.enabled,
        tokenizer_encoding=cfg.tokenizer.encoding,
        ask_max_context_tokens=cfg.ask.max_context_tokens,
        chunking_strategy=cfg.chunking.strategy,
        chunking_max_tokens=cfg.chunking.max_tokens,
        chunking_overlap_tokens=cfg.chunking.overlap_tokens,
        retrieval_vector_score_threshold=cfg.retrieval.vector_score_threshold,
        retrieval_keyword_enabled=cfg.retrieval.keyword_enabled,
        retrieval_keyword_candidate_limit=cfg.retrieval.keyword_candidate_limit,
        retrieval_keyword_weight=cfg.retrieval.keyword_weight,
        retrieval_metadata_weight=cfg.retrieval.metadata_weight,
        retrieval_candidate_multiplier=cfg.retrieval.candidate_multiplier,
        graph_enabled=cfg.memory.graph_enabled,
        retrieval_graph_enabled=cfg.retrieval.graph_enabled,
        retrieval_graph_quality_gate_passed=cfg.retrieval.graph_quality_gate_passed,
        retrieval_graph_weight=cfg.retrieval.graph_weight,
        retrieval_advanced_scoring_enabled=cfg.retrieval.advanced_scoring_enabled,
        retrieval_recency_weight=cfg.retrieval.recency_weight,
        retrieval_importance_weight=cfg.retrieval.importance_weight,
        retrieval_pin_weight=cfg.retrieval.pin_weight,
        retrieval_access_weight=cfg.retrieval.access_weight,
    )


def _build_vector_store(
    *,
    provider: str,
    cfg: HubConfig,
    data_dir: Path,
    expected_dimension: int,
) -> Any:
    if provider == VectorProviderName.LANCEDB.value:
        return LanceDBVectorStore(
            data_dir / "lancedb", dimension=expected_dimension
        )
    if provider == VectorProviderName.CHROMADB.value:
        chroma_config = cfg.storage.vector_providers.chromadb
        return ChromaDBVectorStore(
            path=chroma_config.path,
            url=chroma_config.url,
            host=chroma_config.host,
            port=chroma_config.port,
            collection_name=chroma_config.collection,
            dimension=expected_dimension,
        )
    if provider == VectorProviderName.QDRANT.value:
        qdrant_config = cfg.storage.vector_providers.qdrant
        return QdrantVectorStore(
            url=qdrant_config.url,
            api_key=qdrant_config.api_key,
            collection_name=qdrant_config.collection,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
            prefer_grpc=qdrant_config.prefer_grpc,
        )
    if provider == VectorProviderName.MILVUS.value:
        milvus_config = cfg.storage.vector_providers.milvus
        return MilvusVectorStore(
            uri=milvus_config.uri,
            token=milvus_config.token,
            collection_name=milvus_config.collection,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.WEAVIATE.value:
        weaviate_config = cfg.storage.vector_providers.weaviate
        return WeaviateVectorStore(
            url=weaviate_config.url,
            api_key=weaviate_config.api_key,
            collection_name=weaviate_config.collection,
            dimension=expected_dimension,
        )
    if provider == VectorProviderName.MONGODB_ATLAS.value:
        atlas_config = cfg.storage.vector_providers.mongodb_atlas
        return MongoDBAtlasVectorStore(
            uri=atlas_config.uri,
            database=atlas_config.database,
            collection_name=atlas_config.collection,
            index_name=atlas_config.index,
            dimension=expected_dimension,
        )
    if provider == VectorProviderName.ELASTICSEARCH.value:
        elastic_config = cfg.storage.vector_providers.elasticsearch
        return ElasticsearchVectorStore(
            url=elastic_config.url,
            username=elastic_config.username,
            password=elastic_config.password,
            index_name=elastic_config.index,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.OPENSEARCH.value:
        opensearch_config = cfg.storage.vector_providers.opensearch
        return OpenSearchVectorStore(
            url=opensearch_config.url,
            username=opensearch_config.username,
            password=opensearch_config.password,
            index_name=opensearch_config.index,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.REDIS.value:
        redis_config = cfg.storage.vector_providers.redis
        return RedisVectorStore(
            url=redis_config.url,
            index_name=redis_config.index,
            key_prefix=redis_config.key_prefix,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.PINECONE.value:
        pinecone_config = cfg.storage.vector_providers.pinecone
        return PineconeVectorStore(
            api_key=pinecone_config.api_key,
            index_name=pinecone_config.index,
            namespace=pinecone_config.namespace,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
            cloud=pinecone_config.cloud,
            region=pinecone_config.region,
            create_index=pinecone_config.create_index,
        )
    if provider == VectorProviderName.TURBOPUFFER.value:
        turbopuffer_config = cfg.storage.vector_providers.turbopuffer
        return TurbopufferVectorStore(
            api_key=turbopuffer_config.api_key,
            namespace=turbopuffer_config.namespace,
            region=turbopuffer_config.region,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.VESPA.value:
        vespa_config = cfg.storage.vector_providers.vespa
        return VespaVectorStore(
            url=vespa_config.url,
            token=vespa_config.token,
            namespace=vespa_config.namespace,
            schema=vespa_config.schema_name,
            rank_profile=vespa_config.rank_profile,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.TYPESENSE.value:
        typesense_config = cfg.storage.vector_providers.typesense
        return TypesenseVectorStore(
            url=typesense_config.url,
            api_key=typesense_config.api_key,
            collection_name=typesense_config.collection,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.PGVECTOR.value:
        pgvector_config = cfg.storage.vector_providers.pgvector
        return PGVectorStore(
            pgvector_config.url,
            table_name=pgvector_config.table_name,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    raise ValueError(f"Unsupported vector provider: {provider}")


def configure_runtime(
    *,
    runtime: RuntimeDependencies | None = None,
    config: HubConfig | dict[str, Any] | None = None,
) -> RuntimeDependencies:
    global _RUNTIME
    _RUNTIME = runtime or build_runtime(config)
    return _RUNTIME


@contextmanager
def runtime_context(runtime: RuntimeDependencies) -> Iterator[None]:
    token = _RUNTIME_OVERRIDE.set(runtime)
    try:
        yield
    finally:
        _RUNTIME_OVERRIDE.reset(token)


class MVPIngestionService:
    """Runtime-bound facade for the deterministic MVP ingestion operations."""

    def __init__(self, runtime: RuntimeDependencies):
        self._runtime = runtime

    def _call(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with runtime_context(self._runtime):
            return operation(*args, **kwargs)

    def ingest_messages(self, conversation_json: Any, **kwargs: Any) -> dict[str, Any]:
        return self._call(ingest_messages, conversation_json, **kwargs)

    def store_pending_review_memory(
        self, conversation_json: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return self._call(store_pending_review_memory, conversation_json, **kwargs)

    def search(self, *, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(search, query=query, **kwargs)

    def retrieve(self, memory_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self._call(retrieve, memory_id, **kwargs)

    def ask(self, *, question: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(ask, question=question, **kwargs)

    def runtime_health(self) -> dict[str, Any]:
        return self._call(runtime_health)

    def fact_search(self, **kwargs: Any) -> dict[str, Any]:
        return self._call(fact_search, **kwargs)

    def profile_get(self, subject: str = "user", **kwargs: Any) -> dict[str, Any]:
        return self._call(profile_get, subject, **kwargs)

    def fact_supersede(
        self, fact_id: str, superseded_by: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._call(fact_supersede, fact_id, superseded_by, **kwargs)

    def approve_pending_memory(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(approve_pending_memory, memory_id, **kwargs)

    def reject_pending_memory(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(reject_pending_memory, memory_id, **kwargs)

    def project_list(self, **kwargs: Any) -> dict[str, Any]:
        return self._call(project_list, **kwargs)

    def project_default_get(self, **kwargs: Any) -> dict[str, Any]:
        return self._call(project_default_get, **kwargs)

    def project_get(self, project_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._call(project_get, project_id, **kwargs)

    def authenticate_bearer_token(self, token: str) -> str | None:
        return self._call(authenticate_bearer_token, token)

    def authenticate_bearer_token_context(self, token: str) -> dict[str, object] | None:
        return self._call(authenticate_bearer_token_context, token)

    def find_or_create_oauth_identity(self, **kwargs: Any) -> dict[str, object]:
        return self._call(find_or_create_oauth_identity, **kwargs)

    def create_web_session(self, **kwargs: Any) -> dict[str, object]:
        return self._call(create_web_session, **kwargs)

    def web_session_for_hash(self, session_id_hash: str) -> dict[str, object] | None:
        return self._call(web_session_for_hash, session_id_hash)

    def revoke_web_session(self, session_id_hash: str) -> bool:
        return self._call(revoke_web_session, session_id_hash)

    def create_auth_token(self, **kwargs: Any) -> dict[str, object]:
        return self._call(create_auth_token, **kwargs)

    def revoke_auth_token(self, token_id: str) -> dict[str, object] | None:
        return self._call(revoke_auth_token, token_id)

    def create_oauth_client(self, **kwargs: Any) -> dict[str, object]:
        return self._call(create_oauth_client, **kwargs)

    def oauth_client(self, client_id: str) -> dict[str, object] | None:
        return self._call(oauth_client, client_id)

    def create_oauth_refresh_token(self, **kwargs: Any) -> dict[str, object]:
        return self._call(create_oauth_refresh_token, **kwargs)

    def oauth_refresh_token(self, refresh_token: str) -> dict[str, object] | None:
        return self._call(oauth_refresh_token, refresh_token)

    def consume_oauth_refresh_token(self, refresh_token: str) -> dict[str, object] | None:
        return self._call(consume_oauth_refresh_token, refresh_token)

    def revoke_oauth_refresh_token(self, refresh_token: str) -> bool:
        return self._call(revoke_oauth_refresh_token, refresh_token)

    def revoke_oauth_refresh_token_family(self, token_family_id: str) -> bool:
        return self._call(revoke_oauth_refresh_token_family, token_family_id)

    def revoke_oauth_authorization_for_access_token(self, access_token: str) -> bool:
        return self._call(revoke_oauth_authorization_for_access_token, access_token)


def _runtime() -> RuntimeDependencies:
    global _RUNTIME
    runtime_override = _RUNTIME_OVERRIDE.get()
    if runtime_override is not None:
        return runtime_override
    if _RUNTIME is None:
        _RUNTIME = build_runtime()
    return _RUNTIME


def validate_json(obj: dict[str, Any]) -> None:
    runtime = _runtime()
    schema = runtime.conversation_schema or load_schema(runtime.schema_path)
    validate_conversation(obj, schema=schema)


def infer_topics(messages: list[dict[str, Any]]) -> list[str]:
    text_blob = " ".join(str(message.get("text", "")) for message in messages)
    topics: list[str] = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text_blob):
            topics.append(topic)
    return topics


def enrich_topics(obj: dict[str, Any]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        return

    existing = metadata.get("topics")
    inferred = infer_topics(obj.get("messages", []))
    if not inferred and not isinstance(existing, list):
        return

    merged: list[str] = []
    for topic in existing if isinstance(existing, list) else []:
        if isinstance(topic, str) and topic not in merged:
            merged.append(topic)
    for topic in inferred:
        if topic not in merged:
            merged.append(topic)

    metadata["topics"] = merged


def enrich_auto_tags(obj: dict[str, Any]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        return

    tag_sources: dict[str, list[str]] = {}
    for tag in _auto_tags_from_source(obj):
        _add_auto_tag(tag_sources, tag, "source")
    for tag in _auto_tags_from_topics(metadata):
        _add_auto_tag(tag_sources, tag, "topic")
    for tag in _auto_tags_from_entities(obj):
        _add_auto_tag(tag_sources, tag, "entity")
    for tag in _auto_tags_from_fact_predicates(obj):
        _add_auto_tag(tag_sources, tag, "fact_predicate")

    manual_tags = _manual_tag_set(metadata)
    auto_tags = [
        tag
        for tag in tag_sources
        if tag not in manual_tags
    ][:_MAX_AUTO_TAGS]
    metadata["auto_tags"] = auto_tags
    metadata["tag_sources"] = {
        tag: tag_sources[tag]
        for tag in auto_tags
    }


def _add_auto_tag(tag_sources: dict[str, list[str]], tag: str, source: str) -> None:
    normalized = _normalize_auto_tag(tag)
    if not normalized:
        return
    sources = tag_sources.setdefault(normalized, [])
    if source not in sources:
        sources.append(source)


def _auto_tags_from_source(obj: dict[str, Any]) -> list[str]:
    source = str(obj.get("source", "")).strip()
    return [f"source:{source}"] if source else []


def _auto_tags_from_topics(metadata: dict[str, Any]) -> list[str]:
    topics = metadata.get("topics")
    if not isinstance(topics, list):
        return []
    return [str(topic) for topic in topics if isinstance(topic, str)]


def _auto_tags_from_entities(obj: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    title = obj.get("title")
    if isinstance(title, str):
        candidates.append(title)
    for message in obj.get("messages", []):
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        if not isinstance(text, str):
            continue
        candidates.extend(match.group(0) for match in re.finditer(r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){1,4}\b", text))
    return candidates


def _auto_tags_from_fact_predicates(obj: dict[str, Any]) -> list[str]:
    predicates = {
        str(fact.get("predicate", ""))
        for fact in extract_facts(obj)
        if isinstance(fact, dict) and fact.get("predicate")
    }
    return [f"fact:{predicate}" for predicate in sorted(predicates)]


def _manual_tag_set(metadata: dict[str, Any]) -> set[str]:
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        return set()
    return {_normalize_auto_tag(str(tag)) for tag in tags if isinstance(tag, str)}


def _normalize_auto_tag(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9:_-]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_:")
    return normalized[:80]


def chunk_messages(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return chunk_selected_messages(obj, obj.get("messages", []), start_index=0)


def chunk_selected_messages(
    obj: dict[str, Any], messages: list[dict[str, Any]], *, start_index: int
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if not isinstance(messages, list):
        raise ValueError("Missing or invalid 'messages' key in conversation object")
    runtime = _runtime()
    conversation_id = str(obj["id"])
    for offset, message in enumerate(messages):
        message_hash = str(message["hash"])
        role = str(message["role"])
        text = str(message["text"])
        token_windows = _message_token_windows(
            text,
            strategy=runtime.chunking_strategy,
            max_tokens=runtime.chunking_max_tokens,
            overlap_tokens=runtime.chunking_overlap_tokens,
            encoding=runtime.tokenizer_encoding,
        )
        for token_window_index, chunk_text in enumerate(token_windows):
            chunk_index = start_index + len(chunks)
            chunks.append(
                {
                    "chunk_id": f"{conversation_id}:{chunk_index}:{message_hash}",
                    "chunk_index": chunk_index,
                    "conversation_id": conversation_id,
                    "message_hash": message_hash,
                    "message_index": start_index + offset,
                    "token_window_index": token_window_index,
                    "role": role,
                    "text": chunk_text,
                    "index_state": "pending_index",
                }
            )
    return chunks


def _message_token_windows(
    text: str,
    *,
    strategy: str,
    max_tokens: int,
    overlap_tokens: int,
    encoding: str,
) -> list[str]:
    if strategy == "message":
        return [text]
    if overlap_tokens >= max_tokens:
        raise ValueError("chunking.overlap_tokens must be less than chunking.max_tokens")
    return split_token_windows(
        text,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        encoding=encoding,
    )


def _attach_index_chunks(obj: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        obj["metadata"] = metadata
    metadata["index_chunks"] = [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "chunk_index": int(chunk["chunk_index"]),
            "message_hash": str(chunk["message_hash"]),
            "role": str(chunk["role"]),
            "text": str(chunk["text"]),
            "index_state": str(chunk.get("index_state", "pending_index")),
        }
        for chunk in chunks
    ]


def embed_chunks(
    chunks: list[dict[str, Any]], *, project_id: str | None = None, owner_id: str | None = None
) -> list[dict[str, Any]]:
    runtime = _runtime()
    texts = [chunk["text"] for chunk in chunks]
    provider = str(runtime.health_state.get("embedding", {}).get("provider") or "unknown")
    model = str(runtime.health_state.get("embedding", {}).get("model") or "unknown")
    started = time.perf_counter()
    with _ingestion_stage("embedding", provider=provider, model=model, chunk_count=len(chunks)):
        try:
            vectors = runtime.embedding_provider.embed_texts(texts)
        except Exception as exc:
            metrics.increment(
                "memory_embedding_failures_total",
                provider=provider,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            metrics.observe(
                "memory_embedding_duration_ms",
                (time.perf_counter() - started) * 1000,
                provider=provider,
                model=model,
            )
    if len(vectors) != len(chunks):
        raise ValueError("Embedding provider must return one vector per chunk")

    embeddings: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors):
        embeddings.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "chunk_index": chunk["chunk_index"],
                "conversation_id": chunk.get("conversation_id"),
                "project_id": project_id,
                "owner_id": owner_id,
                "message_hash": chunk.get("message_hash"),
                "index_state": "indexed",
                "role": chunk["role"],
                "text": chunk["text"],
                "vector": [float(v) for v in vector],
            }
        )
    return embeddings


def store_metadata(obj: dict[str, Any]) -> str:
    runtime = _runtime()
    return runtime.metadata_store.insert(obj)


def store_vectors(metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
    runtime = _runtime()
    provider = str(runtime.health_state.get("vector_provider") or "unknown")
    with _ingestion_stage(
        "vector_insert",
        vector_provider=provider,
        embedding_count=len(embeddings),
        replace=replace,
    ):
        runtime.vector_store.insert(metadata_id, embeddings, replace=replace)


def _lookup_by_conversation_hash(
    conversation_hash: str | None, *, project_id: str | None = None
) -> dict[str, Any] | None:
    if conversation_hash is None:
        return None
    store = _runtime().metadata_store
    if hasattr(store, "get_by_conversation_hash"):
        return store.get_by_conversation_hash(conversation_hash, project_id=project_id)
    for attr in ("by_id", "rows"):
        rows = getattr(store, attr, None)
        if isinstance(rows, dict):
            for conversation in rows.values():
                if (
                    _conversation_hash(conversation) == conversation_hash
                    and _conversation_project_matches(conversation, project_id)
                ):
                    return conversation
    return None


def _lookup_same_thread(
    incoming: dict[str, Any], *, owner_id: str | None = None, project_id: str | None = None
) -> dict[str, Any] | None:
    if not _runtime().allow_trusted_appends:
        return None
    store = _runtime().metadata_store
    memory_id = str(incoming.get("id", ""))
    if memory_id:
        existing = store.get(memory_id) if hasattr(store, "get") else None
        if isinstance(existing, dict):
            return existing if _conversation_allowed(existing, owner_id, project_id) else None

    metadata = incoming.get("metadata", {})
    upstream_thread_id = (
        metadata.get(ThreadMetadataKey.UPSTREAM_THREAD_ID) if isinstance(metadata, dict) else None
    )
    if isinstance(upstream_thread_id, str) and upstream_thread_id:
        source = str(incoming.get("source", ""))
        if hasattr(store, "get_by_upstream_thread"):
            candidate = store.get_by_upstream_thread(source, upstream_thread_id, project_id=project_id)
            if isinstance(candidate, dict) and _conversation_allowed(candidate, owner_id, project_id):
                return candidate
            return None
        for attr in ("by_id", "rows"):
            rows = getattr(store, attr, None)
            if isinstance(rows, dict):
                for conversation in rows.values():
                    conversation_metadata = conversation.get("metadata", {})
                    if (
                        isinstance(conversation_metadata, dict)
                        and conversation.get("source") == source
                        and conversation_metadata.get(ThreadMetadataKey.UPSTREAM_THREAD_ID)
                        == upstream_thread_id
                    ):
                        if _conversation_allowed(conversation, owner_id, project_id):
                            return conversation
    return None


def _insert_new_conversation(obj: dict[str, Any]) -> tuple[str, bool]:
    store = _runtime().metadata_store
    provider = str(_runtime().health_state.get("metadata_provider") or "unknown")
    with _ingestion_stage("metadata_insert", metadata_provider=provider):
        if hasattr(store, "insert_new"):
            return store.insert_new(obj)
        duplicate = _lookup_by_conversation_hash(_conversation_hash(obj))
        if duplicate is not None:
            return str(duplicate["id"]), False
        if hasattr(store, "get"):
            existing = store.get(str(obj.get("id", "")))
            if isinstance(existing, dict):
                raise ValueError("unauthorized_update: conversation id already exists")
        return store.insert(obj), True


def _append_conversation(
    existing: dict[str, Any], incoming: dict[str, Any], new_messages: list[dict[str, Any]]
) -> dict[str, Any]:
    updated = dict(existing)
    metadata = dict(updated.get("metadata", {}))
    incoming_metadata = incoming.get("metadata", {})
    messages = list(updated.get("messages", [])) + new_messages
    metadata["updated_at"] = incoming["metadata"]["updated_at"]
    metadata["message_hashes"] = [str(message["hash"]) for message in messages]
    metadata["conversation_hash"] = hash_ordered_messages(messages)
    if isinstance(incoming_metadata, dict):
        _merge_thread_metadata(metadata, incoming_metadata)
    updated["messages"] = messages
    updated["metadata"] = metadata
    store = _runtime().metadata_store
    provider = str(_runtime().health_state.get("metadata_provider") or "unknown")
    with _ingestion_stage("metadata_insert", metadata_provider=provider, append=True):
        if hasattr(store, "append_messages"):
            store.append_messages(updated, new_messages)
        else:
            store.insert(updated)
    return updated


def _conversation_hash(conversation: dict[str, Any]) -> str | None:
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        value = metadata.get("conversation_hash")
        if isinstance(value, str):
            return value
    return None


def _stamp_owner(conversation: dict[str, Any], *, owner_id: str | None) -> None:
    if owner_id is None:
        return
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation["metadata"] = metadata
    metadata["owner_id"] = _validate_owner_id(owner_id)


def _stamp_project(conversation: dict[str, Any], *, project_id: str) -> None:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation["metadata"] = metadata
    metadata["project_id"] = _validate_project_id(project_id)


def _scope_conversation_hash(
    conversation: dict[str, Any], *, project_id: str | None
) -> None:
    if project_id is None:
        return
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return
    conversation_hash = metadata.get("conversation_hash")
    if not isinstance(conversation_hash, str):
        return
    digest = hashlib.sha256(f"{project_id}\n{conversation_hash}".encode("utf-8")).hexdigest()
    metadata["conversation_hash"] = f"sha256:{digest}"


def _normalize_thread_metadata(conversation: dict[str, Any]) -> None:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return
    thread_metadata = thread_metadata_from_mapping(metadata)
    if thread_metadata.thread_id is None and thread_metadata.upstream_thread_id:
        thread_metadata.thread_id = _derived_thread_id(
            str(conversation.get("source", "")), thread_metadata.upstream_thread_id
        )
    for key in ThreadMetadataKey:
        metadata.pop(key, None)
    metadata.update(thread_metadata.to_metadata_update())


def _merge_thread_metadata(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key in (
        ThreadMetadataKey.THREAD_ID,
        ThreadMetadataKey.UPSTREAM_THREAD_ID,
        ThreadMetadataKey.PARENT_CONVERSATION_ID,
    ):
        if key not in target and isinstance(incoming.get(key), str):
            target[key] = str(incoming[key])
    existing_related = target.get(ThreadMetadataKey.RELATED_CONVERSATION_IDS)
    incoming_related = incoming.get(ThreadMetadataKey.RELATED_CONVERSATION_IDS)
    related: list[str] = []
    if isinstance(existing_related, list):
        related.extend(str(item) for item in existing_related if isinstance(item, str))
    if isinstance(incoming_related, list):
        related.extend(str(item) for item in incoming_related if isinstance(item, str))
    if related:
        target[ThreadMetadataKey.RELATED_CONVERSATION_IDS] = _unique_strings(related)


def _derived_thread_id(source: str, upstream_thread_id: str) -> str:
    source_part = source.strip() or "unknown"
    return f"{source_part}:{upstream_thread_id}"


def _owner_id_from_conversation(conversation: Any) -> str | None:
    if not isinstance(conversation, dict):
        return None
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("owner_id")
    return str(value) if value is not None else None


def _conversation_owner_matches(conversation: Any, owner_id: str | None) -> bool:
    if owner_id is None:
        return True
    return _owner_id_from_conversation(conversation) == owner_id


def _project_id_from_conversation(conversation: Any) -> str | None:
    if not isinstance(conversation, dict):
        return None
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("project_id")
    return str(value) if value is not None else None


def _conversation_project_matches(conversation: Any, project_id: str | None) -> bool:
    if project_id is None:
        return True
    conversation_project_id = _project_id_from_conversation(conversation)
    if conversation_project_id is None and project_id == LOCAL_DEFAULT_PROJECT_ID:
        return True
    return conversation_project_id == project_id


def _memory_status(conversation: Any) -> str:
    if not isinstance(conversation, dict):
        return _MEMORY_STATUS_ACTIVE
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return _MEMORY_STATUS_ACTIVE
    value = metadata.get("memory_status")
    return str(value) if value is not None else _MEMORY_STATUS_ACTIVE


def _conversation_is_active(conversation: Any) -> bool:
    return _memory_status(conversation) == _MEMORY_STATUS_ACTIVE


def _validate_memory_status_filter(memory_status: str | None) -> str:
    normalized = str(memory_status or _MEMORY_STATUS_ACTIVE).lower()
    if normalized not in _MEMORY_STATUS_VALUES:
        raise ValueError(
            "memory_status must be one of: " + ", ".join(sorted(_MEMORY_STATUS_VALUES))
        )
    return normalized


def _conversation_authorized(
    conversation: Any, owner_id: str | None, project_id: str | None
) -> bool:
    if project_id is not None:
        return _conversation_project_matches(conversation, project_id)
    return _conversation_owner_matches(conversation, owner_id) and _conversation_project_matches(
        conversation, project_id
    )


def _conversation_allowed(
    conversation: Any, owner_id: str | None, project_id: str | None
) -> bool:
    return _conversation_authorized(
        conversation, owner_id, project_id
    ) and _conversation_is_active(conversation)


def _conversation_visible(
    conversation: Any, owner_id: str | None, project_id: str | None, memory_status: str | None
) -> bool:
    if not _conversation_authorized(conversation, owner_id, project_id):
        return False
    status_filter = _validate_memory_status_filter(memory_status)
    if status_filter == _MEMORY_STATUS_ALL:
        return True
    return _memory_status(conversation) == status_filter


def _conversation_matches_filters(conversation: Any, filters: ConversationFilters) -> bool:
    if not isinstance(conversation, dict):
        return False
    if filters.source and str(conversation.get("source", "")) != filters.source:
        return False
    metadata = conversation.get("metadata", {})
    if filters.thread_id:
        if (
            not isinstance(metadata, dict)
            or str(metadata.get(ThreadMetadataKey.THREAD_ID, "")) != filters.thread_id
        ):
            return False
    if not _datetime_in_range(
        str(conversation.get("timestamp", "")),
        date_from=filters.date_from,
        date_to=filters.date_to,
        field_name="conversation.timestamp",
    ):
        return False
    if not filters.tags:
        return True
    conversation_tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
    if not isinstance(conversation_tags, list):
        return False
    tag_set = {str(tag) for tag in conversation_tags if isinstance(tag, str)}
    return set(filters.tags).issubset(tag_set)


def _fact_owner_matches(fact: Any, owner_id: str | None) -> bool:
    if owner_id is None:
        return True
    if not isinstance(fact, dict):
        return False
    return str(fact.get("owner_id", "")) == owner_id


def _fact_project_matches(fact: Any, project_id: str | None) -> bool:
    if project_id is None:
        return True
    if not isinstance(fact, dict):
        return False
    fact_project_id = fact.get("project_id")
    if fact_project_id is None and project_id == LOCAL_DEFAULT_PROJECT_ID:
        return True
    return str(fact_project_id) == project_id


def _fact_allowed(fact: Any, owner_id: str | None, project_id: str | None) -> bool:
    if project_id is not None:
        return _fact_project_matches(fact, project_id)
    return _fact_owner_matches(fact, owner_id) and _fact_project_matches(fact, project_id)


def _resolve_project(
    *,
    owner_id: str | None,
    project_id: str | None,
    required_role: str,
) -> str:
    store = _runtime().metadata_store
    if project_id is None:
        if hasattr(store, "ensure_default_project"):
            project = store.ensure_default_project(owner_id)
            return str(project["id"])
        return _default_project_id(owner_id) if owner_id is not None else LOCAL_DEFAULT_PROJECT_ID

    project = _validate_project_id(project_id)
    if owner_id is None:
        if project != LOCAL_DEFAULT_PROJECT_ID:
            _record_audit_event(
                "project.access_denied",
                owner_id=None,
                project_id=project,
                outcome="denied",
                reason_code="anonymous_non_default_project",
                metadata={"required_role": required_role},
            )
            raise PermissionError("project access denied")
        return project
    if hasattr(store, "project_has_role"):
        if not store.project_has_role(project_id=project, user_id=owner_id, role=required_role):
            _record_audit_event(
                "project.access_denied",
                owner_id=owner_id,
                project_id=project,
                outcome="denied",
                reason_code="missing_project_role",
                metadata={"required_role": required_role},
            )
            raise PermissionError("project access denied")
    return project


def _detect_new_messages(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> list[dict[str, Any]]:
    existing_messages = existing.get("messages", [])
    incoming_messages = incoming.get("messages", [])
    if not isinstance(existing_messages, list) or not isinstance(incoming_messages, list):
        raise ValueError("duplicate_conflict: stored or incoming messages are invalid")
    existing_hashes = [str(message.get("hash", "")) for message in existing_messages]
    incoming_hashes = [str(message.get("hash", "")) for message in incoming_messages]
    shared_prefix_len = 0
    for existing_hash, incoming_hash in zip(existing_hashes, incoming_hashes, strict=False):
        if existing_hash != incoming_hash:
            break
        shared_prefix_len += 1
    common_prefix_len = min(len(existing_hashes), len(incoming_hashes))
    if shared_prefix_len == 0 and common_prefix_len > 0:
        raise ValueError("duplicate_conflict: same thread has conflicting message history")
    if shared_prefix_len == common_prefix_len and len(incoming_hashes) <= len(existing_hashes):
        return []
    seen = set(existing_hashes)
    new_messages: list[dict[str, Any]] = []
    suffix_start = len(existing_hashes) if shared_prefix_len == common_prefix_len else shared_prefix_len
    for message in incoming_messages[suffix_start:]:
        message_hash = str(message["hash"])
        if message_hash not in seen:
            new_messages.append(message)
            seen.add(message_hash)
    return new_messages


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_message(role: str, text: str) -> str:
    digest = hashlib.sha256(f"{role}\n{text}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def hash_ordered_messages(messages: list[dict[str, Any]]) -> str:
    joined = "\n".join(str(message["hash"]) for message in messages)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def normalize_conversation_json(
    payload: Any, *, source: str | None = None, strict_transcript: bool = False
) -> dict[str, Any]:
    """Normalize a conversation JSON object with default values."""
    now = _utc_now_iso()
    payload = _coerce_payload(payload, strict_transcript=strict_transcript)
    _enforce_payload_limits(payload)
    normalized = dict(payload)
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    top_level_tags = normalized.pop("tags", None)
    if top_level_tags is not None and "tags" not in metadata:
        metadata["tags"] = top_level_tags

    if "messages" not in normalized and isinstance(
        normalized.get("conversation"), list
    ):
        normalized["messages"] = normalized["conversation"]
    normalized.pop("conversation", None)

    messages = normalized.get("messages")
    if isinstance(messages, list):
        normalized["messages"] = _normalize_messages(messages)

    normalized.setdefault("id", str(uuid4()))
    normalized.setdefault("source", source or "unknown")
    normalized["source"] = _normalize_source(normalized.get("source"))
    normalized.setdefault("timestamp", now)
    normalized["timestamp"] = _validate_datetime_string(
        normalized["timestamp"], field_name="timestamp"
    )
    if "imported_at" not in metadata and isinstance(metadata.get("saved_at"), str):
        metadata["imported_at"] = metadata["saved_at"]
    _normalize_metadata_summary(metadata)
    metadata.setdefault("imported_at", now)
    metadata["imported_at"] = _validate_datetime_string(
        metadata["imported_at"], field_name="metadata.imported_at"
    )
    metadata["updated_at"] = now
    if not isinstance(normalized.get("messages"), list):
        raise ValueError("ambiguous input: messages must be an array")
    metadata["message_hashes"] = [
        str(message["hash"]) for message in normalized["messages"]
    ]
    metadata["conversation_hash"] = hash_ordered_messages(normalized["messages"])
    normalized["metadata"] = metadata
    _normalize_thread_metadata(normalized)
    _enforce_payload_limits(normalized)
    return normalized


def _coerce_payload(payload: Any, *, strict_transcript: bool) -> dict[str, Any]:
    if isinstance(payload, dict):
        if any(key in payload for key in ("messages", "conversation", "content", "tags")):
            if "content" in payload and "messages" not in payload and "conversation" not in payload:
                content = payload["content"]
                if isinstance(content, list):
                    normalized = dict(payload)
                    normalized["messages"] = content
                    normalized.pop("content", None)
                    return normalized
            return payload
        raise ValueError("ambiguous input: object must include messages, conversation, content, or tags")
    if isinstance(payload, str):
        if not strict_transcript:
            raise ValueError("raw transcript input requires strict_transcript=True")
        return {"messages": _parse_strict_transcript(payload)}
    raise ValueError("ambiguous input: expected object or strict raw transcript string")


def _normalize_metadata_summary(metadata: dict[str, Any]) -> None:
    if "summary" not in metadata:
        return
    summary = metadata["summary"]
    if not isinstance(summary, str):
        raise ValueError("metadata.summary must be a string")
    summary = " ".join(summary.strip().split())
    if not summary:
        metadata.pop("summary", None)
        return
    if len(summary) > _MAX_METADATA_SUMMARY_CHARS:
        raise ValueError(
            f"metadata.summary exceeds max length of {_MAX_METADATA_SUMMARY_CHARS} characters"
        )
    metadata["summary"] = summary


def _parse_strict_transcript(text: str) -> list[dict[str, str]]:
    if len(text.encode("utf-8")) > _MAX_RAW_TRANSCRIPT_BYTES:
        raise ValueError("raw transcript exceeds max_raw_transcript_bytes")
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    lines = text.splitlines()
    for line in lines:
        match = _TRANSCRIPT_LINE_RE.match(line)
        if match:
            role = _normalize_role(match.group(1))
            current = {"role": role, "text": match.group(2)}
            messages.append(current)
            continue
        if current is None:
            if line.strip():
                raise ValueError("ambiguous raw transcript before first speaker boundary")
            continue
        current["text"] += "\n" + line
    if not messages:
        raise ValueError("raw transcript did not contain any speaker boundaries")
    return messages


def _normalize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    normalized_messages: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for index, item in enumerate(messages):
        message = _normalize_message(item, index=index)
        if message["hash"] in seen_hashes:
            continue
        seen_hashes.add(message["hash"])
        normalized_messages.append(message)
    if not normalized_messages:
        raise ValueError("messages must contain at least one non-empty message")
    return normalized_messages


def _normalize_message(message: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ValueError(f"messages[{index}] must be an object")
    normalized = dict(message)
    if "text" not in normalized and "content" in normalized:
        normalized["text"] = normalized["content"]
    normalized.pop("content", None)
    role = _normalize_role(normalized.get("role"))
    text = _normalize_text(normalized.get("text"), index=index)
    computed_hash = sha256_message(role, text)
    supplied_hash = normalized.get("hash")
    if supplied_hash is not None and supplied_hash != computed_hash:
        raise ValueError(f"messages[{index}].hash does not match server-computed hash")
    normalized["role"] = role
    normalized["text"] = text
    normalized["hash"] = computed_hash
    return normalized


def _normalize_role(role: Any) -> str:
    value = str(role).strip().lower()
    value = _ROLE_ALIASES.get(value, value)
    if value not in {"user", "assistant"}:
        raise ValueError(f"unknown message role: {role}")
    return value


def _normalize_source(source: Any) -> str:
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    value = source.strip()
    if not value:
        raise ValueError("source must be non-empty")
    if len(value) > 128:
        raise ValueError("source exceeds max length 128")
    return value


def _validate_datetime_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 datetime string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return value


def _parse_filter_datetime(value: str | None, *, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_in_range(
    value: str | None,
    *,
    date_from: str | None,
    date_to: str | None,
    field_name: str,
) -> bool:
    lower = _parse_filter_datetime(date_from, field_name="date_from")
    upper = _parse_filter_datetime(date_to, field_name="date_to")
    if lower is None and upper is None:
        return True
    if not value:
        return False
    parsed = _parse_filter_datetime(value, field_name=field_name)
    if parsed is None:
        return False
    if lower is not None and parsed < lower:
        return False
    return not (upper is not None and parsed > upper)


def _normalize_text(text: Any, *, index: int) -> str:
    if not isinstance(text, str):
        raise ValueError(f"messages[{index}].text must be a string")
    value = text.strip("\ufeff\r\n")
    if not value.strip():
        raise ValueError(f"messages[{index}].text must be non-empty")
    if len(value.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds max_message_bytes")
    return value


def _enforce_payload_limits(payload: dict[str, Any]) -> None:
    payload_bytes = len(json_dumps(payload).encode("utf-8"))
    if payload_bytes > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds max_payload_bytes")
    messages = payload.get("messages")
    if isinstance(messages, list) and len(messages) > _MAX_MESSAGES:
        raise ValueError("payload exceeds max_messages")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_bytes = len(json_dumps(metadata).encode("utf-8"))
        if metadata_bytes > _MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds max_metadata_bytes")


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _record_audit_event(
    event_type: str,
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_id: str | None = None,
    fact_id: str | None = None,
    outcome: str = "ok",
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    store = _runtime().metadata_store
    context = _AUDIT_CONTEXT.get()
    event = {
        "id": str(uuid4()),
        "event_type": event_type,
        "actor_id": owner_id,
        "project_id": project_id,
        "memory_id": memory_id,
        "fact_id": fact_id,
        "request_id": context.request_id,
        "source_surface": context.source_surface,
        "outcome": outcome,
        "reason_code": reason_code,
        "metadata": dict(metadata or {}),
        "created_at": _utc_now_iso(),
    }
    try:
        if hasattr(store, "append_audit_event"):
            store.append_audit_event(event)
            return
        events = getattr(store, "_audit_events", None)
        if not isinstance(events, list):
            events = []
            setattr(store, "_audit_events", events)
        events.append(event)
    except Exception:
        logger.warning(
            "Audit event persistence failed",
            extra={
                "event": "audit_event_persistence_failed",
                "audit_event_type": event_type,
                "outcome": outcome,
            },
            exc_info=True,
        )


def _audit_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _detect_sensitive_content(conversation_json: dict[str, Any]) -> list[dict[str, str]]:
    metadata = conversation_json.get("metadata")
    if isinstance(metadata, dict) and metadata.get("sensitive_content_approved") is True:
        return []

    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for location, value in _sensitive_scan_strings(conversation_json):
        _extend_sensitive_findings(findings, seen, location=location, value=value)
        if len(findings) >= _MAX_SENSITIVE_FINDINGS:
            break
    return findings


def _sensitive_scan_strings(value: Any, *, location: str = "conversation") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _sensitive_scan_strings(item, location=f"{location}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        key_name = _safe_sensitive_location_key(str(key))
        if key_name in _SENSITIVE_SCAN_EXCLUDED_KEYS:
            continue
        yield from _sensitive_scan_strings(item, location=f"{location}.{key_name}")


def _safe_sensitive_location_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip().lower()).strip("_")
    return normalized[:48] or "field"


def _extend_sensitive_findings(
    findings: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    *,
    location: str,
    value: str,
) -> None:
    for reason_code, pattern in _SENSITIVE_CONTENT_PATTERNS:
        for match in pattern.finditer(value):
            _append_sensitive_finding(
                findings,
                seen,
                reason_code=reason_code,
                location=location,
                match_text=match.group(0),
            )
            if len(findings) >= _MAX_SENSITIVE_FINDINGS:
                return
    for match in _PAYMENT_CARD_RE.finditer(value):
        match_text = match.group(0)
        if _is_luhn_payment_card(match_text):
            _append_sensitive_finding(
                findings,
                seen,
                reason_code="pii.payment_card",
                location=location,
                match_text=match_text,
            )
            if len(findings) >= _MAX_SENSITIVE_FINDINGS:
                return


def _append_sensitive_finding(
    findings: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    *,
    reason_code: str,
    location: str,
    match_text: str,
) -> None:
    match_hash = _audit_hash(match_text)
    key = (reason_code, location, match_hash)
    if key in seen:
        return
    seen.add(key)
    findings.append(
        {
            "reason_code": reason_code,
            "location": location,
            "match_hash": match_hash,
        }
    )


def _is_luhn_payment_card(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19:
        return False
    if len(set(digits)) <= 1:
        return False
    total = 0
    double = False
    for char in reversed(digits):
        number = int(char)
        if double:
            number *= 2
            if number > 9:
                number -= 9
        total += number
        double = not double
    return total % 10 == 0


def ingest_messages(
    conversation_json: Any,
    *,
    strict_transcript: bool = False,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    # 0. Normalize
    with _ingestion_stage("normalize"):
        conversation_json = normalize_conversation_json(
            conversation_json, strict_transcript=strict_transcript
        )
        _stamp_owner(conversation_json, owner_id=owner_id)
        _stamp_project(conversation_json, project_id=effective_project_id)
        _scope_conversation_hash(conversation_json, project_id=effective_project_id)

    # 1. Validate JSON against schema
    with _ingestion_stage("validate_schema"):
        validate_json(conversation_json)

    # 2. Enrich metadata topics from message text
    with _ingestion_stage("normalize", enrichment=True):
        enrich_topics(conversation_json)
        enrich_auto_tags(conversation_json)

    sensitive_findings = _detect_sensitive_content(conversation_json)
    if sensitive_findings:
        return _store_quarantined_memory(
            conversation_json,
            owner_id=owner_id,
            project_id=effective_project_id,
            findings=sensitive_findings,
        )

    with _ingestion_stage("dedupe_lookup"):
        conversation_hash = conversation_json["metadata"]["conversation_hash"]
        duplicate = _lookup_by_conversation_hash(
            conversation_hash, project_id=effective_project_id
        )
        duplicate_metadata_id = None
        if duplicate is not None and _conversation_allowed(
            duplicate, owner_id, effective_project_id
        ):
            duplicate_metadata_id = str(duplicate["id"])
            # We proceed to re-index even if it's a duplicate by hash,
            # to ensure the vector store is in sync with the metadata.
            conversation_json["id"] = duplicate_metadata_id

    _attach_generated_summaries(
        conversation_json,
        owner_id=owner_id,
        project_id=effective_project_id,
    )

    existing_by_id = None
    store = _runtime().metadata_store
    if hasattr(store, "get"):
        existing_by_id = store.get(str(conversation_json.get("id", "")))
    if existing_by_id is not None and not _conversation_allowed(
        existing_by_id, owner_id, effective_project_id
    ):
        raise ValueError("unauthorized_update: conversation id already exists")
    if existing_by_id is not None and not _runtime().allow_trusted_appends:
        # If it's a duplicate by ID but not by hash, it's an unauthorized update.
        # But if it's the SAME hash (we already checked), it's fine.
        if _conversation_hash(existing_by_id) != conversation_hash:
            raise ValueError("unauthorized_update: conversation id already exists")

    same_thread = _lookup_same_thread(
        conversation_json, owner_id=owner_id, project_id=effective_project_id
    )
    if same_thread is not None:
        new_messages = _detect_new_messages(same_thread, conversation_json)
        if not new_messages:
            # Re-index check for same_thread
            # If it's already fully indexed, we COULD skip, 
            # but for consistency we fall through to ensure vectors are there.
            start_index = 0
            updated = same_thread
        else:
            start_index = len(same_thread.get("messages", []))
            updated = _append_conversation(same_thread, conversation_json, new_messages)
            enrich_topics(updated)
            enrich_auto_tags(updated)
            _attach_generated_summaries(
                updated,
                owner_id=owner_id,
                project_id=effective_project_id,
            )
        
        # If we are here, we either have new messages or we are re-indexing existing
        indexing_messages = new_messages if new_messages else updated.get("messages", [])
        chunk_start_index = (
            _next_chunk_index(updated, fallback_start_index=start_index)
            if new_messages
            else 0
        )
        with _ingestion_stage("chunk", message_count=len(indexing_messages)):
            chunks = chunk_selected_messages(
                updated, indexing_messages, start_index=chunk_start_index
            )
        if new_messages:
            _extend_index_chunks(updated, chunks)
        else:
            _attach_index_chunks(updated, chunks)
        if new_messages or chunks:
            provider = str(_runtime().health_state.get("metadata_provider") or "unknown")
            with _ingestion_stage("metadata_insert", metadata_provider=provider):
                store.insert(updated)
        try:
            embeddings = embed_chunks(
                chunks, project_id=effective_project_id, owner_id=owner_id
            )
            store_vectors(str(updated["id"]), embeddings, replace=(start_index == 0))
            _mark_chunks_indexed(str(updated["id"]), chunks)
        except Exception:
            _mark_chunks_indexing_failed(str(updated["id"]), chunks)
            raise
        if new_messages:
            with _ingestion_stage("fact_extract", message_count=len(new_messages)):
                _store_facts_for_messages(
                    updated,
                    new_messages,
                    start_message_index=start_index,
                )
                graph_counts = _store_graph_for_conversation(updated)
        else:
            graph_counts = {"entities": 0, "relationships": 0}
        logger.info(
            "Conversation inserted",
            extra={
                "event": "conversation_inserted",
                "operation": "memory_insert",
                "conversation_id": str(updated["id"]),
                "deduplicated": not bool(new_messages),
                "appended_messages": len(new_messages),
                "embedded_chunks": len(chunks),
            },
        )
        result = {
            "status": "ok",
            "id": str(updated["id"]),
            "deduplicated": not bool(new_messages),
            "appended_messages": len(new_messages),
            "embedded_chunks": len(chunks),
            "chunks": len(chunks),
        }
        _record_audit_event(
            "memory.inserted",
            owner_id=owner_id,
            project_id=effective_project_id,
            memory_id=str(updated["id"]),
            metadata={
                "deduplicated": not bool(new_messages),
                "appended_messages": len(new_messages),
                "embedded_chunks": len(chunks),
                "memory_status": _memory_status(updated),
            },
        )
        return _with_graph_counts(result, graph_counts)

    # 3. Chunk messages
    with _ingestion_stage("chunk", message_count=len(conversation_json.get("messages", []))):
        chunks = chunk_messages(conversation_json)
    _attach_index_chunks(conversation_json, chunks)

    # 4. Store metadata with pending chunks before embedding. Exact duplicates
    # already have metadata; retries should re-index without attempting a second
    # primary-key insert.
    if duplicate_metadata_id is None:
        metadata_id, inserted = _insert_new_conversation(conversation_json)
    else:
        metadata_id, inserted = duplicate_metadata_id, False

    # 5. Embed and write vectors
    try:
        embeddings = embed_chunks(chunks, project_id=effective_project_id, owner_id=owner_id)
        store_vectors(metadata_id, embeddings, replace=not inserted)
        _mark_chunks_indexed(metadata_id, chunks)
    except Exception:
        _mark_chunks_indexing_failed(metadata_id, chunks)
        raise
    if inserted:
        with _ingestion_stage("fact_extract"):
            _store_facts_for_conversation(conversation_json)
            graph_counts = _store_graph_for_conversation(conversation_json)
    else:
        graph_counts = {"entities": 0, "relationships": 0}

    logger.info(
        "Conversation inserted",
        extra={
            "event": "conversation_inserted",
            "operation": "memory_insert",
            "conversation_id": metadata_id,
            "deduplicated": not inserted,
            "embedded_chunks": len(chunks),
        },
    )

    # 6. Return stored object
    result = {
        "status": "ok",
        "id": metadata_id,
        "deduplicated": not inserted,
        "appended_messages": 0,
        "embedded_chunks": len(chunks),
        "chunks": len(chunks),
    }
    _record_audit_event(
        "memory.inserted",
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_id=metadata_id,
        metadata={
            "deduplicated": not inserted,
            "appended_messages": 0,
            "embedded_chunks": len(chunks),
            "memory_status": _memory_status(conversation_json),
        },
    )
    return _with_graph_counts(result, graph_counts)


def _store_review_memory(
    conversation_json: dict[str, Any],
    *,
    owner_id: str | None,
    project_id: str,
    memory_status: str,
    received_at_key: str,
    return_status: str,
    audit_event_type: str,
    audit_outcome: str,
    audit_reason_code: str | None = None,
    audit_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = _ensure_metadata(conversation_json)
    metadata["memory_status"] = memory_status
    metadata[received_at_key] = _utc_now_iso()

    store = _runtime().metadata_store
    existing_by_id = store.get(str(conversation_json.get("id", ""))) if hasattr(store, "get") else None
    if existing_by_id is not None and not _conversation_authorized(
        existing_by_id, owner_id, project_id
    ):
        raise ValueError("unauthorized_update: conversation id already exists")
    if existing_by_id is not None and _conversation_hash(existing_by_id) != metadata["conversation_hash"]:
        raise ValueError("unauthorized_update: conversation id already exists")

    memory_id, inserted = _insert_new_conversation(conversation_json)
    audit_payload = {
        "memory_status": memory_status,
        "inserted": inserted,
        "embedded_chunks": 0,
    }
    if audit_metadata:
        audit_payload.update(audit_metadata)
    _record_audit_event(
        audit_event_type,
        owner_id=owner_id,
        project_id=project_id,
        memory_id=memory_id,
        outcome=audit_outcome,
        reason_code=audit_reason_code,
        metadata=audit_payload,
    )
    return {
        "status": return_status,
        "id": memory_id,
        "memory_status": memory_status,
        "inserted": inserted,
        "chunks": 0,
    }


def _store_quarantined_memory(
    conversation_json: dict[str, Any],
    *,
    owner_id: str | None,
    project_id: str,
    findings: list[dict[str, str]],
) -> dict[str, Any]:
    reason_codes = _unique_strings([finding["reason_code"] for finding in findings])
    metadata = _ensure_metadata(conversation_json)
    metadata["quarantine_reason_codes"] = reason_codes
    metadata["quarantine_findings"] = findings
    result = _store_review_memory(
        conversation_json,
        owner_id=owner_id,
        project_id=project_id,
        memory_status=_MEMORY_STATUS_QUARANTINED,
        received_at_key="quarantine_received_at",
        return_status="quarantined",
        audit_event_type="memory.quarantined",
        audit_outcome="quarantined",
        audit_reason_code="sensitive_content",
        audit_metadata={
            "reason_codes": reason_codes,
            "finding_count": len(findings),
        },
    )
    result["quarantine_reason_codes"] = reason_codes
    return result


def store_pending_review_memory(
    conversation_json: Any,
    *,
    strict_transcript: bool = False,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    conversation_json = normalize_conversation_json(
        conversation_json, strict_transcript=strict_transcript
    )
    _stamp_owner(conversation_json, owner_id=owner_id)
    _stamp_project(conversation_json, project_id=effective_project_id)
    _scope_conversation_hash(conversation_json, project_id=effective_project_id)
    validate_json(conversation_json)
    enrich_topics(conversation_json)
    enrich_auto_tags(conversation_json)
    sensitive_findings = _detect_sensitive_content(conversation_json)
    if sensitive_findings:
        return _store_quarantined_memory(
            conversation_json,
            owner_id=owner_id,
            project_id=effective_project_id,
            findings=sensitive_findings,
        )
    return _store_review_memory(
        conversation_json,
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_status=_MEMORY_STATUS_PENDING_REVIEW,
        received_at_key="pending_review_received_at",
        return_status="pending_review",
        audit_event_type="memory.inserted",
        audit_outcome="pending_review",
    )


def approve_pending_memory(
    memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    conversation = _runtime().metadata_store.get(memory_id)
    if not _conversation_authorized(conversation, owner_id, effective_project_id):
        _record_audit_event(
            "memory.approved",
            owner_id=owner_id,
            project_id=effective_project_id,
            memory_id=memory_id,
            outcome="not_found",
            reason_code="not_visible",
        )
        return {"status": "not_found", "id": memory_id}
    review_status = _memory_status(conversation)
    if review_status not in _REVIEWABLE_MEMORY_STATUSES:
        _record_audit_event(
            "memory.approved",
            owner_id=owner_id,
            project_id=effective_project_id,
            memory_id=memory_id,
            outcome="error",
            reason_code="memory_not_pending_review",
            metadata={"memory_status": _memory_status(conversation)},
        )
        return {
            "status": "error",
            "error_code": "memory_not_pending_review",
            "error_message": "memory is not pending review",
            "id": memory_id,
            "memory_status": _memory_status(conversation),
        }
    assert isinstance(conversation, dict)
    metadata = _ensure_metadata(conversation)
    metadata["memory_status"] = _MEMORY_STATUS_ACTIVE
    metadata["approved_at"] = _utc_now_iso()
    metadata["review_status_before_approval"] = review_status
    if metadata.get("quarantine_reason_codes"):
        metadata["quarantine_reviewed_at"] = metadata["approved_at"]
        metadata["quarantine_decision"] = "approved"
        metadata["sensitive_content_approved"] = True
    metadata.setdefault("save_intent", "user_confirmed")
    _runtime().metadata_store.insert(conversation)
    result = ingest_messages(conversation, owner_id=owner_id, project_id=effective_project_id)
    _store_facts_for_conversation(conversation)
    _store_graph_for_conversation(conversation)
    result["memory_status"] = _MEMORY_STATUS_ACTIVE
    _record_audit_event(
        "memory.approved",
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_id=memory_id,
        metadata={
            "memory_status": _MEMORY_STATUS_ACTIVE,
            "review_status_before_approval": review_status,
        },
    )
    return result


def reject_pending_memory(
    memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    conversation = _runtime().metadata_store.get(memory_id)
    if not _conversation_authorized(conversation, owner_id, effective_project_id):
        _record_audit_event(
            "memory.rejected",
            owner_id=owner_id,
            project_id=effective_project_id,
            memory_id=memory_id,
            outcome="not_found",
            reason_code="not_visible",
        )
        return {"status": "not_found", "id": memory_id}
    if _memory_status(conversation) not in _REVIEWABLE_MEMORY_STATUSES:
        _record_audit_event(
            "memory.rejected",
            owner_id=owner_id,
            project_id=effective_project_id,
            memory_id=memory_id,
            outcome="error",
            reason_code="memory_not_pending_review",
            metadata={"memory_status": _memory_status(conversation)},
        )
        return {
            "status": "error",
            "error_code": "memory_not_pending_review",
            "error_message": "memory is not pending review",
            "id": memory_id,
            "memory_status": _memory_status(conversation),
        }
    assert isinstance(conversation, dict)
    metadata = _ensure_metadata(conversation)
    review_status = _memory_status(conversation)
    metadata["memory_status"] = _MEMORY_STATUS_REJECTED
    metadata["rejected_at"] = _utc_now_iso()
    metadata["review_status_before_rejection"] = review_status
    if metadata.get("quarantine_reason_codes"):
        metadata["quarantine_reviewed_at"] = metadata["rejected_at"]
        metadata["quarantine_decision"] = "rejected"
    _runtime().metadata_store.insert(conversation)
    _record_audit_event(
        "memory.rejected",
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_id=memory_id,
        metadata={"memory_status": _MEMORY_STATUS_REJECTED},
    )
    return {"status": "ok", "id": memory_id, "memory_status": _MEMORY_STATUS_REJECTED}


def _is_fully_indexed(metadata_id: str) -> bool:
    runtime = _runtime()
    # Always re-index for in-memory store to ensure consistency after restart
    if runtime.health_state.get("vector_provider") == "memory":
        return False

    store = runtime.metadata_store
    if hasattr(store, "is_fully_indexed"):
        return store.is_fully_indexed(metadata_id)
    # Default to False to ensure indexing if we can't check
    return False


def _mark_chunks_indexed(metadata_id: str, chunks: list[dict[str, Any]]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "mark_chunks_indexed"):
        store.mark_chunks_indexed(metadata_id, [str(chunk["chunk_id"]) for chunk in chunks])


def _mark_chunks_indexing_failed(metadata_id: str, chunks: list[dict[str, Any]]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "mark_chunks_indexing_failed"):
        store.mark_chunks_indexing_failed(
            metadata_id, [str(chunk["chunk_id"]) for chunk in chunks]
        )


def reindex_stored_conversations(
    *, limit: int | None = None, project_id: str | None = None, include_inactive: bool = False
) -> dict[str, Any]:
    if limit is not None and int(limit) < 1:
        raise ValueError("limit must be a positive integer")
    if project_id is not None:
        project_id = _validate_project_id(project_id)
    store = _runtime().metadata_store
    if not hasattr(store, "list_conversations"):
        raise NotImplementedError("metadata store does not support conversation reindex")

    runtime = _runtime()
    vector_health = runtime.vector_store.health() if hasattr(runtime.vector_store, "health") else {}
    replace_existing = _vector_row_count(
        vector_store=runtime.vector_store,
        vector_health=vector_health,
    ) != 0
    conversations = store.list_conversations(limit=limit, project_id=project_id)
    reindexed = 0
    chunks_reindexed = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    for conversation in conversations:
        memory_id = str(conversation.get("id", ""))
        chunks: list[dict[str, Any]] = []
        try:
            if not include_inactive and not _conversation_is_active(conversation):
                skipped += 1
                continue
            if project_id is not None and not _conversation_project_matches(conversation, project_id):
                skipped += 1
                continue
            owner_id = _owner_id_from_conversation(conversation)
            conversation_project_id = _project_id_from_conversation(conversation)
            with _ingestion_stage(
                "chunk",
                message_count=len(conversation.get("messages", [])),
                reindex=True,
            ):
                chunks = chunk_messages(conversation)
            embeddings = embed_chunks(
                chunks,
                project_id=conversation_project_id,
                owner_id=owner_id,
            )
            store_vectors(memory_id, embeddings, replace=replace_existing)
            _mark_chunks_indexed(memory_id, chunks)
            reindexed += 1
            chunks_reindexed += len(chunks)
        except Exception as exc:
            if chunks:
                _mark_chunks_indexing_failed(memory_id, chunks)
            failures.append({"id": memory_id, "error": str(exc)})
    return {
        "status": "ok" if not failures else "error",
        "total": len(conversations),
        "reindexed": reindexed,
        "chunks": chunks_reindexed,
        "skipped": skipped,
        "failed": len(failures),
        "failures": failures,
    }


def search(
    query: str,
    top_k: int = 5,
    result_mode: str = SearchResultMode.CHUNKS.value,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tags: Sequence[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    _validate_result_mode(result_mode)
    status_filter = _validate_memory_status_filter(memory_status)
    filters = ConversationFilters.from_options(
        source=source,
        date_from=date_from,
        date_to=date_to,
        tags=tags,
        thread_id=thread_id,
    )
    runtime = _runtime()
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    query_vector = runtime.embedding_provider.embed_texts([query])[0]
    candidate_multiplier = max(
        1, int(getattr(runtime, "retrieval_candidate_multiplier", _SEARCH_CANDIDATE_MULTIPLIER))
    )
    candidate_k = 100 if filters.has_filters else max(top_k, min(top_k * candidate_multiplier, 100))
    vector_provider = str(runtime.health_state.get("vector_provider") or "unknown")
    started = time.perf_counter()
    matches = runtime.vector_store.search(query_vector, top_k=candidate_k)
    metrics.observe(
        "memory_vector_search_duration_ms",
        (time.perf_counter() - started) * 1000,
        provider=vector_provider,
    )

    ids = [str(match["memory_id"]) for match in matches]
    keyword_conversations = _keyword_conversations(
        runtime.metadata_store,
        query,
        enabled=runtime.retrieval_keyword_enabled,
        limit=runtime.retrieval_keyword_candidate_limit,
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_status=status_filter,
        filters=filters,
    )
    ids.extend(str(conversation["id"]) for conversation in keyword_conversations if "id" in conversation)
    ids = _unique_strings(ids)
    conversations = runtime.metadata_store.get_many(ids)
    conversations = {
        memory_id: enriched
        for memory_id, conversation in conversations.items()
        if (enriched := _with_generated_summary_metadata(conversation)) is not None
    }
    for conversation in keyword_conversations:
        memory_id = str(conversation.get("id", ""))
        if memory_id and memory_id not in conversations:
            conversations[memory_id] = _with_generated_summary_metadata(conversation) or conversation

    results: list[dict[str, Any]] = []
    for match in matches:
        memory_id = str(match["memory_id"])
        score = float(match["score"])
        conversation = conversations.get(memory_id)
        if not _conversation_visible(conversation, owner_id, effective_project_id, status_filter):
            continue
        if not _conversation_matches_filters(conversation, filters):
            continue
        row = {
            "id": memory_id,
            "score": score,
            "chunk_index": int(match["chunk_index"]),
            "role": match["role"],
            "text": match["text"],
            "conversation": conversation,
        }
        if _passes_retrieval_threshold(
            query=query,
            row=row,
            threshold=runtime.retrieval_vector_score_threshold,
        ):
            results.append(row)

    existing_keys = {
        (str(row["id"]), int(row["chunk_index"]), str(row["text"])) for row in results
    }
    for conversation in keyword_conversations:
        memory_id = str(conversation.get("id", ""))
        if not memory_id:
            continue
        if not _conversation_visible(conversation, owner_id, effective_project_id, status_filter):
            continue
        if not _conversation_matches_filters(conversation, filters):
            continue
        row = _keyword_result_row(
            conversation,
            score=runtime.retrieval_vector_score_threshold,
        )
        key = (str(row["id"]), int(row["chunk_index"]), str(row["text"]))
        if key not in existing_keys:
            results.append(row)
            existing_keys.add(key)

    graph_rows, graph_diagnostics = _graph_candidate_rows(
        query=query,
        owner_id=owner_id,
        project_id=effective_project_id,
        existing_keys=existing_keys,
        score=runtime.retrieval_vector_score_threshold,
        limit=top_k,
    )
    results.extend(graph_rows)

    ranked = _rank_retrieval_results(
        query,
        results,
        keyword_weight=runtime.retrieval_keyword_weight,
        metadata_weight=runtime.retrieval_metadata_weight,
    )
    grouped = group_conversation_results(ranked)
    payload: dict[str, Any] = {"status": "ok", "results": _apply_result_mode(grouped, result_mode)[:top_k]}
    if graph_diagnostics["enabled"] and graph_diagnostics["candidate_count"]:
        payload["diagnostics"] = {"graph": graph_diagnostics}
    _record_audit_event(
        "memory.searched",
        owner_id=owner_id,
        project_id=effective_project_id,
        metadata={
            "query_hash": _audit_hash(query),
            "top_k": top_k,
            "result_count": len(payload["results"]),
            "result_mode": result_mode,
            "memory_status": status_filter,
            "filters": {
                "source": bool(filters.source),
                "date_from": bool(filters.date_from),
                "date_to": bool(filters.date_to),
                "tags": len(filters.tags),
                "thread_id": bool(filters.thread_id),
            },
        },
    )
    return payload


def _validate_result_mode(result_mode: str) -> None:
    if result_mode not in _RESULT_MODES:
        raise ValueError(result_mode_error_message())


def _apply_result_mode(rows: list[dict[str, Any]], result_mode: str) -> list[dict[str, Any]]:
    if result_mode == SearchResultMode.CHUNKS:
        return rows
    if result_mode == SearchResultMode.THREADS:
        return _thread_result_rows(rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        memory_id = str(row.get("id", ""))
        if memory_id not in grouped:
            grouped[memory_id] = []
            order.append(memory_id)
        grouped[memory_id].append(row)

    compacted: list[dict[str, Any]] = []
    for memory_id in order:
        chunk_rows = grouped[memory_id]
        best = dict(chunk_rows[0])
        evidence = [_citation_from_row(row) for row in chunk_rows]
        best["matching_chunks"] = len(chunk_rows)
        best["evidence_chunks"] = evidence
        best["used_in_answer"] = False
        if result_mode == SearchResultMode.CONVERSATIONS:
            best["chunk_index"] = 0
            best["role"] = ""
            best["text"] = _conversation_result_text(best.get("conversation"), evidence)
        compacted.append(best)
    return compacted


def _thread_result_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        thread_id = _thread_id_from_conversation(row.get("conversation"))
        if thread_id not in grouped:
            grouped[thread_id] = []
            order.append(thread_id)
        grouped[thread_id].append(row)

    thread_rows: list[dict[str, Any]] = []
    for thread_id in order:
        thread_matches = grouped[thread_id]
        best = dict(thread_matches[0])
        conversation_ids = _unique_strings(
            [str(row.get("id", "")) for row in thread_matches if row.get("id")]
        )
        best[ThreadResultKey.THREAD_ID] = thread_id
        best[ThreadResultKey.THREAD_CONVERSATION_IDS] = conversation_ids
        best[ThreadResultKey.THREAD_CONVERSATION_COUNT] = len(conversation_ids)
        best[ThreadResultKey.MATCHING_CONVERSATIONS] = len(conversation_ids)
        best[ThreadResultKey.MATCHING_CHUNKS] = sum(
            int(row.get("matching_chunks", 1)) for row in thread_matches
        )
        best[ThreadResultKey.EVIDENCE_CHUNKS] = [_citation_from_row(row) for row in thread_matches]
        best[ThreadResultKey.USED_IN_ANSWER] = False
        best["chunk_index"] = 0
        best["role"] = ""
        best["text"] = _thread_result_text(thread_id, conversation_ids, thread_matches)
        return_row = _strip_internal_ranking_fields(best)
        thread_rows.append(return_row)
    return thread_rows


def _thread_id_from_conversation(conversation: Any) -> str:
    if not isinstance(conversation, dict):
        return "thread:unknown"
    metadata = conversation.get("metadata")
    if isinstance(metadata, dict) and metadata.get(ThreadMetadataKey.THREAD_ID):
        return str(metadata[ThreadMetadataKey.THREAD_ID])
    return f"conversation:{conversation.get('id', 'unknown')}"


def _thread_result_text(
    thread_id: str, conversation_ids: list[str], thread_matches: list[dict[str, Any]]
) -> str:
    return (
        f"{thread_id}: {len(conversation_ids)} conversation(s), "
        f"{len(thread_matches)} matching chunk(s)"
    )


def _strip_internal_ranking_fields(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(row)
    cleaned.pop("_grouped_score", None)
    cleaned.pop("_original_rank", None)
    cleaned.pop("_ranking_score", None)
    cleaned.pop("_ranking_boost", None)
    cleaned.pop("_graph_boost", None)
    cleaned.pop("_graph_relationship_id", None)
    cleaned.pop("_graph_predicate", None)
    return cleaned


def _conversation_result_text(conversation: Any, evidence: list[dict[str, Any]]) -> str:
    if isinstance(conversation, dict):
        title = str(conversation.get("title") or conversation.get("source") or conversation.get("id"))
        return f"{title}: {len(evidence)} matching chunk(s)"
    return f"{len(evidence)} matching chunk(s)"


def group_conversation_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_scores: dict[str, float] = {}
    group_counts: dict[str, int] = {}
    for row in rows:
        memory_id = str(row.get("id", ""))
        if not memory_id:
            continue
        score = float(row.get("_ranking_score", row.get("score", 0.0)))
        group_scores[memory_id] = min(score, group_scores.get(memory_id, score))
        group_counts[memory_id] = group_counts.get(memory_id, 0) + 1

    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        memory_id = str(row.get("id", ""))
        row_score = float(row.get("_ranking_score", row.get("score", 0.0)))
        conversation_score = group_scores.get(memory_id, row_score)
        grouped_score = row_score
        if row_score - conversation_score <= _CONVERSATION_GROUP_SCORE_WINDOW:
            grouped_score = conversation_score
        enriched_row = dict(row)
        enriched_row["conversation_score"] = conversation_score
        enriched_row["conversation_match_count"] = group_counts.get(memory_id, 1)
        enriched_row["_original_rank"] = index
        enriched_row["_grouped_score"] = grouped_score
        enriched.append(enriched_row)

    grouped = sorted(
        enriched,
        key=lambda row: (
            float(row["_grouped_score"]),
            -float(row.get("_ranking_boost", 0.0)),
            str(row.get("id", "")),
            float(row.get("score", 0.0)),
            int(row.get("chunk_index", 0)),
            int(row["_original_rank"]),
        ),
    )
    for row in grouped:
        row.pop("_grouped_score", None)
        row.pop("_original_rank", None)
        row.pop("_ranking_score", None)
        row.pop("_ranking_boost", None)
        row.pop("_graph_boost", None)
        row.pop("_graph_relationship_id", None)
        row.pop("_graph_predicate", None)
    return grouped


def _keyword_conversations(
    metadata_store: Any,
    query: str,
    *,
    enabled: bool,
    limit: int,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    filters: ConversationFilters | None = None,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    status_filter = _validate_memory_status_filter(memory_status)
    filters = filters or ConversationFilters()
    if hasattr(metadata_store, "search_text"):
        try:
            candidates = metadata_store.search_text(query, limit=limit, project_id=project_id)
        except TypeError:
            candidates = metadata_store.search_text(query, limit=limit)
        return [
            item
            for item in candidates
            if isinstance(item, dict)
            and _conversation_visible(item, owner_id, project_id, status_filter)
            and _conversation_matches_filters(item, filters)
        ]
    rows = getattr(metadata_store, "by_id", None) or getattr(metadata_store, "rows", None)
    if not isinstance(rows, dict):
        return []
    tokens = _query_tokens(query)
    if not tokens:
        return []
    matches: list[dict[str, Any]] = []
    for conversation in rows.values():
        if not isinstance(conversation, dict):
            continue
        if not _conversation_visible(conversation, owner_id, project_id, status_filter):
            continue
        if not _conversation_matches_filters(conversation, filters):
            continue
        text = _conversation_search_text(conversation).lower()
        if all(token in text for token in tokens):
            matches.append(conversation)
    return matches[:limit]


def _graph_candidate_rows(
    *,
    query: str,
    owner_id: str | None,
    project_id: str | None,
    existing_keys: set[tuple[str, int, str]],
    score: float,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    runtime = _runtime()
    diagnostics = {
        "enabled": bool(runtime.retrieval_graph_enabled),
        "quality_gate_passed": bool(runtime.retrieval_graph_quality_gate_passed),
        "candidate_count": 0,
        "influenced_ids": [],
    }
    if not runtime.retrieval_graph_enabled or not runtime.retrieval_graph_quality_gate_passed:
        return [], diagnostics
    relationships = graph_relationship_search(owner_id=owner_id, project_id=project_id)["results"]
    query_tokens = set(_query_tokens(query))
    scored: list[tuple[int, dict[str, Any]]] = []
    for relationship in relationships:
        overlap = len(query_tokens & set(_query_tokens(_relationship_search_text(relationship))))
        if overlap > 0:
            scored.append((overlap, relationship))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    rows: list[dict[str, Any]] = []
    influenced_ids: list[str] = []
    for _, relationship in scored[:limit]:
        conversation_id = _relationship_conversation_id(relationship)
        if not conversation_id:
            continue
        conversation = retrieve(conversation_id, owner_id=owner_id, project_id=project_id)
        if not isinstance(conversation, dict):
            continue
        row = _keyword_result_row(conversation, score=score)
        row["_graph_boost"] = float(runtime.retrieval_graph_weight)
        row["_graph_relationship_id"] = relationship.get("id")
        row["_graph_predicate"] = relationship.get("predicate")
        key = (str(row["id"]), int(row["chunk_index"]), str(row["text"]))
        if key in existing_keys:
            continue
        rows.append(row)
        existing_keys.add(key)
        influenced_ids.append(str(row["id"]))
    diagnostics["candidate_count"] = len(rows)
    diagnostics["influenced_ids"] = influenced_ids
    return rows, diagnostics


def _relationship_search_text(relationship: dict[str, Any]) -> str:
    return " ".join(
        [
            str(relationship.get("subject", "")),
            str(relationship.get("predicate", "")),
            str(relationship.get("object", "")),
        ]
    )


def _relationship_conversation_id(relationship: dict[str, Any]) -> str | None:
    provenance = relationship.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        return None
    first = provenance[0]
    if not isinstance(first, dict):
        return None
    conversation_id = first.get("conversation_id")
    return str(conversation_id) if conversation_id else None


def _passes_retrieval_threshold(*, query: str, row: dict[str, Any], threshold: float) -> bool:
    score = float(row.get("score", 0.0))
    if score <= threshold:
        return True
    return _keyword_overlap(query, row) > 0 or _metadata_overlap(query, row.get("conversation")) > 0


def _rank_retrieval_results(
    query: str,
    rows: list[dict[str, Any]],
    *,
    keyword_weight: float,
    metadata_weight: float,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        keyword_overlap = _keyword_overlap(query, row)
        metadata_overlap = _metadata_overlap(query, row.get("conversation"))
        ranking_score = float(row.get("score", 0.0))
        advanced_boost = _advanced_scoring_boost(row)
        ranking_boost = (
            min(keyword_overlap, 3) * keyword_weight
            + min(metadata_overlap, 3) * metadata_weight
            + float(row.get("_graph_boost", 0.0))
            + advanced_boost
        )
        ranking_score -= ranking_boost
        enriched = dict(row)
        if advanced_boost:
            enriched["score"] = max(0.0, float(enriched.get("score", 0.0)) - advanced_boost)
        enriched["_ranking_score"] = max(0.0, ranking_score)
        enriched["_ranking_boost"] = ranking_boost
        enriched["_original_rank"] = index
        ranked.append(enriched)
    return sorted(
        ranked,
        key=lambda row: (
            float(row["_ranking_score"]),
            str(row.get("id", "")),
            int(row.get("chunk_index", 0)),
            int(row["_original_rank"]),
        ),
    )


def _advanced_scoring_boost(row: dict[str, Any]) -> float:
    runtime = _runtime()
    if not runtime.retrieval_advanced_scoring_enabled:
        return 0.0
    conversation = row.get("conversation")
    if not isinstance(conversation, dict):
        return 0.0
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return 0.0
    raw = metadata.get("advanced_memory")
    if not isinstance(raw, dict):
        return 0.0
    try:
        signals = MemoryScoringSignals.model_validate(raw)
    except ValueError:
        return 0.0
    scoring = advanced_relevance_boost(
        signals,
        MemoryScoringWeights(
            recency_weight=runtime.retrieval_recency_weight,
            importance_weight=runtime.retrieval_importance_weight,
            pin_weight=runtime.retrieval_pin_weight,
            access_weight=runtime.retrieval_access_weight,
        ),
    )
    row["ranking_explanation"] = {"advanced_memory": scoring["signals"]}
    return float(scoring["boost"])


def _keyword_result_row(conversation: dict[str, Any], *, score: float) -> dict[str, Any]:
    messages = conversation.get("messages", [])
    first_message = messages[0] if isinstance(messages, list) and messages else {}
    if not isinstance(first_message, dict):
        first_message = {}
    return {
        "id": str(conversation["id"]),
        "score": float(score),
        "chunk_index": 0,
        "role": str(first_message.get("role", "")),
        "text": str(first_message.get("text", "")),
        "conversation": conversation,
    }


def _keyword_overlap(query: str, row: dict[str, Any]) -> int:
    tokens = set(_query_tokens(query))
    if not tokens:
        return 0
    text = str(row.get("text", "")).lower()
    conversation = row.get("conversation")
    if isinstance(conversation, dict):
        text += " " + _conversation_search_text(conversation).lower()
    return sum(1 for token in tokens if token in text)


def _metadata_overlap(query: str, conversation: Any) -> int:
    if not isinstance(conversation, dict):
        return 0
    tokens = set(_query_tokens(query))
    if not tokens:
        return 0
    metadata = conversation.get("metadata", {})
    metadata_values: list[str] = [str(conversation.get("source", "")), str(conversation.get("title", ""))]
    if isinstance(metadata, dict):
        metadata_values.extend(_metadata_search_values(metadata))
    text = " ".join(metadata_values).lower()
    return sum(1 for token in tokens if token in text)


def _conversation_search_text(conversation: dict[str, Any]) -> str:
    parts = [
        str(conversation.get("id", "")),
        str(conversation.get("source", "")),
        str(conversation.get("title", "")),
    ]
    messages = conversation.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                parts.append(str(message.get("role", "")))
                parts.append(str(message.get("text", "")))
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        parts.extend(_metadata_search_values(metadata))
    return " ".join(parts)


def _metadata_search_values(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("tags", "auto_tags", "tag_sources", "topics", "summary", "generated_summary"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, list):
                    values.extend(str(child) for child in item)
                elif item is not None:
                    values.append(str(item))
        elif value is not None:
            values.append(str(value))
    return values


def _query_tokens(query: str, *, limit: int | None = 8) -> list[str]:
    tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
        if len(token) >= 2
    ]
    return tokens[:limit] if limit is not None else tokens


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def retrieve(
    memory_id: str,
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
) -> dict[str, Any] | None:
    runtime = _runtime()
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    status_filter = _validate_memory_status_filter(memory_status)
    conversation = runtime.metadata_store.get(memory_id)
    if not _conversation_visible(conversation, owner_id, effective_project_id, status_filter):
        _record_audit_event(
            "memory.retrieved",
            owner_id=owner_id,
            project_id=effective_project_id,
            memory_id=memory_id,
            outcome="not_found",
            reason_code="not_visible",
            metadata={"memory_status": status_filter},
        )
        return None
    result = _with_generated_summary_metadata(conversation)
    _record_audit_event(
        "memory.retrieved",
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_id=memory_id,
        metadata={"memory_status": status_filter},
    )
    return result


def ask(
    question: str,
    top_k: int = 5,
    max_context_tokens: int | None = None,
    result_mode: str = SearchResultMode.CHUNKS.value,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tags: Sequence[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    _validate_result_mode(result_mode)
    filters = ConversationFilters.from_options(
        source=source,
        date_from=date_from,
        date_to=date_to,
        tags=tags,
        thread_id=thread_id,
    )
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    status_filter = _validate_memory_status_filter(memory_status)
    fact_answer = _answer_from_facts(
        question,
        top_k=top_k,
        result_mode=result_mode,
        owner_id=owner_id,
        project_id=effective_project_id,
        filters=filters,
    )
    if fact_answer is not None and status_filter == _MEMORY_STATUS_ACTIVE:
        _record_audit_event(
            "memory.asked",
            owner_id=owner_id,
            project_id=effective_project_id,
            metadata={
                "question_hash": _audit_hash(question),
                "top_k": top_k,
                "result_count": len(fact_answer.get("results", [])),
                "answer_basis": fact_answer.get("answer_basis"),
                "memory_status": status_filter,
            },
        )
        return fact_answer

    search_result = _search_for_ask(
        question=question,
        top_k=top_k,
        result_mode=result_mode,
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_status=status_filter,
        filters=filters,
    )
    matches = search_result.get("results", [])
    if not matches:
        _record_audit_event(
            "memory.asked",
            owner_id=owner_id,
            project_id=effective_project_id,
            outcome="not_found",
            metadata={
                "question_hash": _audit_hash(question),
                "top_k": top_k,
                "result_count": 0,
                "answer_basis": _AskAnswerBasis.NOT_FOUND.value,
                "memory_status": status_filter,
            },
        )
        return _enum_keyed_payload(
            {
                _AskResponseKey.STATUS: _AskResponseStatus.OK.value,
                _AskResponseKey.RESULTS: [],
                _AskResponseKey.ANSWER: "I could not find relevant memory for that question.",
                _AskResponseKey.CITATIONS: [],
                _AskResponseKey.CONFIDENCE: "none",
                _AskResponseKey.CONFIDENCE_REASON: "No matching memory or facts were found.",
                _AskResponseKey.ANSWER_BASIS: _AskAnswerBasis.NOT_FOUND.value,
                _AskResponseKey.PROVENANCE: [],
                _AskResponseKey.EVIDENCE: [],
                _AskResponseKey.STRUCTURED_EVIDENCE: _enum_keyed_payload(
                    {
                        _StructuredEvidenceKey.FACTS: [],
                        _StructuredEvidenceKey.RESULTS: [],
                    }
                ),
            }
        )

    runtime = _runtime()
    budget_enabled = runtime.tokenizer_enabled or max_context_tokens is not None
    if not budget_enabled:
        result = _ask_from_matches(matches, top_k=top_k, question=question)
        _record_audit_event(
            "memory.asked",
            owner_id=owner_id,
            project_id=effective_project_id,
            metadata={
                "question_hash": _audit_hash(question),
                "top_k": top_k,
                "result_count": len(result.get("results", [])),
                "answer_basis": result.get("answer_basis"),
                "memory_status": status_filter,
            },
        )
        return result

    token_budget = (
        max_context_tokens
        if max_context_tokens is not None
        else runtime.ask_max_context_tokens
    )
    (
        selected_matches,
        citations,
        context_lines,
        tokens_used,
        chunks_dropped,
        context_truncated,
    ) = _select_ask_context(
        matches=matches[:top_k],
        max_context_tokens=token_budget,
        encoding=runtime.tokenizer_encoding,
    )
    result = _budgeted_direct_memory_ask_result(
        selected_matches=selected_matches,
        citations=citations,
        context_lines=context_lines,
        question=question,
        tokens_used=tokens_used,
        chunks_dropped=chunks_dropped,
        context_truncated=context_truncated,
        tokenizer_encoding=runtime.tokenizer_encoding,
    )
    _record_audit_event(
        "memory.asked",
        owner_id=owner_id,
        project_id=effective_project_id,
        metadata={
            "question_hash": _audit_hash(question),
            "top_k": top_k,
            "result_count": len(selected_matches),
            "answer_basis": result.get("answer_basis"),
            "memory_status": status_filter,
            "context_tokens_used": tokens_used,
            "chunks_dropped": chunks_dropped,
        },
    )
    return result


def _search_for_ask(
    question: str,
    *,
    top_k: int,
    result_mode: str,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    filters: ConversationFilters | None = None,
) -> dict[str, Any]:
    filters = filters or ConversationFilters()
    try:
        return search(
            query=question,
            top_k=top_k,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
            memory_status=memory_status,
            source=filters.source,
            date_from=filters.date_from,
            date_to=filters.date_to,
            tags=filters.tags,
            thread_id=filters.thread_id,
        )
    except TypeError:
        if result_mode != SearchResultMode.CHUNKS:
            raise
        return search(query=question, top_k=top_k)


def _next_chunk_index(
    conversation: dict[str, Any], *, fallback_start_index: int
) -> int:
    metadata = conversation.get("metadata", {})
    index_chunks = metadata.get("index_chunks") if isinstance(metadata, dict) else None
    if isinstance(index_chunks, list):
        indexes = [
            int(chunk.get("chunk_index", -1))
            for chunk in index_chunks
            if isinstance(chunk, dict)
        ]
        if indexes:
            return max(indexes) + 1
    return fallback_start_index


def _extend_index_chunks(obj: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        obj["metadata"] = metadata
    existing = metadata.get("index_chunks")
    if not isinstance(existing, list):
        existing = []
    metadata["index_chunks"] = existing + [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "chunk_index": int(chunk["chunk_index"]),
            "message_hash": str(chunk["message_hash"]),
            "role": str(chunk["role"]),
            "text": str(chunk["text"]),
            "index_state": str(chunk.get("index_state", "pending_index")),
        }
        for chunk in chunks
    ]


def _ask_from_matches(
    matches: list[dict[str, Any]], *, top_k: int, question: str
) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    for row in matches:
        citation = _citation_from_row(row)
        citations.append(citation)

    selected = matches[:top_k]
    selected_citations = citations[:top_k]
    return _direct_memory_ask_result(
        selected_matches=selected,
        citations=selected_citations,
        answer=_direct_memory_answer_text(selected, question=question),
        confidence=_confidence_from_matches(selected),
        confidence_reason=_confidence_reason_from_matches(selected),
    )


def _budgeted_direct_memory_ask_result(
    *,
    selected_matches: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    context_lines: list[str],
    question: str,
    tokens_used: int,
    chunks_dropped: int,
    context_truncated: bool,
    tokenizer_encoding: str,
) -> dict[str, Any]:
    answer = (
        _direct_memory_answer_text(selected_matches, question=question)
        if context_lines
        else "I could not fit relevant memory within the context budget."
    )
    return _direct_memory_ask_result(
        selected_matches=selected_matches,
        citations=citations,
        answer=answer,
        confidence=_confidence_from_context(
            selected_matches,
            context_truncated=context_truncated,
        ),
        confidence_reason=_confidence_reason_from_matches(
            selected_matches,
            context_truncated=context_truncated,
        ),
        extra_fields={
            _AskResponseKey.CONTEXT_TOKENS_USED: tokens_used,
            _AskResponseKey.CHUNKS_SELECTED: len(selected_matches),
            _AskResponseKey.CHUNKS_DROPPED: chunks_dropped,
            _AskResponseKey.CONTEXT_TRUNCATED: context_truncated,
            _AskResponseKey.TOKENIZER_USED: tokenizer_used(tokenizer_encoding),
        },
    )


def _direct_memory_ask_result(
    *,
    selected_matches: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    answer: str,
    confidence: str,
    confidence_reason: str,
    extra_fields: dict[_AskResponseKey, Any] | None = None,
) -> dict[str, Any]:
    result = {
        _AskResponseKey.STATUS: _AskResponseStatus.OK.value,
        _AskResponseKey.RESULTS: selected_matches,
        _AskResponseKey.ANSWER: answer,
        _AskResponseKey.CITATIONS: citations,
        _AskResponseKey.CONFIDENCE: confidence,
        _AskResponseKey.CONFIDENCE_REASON: confidence_reason,
        _AskResponseKey.ANSWER_BASIS: _AskAnswerBasis.DIRECT_MEMORY.value,
        _AskResponseKey.PROVENANCE: _provenance_from_matches(
            selected_matches,
            citations,
        ),
        _AskResponseKey.EVIDENCE: _chunk_evidence_from_matches(selected_matches),
        _AskResponseKey.STRUCTURED_EVIDENCE: _enum_keyed_payload(
            {
                _StructuredEvidenceKey.FACTS: [],
                _StructuredEvidenceKey.RESULTS: selected_matches,
            }
        ),
    }
    if extra_fields:
        result.update(extra_fields)
    return _enum_keyed_payload(result)


def _direct_memory_answer_text(
    matches: Sequence[dict[str, Any]], *, question: str | None = None
) -> str:
    snippets = [
        snippet
        for row in matches
        for snippet in (_direct_memory_answer_snippet(row),)
        if snippet
    ]
    for snippet in snippets:
        if not _direct_memory_answer_snippet_is_question_echo(snippet, question):
            return snippet
    if snippets:
        return snippets[0]
    return "I found relevant memory, but it did not include usable text."


def _direct_memory_answer_snippet(row: dict[str, Any]) -> str:
    text = " ".join(str(row.get("text", "")).strip().split())
    if not text:
        return ""
    return _truncate_summary_text(text, limit=600)


def _direct_memory_answer_snippet_is_question_echo(
    snippet: str, question: str | None
) -> bool:
    normalized_snippet = _normalized_question_echo_text(snippet)
    if question is not None and normalized_snippet == _normalized_question_echo_text(question):
        return True
    return snippet.rstrip().endswith("?")


def _normalized_question_echo_text(value: str) -> str:
    return " ".join(_query_tokens(value, limit=None))


def _enum_keyed_payload(payload: Mapping[_PayloadKey, Any]) -> dict[str, Any]:
    return {key.value: value for key, value in payload.items()}


def _fact_correction_qualifiers(
    correction: _FactCorrectionMatch,
) -> dict[str, str]:
    return _enum_keyed_payload(
        {
            _FactQualifierKey.ITEM: correction.item,
            _FactQualifierKey.CORRECTS: correction.old_value,
        }
    )


def _citation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "chunk_index": int(row.get("chunk_index", 0)),
        "score": float(row.get("score", 0.0)),
        "text": row.get("text", ""),
    }


def _select_ask_context(
    *,
    matches: list[dict[str, Any]],
    max_context_tokens: int,
    encoding: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], int, int, bool]:
    selected_matches: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    context_lines: list[str] = []
    tokens_used = 0
    chunks_dropped = 0
    context_truncated = False

    for row in matches:
        citation_id = row.get("id")
        chunk_index = int(row.get("chunk_index", 0))
        prefix = f"- [{citation_id}#{chunk_index}] "
        prefix_tokens = count_tokens(prefix, encoding)
        remaining = max_context_tokens - tokens_used - prefix_tokens
        if remaining <= 0:
            chunks_dropped += 1
            context_truncated = True
            continue

        text = str(row.get("text", ""))
        text_tokens = count_tokens(text, encoding)
        selected_text = text
        if text_tokens > remaining:
            selected_text = truncate_to_tokens(text, remaining, encoding)
            text_tokens = count_tokens(selected_text, encoding)
            context_truncated = True
        if not selected_text:
            chunks_dropped += 1
            context_truncated = True
            continue

        line = prefix + selected_text
        line_tokens = prefix_tokens + text_tokens
        selected_row = dict(row)
        selected_row["text"] = selected_text
        citation = _citation_from_row({**row, "text": selected_text})
        selected_matches.append(selected_row)
        citations.append(citation)
        context_lines.append(line)
        tokens_used += line_tokens

    return (
        selected_matches,
        citations,
        context_lines,
        tokens_used,
        chunks_dropped,
        context_truncated,
    )


def _confidence_from_matches(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "none"
    best = min(float(row.get("score", 0.0)) for row in matches)
    if best <= 1.0:
        return "high"
    if best <= 4.0:
        return "medium"
    return "low"


def _confidence_from_context(
    matches: list[dict[str, Any]], *, context_truncated: bool
) -> str:
    if not matches:
        return "none"
    if context_truncated:
        return "low"
    return _confidence_from_matches(matches)


def _provenance_from_matches(
    matches: list[dict[str, Any]], citations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    used = {(str(item.get("id")), int(item.get("chunk_index", 0))) for item in citations}
    grouped: dict[str, dict[str, Any]] = {}
    for row in matches:
        memory_id = str(row.get("id", ""))
        if not memory_id:
            continue
        conversation = row.get("conversation")
        item = grouped.setdefault(
            memory_id,
            {
                "conversation_id": memory_id,
                "source": conversation.get("source") if isinstance(conversation, dict) else None,
                "title": conversation.get("title") if isinstance(conversation, dict) else None,
                "stored_at": conversation.get("timestamp") if isinstance(conversation, dict) else None,
                "matching_chunks": 0,
                "used_in_answer": False,
            },
        )
        item["matching_chunks"] += int(row.get("matching_chunks", 1))
        if (memory_id, int(row.get("chunk_index", 0))) in used:
            item["used_in_answer"] = True
    return list(grouped.values())


def _chunk_evidence_from_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "chunk",
            "conversation_id": row.get("id"),
            "chunk_index": int(row.get("chunk_index", 0)),
            "role": row.get("role"),
            "text": row.get("text"),
            "score": row.get("score"),
            "used_in_answer": True,
        }
        for row in matches
    ]


def _confidence_reason_from_matches(
    matches: list[dict[str, Any]], *, context_truncated: bool = False
) -> str:
    if context_truncated:
        return "Retrieved memory was truncated by the context budget."
    if not matches:
        return "No retrieved chunks were used."
    return "Answer built from ranked retrieved conversation chunks."


def _store_facts_for_conversation(conversation: dict[str, Any]) -> None:
    facts = extract_facts(conversation)
    _store_facts(facts)


def _store_facts_for_messages(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> None:
    facts = extract_facts_from_messages(
        conversation,
        messages,
        start_message_index=start_message_index,
    )
    _store_facts(facts)


def _store_facts(facts: list[dict[str, Any]]) -> None:
    if not facts:
        return
    store = _runtime().metadata_store
    if hasattr(store, "insert_facts"):
        store.insert_facts(facts)
        return
    existing = getattr(store, "_facts", [])
    if not isinstance(existing, list):
        existing = []
    active = [fact for fact in existing if fact.get("deleted_at") is None]
    for fact in facts:
        _apply_in_memory_fact_supersession(active, fact)
        active.append(fact)
    setattr(store, "_facts", active)


def _store_graph_for_conversation(conversation: dict[str, Any]) -> dict[str, int]:
    runtime = _runtime()
    if not runtime.graph_enabled:
        return {"entities": 0, "relationships": 0}
    graph = extract_memory_graph(conversation)
    entities = graph["entities"]
    relationships = graph["relationships"]
    if not entities and not relationships:
        return {"entities": 0, "relationships": 0}
    store = runtime.metadata_store
    if hasattr(store, "upsert_graph_records"):
        store.upsert_graph_records(entities=entities, relationships=relationships)
    else:
        _store_graph_in_memory(store, entities=entities, relationships=relationships)
    return {"entities": len(entities), "relationships": len(relationships)}


def _with_graph_counts(result: dict[str, Any], graph_counts: dict[str, int]) -> dict[str, Any]:
    if graph_counts["entities"] or graph_counts["relationships"]:
        result["graph"] = graph_counts
    return result


def _store_graph_in_memory(
    store: Any, *, entities: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> None:
    existing_entities = {
        str(entity["id"]): dict(entity)
        for entity in getattr(store, "_graph_entities", [])
        if isinstance(entity, dict) and "id" in entity
    }
    existing_relationships = {
        str(relationship["id"]): dict(relationship)
        for relationship in getattr(store, "_graph_relationships", [])
        if isinstance(relationship, dict) and "id" in relationship
    }
    for entity in entities:
        existing_entities[str(entity["id"])] = dict(entity)
    for relationship in relationships:
        _apply_in_memory_relationship_supersession(existing_relationships, relationship)
        existing_relationships[str(relationship["id"])] = dict(relationship)
    setattr(store, "_graph_entities", list(existing_entities.values()))
    setattr(store, "_graph_relationships", list(existing_relationships.values()))


def _apply_in_memory_relationship_supersession(
    existing: dict[str, dict[str, Any]], relationship: dict[str, Any]
) -> None:
    conflict_group = relationship.get("conflict_group")
    if not conflict_group:
        return
    for current in existing.values():
        if (
            current.get("conflict_group") == conflict_group
            and current.get("id") != relationship.get("id")
            and current.get("object") != relationship.get("object")
            and current.get("owner_id") == relationship.get("owner_id")
            and current.get("project_id") == relationship.get("project_id")
            and not current.get("superseded_by")
        ):
            current["superseded_by"] = relationship["id"]
            current["updated_at"] = relationship.get("updated_at")


def extract_facts(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    messages = conversation.get("messages", [])
    return extract_facts_from_messages(
        conversation,
        messages if isinstance(messages, list) else [],
        start_message_index=0,
    )


def extract_facts_from_messages(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for offset, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        message_index = start_message_index + offset
        text = str(message.get("text", ""))
        for fact in _extract_message_facts(
            text,
            conversation=conversation,
            message_index=message_index,
            source_role=str(message.get("role", "")),
        ):
            facts.append(fact)
    facts.extend(_topic_facts(conversation, messages, start_message_index=start_message_index))
    facts.extend(_external_extracted_facts(conversation, messages, start_message_index=start_message_index))
    return _dedupe_facts(facts)


def _extract_message_facts(
    text: str, *, conversation: dict[str, Any], message_index: int, source_role: str
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    correction_spans: list[tuple[int, int]] = []
    for correction in _fact_correction_matches(text):
        correction_spans.append(correction.span)
        predicate, object_value = _corrected_fact_shape(
            correction.item,
            correction.new_value,
        )
        facts.append(
            _fact(
                subject=_FactSubject.USER.value,
                predicate=predicate,
                object_value=object_value,
                conversation=conversation,
                message_index=message_index,
                qualifiers=_fact_correction_qualifiers(correction),
                source_role=source_role,
            )
        )
    for rule_name, pattern in _FACT_RULES:
        for match in pattern.finditer(text):
            if _span_overlaps(match.span(), correction_spans):
                continue
            if rule_name == _FactRuleName.OWN:
                object_value = _clean_fact_object(match.group(FactField.OBJECT.value))
                facts.append(
                    _fact(
                        subject=_FactSubject.USER.value,
                        predicate=_owned_item_predicate(object_value),
                        object_value=object_value,
                        conversation=conversation,
                        message_index=message_index,
                        qualifiers=_owned_item_qualifiers(object_value),
                        source_role=source_role,
                    )
                )
            elif rule_name == _FactRuleName.FAVORITE:
                name = _normalize_predicate_part(match.group("name"))
                object_value = _clean_fact_object(match.group("object"))
                if _has_inline_correction(object_value):
                    continue
                facts.append(
                    _fact(
                        subject=_FactSubject.USER.value,
                        predicate=f"{_FactPredicatePrefix.FAVORITE.value}{name}",
                        object_value=object_value,
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name == _FactRuleName.LIKES:
                facts.append(
                    _fact(
                        subject=_FactSubject.USER.value,
                        predicate=_FactPredicate.LIKES.value,
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        qualifiers={_FactQualifierKey.PREFERENCE.value: "positive"},
                        source_role=source_role,
                    )
                )
            elif rule_name == _FactRuleName.SUBJECT_CREATOR:
                facts.append(
                    _fact(
                        subject=match.group("subject").strip(),
                        predicate=_FactPredicate.CREATOR.value,
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name in {
                _FactRuleName.CREATOR,
                _FactRuleName.COMMAND_NAME,
                _FactRuleName.INDEXING_STRATEGY,
            }:
                facts.append(
                    _fact(
                        subject=_project_subject(conversation),
                        predicate=rule_name.value,
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name in {
                _FactRuleName.PROFILE_NAME,
                _FactRuleName.PROFILE_IDENTITY,
                _FactRuleName.PROFILE_ROLE,
                _FactRuleName.PROFILE_LOCATION,
            }:
                facts.append(
                    _fact(
                        subject=_FactSubject.USER.value,
                        predicate=rule_name.value,
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name in {
                _FactRuleName.PROJECT_ATTRIBUTE,
                _FactRuleName.PROJECT_ATTRIBUTE_CHANGE,
            }:
                subject = match.group(FactField.SUBJECT.value).strip()
                object_value = _clean_fact_object(match.group(FactField.OBJECT.value))
                if _should_skip_project_attribute_fact(subject, object_value):
                    continue
                facts.append(
                    _fact(
                        subject=subject,
                        predicate=_FactPredicate.DESCRIPTION.value,
                        object_value=object_value,
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
    return _dedupe_facts(facts)


def _fact_correction_matches(text: str) -> list[_FactCorrectionMatch]:
    corrections: list[_FactCorrectionMatch] = []
    for pattern in (_FACT_CORRECTION_RE, _FACT_REPLACES_CORRECTION_RE):
        for match in pattern.finditer(text):
            span = match.span()
            if _span_overlaps(span, [correction.span for correction in corrections]):
                continue
            corrections.append(
                _FactCorrectionMatch(
                    span=span,
                    item=_clean_fact_object(
                        match.group(_FactCorrectionGroup.ITEM.value)
                    ),
                    new_value=_clean_fact_object(
                        match.group(_FactCorrectionGroup.NEW_VALUE.value)
                    ),
                    old_value=_clean_fact_object(
                        match.group(_FactCorrectionGroup.OLD_VALUE.value)
                    ),
                )
            )
    corrections.sort(key=lambda correction: correction.span[0])
    return corrections


def _span_overlaps(span: tuple[int, int], spans: Sequence[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in spans)


def _corrected_fact_shape(item: str, new_value: str) -> tuple[str, str]:
    item = _clean_fact_object(item)
    new_value = _clean_fact_object(new_value)
    if item.lower().startswith(_FactItemPrefix.FAVORITE.value):
        favorite_name = item[len(_FactItemPrefix.FAVORITE.value):].strip()
        predicate = (
            f"{_FactPredicatePrefix.FAVORITE.value}"
            f"{_normalize_predicate_part(favorite_name)}"
        )
        return predicate, new_value
    return _owned_item_predicate(item), f"{item} is {new_value}"


def _has_inline_correction(value: str) -> bool:
    return _FACT_INLINE_CORRECTION_RE.search(value) is not None


def _should_skip_project_attribute_fact(subject: str, object_value: str) -> bool:
    subject_lower = subject.lower()
    if subject_lower in {"i", "my", "the", "this"}:
        return True
    if _has_inline_correction(object_value):
        return True
    subject_tokens = set(_query_tokens(subject, limit=None))
    return bool(subject_tokens.intersection(_NOISY_PROJECT_ATTRIBUTE_SUBJECT_TOKENS))


def _topic_facts(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> list[dict[str, Any]]:
    topics = infer_topics(messages)
    facts: list[dict[str, Any]] = []
    for topic in topics:
        facts.append(
            _fact(
                subject=_FactSubject.USER.value,
                predicate=_FactPredicate.RECURRING_TOPIC.value,
                object_value=topic,
                conversation=conversation,
                message_index=start_message_index,
                qualifiers={_FactQualifierKey.TOPIC.value: topic},
            )
        )
    return facts


def _external_extracted_facts(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> list[dict[str, Any]]:
    extractor = _runtime().fact_extractor
    if not callable(extractor):
        return []
    raw_facts = extractor(conversation=conversation, messages=messages)
    if not isinstance(raw_facts, list):
        return []
    facts: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue
        subject = str(raw.get(FactField.SUBJECT.value, "")).strip()
        predicate = str(raw.get(FactField.PREDICATE.value, "")).strip()
        object_value = str(raw.get(FactField.OBJECT.value, "")).strip()
        if not subject or not predicate or not object_value:
            continue
        indexes = raw.get("source_message_indexes")
        if isinstance(indexes, list) and indexes:
            message_index = int(indexes[0])
        else:
            message_index = start_message_index
        facts.append(
            _fact(
                subject=subject,
                predicate=predicate,
                object_value=object_value,
                conversation=conversation,
                message_index=message_index,
                qualifiers=raw.get(FactField.QUALIFIERS.value)
                if isinstance(raw.get(FactField.QUALIFIERS.value), dict)
                else None,
            )
        )
    return facts


def _fact(
    *,
    subject: str,
    predicate: str,
    object_value: str,
    conversation: dict[str, Any],
    message_index: int,
    qualifiers: dict[str, Any] | None = None,
    source_role: str | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    source_id = str(conversation.get("id", ""))
    identity = f"{subject}\n{predicate}\n{object_value}\n{source_id}\n{message_index}"
    fact_id = "fact-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    normalized_qualifiers = dict(qualifiers or {})
    if source_role in {_FactSourceRole.USER.value, _FactSourceRole.ASSISTANT.value}:
        normalized_qualifiers.setdefault(_FactQualifierKey.SOURCE_ROLE.value, source_role)
    normalized_qualifiers.update(_save_intent_qualifiers(conversation))
    object_normalized, normalization = _normalize_fact_object(
        predicate=predicate, object_value=object_value
    )
    if normalization:
        normalized_qualifiers[_FactQualifierKey.NORMALIZATION.value] = normalization
    fact = {
        FactField.ID.value: fact_id,
        FactField.SUBJECT.value: subject,
        FactField.PREDICATE.value: predicate,
        FactField.OBJECT.value: object_value,
        FactField.QUALIFIERS.value: normalized_qualifiers,
        FactField.CONFIDENCE.value: "medium"
        if normalized_qualifiers.get(_FactQualifierKey.SAVE_INTENT.value) == "client_auto_save"
        else "high",
        FactField.LAST_CONFIRMED_AT.value: now,
        FactField.SOURCE_CONVERSATION_ID.value: source_id,
        "owner_id": _owner_id_from_conversation(conversation),
        "project_id": _project_id_from_conversation(conversation),
        FactField.SOURCE_MESSAGE_INDEXES.value: [message_index],
        FactField.CREATED_AT.value: now,
        FactField.UPDATED_AT.value: now,
        FactField.SUPERSEDED_BY.value: None,
        FactField.SUPERSEDED_AT.value: None,
        FactField.DELETED_AT.value: None,
    }
    fact[FactField.OBJECT_RAW.value] = object_value
    fact[FactField.OBJECT_NORMALIZED.value] = object_normalized
    fact[FactField.SOURCE_QUALITY.value] = _source_quality_for_fact(fact)
    fact[FactField.CONFIDENCE_REASON.value] = _confidence_reason_for_fact(fact)
    return fact


def _save_intent_qualifiers(conversation: dict[str, Any]) -> dict[str, str]:
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    qualifiers: dict[str, str] = {}
    for key in (
        _FactQualifierKey.SAVE_INTENT,
        _FactQualifierKey.SAVE_INTENT_SOURCE,
        _FactQualifierKey.SAVE_INTENT_EVIDENCE,
    ):
        value = metadata.get(key.value)
        if value is not None and str(value):
            qualifiers[key.value] = str(value)
    return qualifiers


def _fact_qualifier_value(fact: dict[str, Any], key: str) -> str | None:
    direct = fact.get(key)
    if direct is not None and str(direct):
        return str(direct)
    qualifiers = fact.get(FactField.QUALIFIERS.value)
    if not isinstance(qualifiers, dict):
        return None
    value = qualifiers.get(key)
    return str(value) if value is not None and str(value) else None


def _fact_save_intent_fields(fact: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in (_FactQualifierKey.SAVE_INTENT, _FactQualifierKey.SAVE_INTENT_SOURCE):
        value = _fact_qualifier_value(fact, key.value)
        if value:
            fields[key.value] = value
    return fields


def _answer_from_facts(
    question: str,
    *,
    top_k: int,
    result_mode: str,
    owner_id: str | None = None,
    project_id: str | None = None,
    filters: ConversationFilters | None = None,
) -> dict[str, Any] | None:
    filters = filters or ConversationFilters()
    query = _fact_query(question)
    if query is None:
        return None
    facts = _search_facts(
        subject=(
            query.get(FactField.SUBJECT.value)
            if query.get(FactField.SUBJECT.value) == _FactSubject.USER.value
            else None
        ),
        predicate=query.get(FactField.PREDICATE.value),
        owner_id=owner_id,
        project_id=project_id,
        conversation_filters=filters,
        fact_filters=FactFilters.from_options(status="all"),
    )
    facts = _filter_facts_for_question(facts, question, query)
    candidates = [fact for fact in facts if fact.get(FactField.DELETED_AT.value) is None]
    candidates = _narrow_facts_for_question(candidates, question, query)
    if not candidates:
        return None
    public_timeline = [_public_fact(fact) for fact in candidates]
    projection = FactTimelineProjector().project(public_timeline)
    selected_entries = list(projection.unique_latest_entries)
    if not selected_entries:
        return None
    needs_context = _fact_question_needs_context(question)
    basis = (
        _AskAnswerBasis.CONFLICT.value
        if projection.has_latest_conflict
        else (
            _AskAnswerBasis.MIXED.value
            if needs_context
            else _AskAnswerBasis.FACT_LAYER.value
        )
    )
    public_active = [entry.fact for entry in selected_entries]
    public_history = [entry.fact for entry in projection.historical_entries]
    confidence = (
        "low"
        if basis == _AskAnswerBasis.CONFLICT.value
        else str(public_active[0].get(FactField.CONFIDENCE.value, "medium"))
    )
    fact_evidence = [_fact_evidence(fact, used_in_answer=True) for fact in public_active]
    fact_evidence.extend(_fact_evidence(fact, used_in_answer=False) for fact in public_history)
    answer = _fact_answer_text(
        question,
        public_active,
        conflict=basis == _AskAnswerBasis.CONFLICT.value,
    )
    citations = [_fact_citation(fact) for fact in public_active]
    results: list[dict[str, Any]] = []
    if basis == _AskAnswerBasis.MIXED.value:
        search_result = _search_for_ask(
            question=question,
            top_k=top_k,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
            filters=filters,
        )
        results = search_result.get("results", [])
        if isinstance(results, list):
            citations.extend(_citation_from_row(row) for row in results[:top_k] if isinstance(row, dict))
            context = "\n".join(
                f"- [{row.get('id')}#{int(row.get('chunk_index', 0))}] {row.get('text', '')}"
                for row in results[:top_k]
                if isinstance(row, dict)
            )
            if context:
                answer = answer + "\nContext from memory:\n" + context
    return _enum_keyed_payload(
        {
            _AskResponseKey.STATUS: _AskResponseStatus.OK.value,
            _AskResponseKey.RESULTS: results,
            _AskResponseKey.ANSWER: answer,
            _AskResponseKey.CITATIONS: citations,
            _AskResponseKey.CONFIDENCE: confidence,
            _AskResponseKey.CONFIDENCE_REASON: _confidence_reason_for_facts(
                public_active, basis
            ),
            _AskResponseKey.ANSWER_BASIS: basis,
            _AskResponseKey.PROVENANCE: _provenance_from_facts(public_active),
            _AskResponseKey.FACTS: public_active,
            _AskResponseKey.LATEST: projection.latest_payload(),
            _AskResponseKey.FACT_TIMELINE: projection.timeline_payload(),
            _AskResponseKey.EVIDENCE: fact_evidence,
            _AskResponseKey.STRUCTURED_EVIDENCE: _enum_keyed_payload(
                {
                    _StructuredEvidenceKey.FACTS: fact_evidence,
                    _StructuredEvidenceKey.RESULTS: results,
                }
            ),
        }
    )


def _fact_query(question: str) -> dict[str, str] | None:
    lowered = question.lower()
    subject = _question_project_subject(question)
    if "guitar" in lowered and any(term in lowered for term in ("own", "have", "my")):
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.OWNS_GUITAR.value,
        }
    if "who am i" in lowered or "what do you know about me" in lowered:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.PROFILE_IDENTITY.value,
        }
    if "my name" in lowered or "what is my name" in lowered:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.PROFILE_NAME.value,
        }
    if "where do i live" in lowered:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.PROFILE_LOCATION.value,
        }
    if "what do i work as" in lowered or "my job" in lowered:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.PROFILE_ROLE.value,
        }
    favorite_match = re.search(r"favorite\s+(?P<name>[A-Za-z0-9 _-]+)", question, re.IGNORECASE)
    if favorite_match:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: (
                f"{_FactPredicatePrefix.FAVORITE.value}"
                f"{_normalize_predicate_part(favorite_match.group('name'))}"
            ),
        }
    if any(term in lowered for term in ("do i like", "i like", "prefer", "preference")):
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.LIKES.value,
        }
    if "topic" in lowered or "recurring" in lowered:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: _FactPredicate.RECURRING_TOPIC.value,
        }
    if "command name" in lowered:
        query = {FactField.PREDICATE.value: _FactPredicate.COMMAND_NAME.value}
        if subject:
            query[FactField.SUBJECT.value] = subject
        return query
    if "indexing strategy" in lowered:
        query = {FactField.PREDICATE.value: _FactPredicate.INDEXING_STRATEGY.value}
        if subject:
            query[FactField.SUBJECT.value] = subject
        return query
    favorite = re.search(r"favorite\s+([A-Za-z0-9 _-]+)", lowered)
    if favorite:
        return {
            FactField.SUBJECT.value: _FactSubject.USER.value,
            FactField.PREDICATE.value: (
                f"{_FactPredicatePrefix.FAVORITE.value}"
                f"{_normalize_predicate_part(favorite.group(1))}"
            ),
        }
    if "creator" in lowered or "who created" in lowered:
        query = {FactField.PREDICATE.value: _FactPredicate.CREATOR.value}
        if subject:
            query[FactField.SUBJECT.value] = subject
        return query
    generic_attribute_query = _generic_attribute_query(question)
    if generic_attribute_query is not None:
        return generic_attribute_query
    return None


def _generic_attribute_query(question: str) -> dict[str, str] | None:
    match = _GENERIC_ATTRIBUTE_QUESTION_RE.search(question)
    if match is None:
        return None
    subject = _clean_generic_attribute_subject(match.group(FactField.SUBJECT.value))
    if _should_skip_generic_attribute_question_subject(subject):
        return None
    return {
        FactField.SUBJECT.value: subject,
        FactField.PREDICATE.value: _FactPredicate.DESCRIPTION.value,
    }


def _clean_generic_attribute_subject(value: str) -> str:
    stripped = _GENERIC_ATTRIBUTE_ARTICLE_RE.sub("", value.strip())
    return _clean_fact_object(stripped)


def _should_skip_generic_attribute_question_subject(subject: str) -> bool:
    if not subject:
        return True
    subject_lower = subject.lower()
    if subject_lower in {"i", "my", "the", "this"}:
        return True
    subject_tokens = set(_query_tokens(subject, limit=None))
    return bool(subject_tokens.intersection(_NOISY_PROJECT_ATTRIBUTE_SUBJECT_TOKENS))


def _question_project_subject(question: str) -> str | None:
    patterns = [
        r"\b(?:who created|creator of|for|about)\s+(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})",
        r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})\?",
    ]
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            return match.group("subject").strip(" ?.")
    return None


def _filter_facts_for_question(
    facts: list[dict[str, Any]], question: str, query: dict[str, str]
) -> list[dict[str, Any]]:
    subject = query.get(FactField.SUBJECT.value)
    if not subject:
        return facts
    subject_tokens = set(_query_tokens(subject))
    if not subject_tokens:
        return facts
    filtered = [
        fact
        for fact in facts
        if subject_tokens.issubset(
            set(_query_tokens(str(fact.get(FactField.SUBJECT.value, ""))))
        )
    ]
    if filtered:
        return filtered
    question_tokens = set(_query_tokens(question))
    return [
        fact
        for fact in facts
        if set(_query_tokens(str(fact.get(FactField.SUBJECT.value, "")))).intersection(
            question_tokens
        )
    ] or facts


def _narrow_facts_for_question(
    facts: list[dict[str, Any]], question: str, query: dict[str, str]
) -> list[dict[str, Any]]:
    if len(facts) < 2:
        return facts
    question_tokens = _specific_fact_question_tokens(question, query)
    if not question_tokens:
        return facts
    scored = [
        (_fact_question_overlap_score(fact, question_tokens), index, fact)
        for index, fact in enumerate(facts)
    ]
    best_score = max(score for score, _index, _fact in scored)
    if best_score <= 0:
        return facts
    narrowed = [fact for score, _index, fact in scored if score == best_score]
    return narrowed if len(narrowed) < len(facts) else facts


def _specific_fact_question_tokens(question: str, query: dict[str, str]) -> set[str]:
    predicate_tokens = set(
        _query_tokens(
            query.get(FactField.PREDICATE.value, "").replace("_", " "),
            limit=None,
        )
    )
    subject_tokens = set(
        _query_tokens(query.get(FactField.SUBJECT.value, ""), limit=None)
    )
    ignored_tokens = _FACT_QUESTION_STOPWORDS | predicate_tokens | subject_tokens
    return {
        token
        for token in _query_tokens(question, limit=None)
        if token not in ignored_tokens
    }


def _fact_question_overlap_score(
    fact: dict[str, Any], question_tokens: set[str]
) -> int:
    fact_tokens = set(_query_tokens(_fact_search_text(fact), limit=None))
    return len(question_tokens.intersection(fact_tokens))


def _fact_search_text(fact: dict[str, Any]) -> str:
    parts = [
        str(fact.get(FactField.SUBJECT.value, "")),
        str(fact.get(FactField.PREDICATE.value, "")).replace("_", " "),
        str(fact.get(FactField.OBJECT.value, "")),
        str(fact.get(FactField.OBJECT_NORMALIZED.value, "")),
        str(fact.get(FactField.SOURCE_CONVERSATION_ID.value, "")),
    ]
    conversation = _source_conversation_for_fact(fact)
    if isinstance(conversation, dict):
        parts.append(_conversation_search_text(conversation))
    return " ".join(parts)


def _fact_question_needs_context(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in ("context", "source", "why", "when", "where did", "discuss"))


def _search_facts(
    subject: str | None = None,
    predicate: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    conversation_filters: ConversationFilters | None = None,
    fact_filters: FactFilters | None = None,
) -> list[dict[str, Any]]:
    store = _runtime().metadata_store
    conversation_filters = conversation_filters or ConversationFilters()
    fact_filters = fact_filters or FactFilters()
    if hasattr(store, "search_facts"):
        facts = store.search_facts(
            subject=subject,
            predicate=predicate,
            include_superseded=fact_filters.include_superseded,
            project_id=project_id,
        )
        return [
            fact
            for fact in facts
            if _fact_allowed(fact, owner_id, project_id)
            and _fact_matches_filters(fact, conversation_filters, fact_filters)
        ]
    facts = getattr(store, "_facts", [])
    if not isinstance(facts, list):
        return []
    return [
        fact
        for fact in facts
        if isinstance(fact, dict)
        and (subject is None or str(fact.get(FactField.SUBJECT.value)) == subject)
        and (predicate is None or str(fact.get(FactField.PREDICATE.value)) == predicate)
        and _fact_allowed(fact, owner_id, project_id)
        and _fact_matches_filters(fact, conversation_filters, fact_filters)
        and not fact.get(FactField.DELETED_AT.value)
    ]


def _fact_matches_filters(
    fact: dict[str, Any],
    conversation_filters: ConversationFilters,
    fact_filters: FactFilters,
) -> bool:
    if fact_filters.status == "active" and fact.get(FactField.SUPERSEDED_BY.value):
        return False
    if fact_filters.status == "superseded" and not fact.get(FactField.SUPERSEDED_BY.value):
        return False
    if (
        fact_filters.confidence
        and str(fact.get(FactField.CONFIDENCE.value)) != fact_filters.confidence
    ):
        return False
    if (
        fact_filters.source_quality
        and str(fact.get(FactField.SOURCE_QUALITY.value)) != fact_filters.source_quality
    ):
        return False
    if (
        fact_filters.save_intent
        and _fact_qualifier_value(fact, _FactQualifierKey.SAVE_INTENT.value)
        != fact_filters.save_intent
    ):
        return False
    if (
        fact_filters.save_intent_source
        and _fact_qualifier_value(fact, _FactQualifierKey.SAVE_INTENT_SOURCE.value)
        != fact_filters.save_intent_source
    ):
        return False
    if not _datetime_in_range(
        str(fact.get(FactField.CREATED_AT.value, "")),
        date_from=fact_filters.date_from,
        date_to=fact_filters.date_to,
        field_name="fact.created_at",
    ):
        return False
    freshness = (
        fact.get(FactField.LAST_CONFIRMED_AT.value)
        or fact.get(FactField.UPDATED_AT.value)
        or fact.get(FactField.CREATED_AT.value)
    )
    if not _datetime_in_range(
        str(freshness or ""),
        date_from=fact_filters.freshness_from,
        date_to=fact_filters.freshness_to,
        field_name="fact.last_confirmed_at",
    ):
        return False
    if conversation_filters.has_filters or fact_filters.source:
        source_filters = conversation_filters
        if fact_filters.source and fact_filters.source != conversation_filters.source:
            source_filters = ConversationFilters.from_options(
                source=fact_filters.source,
                date_from=conversation_filters.date_from,
                date_to=conversation_filters.date_to,
                tags=conversation_filters.tags,
                thread_id=conversation_filters.thread_id,
            )
        conversation = _source_conversation_for_fact(fact)
        if not _conversation_matches_filters(conversation, source_filters):
            return False
    return True


def fact_search(
    *,
    subject: str | None = None,
    predicate: str | None = None,
    include_superseded: bool = False,
    owner_id: str | None = None,
    project_id: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    source_quality: str | None = None,
    save_intent: str | None = None,
    save_intent_source: str | None = None,
    freshness_from: str | None = None,
    freshness_to: str | None = None,
) -> dict[str, Any]:
    fact_filters = FactFilters.from_options(
        source=source,
        date_from=date_from,
        date_to=date_to,
        confidence=confidence,
        status=status or ("all" if include_superseded else "active"),
        source_quality=source_quality,
        save_intent=save_intent,
        save_intent_source=save_intent_source,
        freshness_from=freshness_from,
        freshness_to=freshness_to,
    )
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    store = _runtime().metadata_store
    if hasattr(store, "search_facts"):
        facts = store.search_facts(
            subject=subject,
            predicate=predicate,
            include_superseded=fact_filters.include_superseded,
            project_id=effective_project_id,
        )
        facts = [
            fact
            for fact in facts
            if _fact_allowed(fact, owner_id, effective_project_id)
            and _fact_matches_filters(fact, ConversationFilters(), fact_filters)
        ]
    else:
        facts = getattr(store, "_facts", [])
        if not isinstance(facts, list):
            facts = []
        facts = [
            fact for fact in facts
            if isinstance(fact, dict)
            and (subject is None or str(fact.get(FactField.SUBJECT.value)) == subject)
            and (
                predicate is None
                or str(fact.get(FactField.PREDICATE.value)) == predicate
            )
            and _fact_allowed(fact, owner_id, effective_project_id)
            and _fact_matches_filters(fact, ConversationFilters(), fact_filters)
            and not fact.get(FactField.DELETED_AT.value)
        ]
    return {"status": "ok", "results": [_public_fact(fact) for fact in facts]}


def graph_entity_search(
    *,
    entity_type: str | None = None,
    name: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    store = _runtime().metadata_store
    if hasattr(store, "search_graph_entities"):
        entities = store.search_graph_entities(
            entity_type=entity_type,
            name=name,
            owner_id=owner_id,
            project_id=effective_project_id,
            include_inactive=include_inactive,
        )
    else:
        entities = [
            entity
            for entity in getattr(store, "_graph_entities", [])
            if isinstance(entity, dict)
            and (entity_type is None or str(entity.get("entity_type")) == entity_type)
            and (name is None or str(entity.get("normalized_name")) == " ".join(name.strip().casefold().split()))
            and _record_allowed(entity, owner_id, effective_project_id)
            and (include_inactive or str(entity.get("review_status")) in {"active", "approved", "needs_review"})
        ]
    return {"status": "ok", "results": entities}


def graph_relationship_search(
    *,
    subject: str | None = None,
    predicate: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    store = _runtime().metadata_store
    if hasattr(store, "search_graph_relationships"):
        relationships = store.search_graph_relationships(
            subject=subject,
            predicate=predicate,
            owner_id=owner_id,
            project_id=effective_project_id,
            include_superseded=include_superseded,
        )
    else:
        relationships = [
            relationship
            for relationship in getattr(store, "_graph_relationships", [])
            if isinstance(relationship, dict)
            and (subject is None or str(relationship.get("subject")) == subject)
            and (predicate is None or str(relationship.get("predicate")) == predicate)
            and _record_allowed(relationship, owner_id, effective_project_id)
            and (include_superseded or not relationship.get("superseded_by"))
        ]
    return {"status": "ok", "results": relationships}


def profile_get(
    subject: str = "user",
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
    source: str | None = None,
    predicate: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    source_quality: str | None = None,
    save_intent: str | None = None,
    save_intent_source: str | None = None,
    freshness_from: str | None = None,
    freshness_to: str | None = None,
) -> dict[str, Any]:
    facts = fact_search(
        subject=subject,
        predicate=predicate,
        owner_id=owner_id,
        project_id=project_id,
        source=source,
        date_from=date_from,
        date_to=date_to,
        confidence=confidence,
        status=status,
        source_quality=source_quality,
        save_intent=save_intent,
        save_intent_source=save_intent_source,
        freshness_from=freshness_from,
        freshness_to=freshness_to,
    )["results"]
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    summary = _profile_summary(
        subject=subject,
        facts=facts,
        owner_id=owner_id,
        project_id=effective_project_id,
        filters={
            "predicate": predicate,
            "source": source,
            "date_from": date_from,
            "date_to": date_to,
            "confidence": confidence,
            "status": status or "active",
            "source_quality": source_quality,
            "save_intent": save_intent,
            "save_intent_source": save_intent_source,
            "freshness_from": freshness_from,
            "freshness_to": freshness_to,
        },
    )
    return {
        "status": "ok",
        "subject": subject,
        "summary": summary,
        "facts": facts,
    }


def fact_supersede(
    fact_id: str,
    superseded_by: str,
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    existing = fact_search(
        include_superseded=True, owner_id=owner_id, project_id=effective_project_id
    )["results"]
    if not any(fact.get("id") == fact_id for fact in existing):
        return {"status": "not_found", "id": fact_id, "superseded_by": superseded_by}
    store = _runtime().metadata_store
    if hasattr(store, "supersede_fact"):
        updated = store.supersede_fact(fact_id, superseded_by, project_id=effective_project_id)
    else:
        updated = False
        for fact in getattr(store, "_facts", []):
            if (
                isinstance(fact, dict)
                and fact.get("id") == fact_id
                and _fact_project_matches(fact, effective_project_id)
            ):
                now = _utc_now_iso()
                fact["superseded_by"] = superseded_by
                fact["superseded_at"] = now
                fact["updated_at"] = now
                updated = True
    _record_audit_event(
        "fact.superseded",
        owner_id=owner_id,
        project_id=effective_project_id,
        fact_id=fact_id,
        outcome="ok" if updated else "not_found",
        reason_code=None if updated else "fact_not_found",
        metadata={"superseded_by": superseded_by},
    )
    return {"status": "ok" if updated else "not_found", "id": fact_id, "superseded_by": superseded_by}


def _record_allowed(record: dict[str, Any], owner_id: str | None, project_id: str | None) -> bool:
    if owner_id is not None and record.get("owner_id") != owner_id:
        return False
    return _fact_project_matches(record, project_id)


def project_list(*, owner_id: str | None = None) -> dict[str, Any]:
    store = _runtime().metadata_store
    if hasattr(store, "ensure_default_project"):
        store.ensure_default_project(owner_id)
    if hasattr(store, "list_projects"):
        return {"status": "ok", "results": store.list_projects(user_id=owner_id)}
    return {"status": "ok", "results": [_fallback_project(owner_id)]}


def project_default_get(*, owner_id: str | None = None) -> dict[str, Any]:
    store = _runtime().metadata_store
    if hasattr(store, "ensure_default_project"):
        project = _normalize_project_record(store.ensure_default_project(owner_id), owner_id=owner_id)
    else:
        project = _fallback_project(owner_id)
    return {"status": "ok", "project": project}


def project_get(project_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
    project = _validate_project_id(project_id)
    _resolve_project(owner_id=owner_id, project_id=project, required_role=PROJECT_ROLE_READER)
    visible = project_list(owner_id=owner_id)["results"]
    for candidate in visible:
        if isinstance(candidate, dict) and candidate.get("id") == project:
            return {"status": "ok", "project": candidate}
    return {"status": "not_found", "id": project}


def _fallback_project(owner_id: str | None) -> dict[str, Any]:
    project_id = _default_project_id(owner_id) if owner_id is not None else LOCAL_DEFAULT_PROJECT_ID
    return {
        "id": project_id,
        "owner_id": owner_id,
        "name": "Private Default" if owner_id is not None else "Local Default",
        "description": None,
        "is_default": True,
        "created_at": None,
        "updated_at": None,
        "archived_at": None,
        "role": "admin",
    }


def _normalize_project_record(project: Any, *, owner_id: str | None) -> dict[str, Any]:
    if isinstance(project, dict):
        normalized = dict(project)
        normalized.setdefault("role", "admin")
        return normalized
    project_id = str(project)
    return {
        **_fallback_project(owner_id),
        "id": _validate_project_id(project_id),
    }

def authenticate_bearer_token(token: str) -> str | None:
    context = authenticate_bearer_token_context(token)
    return str(context["owner_id"]) if context is not None else None


def authenticate_bearer_token_context(token: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if hasattr(store, "auth_context_for_token"):
        context = store.auth_context_for_token(token)
        if context is not None:
            return context
    if hasattr(store, "owner_for_token"):
        owner_id = store.owner_for_token(token)
        if owner_id is not None:
            return {
                "owner_id": str(owner_id),
                "token_id": None,
                "scopes": ["memory:read", "memory:write"],
            }
    auth_tokens = getattr(store, "auth_tokens", None)
    if isinstance(auth_tokens, dict):
        owner_id = auth_tokens.get(token)
        if owner_id is not None:
            return {
                "owner_id": str(owner_id),
                "token_id": None,
                "scopes": ["memory:read", "memory:write"],
        }
    return None


def find_or_create_oauth_identity(
    *,
    provider: str,
    provider_subject: str,
    email: str | None = None,
    display_name: str | None = None,
) -> dict[str, object]:
    store = _runtime().metadata_store
    if not hasattr(store, "find_or_create_oauth_identity"):
        raise NotImplementedError("metadata store does not support oauth identities")
    return store.find_or_create_oauth_identity(
        provider=provider,
        provider_subject=provider_subject,
        email=email,
        display_name=display_name,
    )


def create_web_session(
    *,
    session_id_hash: str,
    user_id: str,
    csrf_token_hash: str,
    expires_at: str,
) -> dict[str, object]:
    store = _runtime().metadata_store
    if not hasattr(store, "create_web_session"):
        raise NotImplementedError("metadata store does not support web sessions")
    return store.create_web_session(
        session_id_hash=session_id_hash,
        user_id=user_id,
        csrf_token_hash=csrf_token_hash,
        expires_at=expires_at,
    )


def web_session_for_hash(session_id_hash: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if not hasattr(store, "web_session_for_hash"):
        return None
    return store.web_session_for_hash(session_id_hash)


def revoke_web_session(session_id_hash: str) -> bool:
    store = _runtime().metadata_store
    if not hasattr(store, "revoke_web_session"):
        return False
    return bool(store.revoke_web_session(session_id_hash))


def create_auth_token(
    *,
    owner_id: str,
    token: str,
    token_display_name: str | None = None,
    expires_at: str | None = None,
    scopes: list[str] | None = None,
) -> dict[str, object]:
    store = _runtime().metadata_store
    if not hasattr(store, "create_auth_token"):
        raise NotImplementedError("metadata store does not support auth tokens")
    result = store.create_auth_token(
        owner_id=owner_id,
        token=token,
        token_display_name=token_display_name,
        expires_at=expires_at,
        scopes=scopes,
    )
    result_scopes = result.get("scopes") if isinstance(result, dict) else []
    _record_audit_event(
        "auth.token_created",
        owner_id=owner_id,
        outcome="ok",
        metadata={
            "token_id": result.get("token_id") if isinstance(result, dict) else None,
            "scope_count": len(result_scopes) if isinstance(result_scopes, list) else 0,
            "expires": result.get("expires_at") is not None
            if isinstance(result, dict)
            else False,
        },
    )
    return result


def revoke_auth_token(token_id: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if not hasattr(store, "revoke_auth_token"):
        return None
    result = store.revoke_auth_token(token_id)
    _record_audit_event(
        "auth.token_revoked",
        owner_id=str(result["owner_id"])
        if isinstance(result, dict) and result.get("owner_id")
        else None,
        outcome="ok" if result is not None else "not_found",
        reason_code=None if result is not None else "token_not_found",
        metadata={"token_id": token_id},
    )
    return result


def create_oauth_client(
    *,
    client_id: str,
    client_name: str,
    redirect_uris: list[str],
    expires_at: str,
) -> dict[str, object]:
    store = _runtime().metadata_store
    if not hasattr(store, "create_oauth_client"):
        raise NotImplementedError("metadata store does not support oauth clients")
    return store.create_oauth_client(
        client_id=client_id,
        client_name=client_name,
        redirect_uris=redirect_uris,
        expires_at=expires_at,
    )


def oauth_client(client_id: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if not hasattr(store, "oauth_client"):
        return None
    return store.oauth_client(client_id)


def create_oauth_refresh_token(
    *,
    refresh_token: str,
    token_family_id: str,
    client_id: str,
    owner_id: str,
    scopes: list[str],
    resource: str,
    access_token_id: str | None,
    expires_at: str,
) -> dict[str, object]:
    store = _runtime().metadata_store
    if not hasattr(store, "create_oauth_refresh_token"):
        raise NotImplementedError("metadata store does not support oauth refresh tokens")
    return store.create_oauth_refresh_token(
        refresh_token=refresh_token,
        token_family_id=token_family_id,
        client_id=client_id,
        owner_id=owner_id,
        scopes=scopes,
        resource=resource,
        access_token_id=access_token_id,
        expires_at=expires_at,
    )


def oauth_refresh_token(refresh_token: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if not hasattr(store, "oauth_refresh_token"):
        return None
    return store.oauth_refresh_token(refresh_token)


def consume_oauth_refresh_token(refresh_token: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if not hasattr(store, "consume_oauth_refresh_token"):
        return None
    return store.consume_oauth_refresh_token(refresh_token)


def revoke_oauth_refresh_token(refresh_token: str) -> bool:
    store = _runtime().metadata_store
    if not hasattr(store, "revoke_oauth_refresh_token"):
        return False
    return bool(store.revoke_oauth_refresh_token(refresh_token))


def revoke_oauth_refresh_token_family(token_family_id: str) -> bool:
    store = _runtime().metadata_store
    if not hasattr(store, "revoke_oauth_refresh_token_family"):
        return False
    return bool(store.revoke_oauth_refresh_token_family(token_family_id))


def revoke_oauth_authorization_for_access_token(access_token: str) -> bool:
    store = _runtime().metadata_store
    if not hasattr(store, "revoke_oauth_authorization_for_access_token"):
        return False
    return bool(store.revoke_oauth_authorization_for_access_token(access_token))


def _fact_answer_text(
    question: str,
    facts: list[dict[str, Any]],
    *,
    conflict: bool,
) -> str:
    _ = question
    objects = [
        str(fact.get(FactField.OBJECT_NORMALIZED.value) or fact.get(FactField.OBJECT.value, ""))
        for fact in facts
    ]
    if conflict:
        return "Stored facts disagree: " + "; ".join(objects)
    if len(objects) > 1:
        return "; ".join(objects)
    return objects[0]


def _profile_summary(
    *,
    subject: str,
    facts: list[dict[str, Any]],
    owner_id: str | None,
    project_id: str | None,
    filters: dict[str, Any],
) -> dict[str, Any]:
    active_facts = [fact for fact in facts if not fact.get(FactField.SUPERSEDED_BY.value)]
    freshest_at = _freshest_fact_timestamp(active_facts)
    source_quality_counts = _count_values(active_facts, FactField.SOURCE_QUALITY.value)
    confidence_counts = _count_values(active_facts, FactField.CONFIDENCE.value)
    text = _profile_summary_text(active_facts)
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type="profile",
            target_id=subject,
            project_id=project_id,
            filters=filters,
        ),
        type=SummaryType.PROFILE,
        target_id=subject,
        project_id=project_id,
        owner_id=owner_id,
        text=text,
        basis=SummaryBasis.ACTIVE_FACTS,
        provenance_status=SummaryProvenanceStatus.FACT_IDS,
        fact_count=len(facts),
        active_fact_count=len(active_facts),
        freshest_at=freshest_at,
        source_quality_counts=source_quality_counts,
        confidence_counts=confidence_counts,
        filters={key: str(value) for key, value in filters.items() if value is not None},
        provenance=[
            GeneratedSummaryProvenance(
                fact_id=_optional_string(fact.get(FactField.ID.value)),
                predicate=_optional_string(fact.get(FactField.PREDICATE.value)),
                source_conversation_id=_optional_string(
                    fact.get(FactField.SOURCE_CONVERSATION_ID.value)
                ),
                source_message_indexes=_source_message_indexes(fact),
                last_confirmed_at=_optional_string(
                    fact.get(FactField.LAST_CONFIRMED_AT.value)
                ),
            )
            for fact in active_facts
        ],
        generated_at=_utc_now_iso(),
    )
    payload = summary.model_dump(mode="json")
    _store_generated_summary(payload)
    return payload


def _attach_generated_summaries(
    conversation: dict[str, Any], *, owner_id: str | None, project_id: str | None
) -> None:
    conversation_summary = _conversation_summary(
        conversation, owner_id=owner_id, project_id=project_id
    )
    _store_generated_summary(conversation_summary)
    metadata = _ensure_metadata(conversation)
    metadata["generated_summary"] = {
        "id": conversation_summary["id"],
        "type": conversation_summary["type"],
        "text": conversation_summary["text"],
        "basis": conversation_summary["basis"],
        "provenance_status": conversation_summary["provenance_status"],
        "generated_at": conversation_summary["generated_at"],
    }

    topic_summary_ids: list[str] = []
    for topic in _conversation_topics(conversation):
        topic_summary = _topic_summary(
            topic,
            conversation,
            conversation_summary=conversation_summary,
            owner_id=owner_id,
            project_id=project_id,
        )
        _store_generated_summary(topic_summary)
        topic_summary_ids.append(str(topic_summary["id"]))
    metadata["generated_topic_summary_ids"] = topic_summary_ids

    project_summary = _project_summary(
        conversation,
        conversation_summary=conversation_summary,
        owner_id=owner_id,
        project_id=project_id,
    )
    _store_generated_summary(project_summary)
    metadata["generated_project_summary_id"] = project_summary["id"]


def _conversation_summary(
    conversation: dict[str, Any], *, owner_id: str | None, project_id: str | None
) -> dict[str, Any]:
    conversation_id = str(conversation.get("id", ""))
    message_indexes = _message_indexes(conversation)
    text = _conversation_summary_text(conversation)
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type=SummaryType.CONVERSATION,
            target_id=conversation_id,
            project_id=project_id,
            filters={},
        ),
        type=SummaryType.CONVERSATION,
        target_id=conversation_id,
        project_id=project_id,
        owner_id=owner_id,
        text=text,
        basis=SummaryBasis.CONVERSATION_MESSAGES,
        provenance_status=SummaryProvenanceStatus.CONVERSATION_IDS,
        filters={},
        provenance=[
            GeneratedSummaryProvenance(
                conversation_id=conversation_id,
                source=_optional_string(conversation.get("source")),
                title=_optional_string(conversation.get("title")),
                source_conversation_id=conversation_id,
                source_message_indexes=message_indexes,
            )
        ],
        generated_at=_utc_now_iso(),
    )
    return summary.model_dump(mode="json")


def _topic_summary(
    topic: str,
    conversation: dict[str, Any],
    *,
    conversation_summary: dict[str, Any],
    owner_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    conversation_id = str(conversation.get("id", ""))
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type=SummaryType.TOPIC,
            target_id=topic,
            project_id=project_id,
            filters={"conversation_id": conversation_id},
        ),
        type=SummaryType.TOPIC,
        target_id=topic,
        project_id=project_id,
        owner_id=owner_id,
        text=f"{topic}: {conversation_summary['text']}",
        basis=SummaryBasis.TOPIC_CONVERSATIONS,
        provenance_status=SummaryProvenanceStatus.CONVERSATION_IDS,
        filters={"topic": topic, "conversation_id": conversation_id},
        provenance=[
            GeneratedSummaryProvenance(
                conversation_id=conversation_id,
                source=_optional_string(conversation.get("source")),
                title=_optional_string(conversation.get("title")),
                source_conversation_id=conversation_id,
                source_message_indexes=_message_indexes(conversation),
            )
        ],
        generated_at=_utc_now_iso(),
    )
    return summary.model_dump(mode="json")


def _project_summary(
    conversation: dict[str, Any],
    *,
    conversation_summary: dict[str, Any],
    owner_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    target_id = project_id or LOCAL_DEFAULT_PROJECT_ID
    conversation_id = str(conversation.get("id", ""))
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type=SummaryType.PROJECT,
            target_id=target_id,
            project_id=project_id,
            filters={"conversation_id": conversation_id},
        ),
        type=SummaryType.PROJECT,
        target_id=target_id,
        project_id=project_id,
        owner_id=owner_id,
        text=f"{target_id}: {conversation_summary['text']}",
        basis=SummaryBasis.PROJECT_CONVERSATIONS,
        provenance_status=SummaryProvenanceStatus.CONVERSATION_IDS,
        filters={"conversation_id": conversation_id},
        provenance=[
            GeneratedSummaryProvenance(
                conversation_id=conversation_id,
                source=_optional_string(conversation.get("source")),
                title=_optional_string(conversation.get("title")),
                source_conversation_id=conversation_id,
                source_message_indexes=_message_indexes(conversation),
            )
        ],
        generated_at=_utc_now_iso(),
    )
    return summary.model_dump(mode="json")


def _conversation_summary_text(conversation: dict[str, Any]) -> str:
    subject = _conversation_summary_subject(conversation)
    snippets: list[str] = []
    messages = conversation.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip() or "message"
            text = _compact_summary_text(str(message.get("text", "")))
            if text:
                snippets.append(f"{role}: {text}")
            if len(snippets) >= 3:
                break
    body = "; ".join(snippets) if snippets else "No message text was available."
    return _truncate_summary_text(f"{subject}: {body}")


def _conversation_summary_subject(conversation: dict[str, Any]) -> str:
    title = str(conversation.get("title") or "").strip()
    if title:
        return title
    topics = _conversation_topics(conversation)
    if topics:
        return "Conversation about " + ", ".join(topics[:4])
    source = str(conversation.get("source") or "conversation").strip()
    return f"{source} conversation"


def _compact_summary_text(text: str) -> str:
    value = " ".join(text.strip().split())
    if not value:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", value, maxsplit=1)[0]
    return _truncate_summary_text(sentence, limit=220)


def _truncate_summary_text(text: str, *, limit: int = 600) -> str:
    value = " ".join(text.strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _conversation_topics(conversation: dict[str, Any]) -> list[str]:
    metadata = conversation.get("metadata", {})
    topics = metadata.get("topics") if isinstance(metadata, dict) else None
    if not isinstance(topics, list):
        return []
    return _unique_strings([str(topic) for topic in topics if isinstance(topic, str)])


def _message_indexes(conversation: dict[str, Any]) -> list[int]:
    messages = conversation.get("messages", [])
    if not isinstance(messages, list):
        return []
    return list(range(len(messages)))


def _ensure_metadata(conversation: dict[str, Any]) -> dict[str, Any]:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation["metadata"] = metadata
    return metadata


def _with_generated_summary_metadata(conversation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(conversation, dict):
        return conversation
    enriched = dict(conversation)
    metadata = dict(enriched.get("metadata", {}))
    if not isinstance(metadata.get("generated_summary"), dict):
        summary = _get_conversation_summary(enriched)
        if isinstance(summary, dict):
            metadata["generated_summary"] = {
                "id": summary["id"],
                "type": summary["type"],
                "text": summary["text"],
                "basis": summary["basis"],
                "provenance_status": summary["provenance_status"],
                "generated_at": summary["generated_at"],
            }
    enriched["metadata"] = metadata
    return enriched


def _get_conversation_summary(conversation: dict[str, Any]) -> dict[str, Any] | None:
    metadata = conversation.get("metadata", {})
    summary_id = None
    if isinstance(metadata, dict):
        generated = metadata.get("generated_summary")
        if isinstance(generated, dict) and isinstance(generated.get("id"), str):
            summary_id = generated["id"]
    if summary_id is None:
        summary_id = _generated_summary_id(
            summary_type=SummaryType.CONVERSATION,
            target_id=str(conversation.get("id", "")),
            project_id=_project_id_from_conversation(conversation),
            filters={},
        )
    store = _runtime().metadata_store
    if hasattr(store, "get_generated_summary"):
        return store.get_generated_summary(summary_id)
    summaries = getattr(store, "_generated_summaries", {})
    if isinstance(summaries, dict):
        summary = summaries.get(summary_id)
        return summary if isinstance(summary, dict) else None
    return None


def _profile_summary_text(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "No active profile facts match the requested filters."
    lines: list[str] = []
    for fact in facts[:12]:
        predicate = str(fact.get(FactField.PREDICATE.value, "fact"))
        value = str(
            fact.get(FactField.OBJECT_NORMALIZED.value)
            or fact.get(FactField.OBJECT.value, "")
        )
        if value:
            lines.append(f"{predicate}: {value}")
    remaining = len(facts) - len(lines)
    if remaining > 0:
        lines.append(f"{remaining} more active fact(s).")
    return "; ".join(lines)


def _freshest_fact_timestamp(facts: list[dict[str, Any]]) -> str | None:
    timestamps = [
        str(
            fact.get(FactField.STORED_AT.value)
            or fact.get(FactField.LAST_CONFIRMED_AT.value)
            or fact.get(FactField.UPDATED_AT.value)
            or fact.get(FactField.CREATED_AT.value)
        )
        for fact in facts
        if (
            fact.get(FactField.STORED_AT.value)
            or fact.get(FactField.LAST_CONFIRMED_AT.value)
            or fact.get(FactField.UPDATED_AT.value)
            or fact.get(FactField.CREATED_AT.value)
        )
    ]
    return max(timestamps) if timestamps else None


def _count_values(facts: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        value = fact.get(key)
        if value is None:
            continue
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return counts


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _source_message_indexes(fact: dict[str, Any]) -> list[int]:
    indexes = fact.get(FactField.SOURCE_MESSAGE_INDEXES.value, [])
    if not isinstance(indexes, list):
        return []
    parsed: list[int] = []
    for index in indexes:
        try:
            parsed.append(int(index))
        except (TypeError, ValueError):
            continue
    return parsed


def _generated_summary_id(
    *,
    summary_type: str,
    target_id: str,
    project_id: str | None,
    filters: dict[str, Any],
) -> str:
    material = json_dumps(
        {
            "type": summary_type,
            "target_id": target_id,
            "project_id": project_id,
            "filters": {key: filters[key] for key in sorted(filters) if filters[key] is not None},
        }
    )
    return f"summary:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def _store_generated_summary(summary: dict[str, Any]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "upsert_generated_summary"):
        store.upsert_generated_summary(summary)
        return
    summaries = getattr(store, "_generated_summaries", {})
    if not isinstance(summaries, dict):
        summaries = {}
    summaries[str(summary["id"])] = dict(summary)
    setattr(store, "_generated_summaries", summaries)


def _public_fact(fact: dict[str, Any]) -> dict[str, Any]:
    public = dict(fact)
    computed_source_quality = _source_quality_for_fact(public)
    if (
        not public.get(FactField.SOURCE_QUALITY.value)
        or computed_source_quality == "corrected_by_user"
    ):
        public[FactField.SOURCE_QUALITY.value] = computed_source_quality
    if (
        not public.get(FactField.CONFIDENCE_REASON.value)
        or computed_source_quality == "corrected_by_user"
    ):
        public[FactField.CONFIDENCE_REASON.value] = _confidence_reason_for_fact(public)
    if not public.get(FactField.LAST_CONFIRMED_AT.value):
        public[FactField.LAST_CONFIRMED_AT.value] = public.get(
            FactField.UPDATED_AT.value
        ) or public.get(FactField.CREATED_AT.value)
    if not public.get(FactField.OBJECT_RAW.value):
        public[FactField.OBJECT_RAW.value] = public.get(FactField.OBJECT.value)
    if not public.get(FactField.OBJECT_NORMALIZED.value):
        public[FactField.OBJECT_NORMALIZED.value] = _normalized_fact_object(public)
    public.update(_fact_save_intent_fields(public))
    public.update(temporal_fact_source_metadata(public, _source_conversation_for_fact(public)))
    public.setdefault(FactField.SUPERSEDED_AT.value, None)
    return public


def _source_conversation_for_fact(fact: dict[str, Any]) -> dict[str, Any] | None:
    conversation_id = fact.get(FactField.SOURCE_CONVERSATION_ID.value)
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    conversation = _runtime().metadata_store.get(conversation_id)
    return conversation if isinstance(conversation, dict) else None


def _source_quality_for_fact(fact: dict[str, Any]) -> str:
    qualifiers = fact.get(FactField.QUALIFIERS.value)
    source_role = (
        qualifiers.get(_FactQualifierKey.SOURCE_ROLE.value)
        if isinstance(qualifiers, dict)
        else None
    )
    if (
        isinstance(qualifiers, dict)
        and qualifiers.get(_FactQualifierKey.CORRECTS.value)
        and source_role != _FactSourceRole.ASSISTANT.value
    ):
        return "corrected_by_user"
    if fact.get(FactField.PREDICATE.value) == "recurring_topic":
        return "inferred_from_conversation"
    if source_role == _FactSourceRole.ASSISTANT.value:
        return "assistant_statement"
    return "direct_user_statement"


def _confidence_reason_for_fact(fact: dict[str, Any]) -> str:
    source_quality = str(
        fact.get(FactField.SOURCE_QUALITY.value) or _source_quality_for_fact(fact)
    )
    if _fact_qualifier_value(fact, _FactQualifierKey.SAVE_INTENT.value) == "client_auto_save":
        return "Extracted from client auto-save memory, so confidence is reduced."
    if source_quality == "corrected_by_user":
        return "Extracted from a direct user correction."
    if source_quality == "direct_user_statement":
        return "Extracted from a direct user statement."
    if source_quality == "assistant_statement":
        return "Extracted from an assistant statement."
    if source_quality == "inferred_from_conversation":
        return "Inferred from recurring conversation topics."
    return "Extracted from stored conversation context."


def _confidence_reason_for_facts(facts: list[dict[str, Any]], basis: str) -> str:
    if basis == _AskAnswerBasis.CONFLICT.value:
        return "Multiple latest facts match the question at the same timestamp but disagree."
    if not facts:
        return "No matching facts were used."
    reasons = _unique_strings([str(fact.get("confidence_reason", "")) for fact in facts])
    return "; ".join(reasons) if reasons else "Matching normalized facts were used."


def _normalized_fact_object(fact: dict[str, Any]) -> str:
    normalized, _ = _normalize_fact_object(
        predicate=str(fact.get(FactField.PREDICATE.value, "")),
        object_value=str(fact.get(FactField.OBJECT.value, "")),
    )
    return normalized


def _normalize_fact_object(*, predicate: str, object_value: str) -> tuple[str, dict[str, Any]]:
    normalized = " ".join(object_value.strip().split())
    normalization: dict[str, Any] = {}

    normalized, spelling_corrections = _apply_common_spelling_corrections(normalized)
    if spelling_corrections:
        normalization["spelling_corrections"] = spelling_corrections

    normalized, date_qualifier = _normalize_fact_date(normalized)
    if date_qualifier:
        normalization["date"] = date_qualifier

    if _is_name_like_fact(predicate):
        cased = _title_case_name(normalized)
        if cased != normalized:
            normalization["casing"] = {
                "strategy": "title_case_name",
                "raw": normalized,
                "normalized": cased,
            }
            normalized = cased

    if normalization:
        normalization["object_raw"] = object_value
        normalization["object_normalized"] = normalized
    return normalized, normalization


def _apply_common_spelling_corrections(value: str) -> tuple[str, list[dict[str, str]]]:
    normalized = value
    corrections: list[dict[str, str]] = []
    for misspelled, corrected in _COMMON_FACT_SPELLING_CORRECTIONS.items():
        pattern = re.compile(rf"\b{misspelled}\b", re.IGNORECASE)
        if not pattern.search(normalized):
            continue
        normalized = pattern.sub(corrected, normalized)
        corrections.append({"raw": misspelled, "normalized": corrected})
    return normalized, corrections


def _normalize_fact_date(value: str) -> tuple[str, dict[str, str] | None]:
    month_pattern = re.compile(
        r"^(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?$",
        re.IGNORECASE,
    )
    month_match = month_pattern.match(value)
    if month_match:
        month = _MONTH_NAMES[month_match.group("month").lower()]
        day = int(month_match.group("day"))
        year = month_match.group("year")
        normalized = f"{month} {day}, {year}" if year else f"{month} {day}"
        if normalized != value:
            return normalized, {"raw": value, "normalized": normalized, "precision": "day"}
        return value, {"raw": value, "normalized": normalized, "precision": "day"}

    iso_pattern = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")
    iso_match = iso_pattern.match(value)
    if iso_match:
        month_number = int(iso_match.group("month"))
        if 1 <= month_number <= 12:
            month = list(_MONTH_NAMES.values())[month_number - 1]
            normalized = f"{month} {int(iso_match.group('day'))}, {iso_match.group('year')}"
            return normalized, {"raw": value, "normalized": normalized, "precision": "day"}
    return value, None


def _is_name_like_fact(predicate: str) -> bool:
    return predicate in _NAME_LIKE_PREDICATES


def _title_case_name(value: str) -> str:
    return " ".join(part[:1].upper() + part[1:].lower() for part in value.split())


def _fact_evidence(fact: dict[str, Any], *, used_in_answer: bool) -> dict[str, Any]:
    evidence = {
        "type": _FactEvidenceType.FACT.value,
        "fact_id": fact.get(FactField.ID.value),
        FactField.SUBJECT.value: fact.get(FactField.SUBJECT.value),
        FactField.PREDICATE.value: fact.get(FactField.PREDICATE.value),
        FactField.OBJECT_RAW.value: fact.get(
            FactField.OBJECT_RAW.value, fact.get(FactField.OBJECT.value)
        ),
        FactField.OBJECT_NORMALIZED.value: fact.get(
            FactField.OBJECT_NORMALIZED.value, fact.get(FactField.OBJECT.value)
        ),
        FactField.CONFIDENCE.value: fact.get(FactField.CONFIDENCE.value),
        FactField.CONFIDENCE_REASON.value: fact.get(FactField.CONFIDENCE_REASON.value),
        FactField.SOURCE_QUALITY.value: fact.get(FactField.SOURCE_QUALITY.value),
        FactField.SOURCE_CONVERSATION_ID.value: fact.get(
            FactField.SOURCE_CONVERSATION_ID.value
        ),
        FactField.SOURCE_MESSAGE_INDEXES.value: fact.get(
            FactField.SOURCE_MESSAGE_INDEXES.value, []
        ),
        FactField.CREATED_AT.value: fact.get(FactField.CREATED_AT.value),
        FactField.UPDATED_AT.value: fact.get(FactField.UPDATED_AT.value),
        FactField.LAST_CONFIRMED_AT.value: fact.get(FactField.LAST_CONFIRMED_AT.value),
        FactField.STORED_AT.value: fact.get(FactField.STORED_AT.value),
        FactField.AUTHOR.value: fact.get(FactField.AUTHOR.value),
        FactField.SUPERSEDED_BY.value: fact.get(FactField.SUPERSEDED_BY.value),
        FactField.SUPERSEDED_AT.value: fact.get(FactField.SUPERSEDED_AT.value),
        FactField.DELETED_AT.value: fact.get(FactField.DELETED_AT.value),
        "used_in_answer": used_in_answer,
    }
    evidence.update(_fact_save_intent_fields(fact))
    return evidence


def _fact_citation(fact: dict[str, Any]) -> dict[str, Any]:
    citation = {
        "id": fact.get(FactField.SOURCE_CONVERSATION_ID.value),
        "fact_id": fact.get(FactField.ID.value),
        FactField.PREDICATE.value: fact.get(FactField.PREDICATE.value),
        "text": fact.get(
            FactField.OBJECT_NORMALIZED.value, fact.get(FactField.OBJECT.value)
        ),
        FactField.SOURCE_QUALITY.value: fact.get(FactField.SOURCE_QUALITY.value),
        FactField.CONFIDENCE_REASON.value: fact.get(FactField.CONFIDENCE_REASON.value),
        FactField.LAST_CONFIRMED_AT.value: fact.get(FactField.LAST_CONFIRMED_AT.value),
        FactField.STORED_AT.value: fact.get(FactField.STORED_AT.value),
        FactField.AUTHOR.value: fact.get(FactField.AUTHOR.value),
    }
    citation.update(_fact_save_intent_fields(fact))
    return citation


def _provenance_from_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for fact in facts:
        conversation_id = str(fact.get(FactField.SOURCE_CONVERSATION_ID.value, ""))
        item = grouped.setdefault(
            conversation_id,
            {
                "conversation_id": conversation_id,
                "source": fact.get(FactField.AUTHOR.value),
                "title": None,
                "stored_at": fact.get(FactField.STORED_AT.value),
                "matching_chunks": 0,
                "used_in_answer": True,
                "save_intents": [],
                "save_intent_sources": [],
            },
        )
        conversation = _source_conversation_for_fact(fact)
        if conversation is not None:
            item["source"] = conversation.get(MemoryField.SOURCE.value)
            item["title"] = conversation.get("title")
            item["stored_at"] = conversation.get(MemoryField.TIMESTAMP.value)
        item["matching_chunks"] += 1
        save_intent = _fact_qualifier_value(fact, _FactQualifierKey.SAVE_INTENT.value)
        if save_intent and save_intent not in item["save_intents"]:
            item["save_intents"].append(save_intent)
        save_intent_source = _fact_qualifier_value(
            fact, _FactQualifierKey.SAVE_INTENT_SOURCE.value
        )
        if save_intent_source and save_intent_source not in item["save_intent_sources"]:
            item["save_intent_sources"].append(save_intent_source)
    return list(grouped.values())


def _apply_in_memory_fact_supersession(active: list[dict[str, Any]], new_fact: dict[str, Any]) -> None:
    qualifiers = new_fact.get(FactField.QUALIFIERS.value, {})
    corrects = (
        str(qualifiers.get(_FactQualifierKey.CORRECTS.value, ""))
        if isinstance(qualifiers, dict)
        else ""
    )
    for fact in active:
        if (
            fact.get(FactField.SUBJECT.value) != new_fact.get(FactField.SUBJECT.value)
            or fact.get(FactField.PREDICATE.value)
            != new_fact.get(FactField.PREDICATE.value)
        ):
            continue
        if corrects and corrects.lower() in str(fact.get(FactField.OBJECT.value, "")).lower():
            now = _utc_now_iso()
            fact[FactField.SUPERSEDED_BY.value] = new_fact[FactField.ID.value]
            fact[FactField.SUPERSEDED_AT.value] = now
            fact[FactField.UPDATED_AT.value] = now


def _dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for fact in facts:
        key = (str(fact["subject"]), str(fact["predicate"]), str(fact["object"]))
        if key not in seen:
            seen.add(key)
            output.append(fact)
    return output


def _owned_item_predicate(object_value: str) -> str:
    lowered = object_value.lower()
    if "guitar" in lowered or "gibson" in lowered:
        return _FactPredicate.OWNS_GUITAR.value
    return _FactPredicate.OWNS_ITEM.value


def _owned_item_qualifiers(object_value: str) -> dict[str, Any]:
    qualifiers: dict[str, Any] = {}
    lowered = object_value.lower()
    if "guitar" in lowered or "gibson" in lowered:
        qualifiers["instrument"] = "guitar"
    if "p90" in lowered:
        qualifiers["pickup"] = "P90"
    for color in ("cherry", "tv yellow", "black", "white", "blue", "red"):
        if color in lowered:
            qualifiers["color"] = color
            break
    return qualifiers


def _clean_fact_object(value: str) -> str:
    return value.strip(" .?!\n\t\"'")


def _normalize_predicate_part(value: str) -> str:
    return "_".join(_query_tokens(value)) or "item"


def _project_subject(conversation: dict[str, Any]) -> str:
    title = str(conversation.get("title") or "").strip()
    return title or "project"


def runtime_health() -> dict[str, Any]:
    runtime = _runtime()
    metadata_health = (
        runtime.metadata_store.health()
        if hasattr(runtime.metadata_store, "health")
        else {}
    )
    vector_health = (
        runtime.vector_store.health() if hasattr(runtime.vector_store, "health") else {}
    )
    return {
        **runtime.health_state,
        "metadata_health": metadata_health,
        "vector_health": vector_health,
    }


def _embedding_readiness(
    *,
    cfg: HubConfig,
    embedding_provider: EmbeddingProvider,
    live_probe: bool,
) -> dict[str, Any]:
    provider = str(cfg.providers.embeddings)
    health: dict[str, Any] = {
        "provider": provider,
        "model": _active_embedding_model(cfg=cfg, embedding_provider=embedding_provider),
        "dimension": int(embedding_provider.dimension),
        "status": "ok",
        "live_probe": bool(live_probe),
    }
    if not live_probe:
        health["mode"] = "configuration"
        return health
    try:
        vectors = embedding_provider.embed_texts(["ai-memory-hub readiness probe"])
        first_vector = vectors[0] if vectors else []
        if len(vectors) != 1 or len(first_vector) != embedding_provider.dimension:
            health["status"] = "degraded"
            health["error_type"] = "VectorDimensionError"
        else:
            health["mode"] = "live"
    except Exception as exc:
        health["status"] = "degraded"
        health["error_type"] = type(exc).__name__
        health["error"] = redact_secrets(str(exc))
    return health


def _hash_to_vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest)
    while len(values) < dimensions:
        digest = hashlib.sha256(digest).digest()
        values.extend(list(digest))
    return [(values[index] / 255.0) for index in range(dimensions)]


def _validate_metadata_schema(
    *, metadata_store: Any, supported_versions: Sequence[int]
) -> None:
    version = int(getattr(metadata_store, "schema_version", 0))
    if version not in supported_versions:
        supported = ",".join(str(item) for item in supported_versions)
        raise SchemaVersionError(
            f"Incompatible metadata schema version: {version}. Supported versions: [{supported}]"
        )


def _log_startup_policy(*, cfg: HubConfig, requested_vector_provider: str) -> None:
    logger.info(
        "Runtime startup policy",
        extra={
            "event": "runtime_startup_policy",
            "vector_provider": requested_vector_provider,
            "storage_profile": cfg.storage.profile,
            "vector_fallback_allowed": cfg.storage.vector.allow_fallback,
            "dry_run": cfg.storage.dry_run,
        },
    )
    if (
        cfg.storage.vector.allow_fallback
        and requested_vector_provider != VectorProviderName.MEMORY.value
        and requested_vector_provider not in _FALLBACK_POLICY_WARNED
    ):
        logger.warning(
            "storage.vector.allow_fallback=true for persistent vector provider %s; "
            "startup may fall back to in-memory vectors, making vector data non-durable",
            requested_vector_provider,
            extra={
                "event": "vector_fallback_policy_warning",
                "vector_provider": requested_vector_provider,
                "vector_fallback_allowed": True,
                "non_durable_fallback_possible": True,
            },
        )
        _FALLBACK_POLICY_WARNED.add(requested_vector_provider)
    production_warning_key = f"{cfg.storage.profile}:{requested_vector_provider}"
    if (
        cfg.storage.profile == "production"
        and cfg.storage.vector.allow_fallback
        and requested_vector_provider != VectorProviderName.MEMORY.value
        and production_warning_key not in _PRODUCTION_FALLBACK_POLICY_WARNED
    ):
        logger.warning(
            "storage.profile=production with storage.vector.allow_fallback=true for "
            "persistent vector provider %s; set allow_fallback=false to fail fast",
            requested_vector_provider,
            extra={
                "event": "production_vector_fallback_policy_warning",
                "storage_profile": cfg.storage.profile,
                "vector_provider": requested_vector_provider,
                "vector_fallback_allowed": True,
                "recommended_vector_fallback_allowed": False,
            },
        )
        _PRODUCTION_FALLBACK_POLICY_WARNED.add(production_warning_key)


def _embedding_index_metadata(
    *,
    cfg: HubConfig,
    embedding_provider: EmbeddingProvider,
    vector_provider: str,
    vector_store: Any,
    vector_health: dict[str, Any],
) -> dict[str, Any]:
    embedding_provider_name = str(cfg.providers.embeddings)
    embedding_model = _active_embedding_model(cfg=cfg, embedding_provider=embedding_provider)
    embedding_options = _embedding_options(cfg=cfg, embedding_provider_name=embedding_provider_name)
    vector_index = {
        "provider": vector_provider,
        "id": _vector_index_id(
            provider=vector_provider,
            vector_store=vector_store,
            vector_health=vector_health,
        ),
        "distance": _optional_string(vector_health.get("distance") or cfg.storage.vector.distance),
    }
    return {
        "embedding": {
            "provider": embedding_provider_name,
            "model": embedding_model,
            "dimension": int(embedding_provider.dimension),
            "options": embedding_options,
        },
        "vector_index": vector_index,
    }


def _validate_embedding_index_metadata(
    *,
    metadata_store: Any,
    vector_provider: str,
    vector_store: Any,
    vector_health: dict[str, Any],
    embedding_index_metadata: dict[str, Any],
) -> bool:
    if vector_provider == VectorProviderName.MEMORY.value:
        return False
    if not hasattr(metadata_store, "get_runtime_metadata") or not hasattr(
        metadata_store, "set_runtime_metadata"
    ):
        return False
    key = _vector_compatibility_metadata_key(embedding_index_metadata["vector_index"])
    existing = metadata_store.get_runtime_metadata(key)
    if existing == embedding_index_metadata:
        return False
    rows = _vector_row_count(vector_store=vector_store, vector_health=vector_health)
    if existing is not None and rows != 0:
        raise RuntimeError(
            "Embedding index metadata mismatch: persistent vector index was built "
            "with a different embedding provider, model, dimension, or options. "
            "Reindex the vector data or use a separate vector namespace/index."
        )
    metadata_store.set_runtime_metadata(key, embedding_index_metadata)
    return existing is not None


def _active_embedding_model(*, cfg: HubConfig, embedding_provider: EmbeddingProvider) -> str:
    model = getattr(embedding_provider, "embedding_model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    if cfg.providers.embeddings == "local":
        return "local-deterministic-hash"
    return str(cfg.providers.embedding_model)


def _embedding_options(*, cfg: HubConfig, embedding_provider_name: str) -> dict[str, Any]:
    if embedding_provider_name == "http":
        return {"base_url": redact_secrets(cfg.embedding_endpoint.base_url)}
    return {}

def _vector_index_id(*, provider: str, vector_store: Any, vector_health: dict[str, Any]) -> str:
    candidates = {
        "provider": provider,
        "db_path": _optional_string(getattr(vector_store, "db_path", None)),
        "table_name": _optional_string(getattr(vector_store, "table_name", None)),
        "collection": _optional_string(
            vector_health.get("collection") or getattr(vector_store, "collection_name", None)
        ),
        "index": _optional_string(vector_health.get("index") or getattr(vector_store, "index_name", None)),
        "namespace": _optional_string(
            vector_health.get("namespace") or getattr(vector_store, "namespace", None)
        ),
        "schema": _optional_string(vector_health.get("schema") or getattr(vector_store, "schema", None)),
    }
    material = {key: value for key, value in candidates.items() if value}
    return "sha256:" + hashlib.sha256(json_dumps(material).encode("utf-8")).hexdigest()


def _vector_compatibility_metadata_key(vector_index: dict[str, Any]) -> str:
    return _VECTOR_COMPATIBILITY_METADATA_PREFIX + str(vector_index["id"])


def _vector_row_count(*, vector_store: Any, vector_health: dict[str, Any]) -> int | None:
    rows = vector_health.get("rows")
    if rows is None:
        stats = vector_health.get("stats")
        if isinstance(stats, dict):
            rows = stats.get("rows")
    if rows is None and hasattr(vector_store, "get_stats"):
        stats = vector_store.get_stats()
        if isinstance(stats, dict):
            rows = stats.get("rows")
    if rows is None:
        return None
    try:
        return int(rows)
    except (TypeError, ValueError):
        return None


def _validate_vector_dimension(*, embedding_dimension: int, vector_store: Any) -> None:
    expected = int(getattr(vector_store, "expected_dimensionality", 0))
    if expected != embedding_dimension:
        raise VectorDimensionError(
            f"Vector store dimensionality mismatch at startup: expected {embedding_dimension}, got {expected}"
        )
