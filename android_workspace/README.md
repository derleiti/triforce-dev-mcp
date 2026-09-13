# AILinux Workspace for Android

Native Android companion for `https://api.ailinux.me/v1/mcp`.

- SAF `ACTION_OPEN_DOCUMENT_TREE` workspace selection with persisted URI permission.
- User-visible `specialUse` foreground service owns the MCP WebSocket.
- Same TriForce workspace lease/resume/handoff protocol as browser/Desktop helpers.
- Browser handoff uses only a 90-second one-shot ticket; durable resume credentials never enter URLs.
- Read-only or read/write capability advertisement.
- Automatic exponential reconnect while the user has explicitly enabled the executor.

## Build

Requires JDK 17 and Android SDK 35:

```bash
cd android_workspace
gradle :app:testDebugUnitTest :app:assembleDebug
```

Release automation publishes `AILinux-Workspace-latest.apk` into `releases/android/` for the backend download route.
