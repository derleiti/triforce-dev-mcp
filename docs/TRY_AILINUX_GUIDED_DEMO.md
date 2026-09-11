# Try AILinux Guided Demo

## Goal
Turn the existing Nova playground into a truthful, low-friction onboarding path for first-time visitors. The guided tour and the free playground are the same UI and use the same WordPress -> TriForce -> provider/tool path.

## Product principles
- Real operations only: progress is based on backend-reported activity, never simulated tool calls.
- No account required for the initial tour.
- Least privilege: anonymous demo exposes chat, `web_search`, and `crawl_url`; agent execution remains out of the public tour for v1.
- Same-session handoff: finishing the tour does not destroy chat history or replace the playground.
- Safe public crawl: reject local/private targets, validate redirect hops, limit response size and accepted content types.
- Bounded research: server constrains `max_results` and applies a broad API abuse limit; product-level guest quotas can be tightened independently later.

## Guided flow
1. **Talk to Nova** — completes only after a successful model activity event.
2. **Research the web** — completes only after a successful `web_search` event with results.
3. **Read a website** — completes only after a successful `crawl_url` event.
4. **Understand the stack** — explains the verified activity trail: Browser -> WordPress -> TriForce -> model/tools.
5. **Continue with AILinux** — removes the guided constraint and offers the account handoff while preserving the live playground.

The tour supports Skip and Restart. Progress and the recent activity trail are stored in `sessionStorage`, so no tracking cookie or account is required.

## Activity contract
`POST /v1/nova/playground` returns an `activity` array. Examples:

- model: `{type: "model", name: "mistral/...", status: "completed"}`
- search: `{type: "tool", name: "web_search", status: "completed", result_count: 4}`
- crawl: `{type: "tool", name: "crawl_url", status: "completed", url: "https://..."}`

The frontend treats these events as evidence. A normal response cannot satisfy the search or crawl tutorial steps without the matching tool event.

## Funnel events
The UI emits DOM `nova:demo-event` events so analytics can be connected without coupling the feature to one analytics vendor:

`demo_started -> first_answer -> research_completed -> website_read -> tutorial_step_completed -> tutorial_completed -> signup_clicked`

Server-side signup completion and client pairing should be joined later using account/client telemetry rather than browser claims.

## Security / abuse controls
- `max_results`: 1..8 server-side.
- Public fetch only supports HTTP(S), globally routable DNS targets, textual content and <=2 MB response bodies.
- Redirects are manual and every hop is revalidated.
- Per-visitor anonymous rate limiting remains a follow-up: the installed `fastapi-limiter` is incompatible with FastAPI nested `_IncludedRouter` entries, and WordPress currently proxies visitor calls server-side. A correct quota must use trusted end-to-end visitor identity rather than the shared backend peer.
- Public Agent mode is not linked or exposed by the guided demo until authenticated quota/cost controls are explicit.

## Acceptance criteria for v1
- Existing `[ailinux_ai_playground]` remains functional.
- New `[ailinux_try_demo]` renders the guided layer and existing playground together.
- Steps 1-3 cannot auto-complete from fake/local timers.
- Search/crawl sources remain visible in chat.
- Activity reflects the model/tool actually used.
- Skip, restart, reload/resume and mobile layout work.
- Finishing the tour leaves the same chat usable.
- PHP, JavaScript and Python syntax checks pass; backend regression tests pass; live chat/search/crawl smoke tests pass.

## Phase 2 candidates
- Authenticated, tightly budgeted agent demo.
- Model comparison / arena mode.
- Ephemeral isolated Linux sandbox with strict resource/network policy.
- Persisted onboarding across devices for signed-in users.
- Funnel dashboard based on server-side account/client events.
