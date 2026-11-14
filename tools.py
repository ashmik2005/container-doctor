"""Tools the agent can call. Each tool is a plain Python function that returns a string.

Why strings? Whatever a tool returns gets pasted back into the conversation as a
"tool" message, and the model can only read text. Errors are returned as text
too (starting with "ERROR:") so the model can read them and adjust.
"""
import json
import re
import shutil
import socket
import subprocess

# --- Safety layer 1: command allowlist -------------------------------------
# Every command goes through _run(), and _run() refuses anything whose prefix
# is not listed here. Even if a future tool has a bug, it cannot run `docker rm`.
READ_ONLY_COMMANDS = [
    ["docker", "ps"],
    ["docker", "logs"],
    ["docker", "inspect"],
    ["docker", "system", "df"],
    ["df"],
    ["ss"],
]
# Commands that change state. Allowed only after a human said yes (see agent.py).
ACTION_COMMANDS = [
    ["docker", "restart"],
]

# Docker's own rule for container names. Rejecting anything else also blocks
# option injection like container="--follow" (which would hang `docker logs`).
CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

# Env var names whose values we hide before showing them to the model.
SECRET_KEY_RE = re.compile(r"PASS|SECRET|TOKEN|API_KEY|PRIVATE", re.IGNORECASE)


def _starts_with(args: list[str], prefixes: list[list[str]]) -> bool:
    return any(args[: len(p)] == p for p in prefixes)


def _run(args: list[str], allow_actions: bool = False) -> str:
    """Run an allowlisted command and return its combined stdout+stderr as text.

    We pass a list of arguments (not one string) and never use shell=True, so a
    value like "web; rm -rf /" stays one literal argument instead of becoming
    a second shell command.
    """
    allowed = READ_ONLY_COMMANDS + (ACTION_COMMANDS if allow_actions else [])
    if not _starts_with(args, allowed):
        return f"ERROR: command not allowed: {' '.join(args)}"
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after 30s: {' '.join(args)}"
    except FileNotFoundError:
        return f"ERROR: program not found: {args[0]}"
    # docker writes container logs and most errors to stderr, so keep both.
    output = (result.stdout + result.stderr).strip()
    return output or "(no output)"


def _check_name(container: str) -> str | None:
    """Return an error message if the container name is invalid, else None."""
    if not isinstance(container, str) or not CONTAINER_NAME_RE.match(container):
        return f"ERROR: invalid container name {container!r}. Use a name from list_containers."
    return None


# --- Read-only tools -------------------------------------------------------

def list_containers() -> str:
    """List all containers (running and stopped) with image, status and ports."""
    return _run([
        "docker", "ps", "-a",
        "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
    ])


def get_logs(container: str, tail: int = 50) -> str:
    """Return the last `tail` lines of a container's logs."""
    if error := _check_name(container):
        return error
    tail = max(1, min(int(tail), 200))  # clamp: the model sometimes asks for 10000
    return _run(["docker", "logs", "--tail", str(tail), container])


def _mask_env(env: list[str]) -> list[str]:
    """Hide values of secret-looking variables: 'DB_PASSWORD=x' -> 'DB_PASSWORD=***'."""
    masked = []
    for item in env:
        key, _, value = item.partition("=")
        masked.append(f"{key}=***" if SECRET_KEY_RE.search(key) else item)
    return masked


def inspect_container(container: str) -> str:
    """Return the diagnostically useful parts of `docker inspect` as compact JSON.

    Raw `docker inspect` is ~250 lines of JSON; most of it is noise for a small
    model. We keep state, exit info, health, command, env, mounts, ports, limits.
    """
    if error := _check_name(container):
        return error
    raw = _run(["docker", "inspect", "--type", "container", container])
    try:
        data = json.loads(raw)[0]
    except (json.JSONDecodeError, IndexError):
        return raw  # docker's own error message, e.g. "No such container"

    state, config, host = data["State"], data["Config"], data["HostConfig"]
    summary = {
        "Name": data["Name"].lstrip("/"),
        "Image": config.get("Image"),
        "Entrypoint": config.get("Entrypoint"),
        "Cmd": config.get("Cmd"),
        "User": config.get("User") or "root (default)",
        "State": {k: state.get(k) for k in
                  ("Status", "ExitCode", "Error", "OOMKilled", "StartedAt", "FinishedAt")},
        "RestartCount": data.get("RestartCount"),
        "RestartPolicy": (host.get("RestartPolicy") or {}).get("Name"),
        "MemoryLimitBytes": host.get("Memory") or "unlimited",
        "ReadonlyRootfs": host.get("ReadonlyRootfs"),
        "Tmpfs": host.get("Tmpfs"),
        "Env": _mask_env(config.get("Env") or []),
        "Mounts": [
            {"Type": m.get("Type"), "Source": m.get("Source"),
             "Destination": m.get("Destination"), "RW": m.get("RW")}
            for m in data.get("Mounts") or []
        ],
        "PortBindings": host.get("PortBindings"),
        "Networks": list((data.get("NetworkSettings") or {}).get("Networks") or {}),
        "Healthcheck": (config.get("Healthcheck") or {}).get("Test"),
    }
    if health := state.get("Health"):
        summary["Health"] = {
            "Status": health.get("Status"),
            "FailingStreak": health.get("FailingStreak"),
            "LastChecks": [
                {"ExitCode": h.get("ExitCode"), "Output": (h.get("Output") or "").strip()[:200]}
                for h in (health.get("Log") or [])[-3:]
            ],
        }
    return json.dumps(summary, indent=1)


def check_port(port: int) -> str:
    """Report who publishes a host port, whether something listens, and what a connection returns."""
    port = int(port)
    if not 1 <= port <= 65535:
        return f"ERROR: port must be between 1 and 65535, got {port}"

    publishers = _run(["docker", "ps", "-a", "--filter", f"publish={port}",
                       "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"])
    lines = [f"Containers publishing host port {port}: {publishers}"]
    if shutil.which("ss"):
        listeners = _run(["ss", "-ltnH", f"sport = :{port}"])
        lines.append(f"Host listeners on port {port}: {listeners}")
    lines.append(f"Connection test to 127.0.0.1:{port}: {_probe(port)}")
    return "\n".join(lines)


def _probe(port: int) -> str:
    """Open a TCP connection, send a tiny HTTP request, describe what happens."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            reply = sock.recv(200)
    except ConnectionRefusedError:
        return "connection refused (nothing is listening)"
    except ConnectionResetError:
        return "connection reset by peer (port is published but the app inside does not accept the connection)"
    except socket.timeout:
        return "timed out (no reply within 3s)"
    except OSError as e:
        return f"failed: {e}"
    if not reply:
        return "connected but got an empty reply (port is published but the app inside is not answering on it)"
    return "connected, reply starts with: " + reply.decode(errors="replace").splitlines()[0]


def check_disk() -> str:
    """Show free space on the host root filesystem and Docker's own disk usage."""
    return (
        "Host filesystem:\n" + _run(["df", "-h", "/"]) +
        "\n\nDocker disk usage:\n" + _run(["docker", "system", "df"])
    )


# --- Action tools (need human approval, enforced in agent.py) ---------------

def restart_container(container: str) -> str:
    """Restart a container. Changes state, so the agent asks a human first."""
    if error := _check_name(container):
        return error
    return _run(["docker", "restart", container], allow_actions=True)


# The name -> function lookup table the agent loop uses to execute tool calls.
TOOLS = {
    "list_containers": list_containers,
    "get_logs": get_logs,
    "inspect_container": inspect_container,
    "check_port": check_port,
    "check_disk": check_disk,
    "restart_container": restart_container,
}

# Tools that change something. agent.py asks a human before running them.
NEEDS_APPROVAL = {"restart_container"}


def _schema(name: str, description: str, properties: dict | None = None,
            required: list[str] | None = None) -> dict:
    """Build one tool description in the JSON-schema format Ollama expects."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {},
                           "required": required or []},
        },
    }


CONTAINER_ARG = {"container": {"type": "string",
                               "description": "Exact container name from list_containers."}}

# The model never sees our Python code, only these descriptions, so they must
# say clearly WHEN each tool is useful. They are part of the prompt.
TOOL_SCHEMAS = [
    _schema("list_containers",
            "List all Docker containers (running and stopped) with image, status "
            "(e.g. 'Exited (1)', 'Up (unhealthy)', 'Created') and published ports. Call this first."),
    _schema("get_logs",
            "Get the most recent log lines of one container. Use it to find the error message "
            "explaining why a container crashed or misbehaves.",
            {**CONTAINER_ARG,
             "tail": {"type": "integer", "description": "Lines from the end to return (default 50)."}},
            ["container"]),
    _schema("inspect_container",
            "Get a container's configuration and state: exit code, State.Error, OOMKilled, health "
            "check results, command, user, env vars, mounts, port bindings, memory limit, restart "
            "count. Use it when logs are empty or do not explain the problem.",
            CONTAINER_ARG, ["container"]),
    _schema("check_port",
            "Check a host port: which container publishes it, whether anything listens, and what "
            "happens when connecting to it. Use it when a service is running but unreachable.",
            {"port": {"type": "integer", "description": "Host port number, e.g. 8080."}},
            ["port"]),
    _schema("check_disk",
            "Show free disk space on the host and Docker's disk usage. Use it for "
            "'no space left on device' style errors."),
    _schema("restart_container",
            "Restart a container. This CHANGES state and needs human approval. Only use it if a "
            "restart would actually fix the root cause; it does not fix configuration errors.",
            CONTAINER_ARG, ["container"]),
]
