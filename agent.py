"""The agent loop: ask the model, run the tools it requests, feed results back, repeat.

Run:  .venv/bin/python agent.py "my web app is down"
"""
import json
import sys
import time

import ollama

from tools import NEEDS_APPROVAL, TOOL_SCHEMAS, TOOLS

MODEL = "qwen2.5:3b"
MAX_STEPS = 8                # max model calls; a confused small model can loop forever
MAX_TOOL_OUTPUT_CHARS = 3000 # ~750 tokens; keeps one noisy tool from flooding the context
NUM_CTX = 8192               # context window in tokens. Ollama's default is small and it
                             # silently drops the OLDEST messages (incl. the system prompt) when full.
EVIDENCE_TOOLS = {"get_logs", "inspect_container"}  # must use one before answering

SYSTEM_PROMPT = """You are Container Doctor, an expert at diagnosing broken Docker containers.
Use the tools to gather evidence before answering. Typical approach:
1. list_containers to see what exists and which containers are not healthy.
2. get_logs on the broken container to find the error message.
3. If logs are empty or unclear: inspect_container (exit code, State.Error, OOMKilled, health, mounts, env).
4. If a container is running but unreachable: check_port. For space errors: check_disk.
Call one tool at a time and use the exact container names from list_containers.
Never answer from the container list alone: confirm the cause with get_logs or inspect_container first.
When you know the cause, reply WITHOUT calling a tool, in this format:
ROOT CAUSE: <one sentence>
EVIDENCE: <the log line or status that proves it>
FIX: <concrete steps>"""


def ask_human(name: str, args: dict) -> bool:
    """Human-in-the-loop: the default approval function asks on the terminal."""
    answer = input(f"\n>>> Agent wants to run {name}({json.dumps(args)}). Allow? [y/N] ")
    return answer.strip().lower() == "y"


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Keep the head and (mostly) the tail; errors usually appear at the END of logs."""
    if len(text) <= limit:
        return text
    head, tail = limit // 4, limit - limit // 4
    cut = len(text) - head - tail
    return f"{text[:head]}\n... [{cut} characters truncated] ...\n{text[-tail:]}"


def validate_args(name: str, args) -> tuple[dict, str | None]:
    """Check the model's arguments against the tool's schema.

    Returns (cleaned_args, error). Small models often add made-up arguments or
    forget required ones; we drop unknown keys and report missing ones.
    """
    if not isinstance(args, dict):
        args = {}
    params = next(s["function"]["parameters"] for s in TOOL_SCHEMAS
                  if s["function"]["name"] == name)
    cleaned = {k: v for k, v in args.items() if k in params["properties"]}
    missing = [k for k in params["required"] if k not in cleaned]
    if missing:
        return cleaned, f"ERROR: missing required argument(s) {missing} for tool '{name}'."
    return cleaned, None


def execute_tool_call(name: str, args, approve) -> str:
    """Run one tool call safely. Never raises: every problem becomes a message for the model."""
    if name not in TOOLS:
        # Hallucinated tool: tell the model what actually exists so it can recover.
        return f"ERROR: unknown tool '{name}'. Available tools: {', '.join(TOOLS)}."
    args, error = validate_args(name, args)
    if error:
        return error
    if name in NEEDS_APPROVAL and not approve(name, args):
        return "DENIED: the user did not approve this action. Do not retry it; give your diagnosis instead."
    try:
        return TOOLS[name](**args)
    except (TypeError, ValueError) as e:  # e.g. port="eighty" -> int() fails
        return f"ERROR: bad arguments for '{name}': {e}"
    except Exception as e:  # last resort: a tool bug must not crash the agent
        return f"ERROR: tool '{name}' failed: {type(e).__name__}: {e}"


def print_message(msg: dict) -> None:
    """Pretty-print one message so we can SEE the conversation the model sees."""
    print(f"\n----- {msg['role'].upper()} -----")
    if msg.get("content"):
        print(msg["content"])
    for call in msg.get("tool_calls") or []:
        fn = call["function"]
        print(f"[tool call] {fn['name']}({json.dumps(fn.get('arguments'))})")


def call_model(messages: list, tools: list | None) -> dict:
    response = ollama.chat(
        model=MODEL,
        messages=messages,
        tools=tools,
        options={"temperature": 0, "num_ctx": NUM_CTX},  # temperature 0: repeatable runs
    )
    # Convert the pydantic Message object to a plain dict for printing/storing.
    return response.message.model_dump(exclude_none=True)


def run_agent(question: str, tool_schemas: list = TOOL_SCHEMAS,
              approve=ask_human, verbose: bool = True) -> dict:
    """Diagnose `question`. Returns the answer plus stats (used by eval.py)."""
    start = time.time()
    # The whole "memory" of the agent is this list. Every step we send ALL of it.
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    def log(msg):
        if verbose:
            print_message(msg)

    for m in messages:
        log(m)

    tools_used = []
    seen_calls = set()
    answer = None
    nudged = False

    for step in range(1, MAX_STEPS + 1):
        assistant_msg = call_model(messages, tool_schemas)
        messages.append(assistant_msg)
        log(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls")
        if not tool_calls:
            # Evidence guard: the 3B model ignores "confirm before answering" in the
            # prompt, so we enforce it in code, once, to avoid an endless loop.
            if not nudged and not EVIDENCE_TOOLS & set(tools_used):
                nudged = True
                nudge = {"role": "user", "content":
                         "You have not looked at logs or container details yet. Gather evidence "
                         "with get_logs or inspect_container before giving your final answer."}
                messages.append(nudge)
                log(nudge)
                continue
            # No tool requested = the model is giving its final answer.
            answer = assistant_msg.get("content", "")
            break

        # The model asked for one or more tools. WE execute them, not the model.
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"].get("arguments") or {}
            key = (name, json.dumps(args, sort_keys=True))
            if key in seen_calls:
                # Small models often repeat the same call; nudge them forward.
                result = ("NOTE: you already made this exact call; its result is above. "
                          "Use a different tool or give your final answer.")
            else:
                seen_calls.add(key)
                result = truncate(execute_tool_call(name, args, approve))
            tools_used.append(name)
            tool_msg = {"role": "tool", "tool_name": name, "content": result}
            messages.append(tool_msg)
            log(tool_msg)
    else:
        # A for-loop's `else` runs only if the loop never hit `break`, i.e. we
        # used all MAX_STEPS without an answer. One last call WITHOUT tools
        # forces the model to write a diagnosis.
        messages.append({"role": "user", "content":
                         "Step limit reached. Give your best diagnosis now in the required format."})
        assistant_msg = call_model(messages, tools=None)
        messages.append(assistant_msg)
        log(assistant_msg)
        answer = assistant_msg.get("content", "")
        step += 1

    return {
        "answer": answer,
        "steps": step,               # number of model calls
        "tools_used": tools_used,
        "seconds": round(time.time() - start, 1),
        "messages": messages,
    }


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Something is broken in my Docker setup. What is wrong?"
    result = run_agent(question)
    print("\n===== FINAL ANSWER =====")
    print(result["answer"])
    print(f"\n({result['steps']} model calls, tools: {result['tools_used']}, {result['seconds']}s)")
