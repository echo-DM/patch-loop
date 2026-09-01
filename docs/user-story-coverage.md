# PatchLoop MVP user-story coverage

This matrix accounts for all 54 stories in `.scratch/patchloop-mvp/spec.md`.
“Covered” means an implementation, deterministic test, documentation boundary,
or explicit manual-smoke path exists. It does not claim that Ticket 14's live
Gemini/GitHub release acceptance has already run.

| Story | Status | Evidence |
| ---: | --- | --- |
| 1 | Covered | Ticket 02; `test_github_authorization.py`; reusable caller label filter. |
| 2 | Covered | Ticket 02; write/admin and lower-permission authorization scenarios. |
| 3 | Covered | Ticket 02; frozen Issue/comment fixtures in `test_github_authorization.py`. |
| 4 | Covered | Ticket 02; `GitHubIssueAdapter` task normalization. |
| 5 | Covered | Ticket 02; trusted maintainer-comment acceptance coverage. |
| 6 | Covered | Ticket 02; external comments remain reference material. |
| 7 | Covered | Ticket 02; rejected Gate never starts Agent or model evaluation. |
| 8 | Covered | Ticket 09; per-repository/per-Issue workflow concurrency contract. |
| 9 | Covered | Ticket 12; one controlled branch and Draft PR per Issue. |
| 10 | Covered | Ticket 12; active PR receives an ordinary appended commit. |
| 11 | Covered | Tickets 10/12; non-force branch updates and history assertions. |
| 12 | Covered | Ticket 12; conflict state stops and reports without mutation. |
| 13 | Covered | Ticket 12; closed-unmerged PR blocks recreation. |
| 14 | Covered | Ticket 12; merged PR ends automated work. |
| 15 | Covered | Ticket 09; `examples/patchloop-caller.yml` and caller contract test. |
| 16 | Covered | Ticket 09; reusable workflow runs in caller repository context. |
| 17 | Covered | Tickets 09/10; caller `GITHUB_TOKEN`, no PAT/App requirement. |
| 18 | Covered | Tickets 09/10; Gate, Prepare, Agent, Verifier, Publish permissions. |
| 19 | Covered | Tickets 08/09; Gemini key appears only in Agent execution step. |
| 20 | Covered | Tickets 01/08/09/11; report/artifact/feedback redaction tests. |
| 21 | Covered | Ticket 09; named `PATCHLOOP_GEMINI_API_KEY` caller mapping. |
| 22 | Covered | Ticket 08; validated configuration model recorded in Run Report. |
| 23 | Covered | Ticket 08; explicit Gemini Developer API and non-Vertex adapter. |
| 24 | Covered | Ticket 04; controlled on-demand list/search/read tools. |
| 25 | Covered | Tickets 04/08; six declared tools and unknown-tool rejection. |
| 26 | Covered | Tickets 04/06; only predeclared setup/check commands execute. |
| 27 | Covered | Ticket 06; repository chooses verifier image and commands. |
| 28 | Covered | Ticket 06; disposable-copy side effects do not alter the patch. |
| 29 | Covered | Tickets 05/06; timeout, memory, PID, lifecycle, and output bounds. |
| 30 | Covered | Ticket 07; failed-check evidence drives a bounded repair turn. |
| 31 | Covered | Tickets 05/07; maximum three cumulative iterations. |
| 32 | Covered | Ticket 05; tool, file, diff, and wall-clock budget scenarios. |
| 33 | Covered | Ticket 05; configuration lowers but cannot raise safety ceilings. |
| 34 | Covered | Tickets 05/10; legal patch publishes when checks fail. |
| 35 | Covered | Tickets 05/10; budget exhaustion remains explicit in report/PR. |
| 36 | Covered | Tickets 03/11; bounded clarification feedback without a PR. |
| 37 | Covered | Tickets 01/11; no-change report/feedback without branch or PR. |
| 38 | Covered | Tickets 06/11; configuration, setup, and infrastructure categories. |
| 39 | Covered | Ticket 10; Draft PR body reports intent, files, checks, and budgets. |
| 40 | Covered | Tickets 01/10/11; reports contain observable results, not reasoning. |
| 41 | Covered | Ticket 10; failed checks are prominent and PR remains Draft. |
| 42 | Covered | Ticket 10; publication context links Draft PR to source Issue. |
| 43 | Covered | Ticket 11; actionable Issue feedback for every no-patch outcome. |
| 44 | Covered | Tickets 02/04/11; untrusted text cannot change policy and is escaped. |
| 45 | Covered | Ticket 01 onward; acceptance tests use public `patchloop.core.run`. |
| 46 | Covered | Tickets 01/06/08/10; model, verifier, and GitHub adapter seams. |
| 47 | Covered | Ticket 06; real `alpine:3.22` fixture Docker acceptance scenarios. |
| 48 | Covered | Tickets 08/13/14; explicit skipped-by-default Gemini and Smoke repository paths. |
| 49 | Covered | Tickets 01/09/11; version-1 sanitized Run Report for terminal outcomes. |
| 50 | Covered | Tickets 09/12; no remote checkpoint; durable branch/PR restart state. |
| 51 | Covered | Tickets 09/10; Publish accepts only integrity-checked declared artifacts. |
| 52 | Covered | Tickets 09/10; privileged and unprivileged jobs are visible in workflow. |
| 53 | Covered | Tickets 01/13; installed `patchloop run` and local examples. |
| 54 | Covered | Tickets 01/09/13; CLI and workflow both call `run` with one config/report contract. |

## Release interpretation

Stories 1–54 are covered by the implementation, acceptance tests, documentation,
or explicit manual boundary above. The credentialed proof that a candidate SHA
works against real Gemini and a Smoke repository remains Ticket 14. Until
that succeeds, describe the MVP as offline-validated and installable from a
candidate SHA, not as live release-accepted.
