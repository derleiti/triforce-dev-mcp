import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {stripWakeWord} from './wake_dialogue.mjs';

export const VOICE_OWNER_ID='965583496435367956';
export const VOICE_GUILD_ID='1333886137030283296';
// Kept for existing tool consumers; null=not addressed, empty=only "Nova".
export const extractWake=stripWakeWord;

function callHub(data, timeoutMs = 40000) {
  return new Promise((resolve,reject)=>{
    const proc=spawn('/usr/bin/ssh',[
      '-T','-o','BatchMode=yes','-o','ConnectTimeout=6',
      '-o','StrictHostKeyChecking=yes','-o','LogLevel=ERROR',
      '10.10.0.1',
      '/home/zombie/workspace/triforce/.venv/bin/python',
      '/home/zombie/workspace/triforce/scripts/discord_voice_chat.py'
    ],{stdio:['pipe','pipe','pipe']});
    let output='';
    let finished=false;
    const rejectOnce=error=>{
      if(finished)return;
      finished=true;clearTimeout(timer);reject(error);
    };
    const timer=setTimeout(()=>{
      proc.kill('SIGKILL');
      rejectOnce(new Error('TriForce chat timeout'));
    },timeoutMs);
    proc.stdout.on('data',part=>{
      output+=part.toString('utf8');
      if(output.length>12000){
        proc.kill('SIGKILL');
        rejectOnce(new Error('TriForce response exceeded limit'));
      }
    });
    // stderr could contain upstream info: do not forward it to Discord.
    proc.stderr.on('data',()=>{});
    proc.once('error',rejectOnce);
    proc.once('close',code=>{
      if(finished)return;
      finished=true;clearTimeout(timer);
      let payload;
      try{payload=JSON.parse(output.split('\n')[0]);}
      catch{reject(new Error('TriForce sent invalid response'));return;}
      if(code!==0||!payload?.ok){
        reject(new Error('TriForce '+String(payload?.error||'unavailable').slice(0,70)));
        return;
      }
      resolve(payload);
    });
    proc.stdin.on('error',()=>{});
    proc.stdin.end(JSON.stringify({
      guild_id:VOICE_GUILD_ID,
      user_id:VOICE_OWNER_ID,
      ...data
    }));
  });
}

export async function queryTriForce(message) {
  if(typeof message!=='string'||!message.trim()||message.length>550){
    throw new Error('Invalid voice message');
  }
  const request_id=randomUUID();
  const input={action:'chat',request_id,message:message.trim()};
  try{
    return await callHub(input);
  }catch(firstError){
    // Retry with exactly the same id: if the first answer was stored already,
    // the server returns it without making an extra model call.
    await new Promise(r=>setTimeout(r,450));
    return await callHub(input);
  }
}

export async function historyAction(action) {
  if(!['history','count','reset'].includes(action))throw new Error('Invalid history operation');
  return await callHub({action},12000);
}
