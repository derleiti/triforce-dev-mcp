# AILinux Workspace Desktop Helper

> **Canonical Helper repository:** `derleiti/ailinux-helper` (current release **2.90.13**). This in-tree desktop workspace helper is retained for compatibility/history. New cross-platform Helper releases and branding live in the dedicated repository.

Cross-platform Electron shell for `https://api.ailinux.me/v1/mcp`. It keeps the trusted MCP workspace page in a persistent Chromium profile and remains available from the system tray when the window is closed.

## Platforms

- Linux: AppImage and Debian package
- Windows: NSIS installer and portable executable
- macOS: DMG and ZIP

The same `ailinux-workspace://` deep-link contract is used by Android and desktop. Only `https://api.ailinux.me/v1/mcp` is allowed inside the application; popups and external navigation are denied. Node integration is disabled and the renderer is sandboxed.

## Development

```bash
npm ci
npm run check
npm start
```

## Build

```bash
npm run dist:linux
npm run dist:win
npm run dist:mac
```

Cross-platform release builds are produced by `.github/workflows/workspace-desktop-helper.yml`. Closing the window hides it to the tray; use **Quit AILinux Workspace** from the tray to terminate it.
