from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.importers import CodexRolloutJsonlImporter

_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "importers"
    / "codex_rollout_rich_anonymized.jsonl"
)


def test_salted_codex_rollout_round_trips_through_hub(tmp_path: Path) -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    response_types = Counter(
        record["payload"].get("type")
        for record in records
        if record.get("type") == "response_item"
    )
    assert response_types == {
        "message": 17,
        "custom_tool_call": 14,
        "custom_tool_call_output": 14,
        "reasoning": 14,
    }

    payload = CodexRolloutJsonlImporter().import_text(text)[0]

    assert payload["metadata"]["cli_version"] == "0.152.0"
    assert len(payload["messages"]) == 15
    assert Counter(message["role"] for message in payload["messages"]) == {
        "user": 5,
        "assistant": 10,
    }
    serialized = json.dumps(payload)
    assert "PRIVATE_REASONING_SALTED" not in serialized
    assert "SYNTHETIC_TOOL_INPUT" not in serialized
    assert "RAW_TOOL_OUTPUT_SALTED" not in serialized
    assert "Synthetic runtime context" not in serialized
    assert "Synthetic developer instructions" not in serialized

    with TestClient(
        create_app(
            config={
                "interfaces": {"api": True, "mcp": False},
                "paths": {"data_dir": str(tmp_path / "hub")},
                "providers": {
                    "embeddings": "local",
                    "metadata_db": "sqlite",
                    "vector_db": "in_memory",
                },
            }
        )
    ) as client:
        inserted = client.post("/memory/insert", json=payload)
        assert inserted.status_code == 200, inserted.text
        memory_id = inserted.json()["id"]

        retrieved = client.post("/memory/retrieve", json={"id": memory_id})
        assert retrieved.status_code == 200, retrieved.text
        memory = retrieved.json()["memory"]
        assert len(memory["messages"]) == 15
        assert memory["metadata"]["cli_version"] == "0.152.0"
