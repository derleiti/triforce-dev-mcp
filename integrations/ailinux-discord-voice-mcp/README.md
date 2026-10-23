# AILinux Nova Discord Voice MCP (RC0.1)

Nova's Discord bot speaks with the German Seraphina neural voice.
DAVE E2EE is mandatory for current Discord voice channels.
The Discord user explicitly starts the session with /nova join.
No auto-join. Voice receive/transcription is NOT active in RC0.1.

## Existing credentials

Reads the existing GuildControl JSON and token from
~/.config/guildcontrol/discord-bot-token.
Never copy the token into the repository or print it to a log.
Guilds are restricted to readScope.guildIds.

## Operations

- npm ci (after the lockfile is generated)
- python3 -m venv .venv && .venv/bin/pip install edge-tts
- npm run doctor (does not connect to Discord voice)
- npm run register (upserts exactly one guild-scoped /nova command)
- npm start (connects bot gateway but NOT voice)
- node mcp.mjs (stdio MCP server, controls bot over local UNIX socket)

Discord: /nova join, /nova say text:..., /nova status, /nova leave.
Commands require Discord Administrator and configured GuildControl guild.
MCP tools: discord_voice_status, discord_voice_join(channel_id),
discord_voice_say(text), discord_voice_leave.

## Security

- No recording or ambient listening. Join only on request.
- Minimal permissions: View Channel, Connect, Speak.
- MCP local socket is within a 0700 state dir and has 0600 permissions.
- Commands respond ephemerally; voice audio is heard by channel members.
- Never use self-bot user credentials.
- TTS calls a third-party neural speech synthesis service.
- No service is installed or enabled by default.

## Rollback

Stop the bot process, delete this integration directory, and optionally
remove the guild-scoped /nova command. Existing GuildControl files
are not edited.

## Next phase

Opt-in voice capture, DAVE-safe audio receive, German STT,
TriForce conversational response, and a manual kill switch.
Discord voice receive is unofficial and may break upstream.

## Webhook routing

Discord application 1562939604829470741 has its Interactions Endpoint URL set
to https://api.ailinux.me/discord. Slash commands arrive on the signed HTTPS
webhook, not the bot's gateway InteractionCreate listener. TriForce routes:
- app/routes/discord_interactions.py (signature checks and /nova dispatch)
- app/routes/discord_voice_bridge.py (owner check, async remote RPC, followup)

The voice gateway on zombie-pc remains the DAVE transport endpoint. Only the
Discord guild owner may control the voice in RC0.1. Do not remove Discord's
Interaction Endpoint URL as that would break existing integrations.

### Service

- systemctl --user status ailinux-nova-discord-voice.service
- systemctl --user start ailinux-nova-discord-voice.service
- systemctl --user stop ailinux-nova-discord-voice.service

Service is installed but disabled for autostart. GuildControl settings and
token were not altered. npm run unregister intentionally deletes only /nova.

### Manual end-to-end voice test

1. Join a normal voice channel in the configured guild as its owner.
2. Run /nova status, then /nova join.
3. Confirm a greeting plays via DAVE, then /nova say text:Hallo!
4. Run /nova leave and verify Nova disconnects.

RC0.1 does NOT listen to people's microphones or transcribe voice. If the old
placeholder still responds to /nova, check the TriForce HTTPS webhook deployment.
