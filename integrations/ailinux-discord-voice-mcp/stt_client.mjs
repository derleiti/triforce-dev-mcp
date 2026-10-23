import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {join} from 'node:path';

export class SpeechRecognizer {
  constructor(python, root) {
    this.python=python; this.script=join(root,'stt_worker.py');
    this.process=null;this.ready=false;this.pending=new Map();this.id=0;
  }
  async start() {
    const proc=spawn(this.python,['-u',this.script],{stdio:['pipe','pipe','pipe']});
    this.process=proc;
    proc.stderr.on('data',data=>{
      if(/Traceback|Error:/i.test(data.toString()))console.error('STT:',data.toString().slice(0,160));
    });
    const reader=createInterface({input:proc.stdout});
    const ready=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('STT model startup timeout')),45000);
      const done=(fn,value)=>{clearTimeout(timer);fn(value);};
      proc.once('error',e=>done(reject,e));
      proc.once('exit',code=>done(reject,new Error('STT worker exit '+code)));
      reader.on('line',line=>{
        let data;try{data=JSON.parse(line);}catch{return;}
        if(data.type==='ready'){this.ready=true;done(resolve,data);return;}
        if(data.type==='transcript'){
          const item=this.pending.get(data.id);if(!item)return;
          this.pending.delete(data.id);clearTimeout(item.timer);
          data.error?item.reject(new Error('STT '+data.error)):item.resolve(data);
        }
      });
    });
    proc.on('exit',()=>{
      this.ready=false;this.process=null;
      for(const item of this.pending.values()){clearTimeout(item.timer);item.reject(new Error('STT stopped'));}
      this.pending.clear();
    });
    return ready;
  }
  recognize(path) {
    if(!this.ready||!this.process)return Promise.reject(new Error('STT not ready'));
    const id=++this.id;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('STT timeout'));},30000);
      this.pending.set(id,{timer,resolve,reject});
      this.process.stdin.write(JSON.stringify({id,path})+'\n');
    });
  }
  close(){if(this.process)try{this.process.kill('SIGTERM');}catch{};this.ready=false;}
}
