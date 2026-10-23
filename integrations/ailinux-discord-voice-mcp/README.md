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

## RC0.2: spoken conversation (owner-only)

- Discord DAVE voice receive: uses selfDeaf=false and subscribes exclusively to
  the verified guild owners audio. Other members are ignored.
- When /nova join completes, the offline faster-whisper small model is warm.
- Wake word: start an utterance with Nova or Hey Nova, followed by your question.
- The recognized text after the wake word is sent via SSH to a restricted
  TriForce chat script on the AILinux server. The model and API keys stay there.
- Responses are converted to speech with Seraphina and played in the channel.
- /nova quiet pauses reception; /nova listen resumes; /nova leave disconnects.
- /nova status shows listening, wake word and diagnostic counters.
- Temporary WAV clips are removed after transcription. No permanent audio
  recording, no raw transcript logging and no microphone access when disconnected.
- Conversation history is now stored on the TriForce host (private SQLite),
  with up to 200 successful turns retained for 30 days.
- This is a SEPARATE TriForce AI chat (Groq gpt-oss-20b), not this particular
  ChatGPT conversation or its full memories.
- Audio receive on Discord is not an officially stable API. Test actual DAVE
  receive with the guild owners microphone, not just synthesized Opus packets.

## Diagnostics / rollback

Use the local socket control/status to see speaking_events, audio_packets,
transcribed, wakes, replies and errors. The bot cannot verify capture of
actual Discord mic input until the user speaks after it has joined.
Version 0.1 files are backed up under
~/.local/state/ailinux-nova-voice/backup-pre-listening-20261023.
To roll back, stop the user service, restore bot.mjs, mcp.mjs, and
remote_client.py from this backup, restore package metadata, then restart.

## RC0.3: reliable conversational memory and segmented wake word

- Wake-word-only clips arm a follow-up input window for 18 seconds. The bot
  never substitutes a fake question when only Nova was heard.
- After a completed answer, the authorized speaker can ask a short follow-up
  for another 35 seconds without repeating Nova.
- Real questions are sent over authenticated SSH to TriForce, which loads
  private SQLite chat history server-side and calls a verified Groq model.
- A second Groq model is attempted if the primary request fails. An error
  response is never written to the conversation history, and repeat error
  notices are throttled.
- The remote server stores only recognized questions and successful answers,
  not raw microphone audio or ignored speech clips.
- History survives disconnects and service restarts. Per-owner history is
  limited to 200 turns with a 30-day retention window. Hub DB permissions 0600.
- /nova history shows the recent exchanges privately. /nova forget deletes
  the saved Nova Discord conversation ONLY when explicitly invoked.
- /nova status reports the conversation count alongside the listening state.
- The Discord voice session is powered by a separate TriForce chat provider,
  not this exact ChatGPT conversation or its saved context.

### Rollback

Voice files prior to this patch are in
~/.local/state/ailinux-nova-voice/pre-conversation-fix-20261023/.
The TriForce script and webhook backups are on the ailinux host under the same
snapshot name. New history is never committed to Git.
