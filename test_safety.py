"""Offline tests for the safety layer (no model needed, Docker must be running).

Run:  .venv/bin/python test_safety.py
Plain asserts instead of pytest, to keep dependencies minimal.
"""
from agent import execute_tool_call, truncate, validate_args
from tools import _run, get_logs

ALWAYS_YES = lambda name, args: True
ALWAYS_NO = lambda name, args: False


def test_allowlist_blocks_destructive_commands():
    assert _run(["docker", "rm", "-f", "x"]).startswith("ERROR: command not allowed")
    assert _run(["rm", "-rf", "/tmp/x"]).startswith("ERROR: command not allowed")
    # restart is only allowed when the caller explicitly opts in (after approval)
    assert _run(["docker", "restart", "x"]).startswith("ERROR: command not allowed")


def test_container_name_injection_rejected():
    assert get_logs("web; rm -rf /").startswith("ERROR: invalid container name")
    assert get_logs("--follow").startswith("ERROR: invalid container name")


def test_unknown_tool():
    result = execute_tool_call("delete_everything", {}, ALWAYS_YES)
    assert "unknown tool" in result and "list_containers" in result


def test_bad_arguments():
    assert "missing required" in execute_tool_call("get_logs", {"name": "x"}, ALWAYS_YES)
    assert "bad arguments" in execute_tool_call("check_port", {"port": "eighty"}, ALWAYS_YES)
    cleaned, error = validate_args("get_logs", {"container": "web", "made_up": 1})
    assert cleaned == {"container": "web"} and error is None


def test_approval_required():
    assert execute_tool_call("restart_container", {"container": "x"}, ALWAYS_NO).startswith("DENIED")


def test_truncation_keeps_tail():
    text = "start " + "x" * 10_000 + " FATAL ERROR"
    short = truncate(text, limit=1000)
    assert len(short) < 1100 and short.endswith("FATAL ERROR") and "truncated" in short


if __name__ == "__main__":
    tests = [f for name, f in dict(globals()).items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed.")
