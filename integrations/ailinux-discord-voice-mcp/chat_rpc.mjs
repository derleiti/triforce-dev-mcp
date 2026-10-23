import {spawn} from 'node:child_process';

export const VOICE_OWNER_ID='965583496435367956';
export const VOICE_GUILD_ID='1333886137030283296';

export function extractWake(text) {
  if(typeof text!=='string')return null;
  const match=text.trim().match(/^(?:hey|hallo|hi|servus|hör mal|sag mal)?[\s,!.?]*nova\b[\s,!.?:;-]*(.*)$/iu);
  return match ? (match[1].trim() || 'Hallo Nova, bist du da?') : null;
}

function queryOnce(message,history=[]) {
  if(typeof message!=='string'||message.length>550||!message.trim()){
    return Promise.reject(new Error('Invalid voice message'));
  }
  return new Promise((resolve,reject)=>{
    const proc=spawn('/usr/bin/ssh',[
      '-T','-o','BatchMode=yes','-o','ConnectTimeout=6',
      '-o','StrictHostKeyChecking=yes','-o','LogLevel=ERROR',
      '10.10.0.1',
      '/home/zombie/workspace/triforce/.venv/bin/python',
      '/home/zombie/workspace/triforce/scripts/discord_voice_chat.py'
    ],{stdio:['pipe','pipe','pipe']});
    let output='';
    const timer=setTimeout(()=>proc.kill('SIGKILL'),42000);
    proc.stdout.on('data',part=>{
      output+=part.toString('utf8');
      if(output.length>10000)proc.kill('SIGKILL');
    });
    proc.stderr.on('data',()=>{});
    proc.once('error',e=>{clearTimeout(timer);reject(e);});
    proc.once('close',code=>{
      clearTimeout(timer);
      if(code!==0){reject(new Error('TriForce chat unavailable'));return;}
      try{
        const data=JSON.parse(output.split('\n')[0]);
        if(!data.ok||typeof data.response!=='string')throw new Error('Empty chat answer');
        resolve(data.response.slice(0,380));
      }catch(e){reject(e);}
    });
    proc.stdin.end(JSON.stringify({
      guild_id:VOICE_GUILD_ID,user_id:VOICE_OWNER_ID,
      message:message.trim(),history:history.slice(-8)
    }));
  });
}

export async function queryTriForce(message, history=[]) {
  try {
    return await queryOnce(message, history);
  } catch (firstError) {
    // Network/provider hiccups are common; one bounded retry, no infinite loops.
    await new Promise(resolve=>setTimeout(resolve,350));
    return await queryOnce(message,history);
  }
}
