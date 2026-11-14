"""Stage 0 smoke test: prove the local AI stack works before building on it.

Checks two things against the local Ollama server (http://localhost:11434):
  1. the chat model answers a prompt
  2. the embedding model turns text into a vector

Run:  .venv/bin/python smoke_test.py
"""
import time

import ollama

CHAT_MODEL = "qwen2.5:3b"
EMBED_MODEL = "nomic-embed-text"


def test_chat():
    start = time.time()
    # ollama.chat sends one HTTP POST to /api/chat; the reply is the model's next message.
    response = ollama.chat(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": "In one sentence, what is a Docker container?"}],
    )
    elapsed = time.time() - start
    print(f"[chat] {CHAT_MODEL} answered in {elapsed:.1f}s:")
    print("      ", response.message.content.strip())


def test_embed():
    # ollama.embed returns one vector per input string.
    response = ollama.embed(model=EMBED_MODEL, input="container exited with code 137")
    vector = response.embeddings[0]
    print(f"[embed] {EMBED_MODEL} -> vector of length {len(vector)}")
    print("        first 5 numbers:", [round(x, 4) for x in vector[:5]])


if __name__ == "__main__":
    test_chat()
    test_embed()
    print("Smoke test passed.")
