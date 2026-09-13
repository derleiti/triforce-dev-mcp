# AILinux Workspace Browser

Minimal Chromium/Electron shell for the public TriForce workspace page.

It is intentionally not a general-purpose browser. It keeps a persistent Chromium profile,
allows File System Access only for `https://api.ailinux.me`, disables renderer Node.js access,
keeps background timers unthrottled, prevents application suspension while running, and hides
to the tray rather than terminating when the window is closed.

## Development

```bash
npm install
npm run check
npm start
```

The custom protocol is `ailinux-workspace://`. A pair code can be passed as:

```text
ailinux-workspace://connect?pair_code=ABCD-1234-EF56
```

Only `https://api.ailinux.me/v1/mcp...` is accepted as a top-level target.
