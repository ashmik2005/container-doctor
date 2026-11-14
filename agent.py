"""The agent loop: ask the model, run the tools it requests, feed results back, repeat.

Run:  .venv/bin/python agent.py "my web app is down"
"""
import json
import sys

import ollama

from tools import TOOL_SCHEMAS, TOOLS

MODEL = "qwen2.5:3b"
MAX_STEPS = 10  # Stage 1 safety net so a confused model can't loop forever

SYSTEM_PROMPT = """You are Container Doctor, an expert at diagnosing broken Docker containers.
Use the tools to gather evidence before answering. Typical approach:
1. list_containers to see what exists and which containers are not healthy.
2. get_logs on the broken container to find the error message.
When you know the cause, reply WITHOUT calling a tool, in this format:
ROOT CAUSE: <one sentence>
EVIDENCE: <the log line or status that proves it>
FIX: <concrete steps>"""


def print_message(msg: dict) -> None:
    """Pretty-print one message so we can SEE the conversation the model sees."""
    role = msg["role"].upper()
    print(f"\n----- {role} -----")
    if msg.get("content"):
        print(msg["content"])
    for call in msg.get("tool_calls") or []:
        fn = call["function"]
        print(f"[tool call] {fn['name']}({json.dumps(fn['arguments'])})")


def run_agent(question: str) -> str:
    # The whole "memory" of the agent is this list. Every turn we send ALL of it.
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    for m in messages:
        print_message(m)

    for step in range(1, MAX_STEPS + 1):
        response = ollama.chat(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            options={"temperature": 0},  # deterministic-ish: same input, same diagnosis
        )
        # Convert the pydantic Message object to a plain dict for printing/storing.
        assistant_msg = response.message.model_dump(exclude_none=True)
        messages.append(assistant_msg)
        print_message(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls")
        if not tool_calls:
            # No tool requested = the model is giving its final answer.
            return assistant_msg.get("content", "")

        # The model asked for one or more tools. WE execute them, not the model.
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            result = TOOLS[name](**args)
            tool_msg = {"role": "tool", "tool_name": name, "content": result}
            messages.append(tool_msg)
            print_message(tool_msg)

    return f"Stopped after {MAX_STEPS} steps without a final answer."


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Something is broken in my Docker setup. What is wrong?"
    answer = run_agent(question)
    print("\n===== FINAL ANSWER =====")
    print(answer)
