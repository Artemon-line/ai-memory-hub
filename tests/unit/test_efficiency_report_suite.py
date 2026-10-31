from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tools import run_efficiency_reports


def test_report_suite_persists_success_failure_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def threshold_failure(_report: dict[str, object]) -> None:
        raise AssertionError("synthetic threshold detail must not enter the manifest")

    def build_error() -> dict[str, object]:
        raise RuntimeError("synthetic private detail must not enter the artifact")

    specs = (
        run_efficiency_reports.ReportSpec("passed", lambda: {"before": 2, "after": 1}),
        run_efficiency_reports.ReportSpec(
            "threshold", lambda: {"before": 2, "after": 3}, threshold_failure
        ),
        run_efficiency_reports.ReportSpec("error", build_error),
    )
    monkeypatch.setattr(run_efficiency_reports, "_report_specs", lambda _config: specs)
    config = run_efficiency_reports.SuiteConfig(
        profile="small",
        corpus_limit=8,
        query_requests=4,
        concurrency_levels=(1, 2),
        cache_iterations=2,
        token_iterations=2,
    )

    manifest = run_efficiency_reports.run_report_suite(
        output_dir=tmp_path, config=config
    )

    assert manifest["status"] == "failed"
    assert [entry["status"] for entry in manifest["reports"]] == [
        "passed",
        "threshold_failed",
        "error",
    ]
    assert json.loads((tmp_path / "passed.json").read_text(encoding="utf-8")) == {
        "after": 1,
        "before": 2,
    }
    assert json.loads((tmp_path / "error.json").read_text(encoding="utf-8")) == {
        "error_type": "RuntimeError",
        "report_schema_version": "1.0",
        "status": "error",
    }
    serialized_manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "private detail" not in serialized_manifest
    assert "threshold detail" not in serialized_manifest


def test_efficiency_workflow_is_scheduled_manual_and_not_on_normal_ci() -> None:
    path = Path(".github/workflows/efficiency-profiles.yml")
    workflow_text = path.read_text(encoding="utf-8")
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
    triggers = workflow["on"]

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"][0]["cron"] == "23 3 * * 6"
    assert "pull_request" not in triggers
    assert "push" not in triggers
    assert "if: always()" in workflow_text
    assert "reports/efficiency/*.json" in workflow_text
    assert "retention-days: 30" in workflow_text
    assert "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7" in workflow_text


@pytest.mark.parametrize("raw", ("", "0,1", "1,1"))
def test_concurrency_parser_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        run_efficiency_reports._parse_concurrency(raw)
