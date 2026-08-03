from __future__ import annotations

import gc
import json
import shutil
from pathlib import Path
from typing import Any

from memory import cli
from memory.ingestion import mvp_ingestion


def _run_json(args: list[str], capsys: Any) -> tuple[int, dict[str, Any]]:
    exit_code = cli.main([*args, "--json"])
    output = capsys.readouterr().out
    return exit_code, json.loads(output)


def _release_runtime_handles() -> None:
    mvp_ingestion._RUNTIME = None
    gc.collect()


def test_cli_reindex_rebuilds_lancedb_vectors_from_sqlite_metadata(
    capsys: Any, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "providers:",
                "  embeddings: local",
                "  embedding_dimension: 32",
                "  metadata_db: sqlite",
                "  vector_db: lancedb",
                "paths:",
                f"  data_dir: {data_dir.as_posix()}",
                "storage:",
                "  vector:",
                "    allow_fallback: false",
            ]
        ),
        encoding="utf-8",
    )
    payload_path = tmp_path / "conversation.json"
    payload_path.write_text(
        json.dumps(
            {
                "source": "reindex-integration",
                "timestamp": "2026-01-01T00:00:00Z",
                "title": "Reindex integration smoke",
                "messages": [
                    {
                        "role": "user",
                        "text": "Remember that the saffron reindex marker proves vectors rebuilt.",
                    },
                    {"role": "assistant", "text": "Stored the saffron reindex marker."},
                ],
            }
        ),
        encoding="utf-8",
    )
    config_args = ["--config", str(config_path)]

    ingest_code, ingest = _run_json(["ingest", str(payload_path), *config_args], capsys)
    search_code, search = _run_json(
        ["search", "saffron reindex marker", "--top-k", "5", *config_args], capsys
    )

    assert ingest_code == 0
    assert ingest["status"] == "ok"
    assert search_code == 0
    assert search["results"]
    assert data_dir.joinpath("metadata.sqlite3").exists()

    _release_runtime_handles()
    shutil.rmtree(data_dir / "lancedb")

    storage_empty_code, storage_empty = _run_json(["storage-check", *config_args], capsys)
    assert storage_empty_code == 0
    assert storage_empty["vector"]["stats"]["rows"] == 0

    reindex_code, reindex = _run_json(["reindex", *config_args], capsys)
    _release_runtime_handles()
    storage_code, storage = _run_json(["storage-check", *config_args], capsys)
    search_after_code, search_after = _run_json(
        ["search", "saffron reindex marker", "--top-k", "5", *config_args], capsys
    )
    ask_code, ask = _run_json(
        ["ask", "What do you remember about the saffron marker?", "--top-k", "5", *config_args],
        capsys,
    )

    assert reindex_code == 0
    assert reindex["total"] == 1
    assert reindex["reindexed"] == 1
    assert reindex["chunks"] == 2
    assert reindex["failed"] == 0
    assert storage_code == 0
    assert storage["vector"]["stats"]["rows"] == 2
    assert search_after_code == 0
    assert search_after["results"]
    assert ask_code == 0
    assert ask["citations"]
