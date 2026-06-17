from __future__ import annotations

from pathlib import Path

from memory.config import load_config
from memory.ingestion import mvp_ingestion

OWNER_A_TOKEN = "bruno-owner-a-token"
OWNER_B_TOKEN = "bruno-owner-b-token"
OWNER_C_TOKEN = "bruno-owner-c-token"
SHARED_PROJECT_ID = "bruno-shared-project"


def main() -> None:
    config = load_config(Path("tests/bruno/config.auth.ci.yaml"))
    runtime = mvp_ingestion.configure_runtime(config=config)
    store = runtime.metadata_store

    store.create_user(user_id="bruno-owner-a", display_name="Bruno Owner A")
    store.create_user(user_id="bruno-owner-b", display_name="Bruno Owner B")
    store.create_user(user_id="bruno-owner-c", display_name="Bruno Owner C")
    store.create_auth_token(
        owner_id="bruno-owner-a",
        token=OWNER_A_TOKEN,
        token_display_name="bruno-ci-owner-a",
    )
    store.create_auth_token(
        owner_id="bruno-owner-b",
        token=OWNER_B_TOKEN,
        token_display_name="bruno-ci-owner-b",
    )
    store.create_auth_token(
        owner_id="bruno-owner-c",
        token=OWNER_C_TOKEN,
        token_display_name="bruno-ci-owner-c",
    )
    store.create_project(
        project_id=SHARED_PROJECT_ID,
        owner_id="bruno-owner-a",
        name="Bruno Shared Project",
        description="Synthetic shared project for Bruno integration tests.",
    )
    store.add_project_member(
        project_id=SHARED_PROJECT_ID,
        user_id="bruno-owner-b",
        role="writer",
    )

    print(f"seeded project={SHARED_PROJECT_ID}")


if __name__ == "__main__":
    main()
