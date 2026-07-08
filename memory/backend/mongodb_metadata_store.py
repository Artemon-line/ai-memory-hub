from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import NotSupportedError
from memory.backend.metadata_store import (
    LOCAL_DEFAULT_PROJECT_ID,
    _default_project_id,
    _validate_owner_id,
    _validate_project_id,
)
from memory.provider_models import MetadataProviderName, MongoCollectionName


class MongoDBMetadataStore:
    schema_version: int = 1
    _MAX_GET_MANY_IDS = 500

    def __init__(
        self,
        *,
        uri: str,
        database: str = "ai_memory_hub",
        conversations_collection: str = MongoCollectionName.CONVERSATIONS.value,
        facts_collection: str = MongoCollectionName.FACTS.value,
        generated_summaries_collection: str = MongoCollectionName.GENERATED_SUMMARIES.value,
        schema_collection: str = "schema_versions",
        client: Any | None = None,
    ):
        if not uri and client is None:
            raise ValueError("MongoDB URI is required for providers.metadata_db=mongodb")
        self.uri = uri
        self.database = database
        self.conversations_collection = conversations_collection
        self.facts_collection = facts_collection
        self.generated_summaries_collection = generated_summaries_collection
        self.schema_collection = schema_collection
        self._client = client or self._create_client()
        db = self._client[self.database]
        self._conversations = db[self.conversations_collection]
        self._facts = db[self.facts_collection]
        self._summaries = db[self.generated_summaries_collection]
        self._schema_versions = db[self.schema_collection]
        self._runtime_metadata = db["runtime_metadata"]
        self._ensure_indexes()
        self._ensure_schema_version()

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = self._validate_memory_id(conversation_json["id"])
        conversation = self._prepare_conversation(conversation_json)
        self._conversations.replace_one({"id": memory_id}, conversation, upsert=True)
        return memory_id

    def insert_new(self, conversation_json: dict[str, Any]) -> tuple[str, bool]:
        memory_id = self._validate_memory_id(conversation_json["id"])
        conversation = self._prepare_conversation(conversation_json)
        existing = self.get(memory_id)
        if existing is not None:
            return self._resolve_insert_new_conflict(memory_id, conversation_json)
        existing_hash = self.get_by_conversation_hash(
            self._conversation_hash(conversation_json),
            project_id=self._project_id(conversation_json),
        )
        if existing_hash is not None:
            return str(existing_hash["id"]), False
        self._conversations.insert_one(conversation)
        return memory_id, True

    def append_messages(
        self, conversation_json: dict[str, Any], new_messages: list[dict[str, Any]]
    ) -> str:
        _ = new_messages
        return self.insert(conversation_json)

    def get(self, memory_id: str) -> dict[str, Any] | None:
        row = self._conversations.find_one({"id": self._validate_memory_id(memory_id)})
        return _payload(row)

    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        if len(ids) > self._MAX_GET_MANY_IDS:
            raise ValueError(f"Too many ids requested: {len(ids)} > {self._MAX_GET_MANY_IDS}")
        validated = [self._validate_memory_id(memory_id) for memory_id in ids]
        rows = self._conversations.find({"id": {"$in": validated}})
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = _payload(row)
            if payload is not None:
                output[str(row["id"])] = payload
        return output

    def search_text(
        self, query: str, limit: int = 20, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        normalized = str(query).strip().lower()
        if not normalized:
            return []
        filter_query: dict[str, Any] = {}
        if project_id is not None:
            filter_query["project_id"] = _validate_project_id(project_id)
        rows = self._conversations.find(filter_query).sort("created_at", -1)
        matches: list[dict[str, Any]] = []
        for row in rows:
            payload = _payload(row)
            if payload is not None and normalized in json.dumps(payload, sort_keys=True).lower():
                matches.append(payload)
            if len(matches) >= limit:
                break
        return matches

    def get_by_conversation_hash(
        self, conversation_hash: str | None, project_id: str | None = None
    ) -> dict[str, Any] | None:
        if not conversation_hash:
            return None
        query: dict[str, Any] = {"conversation_hash": str(conversation_hash)}
        if project_id is not None:
            query["project_id"] = _validate_project_id(project_id)
        row = self._conversations.find_one(query)
        return _payload(row)

    def get_by_upstream_thread(
        self, source: str, upstream_thread_id: str, project_id: str | None = None
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {
            "source": str(source),
            "upstream_thread_id": str(upstream_thread_id),
        }
        if project_id is not None:
            query["project_id"] = _validate_project_id(project_id)
        row = self._conversations.find_one(query, sort=[("timestamp", -1)])
        return _payload(row)

    def mark_chunks_indexed(self, memory_id: str, chunk_ids: list[str]) -> None:
        self._conversations.update_one(
            {"id": self._validate_memory_id(memory_id)},
            {"$addToSet": {"indexed_chunk_ids": {"$each": [str(item) for item in chunk_ids]}}},
        )

    def is_fully_indexed(self, memory_id: str) -> bool:
        row = self._conversations.find_one({"id": self._validate_memory_id(memory_id)})
        if row is None:
            return False
        payload = _payload(row) or {}
        messages = payload.get("messages", [])
        expected = len(messages) if isinstance(messages, list) else 0
        indexed = row.get("indexed_chunk_ids", [])
        return expected > 0 and len(indexed) >= expected

    def insert_facts(self, facts: list[dict[str, Any]]) -> None:
        for fact in facts:
            doc = dict(fact)
            doc.setdefault("deleted_at", None)
            doc["updated_at"] = _utc_now()
            self._refresh_matching_fact_confirmation(doc)
            self._supersede_corrected_facts(doc)
            self._facts.replace_one({"id": str(doc["id"])}, doc, upsert=True)

    def search_facts(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        include_superseded: bool = False,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"deleted_at": None}
        if subject is not None:
            query["subject"] = subject
        if predicate is not None:
            query["predicate"] = predicate
        if not include_superseded:
            query["superseded_by"] = None
            query["superseded_at"] = None
        if project_id is not None:
            query["project_id"] = _validate_project_id(project_id)
        return [_clean_mongo_doc(row) for row in self._facts.find(query).sort("created_at", -1)]

    def get_profile(self, subject: str, project_id: str | None = None) -> dict[str, Any]:
        return {"subject": subject, "facts": self.search_facts(subject=subject, project_id=project_id)}

    def upsert_generated_summary(self, summary: Any) -> str:
        payload = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
        summary_id = str(payload["id"])
        self._summaries.replace_one({"id": summary_id}, {"id": summary_id, "payload": payload}, upsert=True)
        return summary_id

    def get_generated_summary(self, summary_id: str) -> dict[str, Any] | None:
        row = self._summaries.find_one({"id": str(summary_id)})
        return _payload(row)

    def supersede_fact(
        self, fact_id: str, superseded_by: str, project_id: str | None = None
    ) -> bool:
        query: dict[str, Any] = {
            "id": str(superseded_by),
            "deleted_at": None,
        }
        if project_id is not None:
            query["project_id"] = _validate_project_id(project_id)
        result = self._facts.update_one(
            query,
            {
                "$set": {
                    "superseded_by": str(fact_id),
                    "superseded_at": _utc_now(),
                    "updated_at": _utc_now(),
                }
            },
        )
        return bool(result.modified_count)

    def ensure_default_project(self, owner_id: str) -> str:
        return _default_project_id(_validate_owner_id(owner_id))

    def project_has_role(self, *, project_id: str, user_id: str, role: str) -> bool:
        _ = role
        return _validate_project_id(project_id) == _default_project_id(_validate_owner_id(user_id))

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tags=True,
            supports_metadata_indexing=True,
            supports_graph_records=False,
            supports_decay_fields=False,
            supports_shared_scopes=True,
            supports_plugin_metadata=False,
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": MetadataProviderName.MONGODB.value,
            "schema_version": self.schema_version,
            "database": self.database,
            "collection": self.conversations_collection,
            "schema_collection": self.schema_collection,
        }

    def get_runtime_metadata(self, key: str) -> dict[str, Any] | None:
        row = self._runtime_metadata.find_one({"key": str(key)})
        if not isinstance(row, dict):
            return None
        value = row.get("value")
        return value if isinstance(value, dict) else None

    def set_runtime_metadata(self, key: str, value: dict[str, Any]) -> None:
        self._runtime_metadata.replace_one(
            {"key": str(key)},
            {"key": str(key), "value": dict(value), "updated_at": _utc_now()},
            upsert=True,
        )

    def begin_transaction(self) -> None:
        raise NotSupportedError("Metadata transactions are not exposed by this adapter API")

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = memory_id, ttl_seconds
        raise NotSupportedError("TTL is not supported by this metadata adapter")

    def _create_client(self) -> Any:
        try:
            from pymongo import MongoClient  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pymongo package is required for providers.metadata_db=mongodb"
            ) from exc
        return MongoClient(self.uri)

    def _ensure_indexes(self) -> None:
        self._conversations.create_index("id", unique=True)
        self._conversations.create_index("conversation_hash")
        self._conversations.create_index(
            [("project_id", 1), ("conversation_hash", 1)],
            unique=True,
            partialFilterExpression={"conversation_hash": {"$type": "string"}},
        )
        self._conversations.create_index(
            [("project_id", 1), ("source", 1), ("upstream_thread_id", 1)],
            partialFilterExpression={"upstream_thread_id": {"$type": "string"}},
        )
        self._facts.create_index([("subject", 1), ("predicate", 1), ("project_id", 1)])
        self._summaries.create_index("id", unique=True)
        self._schema_versions.create_index("id", unique=True)
        self._runtime_metadata.create_index("key", unique=True)

    def _ensure_schema_version(self) -> None:
        row_count = self._schema_versions.count_documents({})
        if row_count == 0:
            self._schema_versions.insert_one({"id": 1, "version": self.schema_version})
            return
        if row_count != 1:
            raise RuntimeError("MongoDB schema version invariant violation")
        row = self._schema_versions.find_one({"id": 1})
        if row is None:
            raise RuntimeError("MongoDB schema version row missing for id=1")
        version = int(row.get("version", 0))
        if version != self.schema_version:
            raise RuntimeError(
                f"Incompatible MongoDB schema version: {version}. "
                f"Supported version: {self.schema_version}"
            )

    def _prepare_conversation(self, conversation_json: dict[str, Any]) -> dict[str, Any]:
        payload = dict(conversation_json)
        memory_id = self._validate_memory_id(payload["id"])
        project_id = self._ensure_payload_project(payload)
        return {
            "id": memory_id,
            "source": str(payload.get("source", "")),
            "timestamp": str(payload.get("timestamp", "")),
            "title": payload.get("title"),
            "conversation_hash": self._conversation_hash(payload),
            "project_id": project_id,
            "upstream_thread_id": self._upstream_thread_id(payload),
            "payload": payload,
            "updated_at": _utc_now(),
            "created_at": _utc_now(),
        }

    def _ensure_payload_project(self, conversation_json: dict[str, Any]) -> str:
        metadata = conversation_json.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            conversation_json["metadata"] = metadata
        owner_id = metadata.get("owner_id")
        project_id = metadata.get("project_id")
        if project_id:
            project = _validate_project_id(str(project_id))
        elif owner_id:
            project = _default_project_id(_validate_owner_id(str(owner_id)))
        else:
            project = LOCAL_DEFAULT_PROJECT_ID
        metadata["project_id"] = project
        return project

    def _resolve_insert_new_conflict(
        self, memory_id: str, conversation_json: dict[str, Any]
    ) -> tuple[str, bool]:
        existing = self.get_by_conversation_hash(
            self._conversation_hash(conversation_json),
            project_id=self._project_id(conversation_json),
        )
        if existing is None:
            existing = self.get(memory_id)
        if existing is None:
            raise RuntimeError("conversation insert conflict could not be resolved")
        if self._conversation_hash(existing) != self._conversation_hash(conversation_json):
            raise ValueError("unauthorized_update: conversation id already exists")
        if self._project_id(existing) != self._project_id(conversation_json):
            raise ValueError("unauthorized_update: conversation id already exists")
        return str(existing["id"]), False

    def _refresh_matching_fact_confirmation(self, fact: dict[str, Any]) -> None:
        query = {
            "subject": fact.get("subject"),
            "predicate": fact.get("predicate"),
            "object_normalized": fact.get("object_normalized"),
            "project_id": fact.get("project_id"),
            "deleted_at": None,
            "superseded_by": None,
        }
        self._facts.update_many(
            query,
            {"$set": {"last_confirmed_at": fact.get("last_confirmed_at"), "updated_at": _utc_now()}},
        )

    def _supersede_corrected_facts(self, fact: dict[str, Any]) -> None:
        corrected = fact.get("corrected_fact_ids")
        if not isinstance(corrected, list):
            return
        self._facts.update_many(
            {"id": {"$in": [str(item) for item in corrected]}, "deleted_at": None},
            {
                "$set": {
                    "superseded_by": str(fact["id"]),
                    "superseded_at": _utc_now(),
                    "updated_at": _utc_now(),
                }
            },
        )

    def _validate_memory_id(self, memory_id: Any) -> str:
        value = str(memory_id)
        uuid.UUID(value)
        return value

    def _conversation_hash(self, conversation_json: dict[str, Any]) -> str | None:
        metadata = conversation_json.get("metadata", {})
        if isinstance(metadata, dict):
            value = metadata.get("conversation_hash")
            return str(value) if value else None
        return None

    def _upstream_thread_id(self, conversation_json: dict[str, Any]) -> str | None:
        metadata = conversation_json.get("metadata", {})
        if isinstance(metadata, dict):
            value = metadata.get("upstream_thread_id")
            return str(value) if value else None
        return None

    def _project_id(self, conversation_json: dict[str, Any]) -> str | None:
        metadata = conversation_json.get("metadata", {})
        if isinstance(metadata, dict):
            value = metadata.get("project_id")
            return str(value) if value else None
        return None


def _payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = row.get("payload")
    return dict(payload) if isinstance(payload, dict) else None


def _clean_mongo_doc(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(row)
    cleaned.pop("_id", None)
    return cleaned


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
