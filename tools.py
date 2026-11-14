"""Tools the agent can call. Each tool is a plain Python function that returns a string.

Why strings? Whatever a tool returns gets pasted back into the conversation as a
"tool" message, and the model can only read text.
"""
import subprocess


def _run(args: list[str]) -> str:
    """Run a command and return its combined stdout+stderr as text.

    We pass a list of arguments (not one string) so no shell is involved.
    That means a container name like "web; rm -rf /" is passed to docker as
    one literal argument instead of being interpreted as two shell commands.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,  # a hung docker command must not hang the agent
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after 30s: {' '.join(args)}"
    # docker writes container logs and most errors to stderr, so keep both.
    output = (result.stdout + result.stderr).strip()
    return output or "(no output)"


def list_containers() -> str:
    """List all containers (running and stopped) with image, status and ports."""
    return _run([
        "docker", "ps", "-a",
        "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
    ])


def get_logs(container: str, tail: int = 50) -> str:
    """Return the last `tail` lines of a container's logs."""
    return _run(["docker", "logs", "--tail", str(int(tail)), container])


# The name -> function lookup table the agent loop uses to execute tool calls.
TOOLS = {
    "list_containers": list_containers,
    "get_logs": get_logs,
}

# JSON-schema descriptions sent to the model. The model never sees our Python
# code, only these descriptions, so they must be clear and precise.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_containers",
            "description": "List all Docker containers (running and stopped) with their image, status (e.g. 'Exited (1)') and published ports. Call this first to find container names.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logs",
            "description": "Get the most recent log lines of one container. Use it to find error messages explaining why a container crashed or misbehaves.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Exact container name from list_containers."},
                    "tail": {"type": "integer", "description": "How many lines from the end to return (default 50)."},
                },
                "required": ["container"],
            },
        },
    },
]
