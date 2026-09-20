# AILinux crash, diagnostics and startup self-test reporting

## Architecture

AILinux clients never contain SMTP credentials. User-facing apps and headless services submit bounded, redacted JSON over HTTPS to:

`POST https://api.ailinux.me/v1/bugs/report`

TriForce persists every accepted report in its local bug-report SQLite store and then sends a notification/archive mail from `submit@ailinux.me` to `bugs@ailinux.me`. `submit@ailinux.me` is a server-side alias, not a client login identity.

The database is the triage source of truth. Mail is the human-readable notification/archive path.

## Report types

- `crash`: uncaught process/thread exception.
- `error`: runtime error that reached a global browser/runtime handler.
- `selftest`: startup self-test failure.
- `manual`: user opened the diagnostics/report UI, optionally entered a description and pressed Submit.

Automatic reports are fingerprinted. Repeated identical automatic failures inside the dedupe window are persisted but do not generate an e-mail storm. Manual reports remain distinct events. A server-wide hourly mail ceiling protects the mailbox from public-endpoint abuse; reports above that ceiling stay persisted with `mail_status=rate_limited` for MCP triage.

## Privacy contract

Reports may contain app/repo/version, OS/architecture, bounded app-owned runtime log lines, exception type/message/stack, startup check results and an optional user message.

Clients and TriForce both redact common secrets, including authorization headers, bearer values, API keys, access/refresh/workspace/resume tokens, passwords, pair codes and secret-bearing URL query parameters. The raw install ID is hashed server-side.

Applications must not attach user workspace files, clipboard contents, screen captures, OCR text, chat/message contents, artwork, wallet/portfolio/order data or arbitrary logcat/system logs to an automatic report. Product-specific diagnostics UIs state the relevant exclusions.

## Offline behavior

Native/web clients keep a small local pending queue and retry later. Queue size and log size are bounded. Queued material is redacted before persistence.

## Startup self-test

Each supported runtime performs a cheap local self-test during startup. A successful self-test is logged locally but is not mailed. Only a failed self-test creates a report. Self-tests must not make paid provider calls or mutate user content.

## Admin triage

TriForce Dev MCP exposes admin-only tools:

- `bug_reports_list`
- `bug_report_get`
- `bug_report_stats`
- `bug_report_status`

Statuses are `new`, `triaged`, `resolved` and `ignored`. These tools operate on the persisted report store rather than parsing the mailbox.

## Mail routing

- envelope/application sender: `submit@ailinux.me`
- destination mailbox: `bugs@ailinux.me`
- client SMTP credentials: none
- SMTP relay credentials/configuration: server-side only, outside distributable clients

## Public ingestion safeguards

The ingestion route is intentionally unauthenticated so an app can report login/startup failures. It is rate-limited, schema-bounded, performs recursive server-side redaction, does not persist raw source IPs, fixes sender/recipient server-side and never accepts arbitrary e-mail destinations.
