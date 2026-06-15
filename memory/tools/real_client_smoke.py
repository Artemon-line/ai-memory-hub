from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Sequence

from memory.backend.log_safety import redact_secrets

DEFAULT_CLIENTS = ("claude", "copilot", "codex", "opencode", "gemini")
STARTER_CLIENTS = {"claude", "copilot"}
SMOKE_MARKER = "weekly real-client smoke test marker"
SMOKE_PROMPT = (
    "Use the ai-memory-hub MCP server. Validate and insert a short conversation "
    f"about the {SMOKE_MARKER}. Then search for it, retrieve it by ID, and ask "
    "what the conversation was about. Report the inserted ID."
)


@dataclass(frozen=True)
class ClientSpec:
    name: str
    executable: str
    command_env: str
    default_command: tuple[str, ...] | None
    env: dict[str, str]
    notes: str


@dataclass
class ClientResult:
    name: str
    status: str
    reason: str
    command: list[str]
    verification: dict[str, Any] | None = None
    stdout_log: str | None = None
    stderr_log: str | None = None


@dataclass
class HarnessResult:
    status: str
    hub_url: str
    gateway_url: str
    artifact_dir: str
    clients: list[ClientResult]


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.gateway_server:
        _run_gateway_server(host=args.gateway_host, port=args.gateway_port, log_file=args.gateway_log)
        return 0
    result = run_harness(args)
    _write_json(Path(result.artifact_dir) / "summary.json", _result_to_dict(result))
    print(json.dumps(_result_to_dict(result), indent=2, sort_keys=True))
    return 1 if result.status == "failed" else 0


def run_harness(args: argparse.Namespace) -> HarnessResult:
    artifact_dir = Path(args.artifact_dir or tempfile.mkdtemp(prefix="amh-real-client-smoke-")).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    selected_clients = tuple(args.client or DEFAULT_CLIENTS)
    hub_process: subprocess.Popen[str] | None = None
    gateway_process: subprocess.Popen[str] | None = None
    workspace = Path(tempfile.mkdtemp(prefix="amh-real-client-work-"))
    try:
        hub_url = args.hub_url or f"http://127.0.0.1:{_free_port()}"
        gateway_url = args.gateway_url or f"http://127.0.0.1:{_free_port()}"
        if not args.hub_url:
            hub_process = _start_hub(hub_url=hub_url, workspace=workspace, artifact_dir=artifact_dir)
        if not args.gateway_url:
            gateway_process = _start_gateway(gateway_url=gateway_url, artifact_dir=artifact_dir)
        _wait_for_hub(hub_url, timeout_seconds=args.startup_timeout)
        _wait_for_gateway(gateway_url, timeout_seconds=args.startup_timeout)

        results = [
            run_client(
                spec=_client_spec(name=name, hub_url=hub_url, gateway_url=gateway_url, workspace=workspace),
                prompt=SMOKE_PROMPT,
                hub_url=hub_url,
                artifact_dir=artifact_dir,
                timeout_seconds=args.client_timeout,
                require_configured=args.require_configured,
            )
            for name in selected_clients
        ]
        status = _aggregate_status(results, require_success_for=tuple(args.require_success_for or ()))
        return HarnessResult(
            status=status,
            hub_url=hub_url,
            gateway_url=gateway_url,
            artifact_dir=str(artifact_dir),
            clients=results,
        )
    finally:
        _terminate_process(gateway_process)
        _terminate_process(hub_process)


def run_client(
    *,
    spec: ClientSpec,
    prompt: str,
    hub_url: str,
    artifact_dir: Path,
    timeout_seconds: int,
    require_configured: bool,
) -> ClientResult:
    stdout_log = artifact_dir / f"{spec.name}.stdout.log"
    stderr_log = artifact_dir / f"{spec.name}.stderr.log"
    command = _resolve_command(spec, prompt=prompt, artifact_dir=artifact_dir)
    if command is None:
        status = "failed" if require_configured else "skipped"
        reason = f"{spec.command_env} is not set and no safe default command is defined. {spec.notes}"
        return ClientResult(spec.name, status, reason, [], stdout_log=str(stdout_log), stderr_log=str(stderr_log))
    executable = shutil.which(command[0])
    if executable is None:
        status = "failed" if require_configured else "skipped"
        reason = f"client executable not found: {command[0]}"
        return ClientResult(spec.name, status, reason, command, stdout_log=str(stdout_log), stderr_log=str(stderr_log))
    process_env = os.environ.copy()
    process_env.update(spec.env)
    process_env["AMH_MCP_URL"] = f"{hub_url.rstrip('/')}/mcp/"
    process_env["AMH_SMOKE_PROMPT"] = prompt
    process_env["AMH_SMOKE_MARKER"] = SMOKE_MARKER
    with stdout_log.open("w", encoding="utf-8") as stdout, stderr_log.open("w", encoding="utf-8") as stderr:
        try:
            completed = subprocess.run(
                [executable, *command[1:]],
                env=process_env,
                cwd=artifact_dir,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ClientResult(
                spec.name,
                "failed",
                f"client timed out after {timeout_seconds}s",
                command,
                stdout_log=str(stdout_log),
                stderr_log=str(stderr_log),
            )
    if completed.returncode != 0:
        _redact_log_file(stdout_log)
        _redact_log_file(stderr_log)
        return ClientResult(
            spec.name,
            "failed",
            f"client exited with code {completed.returncode}",
            command,
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
        )
    _redact_log_file(stdout_log)
    _redact_log_file(stderr_log)
    verification = verify_memory_created(hub_url=hub_url, marker=SMOKE_MARKER)
    if verification["status"] != "ok":
        return ClientResult(
            spec.name,
            "failed",
            verification["reason"],
            command,
            verification=verification,
            stdout_log=str(stdout_log),
            stderr_log=str(stderr_log),
        )
    return ClientResult(
        spec.name,
        "passed",
        "client completed and inserted searchable memory",
        command,
        verification=verification,
        stdout_log=str(stdout_log),
        stderr_log=str(stderr_log),
    )


def verify_memory_created(*, hub_url: str, marker: str) -> dict[str, Any]:
    search = _post_json(
        f"{hub_url.rstrip('/')}/memory/search",
        {"query": marker, "top_k": 5, "result_mode": "compact"},
    )
    results = search.get("results", []) if isinstance(search, dict) else []
    first = results[0] if results else None
    memory_id = first.get("id") if isinstance(first, dict) else None
    if not memory_id:
        return {"status": "failed", "reason": "direct search did not find smoke marker"}
    retrieve = _post_json(f"{hub_url.rstrip('/')}/memory/retrieve", {"id": memory_id})
    ask = _post_json(
        f"{hub_url.rstrip('/')}/memory/ask",
        {"question": f"What was the conversation about: {marker}?", "top_k": 5},
    )
    answer = ask.get("answer", "") if isinstance(ask, dict) else ""
    if marker not in json.dumps(retrieve).lower() and "real-client" not in str(answer).lower():
        return {"status": "failed", "reason": "retrieve/ask verification did not contain smoke evidence"}
    return {"status": "ok", "id": memory_id, "search_results": len(results), "answer": answer}


def _client_spec(*, name: str, hub_url: str, gateway_url: str, workspace: Path) -> ClientSpec:
    mcp_url = f"{hub_url.rstrip('/')}/mcp/"
    env_name = f"AMH_REAL_CLIENT_{name.upper().replace('-', '_')}_COMMAND"
    if name == "claude":
        return ClientSpec(
            name=name,
            executable="claude",
            command_env=env_name,
            default_command=None,
            env={
                "ANTHROPIC_BASE_URL": gateway_url,
                "ANTHROPIC_API_KEY": "test-key",
                "ANTHROPIC_MODEL": "amh-smoke-model",
                "CLAUDE_CODE_MCP_SERVER_URL": mcp_url,
            },
            notes="Set a non-interactive Claude Code command template when the CLI syntax is available.",
        )
    if name == "copilot":
        return ClientSpec(
            name=name,
            executable="copilot",
            command_env=env_name,
            default_command=None,
            env={
                "COPILOT_PROVIDER_BASE_URL": f"{gateway_url.rstrip('/')}/v1",
                "COPILOT_PROVIDER_TYPE": "openai",
                "COPILOT_PROVIDER_API_KEY": "test-key",
                "COPILOT_MODEL": "amh-smoke-model",
                "COPILOT_MCP_SERVER_URL": mcp_url,
            },
            notes="Set a non-interactive Copilot CLI command template when the CLI syntax is available.",
        )
    if name == "codex":
        codex_home = workspace / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        _write_text(
            codex_home / "config.toml",
            "\n".join(
                [
                    'model = "amh-smoke-model"',
                    'model_provider = "local-smoke"',
                    "",
                    "[model_providers.local-smoke]",
                    'name = "local-smoke"',
                    f'base_url = "{gateway_url.rstrip("/")}/v1"',
                    'env_key = "AMH_REAL_CLIENT_TEST_API_KEY"',
                    'wire_api = "chat"',
                    "",
                    "[mcp_servers.ai_memory_hub]",
                    f'url = "{mcp_url}"',
                    "",
                ]
            ),
        )
        return ClientSpec(
            name=name,
            executable="codex",
            command_env=env_name,
            default_command=None,
            env={"CODEX_HOME": str(codex_home), "AMH_REAL_CLIENT_TEST_API_KEY": "test-key"},
            notes="Codex status is documented but not enabled until a reliable headless command is validated.",
        )
    if name == "opencode":
        config_home = workspace / "opencode-home"
        config_home.mkdir(parents=True, exist_ok=True)
        _write_json(
            config_home / "opencode.json",
            {
                "provider": {"local-smoke": {"npm": "", "options": {"baseURL": f"{gateway_url.rstrip('/')}/v1"}}},
                "mcp": {"ai-memory-hub": {"type": "remote", "url": mcp_url}},
            },
        )
        return ClientSpec(
            name=name,
            executable="opencode",
            command_env=env_name,
            default_command=None,
            env={"OPENCODE_CONFIG_DIR": str(config_home)},
            notes="opencode status is documented but not enabled until a reliable headless command is validated.",
        )
    if name == "gemini":
        return ClientSpec(
            name=name,
            executable="gemini",
            command_env=env_name,
            default_command=None,
            env={"GEMINI_MCP_SERVER_URL": mcp_url, "GEMINI_MODEL_BASE_URL": gateway_url},
            notes="Gemini remains skipped until local-gateway, no-vendor-credential mode is confirmed.",
        )
    raise ValueError(f"unknown client: {name}")


def _resolve_command(spec: ClientSpec, *, prompt: str, artifact_dir: Path) -> list[str] | None:
    template = os.environ.get(spec.command_env)
    prompt_file = artifact_dir / f"{spec.name}.prompt.txt"
    _write_text(prompt_file, prompt)
    if template:
        return [
            part.format(prompt=prompt, prompt_file=str(prompt_file), artifact_dir=str(artifact_dir))
            for part in shlex.split(template)
        ]
    return list(spec.default_command) if spec.default_command is not None else None


def _start_hub(*, hub_url: str, workspace: Path, artifact_dir: Path) -> subprocess.Popen[str]:
    host, port = _host_port(hub_url)
    config_path = workspace / "config.yaml"
    data_dir = workspace / "data"
    _write_text(
        config_path,
        "\n".join(
            [
                "providers:",
                "  embeddings: local",
                "  embedding_dimension: 32",
                "  metadata_db: sqlite",
                "  vector_db: lancedb",
                "interfaces:",
                "  api: true",
                "  mcp: true",
                "paths:",
                f"  data_dir: {data_dir.as_posix()}",
                "api:",
                f"  host: {host}",
                f"  port: {port}",
                "",
            ]
        ),
    )
    return _open_process(
        [sys.executable, "-m", "memory.cli", "serve", "--config", str(config_path), "--host", host, "--port", str(port)],
        stdout_path=artifact_dir / "ai-memory-hub.stdout.log",
        stderr_path=artifact_dir / "ai-memory-hub.stderr.log",
    )


def _start_gateway(*, gateway_url: str, artifact_dir: Path) -> subprocess.Popen[str]:
    host, port = _host_port(gateway_url)
    return _open_process(
        [
            sys.executable,
            "-m",
            "memory.tools.real_client_smoke",
            "--gateway-server",
            "--gateway-host",
            host,
            "--gateway-port",
            str(port),
            "--gateway-log",
            str(artifact_dir / "gateway.requests.jsonl"),
        ],
        stdout_path=artifact_dir / "gateway.stdout.log",
        stderr_path=artifact_dir / "gateway.stderr.log",
    )


def _open_process(command: list[str], *, stdout_path: Path, stderr_path: Path) -> subprocess.Popen[str]:
    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    return subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)


def _wait_for_hub(hub_url: str, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _post_json(f"{hub_url.rstrip('/')}/memory/search", {"query": "startup", "top_k": 1})
            return
        except Exception as exc:  # noqa: BLE001 - startup polling reports the last error.
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"ai-memory-hub did not become ready: {last_error}")


def _wait_for_gateway(gateway_url: str, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _get_json(f"{gateway_url.rstrip('/')}/v1/models")
            return
        except Exception as exc:  # noqa: BLE001 - startup polling reports the last error.
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"deterministic gateway did not become ready: {last_error}")


def _run_gateway_server(*, host: str, port: int, log_file: str | None) -> None:
    handler = _make_gateway_handler(Path(log_file) if log_file else None)
    server = ThreadingHTTPServer((host, port), handler)
    stop = threading.Event()

    def handle_signal(signum: int, frame: Any) -> None:
        stop.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    server.serve_forever()


def _make_gateway_handler(log_file: Path | None) -> type[BaseHTTPRequestHandler]:
    class GatewayHandler(BaseHTTPRequestHandler):
        server_version = "AMHDeterministicGateway/1.0"

        def do_GET(self) -> None:
            _log_gateway_request(log_file, "GET", self.path, None)
            if self.path.rstrip("/") in {"/v1/models", "/models"}:
                self._send_json({"object": "list", "data": [{"id": "amh-smoke-model", "object": "model"}]})
                return
            self._send_json({"error": {"message": "not found"}}, status=404)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("content-length", "0") or "0"))
            payload = json.loads(body.decode("utf-8") or "{}")
            _log_gateway_request(log_file, "POST", self.path, payload)
            if self.path.endswith("/chat/completions"):
                self._send_json(_openai_chat_response(payload))
                return
            if self.path.endswith("/responses"):
                self._send_json(_openai_responses_response(payload))
                return
            if self.path.endswith("/messages"):
                self._send_json(_anthropic_messages_response(payload))
                return
            self._send_json({"error": {"message": "not found"}}, status=404)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return GatewayHandler


def _openai_chat_response(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = _next_tool_name(payload.get("messages", []))
    if tool_name:
        return {
            "id": "chatcmpl-amh-smoke",
            "object": "chat.completion",
            "model": payload.get("model", "amh-smoke-model"),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{tool_name}",
                                "type": "function",
                                "function": {"name": tool_name, "arguments": json.dumps(_tool_input(tool_name, payload))},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    return _openai_final_response(payload)


def _openai_responses_response(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = _next_tool_name(payload.get("input", []))
    output: list[dict[str, Any]]
    if tool_name:
        output = [
            {
                "type": "function_call",
                "id": f"fc_{tool_name}",
                "call_id": f"call_{tool_name}",
                "name": tool_name,
                "arguments": json.dumps(_tool_input(tool_name, payload)),
            }
        ]
    else:
        output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Smoke complete."}]}]
    return {"id": "resp_amh_smoke", "object": "response", "status": "completed", "model": "amh-smoke-model", "output": output}


def _anthropic_messages_response(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = _next_tool_name(payload.get("messages", []))
    if tool_name:
        return {
            "id": "msg_amh_smoke",
            "type": "message",
            "role": "assistant",
            "model": payload.get("model", "amh-smoke-model"),
            "content": [
                {
                    "type": "tool_use",
                    "id": f"toolu_{tool_name}",
                    "name": tool_name,
                    "input": _tool_input(tool_name, payload),
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    return {
        "id": "msg_amh_done",
        "type": "message",
        "role": "assistant",
        "model": payload.get("model", "amh-smoke-model"),
        "content": [{"type": "text", "text": "Smoke complete."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _next_tool_name(messages: Any) -> str | None:
    text = json.dumps(messages).lower()
    sequence = ("memory_validate", "memory_insert", "memory_search", "memory_retrieve", "memory_ask")
    for name in sequence:
        if name not in text:
            return name
    return None


def _tool_input(tool_name: str, context: Any | None = None) -> dict[str, Any]:
    conversation = {
        "source": "real-client-smoke",
        "timestamp": "2026-06-12T00:00:00Z",
        "metadata": {"tags": ["real-client-smoke", "weekly"], "client": "real-client-harness"},
        "messages": [
            {"role": "user", "text": f"Please remember the {SMOKE_MARKER}."},
            {"role": "assistant", "text": "The weekly real-client smoke test conversation was saved."},
        ],
    }
    if tool_name in {"memory_validate", "memory_insert"}:
        return {"conversation_json": conversation}
    if tool_name == "memory_search":
        return {"query": SMOKE_MARKER, "top_k": 5, "result_mode": "compact"}
    if tool_name == "memory_retrieve":
        return {"id": _extract_memory_id(context) or "use-search-result-id"}
    if tool_name == "memory_ask":
        return {"question": f"What was the conversation about: {SMOKE_MARKER}?", "top_k": 5}
    return {}


def _extract_memory_id(context: Any) -> str | None:
    text = json.dumps(context)
    for pattern in (
        r'"id"\s*:\s*"([0-9a-fA-F-]{32,36})"',
        r"'id'\s*:\s*'([0-9a-fA-F-]{32,36})'",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _openai_final_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-amh-smoke-final",
        "object": "chat.completion",
        "model": payload.get("model", "amh-smoke-model"),
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Smoke complete."},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _aggregate_status(results: Iterable[ClientResult], *, require_success_for: tuple[str, ...]) -> str:
    result_list = list(results)
    if any(result.status == "failed" for result in result_list):
        return "failed"
    by_name = {result.name: result for result in result_list}
    if any(by_name.get(name, ClientResult(name, "skipped", "", [])).status != "passed" for name in require_success_for):
        return "failed"
    return "ok"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _host_port(url: str) -> tuple[str, int]:
    stripped = url.removeprefix("http://").removeprefix("https://")
    host, _, rest = stripped.partition(":")
    port = rest.split("/", 1)[0]
    return host, int(port)


def _terminate_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _log_gateway_request(log_file: Path | None, method: str, path: str, payload: Any) -> None:
    if log_file is None:
        return
    record = {"method": method, "path": path, "payload": payload}
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _redact_log_file(path: Path) -> None:
    if not path.exists():
        return
    path.write_text(redact_secrets(path.read_text(encoding="utf-8")), encoding="utf-8")


def _result_to_dict(result: HarnessResult) -> dict[str, Any]:
    data = asdict(result)
    data["clients"] = [asdict(client) for client in result.clients]
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run real-client MCP smoke checks.")
    parser.add_argument("--client", action="append", choices=DEFAULT_CLIENTS, help="Client to run. Repeatable.")
    parser.add_argument("--artifact-dir", default=None, help="Directory for logs and summary JSON.")
    parser.add_argument("--hub-url", default=None, help="Use an already running ai-memory-hub base URL.")
    parser.add_argument("--gateway-url", default=None, help="Use an already running deterministic gateway base URL.")
    parser.add_argument("--startup-timeout", type=int, default=30)
    parser.add_argument("--client-timeout", type=int, default=90)
    parser.add_argument("--require-configured", action="store_true", help="Treat missing command templates as failures.")
    parser.add_argument("--require-success-for", action="append", choices=DEFAULT_CLIENTS, default=[])
    parser.add_argument("--gateway-server", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gateway-host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--gateway-port", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--gateway-log", default=None, help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
