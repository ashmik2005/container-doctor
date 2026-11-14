# Manual audit of eval run v1

**What this is:** after `eval.py` scored run v1 with keywords, every one of the
30 final answers was read by hand (by Claude, the AI assistant that built this
project) and compared with the expected root cause. This is a *judgement*, not
a measurement by `eval.py`; the full answers are in `eval_logs/` so you can
check every verdict yourself. Verdict = does the **ROOT CAUSE** identify the
real cause? (correct / partial / wrong)

| Scenario | Mode | Keyword score | Manual verdict | Note |
|---|---|---|---|---|
| 01_missing_env_var | no_rag | correct | correct | |
| 01_missing_env_var | rag | correct | correct | |
| 02_oom_killed | no_rag | correct | **wrong** | Root cause says "infinite loop"; memory only appears in the FIX. Keyword false positive. |
| 02_oom_killed | rag | correct | correct | |
| 03_port_conflict | no_rag | wrong | wrong | Investigated the healthy `admin`, ignored `shop` in status `Created`; invented an "entrypoint misconfiguration". |
| 03_port_conflict | rag | correct | correct | Found `port is already allocated` in State.Error (reached the step limit). |
| 04_db_not_ready | no_rag | wrong | wrong | Ran check_port on the host for 5432 and concluded the DB "is not listening". |
| 04_db_not_ready | rag | correct | correct | Matched the startup-race runbook. |
| 05_wrong_volume_path | no_rag | wrong | wrong | Restated the FileNotFoundError, never compared mount paths. |
| 05_wrong_volume_path | rag | correct | correct | Root cause right, but the FIX is backwards ("use /conf instead of /config"). |
| 06_permission_denied | no_rag | correct | partial | Restates "permission denied" without the reason (UID 1000 vs root-owned dir); suggests a restart. |
| 06_permission_denied | rag | correct | correct | chown 1000:1000 fix; also mentions SELinux, which is irrelevant. |
| 07_dns_wrong_host | no_rag | wrong | wrong | Generic "database not reachable / firewall". |
| 07_dns_wrong_host | rag | correct | correct | Hostname `database` does not resolve, service is `db`. Runbook symptom was added after seeing this scenario (leakage). |
| 08_crash_loop | no_rag | correct | correct | Says WORKERS "not set" but evidence correctly says `four` is not numeric. |
| 08_crash_loop | rag | correct | correct | |
| 09_healthcheck_failing | no_rag | wrong | wrong | "Container is still starting up." |
| 09_healthcheck_failing | rag | wrong | wrong | Suspected healthcheck config but never found the 8080 vs 80 mismatch; also blamed a database race. |
| 10_wrong_command | no_rag | correct | correct | |
| 10_wrong_command | rag | correct | correct | |
| 11_nginx_config_error | no_rag | correct | correct | Quotes `unexpected "}" ... nginx.conf:7`. |
| 11_nginx_config_error | rag | wrong | wrong | **RAG hurt:** followed the "exits immediately" runbook and dropped the nginx error. |
| 12_binds_localhost | no_rag | wrong | wrong | Saw `Serving HTTP on 127.0.0.1` but did not understand it. |
| 12_binds_localhost | rag | correct | correct | |
| 13_wrong_db_password | no_rag | correct | correct | |
| 13_wrong_db_password | rag | wrong | wrong | **Agent bug:** the correct answer was given, then a nudge produced an empty reply that replaced it. Fixed in v2. |
| 14_disk_full | no_rag | correct | partial | "Out of disk space" but blames the host disk, not the 1 MB tmpfs. |
| 14_disk_full | rag | correct | correct | Identifies the tmpfs, but its EVIDENCE invents "OOMKilled true". |
| 15_missing_module | no_rag | correct | correct | Contains a tool call written as plain text inside the answer. |
| 15_missing_module | rag | correct | correct | Suggests `apk add requests`, which is wrong (it is a pip package). |

**Agreement:** in `rag` mode the keyword score and the manual verdict agree on
all 15. In `no_rag` mode they disagree on 3 of the 9 keyword-"correct"
answers (02 wrong, 06 and 14 only partial), so keyword scoring is too generous
there. Recurring problem in both modes: the FIX is often weak or wrong even
when the ROOT CAUSE is right (restart suggestions, backwards fixes).
