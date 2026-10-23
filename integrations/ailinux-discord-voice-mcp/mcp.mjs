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
    const timeout = setTimeout(()=>socket.destroy(new Error('Voice bridge timed out')),55000);
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
  version:'0.2.0'
});

server.registerTool('discord_voice_status',{
  description:'Read Nova voice connection state. Does not join or record anything.',
  inputSchema:{}
},async()=>result(await invoke({op:'status'})));

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

server.registerTool('discord_voice_leave',{
  description:'Disconnect Nova from Discord voice and stop playback.',
  inputSchema:{}
},async()=>result(await invoke({op:'leave'})));

const transport = new StdioServerTransport();
await server.connect(transport);
