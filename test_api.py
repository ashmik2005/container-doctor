"""Offline tests for the REST API. The agent is replaced by a fake, so no model is needed
(Docker must be running for the /containers test).

Run:  .venv/bin/python test_api.py
"""
import time

from fastapi.testclient import TestClient

import api

client = TestClient(api.app)


def fake_run_agent(question, tool_schemas, approve, verbose):
    """Stands in for the real agent: instant, deterministic, and records what it was given."""
    names = [s["function"]["name"] for s in tool_schemas]
    return {"answer": f"ROOT CAUSE: fake diagnosis for '{question}'", "steps": 2,
            "tools_used": ["list_containers"], "seconds": 0.1,
            "messages": [{"role": "user", "content": question}],
            "_saw_runbook_tool": "search_runbooks" in names,
            "_approve_result": approve("restart_container", {"container": "x"})}


def wait_for(job_id: str, timeout: float = 5.0) -> dict:
    """Poll like a real client would, until the job leaves queued/running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/diagnoses/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_create_and_poll_diagnosis():
    r = client.post("/diagnoses", json={"question": "My web app is down"})
    assert r.status_code == 202
    job = r.json()
    assert job["status"] in ("queued", "running", "done")
    assert r.headers["location"] == f"/diagnoses/{job['id']}"
    done = wait_for(job["id"])
    assert done["status"] == "done"
    assert done["result"]["answer"].startswith("ROOT CAUSE: fake diagnosis")
    assert done["messages"] is None  # transcript hidden by default


def test_transcript_on_request():
    job_id = client.post("/diagnoses", json={"question": "db is down"}).json()["id"]
    wait_for(job_id)
    job = client.get(f"/diagnoses/{job_id}", params={"include_messages": True}).json()
    assert job["messages"] == [{"role": "user", "content": "db is down"}]


def test_use_runbooks_flag_and_actions_denied():
    seen = {}

    def spy(question, tool_schemas, approve, verbose):
        out = fake_run_agent(question, tool_schemas, approve, verbose)
        seen.update(out)
        return out

    api.run_agent = spy
    try:
        wait_for(client.post("/diagnoses", json={"question": "abc", "use_runbooks": False}).json()["id"])
        assert seen["_saw_runbook_tool"] is False
        assert seen["_approve_result"] is False  # the API never approves state-changing tools
    finally:
        api.run_agent = fake_run_agent


def test_validation_errors():
    assert client.post("/diagnoses", json={"question": "hi"}).status_code == 422       # too short
    assert client.post("/diagnoses", json={"question": "x" * 501}).status_code == 422  # too long
    assert client.post("/diagnoses", json={}).status_code == 422                       # missing


def test_unknown_job_is_404():
    assert client.get("/diagnoses/doesnotexist").status_code == 404


def test_failed_job_reports_error():
    def broken(*args, **kwargs):
        raise ConnectionError("ollama not running")

    api.run_agent = broken
    try:
        job = wait_for(client.post("/diagnoses", json={"question": "anything"}).json()["id"])
        assert job["status"] == "failed" and "ollama not running" in job["error"]
    finally:
        api.run_agent = fake_run_agent


def test_list_diagnoses_newest_first():
    jobs = client.get("/diagnoses").json()
    times = [j["created_at"] for j in jobs]
    assert len(jobs) >= 3 and times == sorted(times, reverse=True)


def test_containers_endpoint_returns_json_list():
    r = client.get("/containers")
    assert r.status_code == 200 and isinstance(r.json(), list)


if __name__ == "__main__":
    api.run_agent = fake_run_agent
    tests = [f for name, f in dict(globals()).items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed.")
