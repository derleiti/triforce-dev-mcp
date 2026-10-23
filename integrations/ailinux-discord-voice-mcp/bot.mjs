import { Client, GatewayIntentBits, Events, MessageFlags, ChannelType, PermissionFlagsBits, REST, Routes, SlashCommandBuilder } from 'discord.js';
import { joinVoiceChannel, getVoiceConnection, VoiceConnectionStatus, entersState, createAudioPlayer, createAudioResource, AudioPlayerStatus, NoSubscriberBehavior, generateDependencyReport } from '@discordjs/voice';
import { createServer, createConnection } from 'node:net';
import { existsSync, mkdirSync, readFileSync, statSync, chmodSync, unlinkSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { homedir, tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import {attachConversation} from './voice_conversation.mjs';

const ROOT = dirname(new URL(import.meta.url).pathname);
const GCFG = JSON.parse(readFileSync(join(homedir(), '.config/guildcontrol/guildcontrol.json'), 'utf8'));
const GUILD_IDS = GCFG.readScope?.guildIds || [];
const GUILD_ID = GUILD_IDS.length === 1 ? String(GUILD_IDS[0]) : '';
const APP_ID = String(GCFG.identity?.applicationId || '');
const TOKEN_PATH = process.env.NOVA_DISCORD_TOKEN_PATH || join(homedir(), '.config/guildcontrol/discord-bot-token');
const STATE_DIR = process.env.NOVA_VOICE_STATE_DIR || join(homedir(), '.local/state/ailinux-nova-voice');
const SOCKET = join(STATE_DIR, 'control.sock');
const PYTHON = process.env.NOVA_TTS_PYTHON || join(ROOT, '.venv/bin/python');
const VOICE = process.env.NOVA_TTS_VOICE || 'de-DE-SeraphinaMultilingualNeural';
const sessions = new Map();
let server = null;
let client = null;
let createdSocket = false;

const command = new SlashCommandBuilder()
  .setName('nova')
  .setDescription('Nova hört und spricht im Discord-Sprachkanal')
  .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
  .addSubcommand(sub => sub.setName('join').setDescription('Nova kommt in deinen aktuellen Sprachkanal'))
  .addSubcommand(sub => sub.setName('say').setDescription('Nova spricht einen Satz mit Seraphina')
    .addStringOption(opt => opt.setName('text').setDescription('Was soll Nova sagen?').setRequired(true).setMaxLength(400)))
  .addSubcommand(sub => sub.setName('talk').setDescription('Freies Sprachgespräch ohne Nova-Aktivierungswort'))
  .addSubcommand(sub => sub.setName('listen').setDescription('Auf das Aktivierungswort Nova hören'))
  .addSubcommand(sub => sub.setName('quiet').setDescription('Spracherkennung pausieren'))
  .addSubcommand(sub => sub.setName("history").setDescription("Letzte gespeicherte Nova Gespraeche anzeigen"))
  .addSubcommand(sub => sub.setName("forget").setDescription("Gespeicherte Nova Historie loeschen"))
  .addSubcommand(sub => sub.setName('status').setDescription('Verbindung und Sprachstatus anzeigen'))
  .addSubcommand(sub => sub.setName('leave').setDescription('Nova verlässt den Sprachkanal'));

function token() {
  const mode = statSync(TOKEN_PATH).mode & 0o777;
  if ((mode & 0o077) !== 0) throw new Error('Bot token file must be private (mode 0600)');
  const value = readFileSync(TOKEN_PATH, 'utf8').trim();
  if (!value) throw new Error('Empty token file');
  return value;
}

function doctor() {
  console.log('Nova Discord Voice RC0.4.1');
  if (!GUILD_ID || !APP_ID) throw new Error('GuildControl must have exactly one allowed guild and app id');
  token();
  const node = process.versions.node;
  console.log('Config: guild and application present, token protected');
  console.log('Runtime: Node ' + node);
  const ffmpeg = spawnSync('ffmpeg', ['-version'], {encoding:'utf8'});
  if (ffmpeg.error || ffmpeg.status !== 0) throw new Error('ffmpeg missing');
  console.log('Audio: ffmpeg available; TTS python exists: ' + existsSync(PYTHON));
  console.log(generateDependencyReport());
  console.log('Doctor completed; no Discord voice connection attempted.');
}

async function unregister() {
  const rest = new REST({version:'10'}).setToken(token());
  const route = Routes.applicationGuildCommands(APP_ID, GUILD_ID);
  const current = await rest.get(route);
  const installed = current.find(x => x.name === 'nova');
  if (!installed) {console.log('No /nova command registered.'); return;}
  await rest.delete(Routes.applicationGuildCommand(APP_ID, GUILD_ID, installed.id));
  console.log('Removed /nova only. Other commands unchanged.');
}

async function register() {
  if (!GUILD_ID || !APP_ID) throw new Error('GuildControl allowlist is not single-guild');
  const rest = new REST({version:'10'}).setToken(token());
  const route = Routes.applicationGuildCommands(APP_ID, GUILD_ID);
  const current = await rest.get(route);
  if (!Array.isArray(current)) throw new Error('Could not read guild commands');
  const previous = current.find(x => x.name === 'nova');
  if (previous) {
    await rest.patch(Routes.applicationGuildCommand(APP_ID, GUILD_ID, previous.id), {body:command.toJSON()});
    console.log('Updated existing /nova (other guild commands untouched).');
  } else {
    await rest.post(route, {body:command.toJSON()});
    console.log('Registered /nova (other guild commands untouched).');
  }
}

function publicStatus() {
  if (!GUILD_ID) return {ok:false, error:'guild not configured'};
  const s = sessions.get(GUILD_ID);
  return {
    ok:true, ready:Boolean(client?.isReady()),
    connected:Boolean(s?.connection && s.connection.state.status === VoiceConnectionStatus.Ready),
    guild_id:GUILD_ID, channel_id:s?.channelId || null,
    channel_name:s?.channelName || null, state:s?.connection?.state?.status || 'none',
    tts_voice:VOICE, ...(s?.voice?.status() || {listening:false,wake_word:'nova',stt_ready:false}), version:'0.4.1'
  };
}

function generateSpeech(text, output) {
  return new Promise((resolvePromise, reject) => {
    const p = spawn(PYTHON, [join(ROOT, 'tts.py'), output, text], {stdio:['ignore','ignore','pipe']});
    let err='';
    const timeout = setTimeout(() => p.kill('SIGKILL'), 35000);
    p.stderr.on('data', data => {if(err.length < 500) err += data.toString().slice(0,500);});
    p.on('error', reject);
    p.on('close', code => {
      clearTimeout(timeout);
      if(code === 0 && existsSync(output) && statSync(output).size >= 3000) resolvePromise();
      else reject(new Error('TTS failed with exit=' + code + ': ' + err.slice(0,200)));
    });
  });
}

async function play(session, text) {
  const file = join(tmpdir(), 'nova-voice-' + randomUUID() + '.mp3');
  session.speakingBack=true;
  try {
    await generateSpeech(text, file);
    if (sessions.get(GUILD_ID) !== session) throw new Error('voice connection closed during TTS');
    session.player.play(createAudioResource(file));
    await entersState(session.player, AudioPlayerStatus.Idle, 45000);
    return {ok:true, spoken_characters:text.length};
  } finally {
    session.speakingBack=false;
    try {if (existsSync(file)) unlinkSync(file);} catch {}
  }
}

function say(text) {
  if (typeof text !== 'string' || text.trim().length < 1 || text.length > 400) throw new Error('Text length must be 1..400');
  const s = sessions.get(GUILD_ID);
  if (!s || s.connection.state.status !== VoiceConnectionStatus.Ready) throw new Error('Nova is not in a voice channel');
  const job = s.pending.then(() => play(s, text.trim()));
  s.pending = job.catch(error => {console.error('Speech failed:',error.message);});
  return job;
}

const WAKE_ACK_FILE = join(STATE_DIR, 'wake-ack.mp3');

// Play a pre-generated, private Seraphina clip for near-immediate feedback.
// Retain the existing TTS path as a fallback if the cache is missing.
function acknowledgeWake() {
  const session=sessions.get(GUILD_ID);
  if (!session || session.connection.state.status !== VoiceConnectionStatus.Ready) {
    return Promise.reject(new Error('Voice channel is not ready'));
  }
  if (!existsSync(WAKE_ACK_FILE) || statSync(WAKE_ACK_FILE).size < 3000) {
    return say('Ja, Zombie?');
  }
  const job=session.pending.then(async () => {
    session.speakingBack=true;
    try {
      session.player.play(createAudioResource(WAKE_ACK_FILE));
      await entersState(session.player, AudioPlayerStatus.Idle, 18000);
      return {ok:true,cache:true};
    } finally {
      session.speakingBack=false;
    }
  });
  session.pending=job.catch(e=>console.error('Wake acknowledgement error:',e.message));
  return job;
}

async function joinChannel(channelId) {
  if (!client?.isReady()) throw new Error('Discord bot gateway is not connected');
  const guild = client.guilds.cache.get(GUILD_ID) || await client.guilds.fetch(GUILD_ID);
  const channel = await guild.channels.fetch(String(channelId));
  if (!channel || channel.guildId !== GUILD_ID || channel.type !== ChannelType.GuildVoice) {
    throw new Error('Channel is not an authorized standard voice channel');
  }
  const me = await guild.members.fetchMe();
  const allowed = channel.permissionsFor(me);
  if (!allowed?.has(PermissionFlagsBits.ViewChannel) ||
      !allowed.has(PermissionFlagsBits.Connect) ||
      !allowed.has(PermissionFlagsBits.Speak)) throw new Error('Bot missing View Channel / Connect / Speak permissions');
  const previous = sessions.get(GUILD_ID);
  if (previous?.channelId === channel.id && previous.connection.state.status === VoiceConnectionStatus.Ready) {
    return {ok:true, channel_id:channel.id, channel_name:channel.name, reused:true};
  }
  if (previous) await leave();
  const connection = joinVoiceChannel({
    channelId:channel.id, guildId:GUILD_ID, adapterCreator:guild.voiceAdapterCreator,
    selfDeaf:false, selfMute:false
  });
  connection.on('error', error => console.error('Voice connection error:', error.message));
  const player = createAudioPlayer({behaviors:{noSubscriber:NoSubscriberBehavior.Pause}});
  player.on('error', error => console.error('Audio player error:', error.message));
  connection.subscribe(player);
  const session = {channelId:channel.id, channelName:channel.name, connection, player, pending:Promise.resolve()};
  sessions.set(GUILD_ID, session);
  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 25000);
  } catch (err) {
    await leave();
    throw new Error('Discord voice/DAVE handshake failed: ' + err.message);
  }
  const listening=attachConversation(session,{
    python:PYTHON, root:ROOT, say, ack:acknowledgeWake,
  });
  try {await listening.ready;}
  catch(error){await leave();throw new Error('Offline speech recognition unavailable: '+error.message);}
  console.log('Voice connected with owner-only wake word: ' + channel.name);
  return {ok:true, channel_id:channel.id, channel_name:channel.name, reused:false, listening:session.listening};
}

async function joinUser(userId) {
  if (String(userId)!=='965583496435367956') throw new Error('Only the guild owner may activate listening');
  if (!client?.isReady()) throw new Error('Discord bot is not ready');
  const guild = client.guilds.cache.get(GUILD_ID) || await client.guilds.fetch(GUILD_ID);
  let channelId = guild.voiceStates.cache.get(String(userId))?.channelId;
  if (!channelId) {
    try {
      const state = await client.rest.get('/guilds/' + GUILD_ID + '/voice-states/' + userId);
      channelId = state?.channel_id;
    } catch (error) {
      if (error?.status !== 404) console.error('Voice state lookup failed:', error.message);
    }
  }
  if (!channelId) throw new Error('Du bist derzeit in keinem Sprachkanal. Bitte zuerst beitreten.');
  return await joinChannel(channelId);
}

async function leave() {
  const s = sessions.get(GUILD_ID);
  if (!s) return {ok:true, already_left:true};
  sessions.delete(GUILD_ID);
  try{s.voice?.close();}catch{}
  try{s.player.stop(true);}catch{}
  try{s.connection.destroy();}catch{}
  return {ok:true, left:true};
}

async function processControl(request) {
  if (!request || typeof request !== 'object') throw new Error('Invalid request');
  if (request.op === 'status') return publicStatus();
  if (request.op === 'join') {
    if (!/^\d{17,20}$/.test(String(request.channel_id || ''))) throw new Error('Invalid channel ID');
    return await joinChannel(request.channel_id);
  }
  if (request.op === 'join_user') return await joinUser(request.user_id);
  if (request.op === 'say') return await say(request.text);
  if (request.op === 'exchange') {
    if(typeof request.text!=='string'||request.text.trim().length<1||request.text.length>400)
      throw new Error('Exchange message must be 1..400 characters');
    const wait=Number(request.wait_seconds??15);
    if(!Number.isInteger(wait)||wait<4||wait>22)
      throw new Error('wait_seconds must be an integer from 4 to 22');
    const session=sessions.get(GUILD_ID);
    if(!session?.voice || session.connection.state.status!==VoiceConnectionStatus.Ready)
      throw new Error('Nova must be in the authorized voice channel');
    const capture=session.voice.captureNext();
    try {
      const spoken=await say(request.text);
      const result=await capture.wait(wait);
      return {...result,spoken_characters:spoken.spoken_characters,wait_seconds:wait};
    }catch(error){
      capture.cancel();
      throw error;
    }
  }
  if (request.op === 'talk') {
    const session=sessions.get(GUILD_ID);
    if(!session?.voice)throw new Error('Nova is not in a voice channel');
    return session.voice.talk();
  }
  if (request.op === 'listen') {
    const session=sessions.get(GUILD_ID);
    if(!session?.voice)throw new Error('Nova is not in a voice channel');
    if(typeof request.enabled!=='boolean')throw new Error('Expected boolean enabled');
    return session.voice.set(request.enabled);
  }
  if (request.op === 'leave') return await leave();
  throw new Error('Unsupported control operation');
}

async function startControlSocket() {
  mkdirSync(STATE_DIR, {recursive:true, mode:0o700});
  chmodSync(STATE_DIR, 0o700);
  if (existsSync(SOCKET)) {
    if (!statSync(SOCKET).isSocket()) throw new Error('Control path is not a socket');
    await new Promise((resolve, reject) => {
      const probe = createConnection(SOCKET);
      probe.once('connect', () => {probe.destroy(); reject(new Error('Another voice instance is already active'));});
      probe.once('error', e => {if (e.code === 'ECONNREFUSED') resolve(); else reject(e);});
      probe.setTimeout(1000, () => probe.destroy(new Error('Socket probe timeout')));
    });
    unlinkSync(SOCKET);
    console.log('Recovered a stale control socket.');
  }
  server = createServer(socket => {
    let input = '';
    socket.setTimeout(78000, () => socket.destroy());
    socket.on('data', async chunk => {
      input += chunk.toString('utf8');
      if (input.length > 4096) {socket.destroy();return;}
      if (!input.includes('\n')) return;
      const line = input.split('\n')[0];
      input = '';
      try {
        const result = await processControl(JSON.parse(line));
        socket.end(JSON.stringify(result) + '\n');
      } catch (err) {
        socket.end(JSON.stringify({ok:false, error:String(err.message).slice(0,250)}) + '\n');
      }
    });
  });
  server.listen(SOCKET, () => {
    createdSocket = true;
    chmodSync(SOCKET, 0o600);
    console.log('Local MCP control socket ready.');
  });
  server.on('error', error => {console.error('Control socket error:',error.message);process.exitCode=1;});
}

async function handleInteraction(interaction) {
  if (!interaction.isChatInputCommand() || interaction.commandName !== 'nova') return;
  if (interaction.guildId !== GUILD_ID) return;
  const member = interaction.memberPermissions;
  if (!member?.has(PermissionFlagsBits.Administrator) || interaction.user.id !== '965583496435367956') {
    await interaction.reply({content:'Nur für Server-Administratoren.', flags:MessageFlags.Ephemeral});
    return;
  }
  const sub = interaction.options.getSubcommand();
  try {
    if (sub === 'status') {
      const info=publicStatus();
      await interaction.reply({content:'Nova Voice: ' + (info.connected ? 'in ' + info.channel_name : 'nicht verbunden') + ', Seraphina, Zuhören: ' + (info.listening ? 'an' : 'aus') + '.',flags:MessageFlags.Ephemeral});
      return;
    }
    await interaction.deferReply({flags:MessageFlags.Ephemeral});
    if(sub === 'join') {
      const m = await interaction.guild.members.fetch(interaction.user.id);
      const channel = m.voice.channel;
      if(!channel) throw new Error('Geh zuerst in einen Discord-Sprachkanal.');
      const result = await joinChannel(channel.id);
      if(!result.reused) await say('Servus! Nova ist da. Und Brumo kontrolliert natürlich wieder die Tonqualität.');
      await interaction.editReply('Nova ist in ' + result.channel_name + '. Sag Nova und stell eine Frage. /nova quiet schaltet das Zuhören aus.');
    } else if(sub === 'talk') {
      const s=sessions.get(GUILD_ID);
      if(!s?.voice)throw new Error('Nova ist nicht im Sprachkanal.');
      s.voice.talk();
      await interaction.editReply('Freies Gespräch aktiv – ich antworte ohne Aktivierungswort. /nova quiet beendet das Zuhören.');
    } else if(sub === 'listen' || sub === 'quiet') {
      const s=sessions.get(GUILD_ID);
      if(!s?.voice)throw new Error('Nova ist nicht im Sprachkanal.');
      const info=s.voice.set(sub==='listen');
      await interaction.editReply('Zuhören '+(info.listening?'eingeschaltet':'ausgeschaltet')+'.');
    } else if(sub === 'say') {
      const text=interaction.options.getString('text',true);
      await say(text);
      await interaction.editReply('Gesprochen: ' + text.slice(0,120));
    } else if(sub === 'leave') {
      await leave();
      await interaction.editReply('Nova hat den Sprachkanal verlassen.');
    }
  } catch(err) {
    console.error('Interaction failed:',err.message);
    if(interaction.deferred || interaction.replied) await interaction.editReply('Nova-Fehler: ' + err.message.slice(0,180));
    else await interaction.reply({content:'Nova-Fehler: ' + err.message.slice(0,180),flags:MessageFlags.Ephemeral});
  }
}

async function shutdown() {
  await leave();
  if(server) server.close();
  if(createdSocket) {try{unlinkSync(SOCKET);}catch{}}
  if(client) client.destroy();
}

async function main() {
  if(process.argv.includes('--doctor')) {doctor();return;}
  if(process.argv.includes('--register')) {await register();return;}
  if(process.argv.includes('--unregister')) {await unregister();return;}
  if(!process.argv.includes('--run')) throw new Error('Use --doctor, --register, --unregister or --run');
  doctor();
  client = new Client({intents:[GatewayIntentBits.Guilds,GatewayIntentBits.GuildVoiceStates]});
  client.once(Events.ClientReady, user => {
    console.log('Discord bot gateway ready: ' + user.user.username + '. No voice auto-join.');
    startControlSocket().catch(e => {console.error('Control setup failed:',e.message);process.exit(1);});
  });
  client.on(Events.InteractionCreate, interaction => handleInteraction(interaction).catch(e=>console.error('interaction error',e.message)));
  client.on(Events.Error,error=>console.error('Discord error:',error.message));
  process.once('SIGINT', ()=>{shutdown().finally(()=>process.exit(0));});
  process.once('SIGTERM', ()=>{shutdown().finally(()=>process.exit(0));});
  await client.login(token());
}

main().catch(error => {
  console.error('Nova voice setup error:',error.message);
  process.exitCode=1;
});
