# Manual audit of eval run v2 (the headline run)

Same method as `manual_audit_v1.md`: every final answer was read by hand
(by Claude, the AI assistant that built this project) and its ROOT CAUSE
compared with `expected.json`. This is a **judgement, not an `eval.py`
measurement**; check it against the answers in `eval_logs/`.

| Scenario | no_rag keyword | no_rag manual | rag keyword | rag manual | Notes |
|---|---|---|---|---|---|
| 01_missing_env_var | yes | correct | yes | correct | |
| 02_oom_killed | no | wrong | yes | correct | no_rag: "infinite loop". rag: OOM correct, but calls 137 SIGTERM (it is SIGKILL). |
| 03_port_conflict | no | wrong | yes | correct | no_rag again investigated the running container, ignored the `Created` one. |
| 04_db_not_ready | no | wrong | yes | correct | |
| 05_wrong_volume_path | no | wrong | yes | **partial** | rag mentions the `/conf` mount and the `/config` error and says the mount path is wrong, but never states that they differ. |
| 06_permission_denied | yes | **partial** | no | wrong | no_rag restates "permission error" without the UID reason. rag: **misled by the `exits_immediately` runbook** (searched "uploader container Exited (1)"). |
| 07_dns_wrong_host | no | wrong | yes | correct | |
| 08_crash_loop | yes | correct | yes | correct | rag answer was rescued by the v2 fallback fix. |
| 09_healthcheck_failing | no | wrong | no | partial | rag: "healthcheck issue" but never finds 8080 vs 80. |
| 10_wrong_command | yes | correct | yes | correct | |
| 11_nginx_config_error | yes | correct | yes | correct | rag answer was rescued by the v2 fallback fix. |
| 12_binds_localhost | no | wrong | yes | correct | |
| 13_wrong_db_password | yes | correct | yes | correct | |
| 14_disk_full | yes | **partial** | no | wrong | no_rag blames the host disk, not the 1 MB tmpfs. rag hallucinates "OOMKilled". |
| 15_missing_module | yes | correct | yes | correct | |

Manual totals (judgement): **no_rag** 6 correct, 2 partial, 7 wrong;
**rag** 11 correct, 2 partial, 2 wrong.
Keyword score vs manual: the keyword score counted 3 "partial" answers as
correct (no_rag 06 and 14, rag 05). It never counted a manually "wrong"
answer as correct in v2 (it did once in v1, 02 no_rag).

## Retrieval failure pattern (verified in the logs)
In both runs, whenever `exits_immediately.md` was the **top** search result
the answer was wrong (v1 11 rag, v2 06 rag). In both cases the model searched
for the container *status* (`"Exited (1)"`) instead of the error message.
Embeddings barely separate "Exited (1)" from "Exited (0)", so the runbook about
clean exit-0 shutdowns wins. Possible fixes (not applied, to avoid tuning to
the test set): hybrid keyword + vector search, or rejecting status-only
queries in `search_runbooks`.
