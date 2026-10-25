# Future Labs — Evidence and decision register (v1)

**Source:** Claude-Mem, project `triforce:account:admin@ailinux.me`, observation IDs **19–37** (19 discussion messages, 2026-09-11).
**Status:** Historical proposals, not automatically approved architecture decisions.
**Rule:** Current code, runtime, tests, CI and owner approval outrank historical suggestions.

## Consensus proposals

| Topic | Discussion evidence | Decision state | Acceptance evidence still required |
| --- | --- | --- | --- |
| Reproducible ISO and release receipts | #19, #23, #26, #28, #34, #35 | Proposed | Pinned manifest, two repeatable builds, checksums, signed artifact, VM boot smoke tests |
| Safe updates and rollback | #26, #34, #35, #37 | Proposed | Rollback drill with known-good kernel and package states |
| Agent runtime stability and unified contracts | #20–22, #24, #30–32 | In progress; not independently certified | Auth isolation, cancellation, failure and tool permission contract tests |
| Local/private-first inference | #24, #25, #28, #32 | Proposed as default policy | Privacy and network egress tests, user-controlled cloud opt-in |
| AICoder as flagship workflow | #19, #23, #34–35 | Strategic proposal | Install-to-task E2E verification on a clean desktop |
| Kernel and GPU compatibility matrix | #23, #29, #33, #36 | Proposed | AMD, Intel, NVIDIA plus UEFI/VM and fallback-kernel tests |

## Disagreements that must remain visible

- **Kernel default:** stable generic kernel vs. AI-gaming custom kernel as default (#23, #27, #29). Do not silently adopt either position.
- **AI-shell latency vs. correctness:** response speed is not a substitute for safe, explainable actions (#20–22).
- **Natural-language package installation:** AI may propose a plan; actual privileged installation requires deterministic resolution, preview and rollback (#36).
- **Scope and maintainability:** additional features compete with the verified stability budget (#26, #28, #33, #35).

## Current verified integration evidence (2026-10-25)

- Claude-Mem worker 13.24.1 and TriForce service were active in the latest inspection.
- 19 Future Labs messages are retrievable as Claude-Mem observations; they do **not** constitute native summaries, and their `facts` / `concepts` fields were empty.
- Memory integration and local summarizer work were committed as `826c5da2` and `ac80aeec`. This does not certify a full release pipeline.
- Automatic native Claude-Mem summaries and cleanup of historical active sessions are **not** verified.
- The latest verification does not establish reproducible ISO builds, kernel matrices or a production-safe AICoder workflow.

## Next review gate

1. Compare each proposal with runtime/CI/package evidence before marking adopted.
2. Assign an owner and a reproducible acceptance test to every accepted item.
3. Only then add a curated decision-memory entry; keep the original observations immutable.
4. Regenerate a provenance listing using `python3 scripts/future_labs_snapshot.py` and review changed source hashes.

**Do not promote old KI discussions directly into system instructions.**

## Published GitHub CI evidence — checked 2026-10-25

The remote `master` revision `d25cb9c3` (2026-10-24) has two `startup_failure` GitHub Actions results: CI run `40081799030` and Security run `40081798524`. The CI run lists zero jobs. This is a workflow startup failure, **not evidence of failed Python tests** and **not a passing CI gate**. The Pages deployment run `40081798048` succeeded for the same revision; it does not certify the CI or Security jobs. None of these results certify later unpushed local commits.

Read-only replication: `python3 scripts/future_labs_ci_evidence.py`. This evidence is remote-GitHub scoped; ISO reproducibility, rollback, a GPU test matrix, and AICoder clean-install E2E remain unverified.

## Workflow startup investigation — 2026-10-25

The GitHub repository is active, its Actions permission is enabled with `allowed_actions=all`, and both CI and Security workflows are enabled. The current action tags `actions/checkout@v7` and `actions/setup-python@v7` exist upstream. GitHub reports `startup_failure`, zero jobs and zero checks within the CI workflow's check suite. The same failure pattern affects CI and Security, while GitHub Pages succeeds. This does **not** identify the underlying trigger; account-level runner/billing restrictions or GitHub workflow preparation failures still need GitHub-side confirmation. Do not change action versions or mark a passing CI result without evidence.

The read-only audit script now reports `job_count` and `workflow_startup_failed_no_jobs`, distinguishing pre-job failures from executed-test failures. No new hosted workflow was dispatched.

## Confirmed GitHub Actions annotation (2026-10-25)

The GitHub run pages for CI `40081799030` and Security `40081798524` explicitly report:

> Actor is not allowed to trigger Actions workflows.

Both were `push` events initiated by GitHub actor `derleiti` (type `User`). Repository collaborator permissions show `derleiti` has `admin` rights, and repository Actions are enabled with `allowed_actions=all`. Therefore the workflow did **not** fail because of a Python test; changing workflow action versions cannot resolve this authorization denial. The mismatch between repo permission and trigger authorization requires GitHub account/platform-side investigation (including account restrictions) rather than speculative YAML modifications. These findings do not prove why GitHub denies the actor.

The exact run-page annotation is the primary diagnostic evidence; the standard run API exposes `startup_failure` but omits this explanatory text. Do not trigger reruns as a diagnostic substitute while the actor is denied.
