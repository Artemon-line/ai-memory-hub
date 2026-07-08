from __future__ import annotations

import pytest

from memory.plugin_contracts import (
    PluginManifest,
    PluginSafetyPolicy,
    validate_plugin_conversation_output,
    validate_plugin_enrichment_output,
)


def test_plugin_manifest_defaults_to_safe_policy() -> None:
    manifest = PluginManifest(name="manual-import", version="1")

    assert manifest.safety == PluginSafetyPolicy()
    assert manifest.safety.allow_secrets is False
    assert manifest.safety.require_deterministic_fixtures is True


def test_plugin_conversation_output_accepts_normalized_shape() -> None:
    payload = {
        "source": "plugin:test",
        "messages": [{"role": "user", "text": "remember this"}],
    }

    assert validate_plugin_conversation_output(payload) is payload


@pytest.mark.parametrize("key", ["owner_id", "project_id", "api_key", "raw_html", "screenshot"])
def test_plugin_conversation_output_rejects_protected_or_unsafe_keys(key: str) -> None:
    payload = {
        "source": "plugin:test",
        "messages": [{"role": "user", "text": "remember this"}],
        key: "bad",
    }

    with pytest.raises(ValueError, match="protected or unsafe"):
        validate_plugin_conversation_output(payload)


def test_plugin_conversation_output_rejects_malformed_messages() -> None:
    with pytest.raises(ValueError, match="messages"):
        validate_plugin_conversation_output({"messages": [{"role": "user"}]})


def test_plugin_enrichment_output_requires_list_fields() -> None:
    with pytest.raises(ValueError, match="facts"):
        validate_plugin_enrichment_output({"facts": {"subject": "user"}})


def test_plugin_enrichment_output_rejects_auth_bypass_keys() -> None:
    with pytest.raises(ValueError, match="protected or unsafe"):
        validate_plugin_enrichment_output({"entities": [], "authorization": "Bearer secret"})
