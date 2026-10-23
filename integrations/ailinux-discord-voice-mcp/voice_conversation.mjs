import {EndBehaviorType} from '@discordjs/voice';
import prism from 'prism-media';
import {spawn} from 'node:child_process';
export const RECEIVER_VERSION='0.2.0';
import {VoiceConnectionStatus} from '@discordjs/voice';
import {existsSync,statSync,unlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SpeechRecognizer} from './stt_client.mjs';
import {queryTriForce,historyAction,VOICE_OWNER_ID,VOICE_GUILD_ID} from './chat_rpc.mjs';
import {WakeDialogue} from './wake_dialogue.mjs';

function remove(p){try{if(existsSync(p))unlinkSync(p);}catch{}}
export function attachConversation(session,{python,root,say,chat=queryTriForce}) {
  const receiver=session.connection.receiver;
  const worker=new SpeechRecognizer(python,root);
  const dialogue=new WakeDialogue();
  let lastErrorNotice=0;
  let active=true, recording=false, processing=false, recorder=null,lastRecordAt=0;
  const metrics={speaking_events:0,audio_packets:0,clips:0,transcribed:0,
    wakes:0,wake_only:0,followups:0,chat_requests:0,replies:0,errors:0,
    history_count:null,last_question_length:0,last_model:null,
    last_audio_at:null,last_wake_at:null,last_response_at:null,
    last_error:null,stt_ready:false};
  session.listening=false; session.responding=false;
  session.metrics=metrics;
  const error=(stage,e)=>{
    metrics.errors++;metrics.last_error=stage;
    console.error('Nova voice '+stage+':',String(e?.message||e).slice(0,155));
  };
  const handleWav=async file=>{
    if(!active||!session.listening||processing||!worker.ready)return;
    if(!existsSync(file))return;
    const size=statSync(file).size;
    if(size<11000||size>650000)return;
    processing=true;metrics.clips++;
    try{
      const result=await worker.recognize(file);
      if(!active)return;
      const words=String(result.text||'').trim();
      if(!words)return;
      metrics.transcribed++;
      const event=dialogue.interpret(words);
      if(event.kind==='ignore')return;
      if(event.kind==='armed'){
        metrics.wakes++;
        metrics.wake_only++;
        metrics.last_wake_at=new Date().toISOString();
        return; // Wake-only: wait for next clip, never invent a question.
      }
      if(event.source==='wake'){
        metrics.wakes++;
        metrics.last_wake_at=new Date().toISOString();
      }
      if(event.source==='followup')metrics.followups++;
      const userText=event.text;
      metrics.last_question_length=userText.length;
      metrics.chat_requests++;
      if(!active||!session.listening)return;
      session.responding=true;
      let chatResult;
      try{
        chatResult=await chat(userText);
      }catch(e){
        error('chat',e);
        // Do not persist or endlessly repeat a fallback as conversation history.
        if(Date.now()-lastErrorNotice>45000 && active && session.listening){
          lastErrorNotice=Date.now();
          await say('Die Chatverbindung hat gerade ein Problem. Frag mich gleich noch mal.');
        }
        return;
      }
      if(!active||!session.listening)return;
      metrics.history_count=chatResult.history_count;
      metrics.last_model=chatResult.model||metrics.last_model;
      await say(chatResult.response);
      dialogue.markReplied();
      metrics.replies++;
      metrics.last_response_at=new Date().toISOString();
    }catch(e){error('recognition',e);}
    finally{session.responding=false;processing=false;}
  };
  const onStart=userId=>{
    if(!active||!session.listening||String(userId)!==VOICE_OWNER_ID)return;
    metrics.speaking_events++;
    if(recording||processing||session.responding||session.speakingBack)return;
    if(session.connection.state.status!==VoiceConnectionStatus.Ready)return;
    if(Date.now()-lastRecordAt<400)return;
    lastRecordAt=Date.now();
    metrics.last_audio_at=new Date().toISOString();
    recording=true;
    const wav=join(tmpdir(),'nova-discord-'+randomUUID()+'.wav');
    const opus=receiver.subscribe(userId,{
      end:{behavior:EndBehaviorType.AfterSilence,duration:1050}
    });
    recorder=opus;
    const decoder=new prism.opus.Decoder({rate:48000,channels:2,frameSize:960});
    const ffmpeg=spawn('ffmpeg',[
      '-nostdin','-loglevel','error','-hide_banner','-y',
      '-f','s16le','-ar','48000','-ac','2','-i','pipe:0',
      '-ar','16000','-ac','1','-c:a','pcm_s16le','-f','wav',wav
    ],{stdio:['pipe','ignore','pipe']});
    let fferr='';
    ffmpeg.stderr.on('data',chunk=>{if(fferr.length<170)fferr+=chunk.toString().slice(0,170);});
    const timer=setTimeout(()=>{try{opus.destroy();}catch{}},16000);
    opus.on('data',packet=>{
      const data=Buffer.isBuffer(packet)?packet:packet?.payload;
      if(!Buffer.isBuffer(data))return;
      metrics.audio_packets++;
      if(!decoder.destroyed)decoder.write(data);
    });
    opus.once('end',()=>decoder.end());
    opus.once('close',()=>decoder.end());
    opus.on('error',e=>{error('opus',e);decoder.destroy();});
    decoder.on('error',e=>{error('decode',e);try{opus.destroy();}catch{}});
    ffmpeg.stdin.on('error',()=>{});
    decoder.pipe(ffmpeg.stdin);
    ffmpeg.once('error',e=>error('ffmpeg',e));
    ffmpeg.once('close',code=>{
      clearTimeout(timer);
      recording=false;recorder=null;
      if(!active){remove(wav);return;}
      if(code!==0&&metrics.audio_packets>0&&fferr)error('ffmpeg',new Error(fferr));
      handleWav(wav).catch(e=>error('clip',e)).finally(()=>remove(wav));
    });
  };
  const ready=worker.start().then(()=>{
    if(!active)return;
    metrics.stt_ready=true;session.listening=true;
    receiver.speaking.on('start',onStart);
    historyAction('count').then(result=>{
      if(active)metrics.history_count=chatResult.history_count;
    }).catch(()=>{});
    console.log('Nova wake receiver active with split-wake/follow-up support.');
  }).catch(e=>{error('start',e);throw e;});
  const listener={
    ready,
    set(enabled){
      if(!active)throw new Error('Listener stopped');
      if(enabled&&!worker.ready)throw new Error('STT not ready');
      session.listening=Boolean(enabled);
      if(!enabled)dialogue.reset();
      return {ok:true,listening:session.listening,wake_word:'nova'};
    },
    close(){
      active=false;session.listening=false;
      dialogue.reset();
      receiver.speaking.off('start',onStart);
      try{recorder?.destroy();}catch{}
      worker.close();
    },
    status(){return {
      listening:Boolean(session.listening&&worker.ready),
      responding:Boolean(session.responding),
      wake_word:'nova',stt_model:'small',...metrics,...dialogue.status()
    };}
  };
  session.voice=listener;
  return listener;
}
