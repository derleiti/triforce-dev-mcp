import { createConnection } from 'node:net';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

const directory = process.env.NOVA_VOICE_STATE_DIR || join(homedir(), '.local/state/ailinux-nova-voice');
const socketPath = join(directory, 'control.sock');

function invoke(data) {
  return new Promise((resolvePromise,reject) => {
    const socket = createConnection(socketPath);
    let reply='';
    const timeout = setTimeout(()=>socket.destroy(new Error('Voice bridge timed out')),76000);
    socket.on('connect',()=>socket.write(JSON.stringify(data)+'\n'));
    socket.on('data',chunk=>{
      reply += chunk.toString('utf8');
      if(reply.length > 8192) socket.destroy(new Error('Unexpected oversized response'));
      if(!reply.includes('\n'))return;
      clearTimeout(timeout);
      socket.end();
      try {
        resolvePromise(JSON.parse(reply.split('\n')[0]));
      } catch(e){reject(e);}
    });
    socket.on('error',e=>{clearTimeout(timeout);reject(e);});
    socket.on('close',()=>{
      clearTimeout(timeout);
      if(!reply.includes('\n'))reject(new Error('Voice bot not running or disconnected'));
    });
  });
}

function result(data) {
  return {
    isError: !data?.ok,
    content:[{type:'text',text:JSON.stringify(data)}]
  };
}

const server = new McpServer({
  name:'ailinux-discord-voice-mcp',
  version:'0.5.0'
});

server.registerTool('discord_voice_status',{
  description:'Read Nova voice connection state. Does not join or record anything.',
  inputSchema:{}
},async()=>result(await invoke({op:'status'})));

server.registerTool('discord_voice_connect',{
  description:'Connect the configured Discord owner in their current voice channel and enable owner-only free conversation. Useful when a NEW ChatGPT GPT-6 chat requests Nova, verbinde dich mit Discord. This does not run this GPT-6 chat autonomously between tool calls; the background voice agent is separate TriForce Nova. Use discord_voice_exchange for explicit GPT-6 speech/reply turns.',
  inputSchema:{}
},async()=>result(await invoke({op:'connect'})));

server.registerTool('discord_voice_join',{
  description:'Join one authorized Discord voice channel. Explicit channel ID only. Enables owner-only wake-word recognition.',
  inputSchema:{channel_id:z.string().regex(/^\d{17,20}$/)}
},async({channel_id})=>result(await invoke({op:'join',channel_id})));

server.registerTool('discord_voice_say',{
  description:'Speak a German text into the active Discord voice channel using Seraphina neural TTS. Other connected users can hear it.',
  inputSchema:{text:z.string().min(1).max(400)}
},async({text})=>result(await invoke({op:'say',text})));

server.registerTool('discord_voice_listen',{
  description:'Enable or disable owner-only Discord voice recognition. Wake word Nova.',
  inputSchema:{enabled:z.boolean()}
},async({enabled})=>result(await invoke({op:'listen',enabled})));

server.registerTool('discord_voice_talk',{
  description:'Opt in to persistent, owner-only hands-free Discord conversation; no wake word. Only the authorized guild owner microphone is processed. Use discord_voice_listen(enabled=false) or leave to stop.',
  inputSchema:{}
},async()=>result(await invoke({op:'talk'})));

server.registerTool('discord_voice_exchange',{
  description:'Speak one GPT-6-authored message in Discord, wait for the authorized owner to reply, and return the exact Whisper transcript to the caller. Temporarily suppresses auto-Groq answers for this ONE speech turn only. Returns heard:false on timeout. Requires the voice session already joined/listening; this does not run GPT-6 continuously or create a background AI loop.',
  inputSchema:{text:z.string().min(1).max(400),wait_seconds:z.number().int().min(4).max(22).default(15)}
},async({text,wait_seconds})=>result(await invoke({op:'exchange',text,wait_seconds})));

server.registerTool('discord_voice_leave',{
  description:'Disconnect Nova from Discord voice and stop playback.',
  inputSchema:{}
},async()=>result(await invoke({op:'leave'})));

const transport = new StdioServerTransport();
await server.connect(transport);
