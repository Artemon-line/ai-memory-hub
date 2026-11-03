from __future__ import annotations

import threading

from memory.ingestion import mvp_ingestion


def test_runtime_context_isolates_concurrent_benchmark_dependencies() -> None:
    live_runtime = _runtime("live")
    benchmark_runtime = _runtime("benchmark")
    previous_runtime = mvp_ingestion._RUNTIME
    entered = threading.Event()
    release = threading.Event()
    observed: list[object] = []
    mvp_ingestion.configure_runtime(runtime=live_runtime)

    def benchmark_request() -> None:
        with mvp_ingestion.runtime_context(benchmark_runtime):
            observed.append(mvp_ingestion._runtime())
            entered.set()
            release.wait(timeout=5)
            observed.append(mvp_ingestion._runtime())

    thread = threading.Thread(target=benchmark_request)
    try:
        thread.start()
        assert entered.wait(timeout=5)
        assert mvp_ingestion._runtime() is live_runtime
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert observed == [benchmark_runtime, benchmark_runtime]
        assert mvp_ingestion._runtime() is live_runtime
    finally:
        release.set()
        thread.join(timeout=5)
        mvp_ingestion._RUNTIME = previous_runtime


def _runtime(mode: str) -> mvp_ingestion.RuntimeDependencies:
    return mvp_ingestion.RuntimeDependencies(
        embedding_provider=object(),
        metadata_store=object(),
        vector_store=object(),
        health_state={"mode": mode},
    )
