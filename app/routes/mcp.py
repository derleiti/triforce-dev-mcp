from __future__ import annotations
from .widget_handlers import handle_weather, handle_crypto_prices, handle_stock_indices, handle_market_overview, handle_google_deep_search, handle_current_time, handle_list_timezones

import base64
import inspect
import logging
import os
from datetime import datetime, timezone

# Logger für MCP Routes
logger = logging.getLogger("ailinux.mcp.routes")
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from ..config import VERSION

# Crawler imports are loaded lazily inside handlers to avoid heavy startup imports and optional dependencies (playwright).
from ..services.wordpress import wordpress_service
from ..services import chat as chat_service
from ..services.model_registry import registry
from ..utils.throttle import request_slot
from ..services.ollama_mcp import OLLAMA_TOOLS, OLLAMA_HANDLERS
from ..services.tristar_mcp import TRISTAR_TOOLS, TRISTAR_HANDLERS
from ..services.gemini_access import GEMINI_ACCESS_TOOLS, GEMINI_ACCESS_HANDLERS
from ..services.command_queue import QUEUE_TOOLS, QUEUE_HANDLERS
from ..routes.mesh import MESH_TOOLS, MESH_HANDLERS
from ..services.mcp_filter import MESH_FILTER_TOOLS, MESH_FILTER_HANDLERS
# New Client-Server Architecture
from ..services.api_vault import VAULT_HANDLERS
from ..services.chat_router import CHAT_ROUTER_HANDLERS
from ..services.task_spawner import TASK_SPAWNER_HANDLERS
from ..services.init_service import INIT_TOOLS, INIT_HANDLERS, init_service, loadbalancer, mcp_brain
from ..services.gemini_model_init import MODEL_INIT_TOOLS, MODEL_INIT_HANDLERS, gemini_model_init
from ..services.agent_bootstrap import BOOTSTRAP_TOOLS, BOOTSTRAP_HANDLERS, bootstrap_service, chat_processor, shortcode_filter
from ..routes.admin_crawler import (
    CrawlerConfigUpdate,
    CrawlerConfigUpdateResponse,
    CrawlerControlRequest,
    control_crawler,
    get_crawler_config,
    update_crawler_config,
)
from ..mcp.api_docs import get_api_docs, get_endpoint_for_task
from ..mcp.translation import BidirectionalTranslator, APIToMCPTranslator, MCPToAPITranslator
from ..mcp.specialists import specialist_router, SPECIALISTS
from ..mcp.context import context_manager, prompt_library, workflow_manager
from ..mcp.agent_instructions import build_mcp_instructions
from ..mcp.adaptive_code import ADAPTIVE_CODE_TOOLS, ADAPTIVE_CODE_HANDLERS
from ..mcp.adaptive_code_v4 import ADAPTIVE_CODE_V4_TOOLS, ADAPTIVE_CODE_V4_HANDLERS
from ..mcp.handlers_group_chat import GROUP_CHAT_HANDLERS
from ..mcp.handlers_telegram_agent import TELEGRAM_AGENT_HANDLERS
from ..mcp.handlers_swarm import SWARM_HANDLERS
from ..services.n8n_mcp import N8N_TOOLS, N8N_HANDLERS
from ..mcp.flarum_tools import FLARUM_TOOL_HANDLERS
from ..mcp.tool_registry_v3 import (
    get_all_tools as registry_v3_get_all_tools,
    get_tool_count as registry_v3_tool_count,
    register_handlers_from_dict,
)
# v4 Consolidated Registry (52 tools, optimized from 134)
from ..mcp.tool_registry_v4 import (
    get_all_tools as registry_v4_get_all_tools,
    get_tool_count as registry_v4_tool_count,
    resolve_alias_reverse,
)
from ..mcp.handlers_v4 import (
    init_handlers as init_v4_handlers,
    call_tool as call_v4_tool,
)
from ..services.compatibility_layer import compatibility_layer
from ..services.system_control import system_control, HOTRELOAD_HANDLERS
from ..services.memory_index import MEMORY_INDEX_HANDLERS
from ..services.mcp_debugger import mcp_debugger
from ..services.llm_compat import LLM_COMPAT_HANDLERS
from ..utils.mcp_auth import require_mcp_auth
from ..utils.tool_normalizer import is_readonly_tool
from ..utils.mcp_security import (
    client_ip,
    filter_tools_for_external,
    is_ai_coder_request,
    is_internal_full_request,
    is_tool_allowed,
)
import logging

mcp_logger = logging.getLogger("ailinux.mcp")

router = APIRouter(dependencies=[Depends(require_mcp_auth)])
public_router = APIRouter()


def _mcp_instructions_for_request(request: Request) -> str:
    try:
        from app.services.mcp_workspace_bridge import is_public_guest, public_instructions
        if is_public_guest(request):
            return public_instructions(request)
    except Exception:
        pass
    return build_mcp_instructions()


def _workspace_setup_html() -> str:
    return r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TriForce MCP · Browser Workspace</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0}main{max-width:860px;margin:5vh auto;padding:24px}section{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:22px;margin:16px 0}h1{font-size:2rem;margin:.25rem 0}h2{margin-top:0}p{line-height:1.5;color:#b8c1cc}.muted{color:#8b949e}.ok{color:#63d471}.warn{color:#e3b341}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}button{background:#238636;color:#fff;border:0;border-radius:8px;padding:11px 16px;font-weight:650;cursor:pointer}button.secondary{background:#30363d}button:disabled{opacity:.45;cursor:not-allowed}input[type=text],textarea{width:100%;background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:11px;border-radius:8px}textarea{min-height:76px;resize:vertical}code,.mono{font-family:ui-monospace,SFMono-Regular,monospace}.pair{font-size:1.05rem;letter-spacing:.04em}.pill{display:inline-block;border:1px solid #1f6feb;background:#1f6feb22;color:#79c0ff;padding:5px 10px;border-radius:999px}.hidden{display:none}.status{font-weight:650}.choice{padding:9px 12px;border:1px solid #30363d;border-radius:9px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.drop{margin-top:10px;border-style:dashed;text-align:center}@media(max-width:650px){.grid{grid-template-columns:1fr}}
</style></head><body><main>
<span class="pill">AILinux · TriForce MCP</span><h1>Local Workspace in your browser</h1>
<p>The selected folder stays on this device. TriForce stores only a browser directory handle and accesses individual paths later when the AI calls a local tool. Selecting a folder does not enumerate or analyze it.</p>
<section><h2>1 · Share workspace</h2>
<div class="grid"><label class="choice"><input type="radio" name="mode" value="read_only" checked> Read only</label><label class="choice"><input id="writeMode" type="radio" name="mode" value="write"> Write</label></div>
<div class="row" style="margin-top:14px"><button id="chooseBtn">Choose local folder</button><button id="openChromeBtn" class="secondary hidden">Open in Chrome</button><span id="folderName" class="muted">No folder shared</span></div>
<div id="dropZone" class="choice drop">Desktop fallback: drag a folder here for lazy read-only access</div>
<p id="browserNote" class="muted"></p><p id="browserCaps" class="muted mono"></p>
<label for="task" class="muted">Optional task/context for the AI</label><textarea id="task" placeholder="e.g. Review this project and fix the login flow"></textarea>
<div class="row" style="margin-top:12px"><button id="connectBtn" disabled>Connect workspace</button><button id="disconnectBtn" class="secondary" disabled>Disconnect</button></div>
<p id="status" class="status">Choose a folder first.</p><pre id="analysis" class="muted"></pre></section>
<section id="pairPanel" class="hidden"><h2 class="ok">2 · Workspace ready</h2><p>Paste this one-time ID into the ChatGPT, Codex or Mistral conversation that uses TriForce. The AI will call <code>workspace_pair</code> and bind this browser workspace to that MCP session.</p><div class="row"><input id="pair" class="pair mono" type="text" readonly value=""><button id="copyBtn">Copy ID</button></div><p class="muted">The ID expires after 15 minutes and is single-use. Keep this browser tab open.</p></section>
<section><h2>Public MCP URL</h2><div class="row"><input id="mcpUrl" type="text" readonly value="https://api.ailinux.me/v1/mcp"><button id="copyMcp" class="secondary">Copy MCP URL</button></div></section>
<script data-cfasync="false">
'use strict';
const $=id=>document.getElementById(id);
const READ_TOOLS=['workspace_info','file_read','file_tree','code_read','code_tree','code_search','code_grep'];
const WRITE_TOOLS=['file_edit','directory_create'];
const IGNORE=new Set(['.git','.venv','node_modules','__pycache__','.pytest_cache','.mypy_cache']);
const MAX_TEXT=2*1024*1024;
let pairCode=sessionStorage.getItem('tf_pair_code')||'',resumeToken=sessionStorage.getItem('tf_resume_token')||'',rootHandle=null,rootEntry=null,ws=null,workspaceMode=sessionStorage.getItem('tf_workspace_mode')||'read_only',capabilities=[],reconnectTimer=null,reconnectAttempt=0,manualDisconnect=false;
const directPicker=typeof window.showDirectoryPicker==='function';
const android=/Android/i.test(navigator.userAgent),firefox=/Firefox\//i.test(navigator.userAgent);
const persistentHandleStore=!android&&'indexedDB' in window;
const dropHandleCap=typeof DataTransferItem!=='undefined'&&typeof DataTransferItem.prototype.getAsFileSystemHandle==='function';
const dropEntryCap=typeof DataTransferItem!=='undefined'&&(typeof DataTransferItem.prototype.getAsEntry==='function'||typeof DataTransferItem.prototype.webkitGetAsEntry==='function');
if(!directPicker){$('writeMode').disabled=true;if(android)$('openChromeBtn').classList.remove('hidden');$('browserNote').textContent=(android?'Android: ':'')+(firefox?'Firefox: ':'')+'this browser does not expose showDirectoryPicker(), so full lazy Read/Write workspace selection is unavailable. '+(android?'Open this page in Chrome/Chromium to share a folder lazily with Read/Write access. ':'Use a Chromium browser with File System Access support. Desktop drag-and-drop may still provide lazy read-only access.');}else{$('browserNote').textContent='Direct directory handles are available. Choose Read only or Write, then choose the folder. No file list is built during selection.';}
$('browserCaps').textContent='Origin: '+location.origin+' · secure='+((window.isSecureContext)?'yes':'no')+' · directory-picker='+(directPicker?'yes':'no')+' · persistent-handle='+(persistentHandleStore?'yes':'no')+' · drop-handle='+(dropHandleCap?'yes':'no')+' · drop-entry='+(dropEntryCap?'yes':'no')+' · android='+(android?'yes':'no')+' · firefox='+(firefox?'yes':'no');
function status(text,cls=''){ $('status').textContent=text;$('status').className='status '+cls; }
function openWorkspaceDb(){return new Promise((resolve,reject)=>{const r=indexedDB.open('triforce-browser-workspace',1);r.onupgradeneeded=()=>{if(!r.result.objectStoreNames.contains('state'))r.result.createObjectStore('state')};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
async function saveDirectoryHandle(handle){try{const db=await openWorkspaceDb();await new Promise((resolve,reject)=>{const tx=db.transaction('state','readwrite');tx.objectStore('state').put(handle,'directoryHandle');tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error)});db.close()}catch{}}
async function loadDirectoryHandle(){try{const db=await openWorkspaceDb();const value=await new Promise((resolve,reject)=>{const tx=db.transaction('state','readonly');const r=tx.objectStore('state').get('directoryHandle');r.onsuccess=()=>resolve(r.result||null);r.onerror=()=>reject(r.error)});db.close();return value}catch{return null}}
async function clearDirectoryHandle(){try{const db=await openWorkspaceDb();await new Promise((resolve,reject)=>{const tx=db.transaction('state','readwrite');tx.objectStore('state').delete('directoryHandle');tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error)});db.close()}catch{}}
function cleanPath(value){let p=String(value||'.').replaceAll('\\','/').replace(/^\.\//,'');if(!p||p==='.')return '';if(p.startsWith('/')||p.includes('\0'))throw new Error('absolute or invalid path rejected');const parts=p.split('/').filter(Boolean);if(parts.some(x=>x==='..'))throw new Error('path traversal rejected');return parts.join('/');}
function ignored(path){return path.split('/').some(x=>IGNORE.has(x));}
function globRe(glob){const esc=String(glob||'*').replace(/[.+^${}()|[\]\\]/g,'\\$&').replace(/\*/g,'.*').replace(/\?/g,'.');return new RegExp('^'+esc+'$');}
async function modernDir(path='',create=false){let cur=rootHandle;for(const part of cleanPath(path).split('/').filter(Boolean))cur=await cur.getDirectoryHandle(part,{create});return cur;}
async function modernFileHandle(path,create=false){const p=cleanPath(path),parts=p.split('/').filter(Boolean),name=parts.pop();if(!name)throw new Error('file path required');const dir=await modernDir(parts.join('/'),create);return await dir.getFileHandle(name,{create});}
function legacyEntries(dir){return new Promise((resolve,reject)=>{const reader=dir.createReader(),out=[];const next=()=>reader.readEntries(batch=>{if(!batch.length)return resolve(out);out.push(...batch);next()},reject);next()})}
function legacyGetDirectory(dir,name){return new Promise((resolve,reject)=>dir.getDirectory(name,{},resolve,reject))}
function legacyGetFileEntry(dir,name){return new Promise((resolve,reject)=>dir.getFile(name,{},resolve,reject))}
async function legacyDir(path=''){let cur=rootEntry;for(const part of cleanPath(path).split('/').filter(Boolean))cur=await legacyGetDirectory(cur,part);return cur;}
async function legacyFile(path){const p=cleanPath(path),parts=p.split('/').filter(Boolean),name=parts.pop();if(!name)throw new Error('file path required');const dir=await legacyDir(parts.join('/'));const entry=await legacyGetFileEntry(dir,name);return await new Promise((resolve,reject)=>entry.file(resolve,reject));}
async function fileObj(path){if(rootHandle)return await (await modernFileHandle(path,false)).getFile();if(rootEntry)return await legacyFile(path);throw new Error('workspace not shared');}
async function readText(path){const f=await fileObj(path);if(f.size>MAX_TEXT)throw new Error('file exceeds 2 MiB text limit');return await f.text();}
function lineSlice(text,args){const lines=text.split(/\r?\n/),start=Math.max(1,Number(args.start_line||1)),end=args.end_line?Math.min(lines.length,Number(args.end_line)):lines.length;return {text:lines.slice(start-1,end).join('\n'),total_lines:lines.length,start_line:start,end_line:end};}
async function walkModern(dir,prefix,out,maxEntries){for await(const [name,h] of dir.entries()){const path=prefix?prefix+'/'+name:name;if(ignored(path))continue;out.push({path,kind:h.kind,handle:h});if(out.length>=maxEntries)return;if(h.kind==='directory'){await walkModern(h,path,out,maxEntries);if(out.length>=maxEntries)return}}}
async function walkLegacy(dir,prefix,out,maxEntries){for(const e of await legacyEntries(dir)){const path=prefix?prefix+'/'+e.name:e.name;if(ignored(path))continue;out.push({path,kind:e.isDirectory?'directory':'file',entry:e});if(out.length>=maxEntries)return;if(e.isDirectory){await walkLegacy(e,path,out,maxEntries);if(out.length>=maxEntries)return}}}
async function fullEntries(maxEntries=20000){const out=[];if(rootHandle)await walkModern(rootHandle,'',out,maxEntries);else if(rootEntry)await walkLegacy(rootEntry,'',out,maxEntries);return out;}
async function treeModern(dir,prefix,depth,maxDepth,maxEntries,out){for await(const [name,h] of dir.entries()){const path=prefix?prefix+'/'+name:name;if(ignored(path))continue;out.push(path+(h.kind==='directory'?'/':''));if(out.length>=maxEntries)return;if(h.kind==='directory'&&depth<maxDepth){await treeModern(h,path,depth+1,maxDepth,maxEntries,out);if(out.length>=maxEntries)return}}}
async function treeLegacy(dir,prefix,depth,maxDepth,maxEntries,out){for(const e of await legacyEntries(dir)){const path=prefix?prefix+'/'+e.name:e.name;if(ignored(path))continue;out.push(path+(e.isDirectory?'/':''));if(out.length>=maxEntries)return;if(e.isDirectory&&depth<maxDepth){await treeLegacy(e,path,depth+1,maxDepth,maxEntries,out);if(out.length>=maxEntries)return}}}
async function tree(args){const base=cleanPath(args.path||''),maxDepth=Math.min(8,Math.max(1,Number(args.max_depth||args.depth||3))),maxEntries=Math.min(1000,Math.max(1,Number(args.max_entries||300))),out=[];if(rootHandle)await treeModern(await modernDir(base),base,1,maxDepth,maxEntries,out);else if(rootEntry)await treeLegacy(await legacyDir(base),base,1,maxDepth,maxEntries,out);return {path:base||'.',entries:out,truncated:out.length>=maxEntries};}
async function workspaceInfo(){const sample=await tree({path:'',max_depth:1,max_entries:100});return {workspace:rootHandle?.name||rootEntry?.name||'local',mode:workspaceMode,access:rootHandle?'directory-handle':'legacy-read-only-entry',top_level:sample.entries,capabilities};}
async function searchableFiles(){const entries=await fullEntries(20000);return entries.filter(e=>e.kind==='file').map(e=>e.path);}
async function search(args,forceRegex=false){const base=cleanPath(args.path||''),max=Math.min(500,Math.max(1,Number(args.max_results||100))),pattern=forceRegex?String(args.pattern||''):String(args.query||'');if(!pattern)throw new Error('search pattern required');const regexMode=forceRegex||Boolean(args.regex),flags=args.case_sensitive?'g':'gi',re=regexMode?new RegExp(pattern,flags):null,glob=globRe(args.file_pattern||args.glob||'*'),needle=args.case_sensitive?pattern:pattern.toLowerCase(),hits=[];for(const path of await searchableFiles()){if(base&&!(path===base||path.startsWith(base+'/')))continue;if(!glob.test(path.split('/').pop()))continue;let text;try{text=await readText(path)}catch{continue}for(const [i,line] of text.split(/\r?\n/).entries()){let ok;if(re){re.lastIndex=0;ok=re.test(line)}else ok=(args.case_sensitive?line:line.toLowerCase()).includes(needle);if(ok){hits.push({path,line:i+1,text:line.slice(0,500)});if(hits.length>=max)return {results:hits,truncated:true}}}}return {results:hits,truncated:false};}
async function ensureHandlePermission(mode='read'){if(!rootHandle)throw new Error('a real directory handle is required');const opts={mode};if(typeof rootHandle.queryPermission==='function'&&await rootHandle.queryPermission(opts)==='granted')return true;if(typeof rootHandle.requestPermission==='function'&&await rootHandle.requestPermission(opts)==='granted')return true;throw new Error(mode==='readwrite'?'write permission was not granted by the browser':'folder permission was not granted by the browser');}
async function ensureWritePermission(){return ensureHandlePermission('readwrite');}
async function editFile(args){if(workspaceMode!=='write'||!rootHandle)throw new Error('workspace is read-only');await ensureWritePermission();const path=cleanPath(args.path),op=String(args.operation||'write');let old='';try{old=await readText(path)}catch(e){if(!['create','write'].includes(op))throw e}let next;if(op==='create')next=String(args.content||'');else if(op==='write')next=String(args.content||'');else if(op==='append')next=old+String(args.content||'');else if(op==='replace'){const needle=String(args.old_text||'');if(!needle)throw new Error('old_text required');const count=old.split(needle).length-1;if(count!==1)throw new Error('old_text must occur exactly once');next=old.replace(needle,String(args.new_text||''))}else throw new Error('unknown edit operation');const h=await modernFileHandle(path,true),w=await h.createWritable();await w.write(next);await w.close();return {path,operation:op,bytes:new Blob([next]).size};}
async function createDir(args){if(workspaceMode!=='write'||!rootHandle)throw new Error('workspace is read-only');await ensureWritePermission();const p=cleanPath(args.path);await modernDir(p,true);return {path:p,created:true};}
function result(data,isError=false){const structured=(data&&typeof data==='object'&&!Array.isArray(data))?data:{result:data};return {content:[{type:'text',text:typeof data==='string'?data:JSON.stringify(data)}],structuredContent:structured,isError};}
async function execute(tool,args){if(!capabilities.includes(tool))throw new Error('tool not available in this workspace: '+tool);if(tool==='workspace_info')return result(await workspaceInfo());if(tool==='file_read'||tool==='code_read')return result({path:cleanPath(args.path),...lineSlice(await readText(args.path),args)});if(tool==='file_tree'||tool==='code_tree')return result(await tree(args));if(tool==='code_search')return result(await search(args,false));if(tool==='code_grep')return result(await search(args,true));if(tool==='file_edit')return result(await editFile(args));if(tool==='directory_create')return result(await createDir(args));throw new Error('unsupported browser tool');}
async function chooseFolder(){await disconnect(true);$('pairPanel').classList.add('hidden');workspaceMode=document.querySelector('input[name=mode]:checked').value;if(!directPicker){status('This browser cannot create a lazy local directory handle from a picker. For full browser workspace access use Chrome/Chromium/Edge.','warn');return}try{const h=await window.showDirectoryPicker({mode:workspaceMode==='write'?'readwrite':'read',id:'triforce-workspace'});rootHandle=h;rootEntry=null;sessionStorage.setItem('tf_workspace_mode',workspaceMode);void saveDirectoryHandle(h);$('folderName').textContent=(h.name||'Local folder')+' · '+(workspaceMode==='write'?'Read/Write':'Read only');$('connectBtn').disabled=false;status('Folder handle shared. Nothing has been enumerated or read.','ok');$('analysis').textContent='Lazy directory handle ready.';}catch(e){if(e.name==='AbortError')status('Folder was not shared. The browser may have cancelled the selection or rejected a sensitive directory such as the filesystem/home root.','warn');else status('Folder selection failed: '+e.message,'warn')}}
function useDroppedHandle(h){rootHandle=h;rootEntry=null;workspaceMode='read_only';document.querySelector('input[name=mode][value=read_only]').checked=true;$('folderName').textContent=(h.name||'Dropped folder')+' · lazy Read only';$('connectBtn').disabled=false;status('Folder handle shared by drag-and-drop. Nothing has been enumerated.','ok');$('analysis').textContent='Lazy directory handle ready.';}
function useDroppedEntry(e){rootEntry=e;rootHandle=null;workspaceMode='read_only';document.querySelector('input[name=mode][value=read_only]').checked=true;$('folderName').textContent=(e.name||'Dropped folder')+' · lazy Read only';$('connectBtn').disabled=false;status('Folder entry shared by drag-and-drop. Nothing has been enumerated.','ok');$('analysis').textContent='Lazy read-only directory entry ready.';}
const dz=$('dropZone');for(const ev of ['dragenter','dragover'])dz.addEventListener(ev,e=>{e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect='copy';dz.style.borderColor='#58a6ff'});dz.addEventListener('dragleave',()=>dz.style.borderColor='');dz.addEventListener('drop',async e=>{e.preventDefault();e.stopPropagation();dz.style.borderColor='';const items=[...(e.dataTransfer?.items||[])];const handlePromises=items.map(i=>typeof i.getAsFileSystemHandle==='function'?i.getAsFileSystemHandle().catch(()=>null):Promise.resolve(null));const legacy=items.map(i=>{try{const fn=i.getAsEntry||i.webkitGetAsEntry;return typeof fn==='function'?fn.call(i):null}catch{return null}}).filter(Boolean);const handles=(await Promise.all(handlePromises)).filter(Boolean),dirHandle=handles.find(h=>h.kind==='directory'),dirEntry=legacy.find(x=>x.isDirectory);if(dirHandle)return useDroppedHandle(dirHandle);if(dirEntry)return useDroppedEntry(dirEntry);status('The browser did not expose a directory handle/entry for this drop.','warn')});
async function handleServer(msg){if(msg.method==='connected'){ws.send(JSON.stringify({jsonrpc:'2.0',method:'client/info',params:{client:'triforce-browser-workspace',platform:navigator.platform||'browser',hostname:'browser',server_version:'2.85-browser',mode:'workspace',workspace:rootHandle?.name||rootEntry?.name||'local',remote_profile:workspaceMode}}));ws.send(JSON.stringify({jsonrpc:'2.0',method:'tools/list',params:{tools:['client_workspace_tool']}}));ws.send(JSON.stringify({jsonrpc:'2.0',method:'workspace/share',params:{task:$('task').value.trim(),mode:workspaceMode,capabilities}}));return}if(msg.method==='workspace/shared'){if(msg.params?.ok){if(msg.params.resume_token){resumeToken=String(msg.params.resume_token);sessionStorage.setItem('tf_resume_token',resumeToken)}reconnectAttempt=0;if(pairCode){$('pair').value=pairCode;$('pairPanel').classList.remove('hidden')}status(msg.params?.resumed?'Workspace resumed after background suspension.':'Workspace ready. Paste the ID into your AI chat.','ok');$('connectBtn').disabled=true;$('disconnectBtn').disabled=false}else status('TriForce rejected workspace: '+(msg.params?.error||'unknown error'),'warn');return}if(msg.method==='tools/call'){let r;try{if(msg.params?.name!=='client_workspace_tool')throw new Error('unexpected tool');const outer=msg.params.arguments||{};r=await execute(String(outer.tool||''),outer.arguments||{})}catch(e){r=result({ok:false,error:String(e.message||e)},true)}ws.send(JSON.stringify({jsonrpc:'2.0',id:msg.id,result:r}));return}if(msg.method==='ping')ws.send(JSON.stringify({jsonrpc:'2.0',method:'pong'}));}
function stopHeartbeat(){if(heartbeatTimer){clearInterval(heartbeatTimer);heartbeatTimer=null}}
function startHeartbeat(){stopHeartbeat();heartbeatTimer=setInterval(()=>{if(ws&&ws.readyState===WebSocket.OPEN){try{ws.send(JSON.stringify({jsonrpc:'2.0',method:'ping',params:{ts:Date.now()}}))}catch{}}},10000)}
async function openWorkspaceSocket(resume=false){if(ws&&ws.readyState<=1)return;const proto=location.protocol==='https:'?'wss:':'ws:';const credential=resume?'&resume_token='+encodeURIComponent(resumeToken):'&pair_code='+encodeURIComponent(pairCode);manualDisconnect=false;ws=new WebSocket(proto+'//'+location.host+'/v1/mcp/node/connect?mode=workspace'+credential+'&machine_id=browser&client_version=2.85-browser');ws.onopen=()=>startHeartbeat();ws.onmessage=async e=>{try{await handleServer(JSON.parse(e.data))}catch(err){status('Workspace error: '+err.message,'warn')}};ws.onerror=()=>{};ws.onclose=()=>{stopHeartbeat();ws=null;$('disconnectBtn').disabled=true;if(manualDisconnect)return;if(rootHandle||rootEntry)$('connectBtn').disabled=false;if(resumeToken){status(document.hidden?'Workspace paused by the browser. It will reconnect when visible.':'Workspace connection lost. Reconnecting…','warn');scheduleReconnect()}};}
async function connect(){if(!rootHandle&&!rootEntry)return;workspaceMode=rootEntry?'read_only':document.querySelector('input[name=mode]:checked').value;sessionStorage.setItem('tf_workspace_mode',workspaceMode);if(rootHandle)await ensureHandlePermission(workspaceMode==='write'?'readwrite':'read');capabilities=[...READ_TOOLS,...(workspaceMode==='write'&&rootHandle?WRITE_TOOLS:[])];if(resumeToken){status('Resuming workspace…');await openWorkspaceSocket(true);return}status('Creating one-time workspace ID…');$('connectBtn').disabled=true;try{const r=await fetch('/v1/mcp/workspace/pair-ticket',{method:'POST',headers:{Accept:'application/json'}});if(!r.ok)throw new Error('ticket request failed: '+r.status);const ticket=await r.json();pairCode=String(ticket.pair_code||'');if(!pairCode)throw new Error('server returned no pairing ID');sessionStorage.setItem('tf_pair_code',pairCode)}catch(e){status('Could not create pairing ID: '+e.message,'warn');$('connectBtn').disabled=false;return}status('Connecting browser workspace to TriForce…');await openWorkspaceSocket(false);}
function scheduleReconnect(){if(manualDisconnect||!resumeToken||reconnectTimer||document.hidden||!navigator.onLine)return;const delay=Math.min(10000,500*Math.pow(2,Math.min(reconnectAttempt++,4)));reconnectTimer=setTimeout(async()=>{reconnectTimer=null;await autoResume()},delay)}
async function autoResume(){if(manualDisconnect||!resumeToken||(!rootHandle&&!rootEntry)||document.hidden||!navigator.onLine||(ws&&ws.readyState<=1))return;if(rootHandle&&typeof rootHandle.queryPermission==='function'){const needed=workspaceMode==='write'?'readwrite':'read';let perm='prompt';try{perm=await rootHandle.queryPermission({mode:needed})}catch{}if(perm!=='granted'){status('Workspace is paused. Tap Connect workspace to restore browser permission.','warn');$('connectBtn').disabled=false;return}}status('Restoring workspace connection…');await openWorkspaceSocket(true)}
async function restoreSavedWorkspace(){if(!directPicker||rootHandle||rootEntry)return;const h=await loadDirectoryHandle();if(!h||h.kind!=='directory')return;rootHandle=h;workspaceMode=sessionStorage.getItem('tf_workspace_mode')||'read_only';const radio=document.querySelector('input[name=mode][value='+workspaceMode+']');if(radio)radio.checked=true;$('folderName').textContent=(h.name||'Local folder')+' · saved '+(workspaceMode==='write'?'Read/Write':'Read only');$('connectBtn').disabled=false;status('Saved local folder restored. Checking connection…','ok');await autoResume()}
async function disconnect(clearHandle=true){manualDisconnect=true;stopHeartbeat();if(reconnectTimer){clearTimeout(reconnectTimer);reconnectTimer=null}if(ws){try{ws.send(JSON.stringify({jsonrpc:'2.0',method:'workspace/revoke',params:{}}));ws.close()}catch{}ws=null}resumeToken='';pairCode='';sessionStorage.removeItem('tf_resume_token');sessionStorage.removeItem('tf_pair_code');if(clearHandle){sessionStorage.removeItem('tf_workspace_mode');await clearDirectoryHandle();rootHandle=null;rootEntry=null;$('folderName').textContent='No folder shared'}$('disconnectBtn').disabled=true;$('pairPanel').classList.add('hidden');$('connectBtn').disabled=!(rootHandle||rootEntry);if(clearHandle)status('Workspace disconnected.','warn')}
$('chooseBtn').onclick=chooseFolder;$('openChromeBtn').onclick=()=>{const target='intent://'+location.host+location.pathname+location.search+'#Intent;scheme=https;package=com.android.chrome;end';location.href=target};$('connectBtn').onclick=connect;$('disconnectBtn').onclick=disconnect;$('copyBtn').onclick=()=>navigator.clipboard.writeText(pairCode);$('copyMcp').onclick=()=>navigator.clipboard.writeText($('mcpUrl').value);document.addEventListener('visibilitychange',()=>{if(!document.hidden)autoResume()});window.addEventListener('pageshow',()=>autoResume());window.addEventListener('online',()=>autoResume());window.addEventListener('pagehide',()=>{if(ws)try{ws.close()}catch{}});window.addEventListener('error',e=>{status('Browser script error: '+(e.message||'unknown error'),'warn')});window.addEventListener('unhandledrejection',e=>{status('Browser script error: '+String(e.reason?.message||e.reason||'unknown error'),'warn')});restoreSavedWorkspace().catch(e=>status('Workspace restore error: '+e.message,'warn'));
</script></main></body></html>'''

def _build_tool_result(result: Any, *, is_error: bool = False) -> Dict[str, Any]:
    """Build an MCP tool result compatible with legacy and structured clients.

    MCP clients that consume ``outputSchema`` expect ``structuredContent`` to
    be a JSON object. Legacy clients still read serialized JSON from the text
    content block, so both representations share one canonical serialization.
    """
    text = json.dumps(
        result,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    json_value = json.loads(text)
    structured_content = json_value if isinstance(json_value, dict) else {"result": json_value}
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": is_error,
    }


def _maybe_block_write_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    request: Optional[Request] = None,
    requested_tool_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Block tools that are not allowed for the current MCP client."""
    if request is None or is_tool_allowed(tool_name, request, arguments):
        return None
    payload = {
        "ok": False,
        "error": "Tool is not allowed for this MCP client",
        "code": "MCP_TOOL_FORBIDDEN",
        "tool_name": requested_tool_name or tool_name,
        "resolved_tool_name": tool_name,
    }
    state = getattr(request, "state", None) if request is not None else None
    mcp_logger.warning(
        "MCP_TOOL_FORBIDDEN | requested=%s | resolved=%s | auth_method=%s | auth_user=%s | ip=%s",
        requested_tool_name or tool_name, tool_name,
        getattr(state, "mcp_auth_method", None) if state is not None else None,
        getattr(state, "mcp_auth_user", None) if state is not None else None,
        client_ip(request) if request is not None else None,
    )
    return _build_tool_result(payload, is_error=True)


def _tool_handler_accepts_request(handler: Callable[..., Awaitable[Any]]) -> bool:
    try:
        return "request" in inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False


async def _call_tool_handler(
    handler: Callable[..., Awaitable[Any]],
    arguments: Dict[str, Any],
    request: Optional[Request] = None,
) -> Any:
    if request is not None and _tool_handler_accepts_request(handler):
        request_kwargs = {"request": request}
        return await handler(arguments, **request_kwargs)
    return await handler(arguments)


async def _call_mcp_method_handler(
    method: str,
    handler: Callable[..., Awaitable[Any]],
    params: Dict[str, Any],
    request: Request,
) -> Any:
    if method in {"tools/list", "tools/call"}:
        return await handler(params, request=request)
    return await handler(params)


def _finish_tools_list(
    tools: List[Dict[str, Any]],
    version: str,
    request: Optional[Request] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    filtered_tools = []
    for tool in _filter_tools_for_client(tools, request):
        # Do not mutate shared registry definitions while supplying the MCP
        # safety metadata consumed by ai-coder's local approval broker.
        decorated = dict(tool)
        annotations = dict(decorated.get("annotations") or {})
        annotations.setdefault("readOnlyHint", is_readonly_tool(str(decorated.get("name") or "")))
        decorated["annotations"] = annotations
        filtered_tools.append(decorated)
    if request is not None:
        try:
            from app.services.mcp_workspace_bridge import merge_public_workspace_tools
            filtered_tools = merge_public_workspace_tools(filtered_tools, request)
        except Exception as exc:
            mcp_logger.warning("Public workspace tool merge failed: %s", exc)
    result: Dict[str, Any] = {
        "tools": filtered_tools,
        "version": version,
        "count": len(filtered_tools),
    }
    if note:
        result["note"] = note
    return result


def _filter_tools_for_client(tools: List[Dict[str, Any]], request: Optional[Request] = None) -> List[Dict[str, Any]]:
    """Apply normal MCP authorization; ai-coder is an identity, not a deny profile."""
    if request is None:
        return tools
    if is_internal_full_request(request):
        return tools
    return filter_tools_for_external(tools, request=request)


@public_router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    """Public OAuth metadata for MCP client discovery."""
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
    })


public_oauth_authorization_server_metadata = oauth_authorization_server_metadata


@public_router.post("/mcp/workspace/pair-ticket", tags=["MCP"], summary="Create one-time browser workspace pairing ticket")
async def create_browser_workspace_pair_ticket(request: Request) -> JSONResponse:
    """Mint a bounded, short-lived ID after the browser selected a local folder."""
    from app.services.mcp_workspace_sessions import PAIR_TTL_SECONDS, create_web_pair_code
    code = create_web_pair_code()
    return JSONResponse(
        {"pair_code": code, "expires_seconds": PAIR_TTL_SECONDS},
        headers={"Cache-Control": "no-store"},
    )


@public_router.get("/.well-known/mcp")
async def public_mcp_metadata(request: Request) -> Dict[str, Any]:
    """Public MCP server metadata for desktop and web clients."""
    base = str(request.base_url).rstrip("/")
    return {
        "name": "ailinux-mcp-server",
        "version": VERSION,
        "transport": "streamable-http",
        "endpoint": f"{base}/v1/mcp",
        "authorization_server": f"{base}/v1/.well-known/oauth-authorization-server",
        "protocol_versions": ["2024-11-05", "2025-03-26"],
    }


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text.split()))


def _serialize_job(job) -> Dict[str, Any]:
    payload = job.to_dict()
    payload["allowed_domains"] = list(job.allowed_domains)
    return payload


async def handle_crawl_url(params: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    url = params.get("url")
    if not url:
        raise ValueError("'url' parameter is required for crawl.url")

    keywords = params.get("keywords")
    if keywords is not None and not isinstance(keywords, Iterable):
        raise ValueError("'keywords' must be an iterable of strings")

    from ..services.crawler.user_crawler import get_user_crawler
    job = await get_user_crawler().crawl_url(
        url=url,
        keywords=list(keywords) if keywords else None,
        max_pages=int(params.get("max_pages", 10)),
        idempotency_key=params.get("idempotency_key"),
        allow_private_networks=bool(request and is_internal_full_request(request)),
    )
    return {"job": _serialize_job(job)}


async def handle_crawl_site(params: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    site_url = params.get("site_url")
    if not site_url:
        raise ValueError("'site_url' parameter is required for crawl.site")

    seeds = params.get("seeds") or [site_url]
    if not isinstance(seeds, Iterable):
        raise ValueError("'seeds' must be an iterable of URLs")

    keywords = params.get("keywords") or []
    if keywords and not isinstance(keywords, Iterable):
        raise ValueError("'keywords' must be iterable when provided")

    from ..services.crawler.manager import crawler_manager as _crawler_manager
    job = await _crawler_manager.create_job(
        keywords=list(keywords) if keywords else [site_url],
        seeds=[str(seed) for seed in seeds],
        max_depth=int(params.get("max_depth", 2)),
        max_pages=int(params.get("max_pages", 40)),
        allow_external=bool(params.get("allow_external", False)),
        relevance_threshold=float(params.get("relevance_threshold", 0.35)),
        requested_by="mcp",
        priority=params.get("priority", "low"),
        idempotency_key=params.get("idempotency_key"),
        allow_private_networks=bool(request and is_internal_full_request(request)),
    )
    return {"job": _serialize_job(job)}


async def handle_crawl_status(params: Dict[str, Any]) -> Dict[str, Any]:
    job_id = params.get("job_id")
    if not job_id:
        raise ValueError("'job_id' parameter is required for crawl.status")

    from ..services.crawler.user_crawler import get_user_crawler
    job = await get_user_crawler().get_job(job_id)
    source = "user"
    from ..services.crawler.user_crawler import get_user_crawler
    manager = get_user_crawler()
    if not job:
        from ..services.crawler.manager import crawler_manager as _crawler_manager
        job = await _crawler_manager.get_job(job_id)
        source = "manager"
        from ..services.crawler.manager import crawler_manager as _crawler_manager
        manager = _crawler_manager
    if not job:
        raise ValueError(f"Crawler job '{job_id}' not found")

    include_results = params.get("include_results", False)
    include_content = params.get("include_content", False)
    results: List[Dict[str, Any]] = []
    if include_results:
        for result_id in job.results:
            result = await manager.get_result(result_id)  # type: ignore[attr-defined]
            if result:
                results.append(result.to_dict(include_content=include_content))

    payload = _serialize_job(job)
    payload["source"] = source
    payload["results"] = results
    return payload


async def handle_posts_create(params: Dict[str, Any]) -> Dict[str, Any]:
    title = params.get("title")
    content = params.get("content")
    status_value = params.get("status", "publish")
    categories = params.get("categories")
    featured_media = params.get("featured_media")

    if not title or not content:
        raise ValueError("'title' and 'content' are required for posts.create")

    result = await wordpress_service.create_post(
        title=title,
        content=content,
        status=status_value,
        categories=categories,
        featured_media=featured_media,
    )
    return result


async def handle_media_upload(params: Dict[str, Any]) -> Dict[str, Any]:
    file_data = params.get("file_data")
    filename = params.get("filename")
    content_type = params.get("content_type", "application/octet-stream")

    if not file_data or not filename:
        raise ValueError("'file_data' and 'filename' are required for media.upload")

    try:
        binary = base64.b64decode(file_data)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid base64 payload: {exc}") from exc

    result = await wordpress_service.upload_media(
        filename=filename,
        file_content=binary,
        content_type=content_type,
    )
    return result




async def handle_web_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search the web with pagination and language support (Multi-API)."""
    from ..services.web_search import search_web
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required for web_search")
    
    num_results = params.get("num_results", 50)
    page = params.get("page", 1)
    per_page = params.get("per_page", 50)
    lang = params.get("lang", "de")  # Sprachparameter
    
    # Multi-API Web-Suche mit Sprachunterstützung
    result = await search_web(
        query, 
        num_results=num_results,
        page=page,
        per_page=per_page,
        lang=lang
    )
    
    return result


async def handle_multi_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Extended Multi-API Search with all providers including Grokipedia and AILinux News."""
    from ..services.multi_search import multi_search_extended
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required for multi_search")
    
    result = await multi_search_extended(
        query=query,
        max_results=params.get("max_results", 50),
        lang=params.get("lang", "de"),
        use_searxng=params.get("use_searxng", True),
        use_ddg=params.get("use_ddg", True),
        use_wiby=params.get("use_wiby", True),
        use_wikipedia=params.get("use_wikipedia", True),
        use_grokipedia=params.get("use_grokipedia", True),
        use_ailinux_news=params.get("use_ailinux_news", True),
        searxng_categories=params.get("categories", "general"),
    )
    return result


async def handle_smart_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """🚀 AI-Powered Smart Search with LLM enhancement (Cerebras/Groq).
    
    Features:
    - Query Expansion (~50ms with Cerebras)
    - Intent Detection (~30ms)
    - Smart Re-Ranking (~80ms)
    - Result Summarization (~300ms with Groq)
    
    Total target: <1000ms
    """
    from ..services.multi_search import smart_search
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required for smart_search")
    
    result = await smart_search(
        query=query,
        max_results=params.get("max_results", 30),
        lang=params.get("lang", "de"),
        use_searxng=params.get("use_searxng", True),
        use_ddg=params.get("use_ddg", True),
        use_wikipedia=params.get("use_wikipedia", True),
        use_grokipedia=params.get("use_grokipedia", True),
        use_ailinux_news=params.get("use_ailinux_news", True),
        expand_query_enabled=params.get("expand_query", True),
        detect_intent_enabled=params.get("detect_intent", True),
        summarize_enabled=params.get("summarize", True),
        smart_rank_enabled=params.get("smart_rank", True),
    )
    return result


async def handle_quick_smart_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """⚡ Quick Smart Search - Speed-optimized for <500ms.
    
    - Query expansion only
    - Fewer sources
    - No summarization
    """
    from ..services.multi_search import quick_smart_search
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required")
    
    result = await quick_smart_search(
        query=query,
        max_results=params.get("max_results", 15),
        lang=params.get("lang", "de"),
    )
    return result


async def handle_search_llm_config(params: Dict[str, Any]) -> Dict[str, Any]:
    """Configure or view LLM settings for smart search."""
    from ..services.multi_search import configure_search_llm, get_available_search_models
    
    action = params.get("action", "view")
    
    if action == "view":
        return get_available_search_models()
    elif action == "configure":
        return configure_search_llm(
            fast_model=params.get("fast_model"),
            quality_model=params.get("quality_model"),
            enable_expansion=params.get("enable_expansion"),
            enable_summary=params.get("enable_summary"),
            enable_ranking=params.get("enable_ranking"),
        )
    else:
        return get_available_search_models()


async def handle_search_health(params: Dict[str, Any]) -> Dict[str, Any]:
    """Check health of all search providers."""
    from ..services.multi_search import check_search_health
    return await check_search_health()


async def handle_ailinux_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search AILinux.me News Archive via WordPress REST API."""
    from ..services.multi_search import search_ailinux
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required")
    
    result = await search_ailinux(query, params.get("num_results", 20))
    # search_ailinux gibt bereits {query, results, total} zurueck
    return result

async def handle_grokipedia_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search Grokipedia.com - xAI knowledge base with 885K+ articles."""
    from ..services.multi_search import search_grokipedia
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required")
    
    result = await search_grokipedia(query, params.get("num_results", 5))
    # search_grokipedia gibt bereits {query, results, total} zurueck
    return result


async def handle_image_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Bildersuche via SearXNG."""
    from ..services.multi_search import image_search
    
    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required")
    
    return await image_search(
        query,
        params.get("num_results", 30),
        params.get("lang", "de")
    )

async def handle_llm_invoke(params: Dict[str, Any]) -> Dict[str, Any]:
    """Chat/LLM invoke - supports both 'message' (string) and 'messages' (array)."""
    model_id = params.get("model") or params.get("provider_id")
    messages_input = params.get("messages")
    message = params.get("message")
    system_prompt = params.get("system_prompt")
    options = params.get("options") or {}
    temperature = options.get("temperature", params.get("temperature"))
    
    # Support both formats
    if messages_input and isinstance(messages_input, list):
        messages = messages_input
    elif message:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})
    else:
        raise ValueError("'message' or 'messages' is required")
    
    if not model_id:
        model_id = "gemini/gemini-2.0-flash"
    
    # Auto-prefix: wenn kein Provider angegeben, versuche bekannte Prefixe
    if "/" not in model_id:
        for prefix in ["gemini", "ollama", "mistral", "groq", "anthropic", "openrouter"]:
            test_id = f"{prefix}/{model_id}"
            test_model = await registry.get_model(test_id)
            if test_model and "chat" in test_model.capabilities:
                model_id = test_id
                break
    
    model = await registry.get_model(model_id)
    if not model or "chat" not in model.capabilities:
        raise ValueError(f"Model '{model_id}' does not support chat capability")
    
    formatted_messages: List[Dict[str, str]] = []
    for entry in messages:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if not role or content is None:
            raise ValueError("Each message must include 'role' and 'content'")
        formatted_messages.append({"role": role, "content": content})
    
    stream = bool(options.get("stream", False))
    chunks: List[str] = []
    async with request_slot():
        async for chunk in chat_service.stream_chat(
            model, model_id,
            (m for m in formatted_messages),
            stream=stream, temperature=temperature,
        ):
            if chunk:
                chunks.append(chunk)
    
    completion = "".join(chunks)
    return {"model": model_id, "provider": model.provider, "output": completion, "response": completion}


async def handle_admin_control(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action")
    instance = params.get("instance")
    if not action or not instance:
        raise ValueError("'action' and 'instance' parameters are required")
    request = CrawlerControlRequest(action=action, instance=instance)
    result = await control_crawler(request)
    return result


async def handle_admin_config_get(_: Dict[str, Any]) -> Dict[str, Any]:
    return await get_crawler_config()


async def handle_admin_config_set(params: Dict[str, Any]) -> Dict[str, Any]:
    allowed_fields = {"user_crawler_workers", "user_crawler_max_concurrent", "auto_crawler_enabled"}
    updates = {key: value for key, value in (params or {}).items() if key in allowed_fields}
    if not updates:
        raise ValueError("No allowed configuration fields provided")
    update_request = CrawlerConfigUpdate(**updates)
    response: CrawlerConfigUpdateResponse = await update_crawler_config(update_request)
    return {"updated": response.updated, "config": response.config.dict()}


# =============================================================================
# NEW: API Documentation Handlers
# =============================================================================

async def handle_api_docs(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get API documentation for Claude Code integration."""
    section = params.get("section")
    return get_api_docs(section)


async def handle_api_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search for relevant API endpoints for a task."""
    task = params.get("task")
    if not task:
        raise ValueError("'task' parameter is required")
    endpoints = get_endpoint_for_task(task)
    return {
        "task": task,
        "endpoints": [
            {
                "path": ep.path,
                "method": ep.method.value,
                "summary": ep.summary,
                "mcp_method": ep.mcp_method
            }
            for ep in endpoints
        ]
    }


# =============================================================================
# NEW: Translation Layer Handlers
# =============================================================================

_translator = BidirectionalTranslator()


async def handle_translate(params: Dict[str, Any]) -> Dict[str, Any]:
    """Translate between API and MCP formats."""
    request = params.get("request")
    if not request:
        raise ValueError("'request' parameter is required")

    output_format = params.get("output_format", "auto")
    result = _translator.translate_and_format(request, output_format)

    if isinstance(result, str):
        return {"format": "curl", "command": result}
    return {"format": "json", "data": result}


async def handle_api_to_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    """Convert REST API call to MCP JSON-RPC."""
    method = params.get("method", "GET")
    path = params.get("path")
    body = params.get("body", {})

    if not path:
        raise ValueError("'path' parameter is required")

    api_translator = APIToMCPTranslator()
    result = api_translator.translate(method, path, body)

    if not result.success:
        raise ValueError(result.error)

    return {
        "mcp_method": result.method,
        "params": result.data,
        "jsonrpc": {
            "jsonrpc": "2.0",
            "method": result.method,
            "params": result.data,
            "id": 1
        }
    }


async def handle_mcp_to_api(params: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MCP method call to REST API request."""
    mcp_method = params.get("method")
    mcp_params = params.get("params", {})

    if not mcp_method:
        raise ValueError("'method' parameter is required")

    mcp_translator = MCPToAPITranslator()
    result = mcp_translator.translate(mcp_method, mcp_params)

    if not result.success:
        raise ValueError(result.error)

    return {
        "http_method": result.method,
        "body": result.data,
        "query_params": result.query_params,
        "curl": mcp_translator.to_curl(mcp_method, mcp_params)
    }


# =============================================================================
# NEW: Specialist Routing Handlers
# =============================================================================

async def handle_models_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """
    List available AI models, optimized for context window usage.
    Returns models grouped by provider and capability.
    """
    models = await registry.list_models()
    
    # Group by provider
    by_provider = {}
    for m in models:
        if m.provider not in by_provider:
            by_provider[m.provider] = []
        by_provider[m.provider].append(m.id)

    # Group by key capabilities — these become the tabs in Nova-AI-Frontend.
    # Each tab corresponds to one capability tag emitted by detect_capabilities()
    # in app/services/model_registry.py. Keep order stable: it drives tab order.
    by_capability = {
        "chat": [],            # Standard text chat & multi-turn dialogue
        "vision": [],          # Multimodal models accepting image input (Gemini, Pixtral, GPT-4o, ...)
        "image_gen": [],       # Imagen 4, Nano Banana 2, FLUX, SDXL, dall-e
        "video_gen": [],       # Veo 3.1, Sora
        "audio": [],           # Whisper / Voxtral STT, Gemini TTS, Live API, native-audio
        "audio_gen": [],       # Lyria 3, Suno-style music generators
        "embedding": [],       # gemini-embedding-001, mistral-embed, codestral-embed, BGE
        "code": [],             # Codestral, DeepSeek-Coder, qwen-coder, codex
        "reasoning": [],       # o1/o3, DeepSeek-R1, Magistral, thinking variants
        "function_calling": [], # Models advertising native tool use
        "moderation": [],      # llama-guard, mistral-moderation
        "ocr": [],             # mistral-ocr, document-vision
    }

    for m in models:
        for cap in m.capabilities:
            if cap in by_capability:
                by_capability[cap].append(m.id)

    # Simplify lists - strictly limit to ID strings to save tokens
    return {
        "summary": {
            "total_models": len(models),
            "providers": list(by_provider.keys())
        },
        "by_provider": by_provider,
        "by_capability": {k: v[:50] for k, v in by_capability.items()}, # Limit to top 50 per cap to prevent overflow
        "note": "Lists are truncated to top 50 per capability to save context."
    }


async def handle_specialists_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List all available model specialists."""
    return {
        "specialists": specialist_router.list_specialists(),
        "count": len(SPECIALISTS)
    }


async def handle_specialists_route(params: Dict[str, Any]) -> Dict[str, Any]:
    """Route a task to the best specialist(s)."""
    task = params.get("task")
    if not task:
        raise ValueError("'task' parameter is required")

    preferred_speed = params.get("preferred_speed")
    max_cost = params.get("max_cost_tier")

    specialists = specialist_router.route(
        task,
        preferred_speed=preferred_speed,
        max_cost_tier=max_cost
    )

    return {
        "task": task,
        "recommended": [s.to_dict() for s in specialists[:3]],
        "all_matches": len(specialists)
    }


async def handle_specialists_invoke(params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a specialist model with automatic routing."""
    task = params.get("task")
    message = params.get("message")
    specialist_id = params.get("specialist_id")

    if not message:
        raise ValueError("'message' parameter is required")

    # Get specialist - either specified or auto-routed
    if specialist_id:
        specialist = specialist_router.get_specialist_by_id(specialist_id)
        if not specialist:
            raise ValueError(f"Specialist '{specialist_id}' not found")
    elif task:
        specialist = specialist_router.get_best_specialist(task)
        if not specialist:
            raise ValueError("No suitable specialist found for task")
    else:
        raise ValueError("Either 'task' or 'specialist_id' is required")

    # Build messages with optional system prompt
    messages = []
    if specialist.system_prompt_template:
        messages.append({"role": "system", "content": specialist.system_prompt_template})
    messages.append({"role": "user", "content": message})

    # Invoke the model
    result = await handle_llm_invoke({
        "model": specialist.id,
        "messages": messages,
        "options": params.get("options", {})
    })

    result["specialist"] = specialist.to_dict()
    return result


async def handle_nova_chat_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke Nova's account-backed chat agent."""
    from ..services.nova_chat_agent import nova_chat_agent_service

    return await nova_chat_agent_service.chat(
        provider=params.get("provider", "auto"),
        message=params.get("message", ""),
        messages=params.get("messages"),
        system=params.get("system", ""),
        model=params.get("model"),
        temperature=float(params.get("temperature", 0.4)),
        max_tokens=int(params.get("max_tokens", 1200)),
        timeout=int(params.get("timeout", 120)),
    )


# =============================================================================
# NEW: Context Management Handlers
# =============================================================================

async def handle_context_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new conversation context."""
    session_id = params.get("session_id")
    system_prompt = params.get("system_prompt")
    metadata = params.get("metadata", {})

    context = context_manager.create_context(session_id, system_prompt, metadata)
    return context.get_summary()


async def handle_context_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get an existing conversation context."""
    session_id = params.get("session_id")
    if not session_id:
        raise ValueError("'session_id' is required")

    context = context_manager.get_context(session_id)
    if not context:
        raise ValueError(f"Context '{session_id}' not found or expired")

    return context.to_dict()


async def handle_context_message(params: Dict[str, Any]) -> Dict[str, Any]:
    """Add a message to a context and optionally get LLM response."""
    session_id = params.get("session_id")
    message = params.get("message")
    get_response = params.get("get_response", False)
    model = params.get("model")

    if not session_id or not message:
        raise ValueError("'session_id' and 'message' are required")

    context = context_manager.get_or_create_context(session_id)
    context.add_user_message(message)

    result: Dict[str, Any] = {"session_id": session_id, "message_added": True}

    if get_response and model:
        # Get LLM response
        messages = context.get_messages_for_api()
        llm_result = await handle_llm_invoke({
            "model": model,
            "messages": messages,
            "options": params.get("options", {})
        })
        context.add_assistant_message(llm_result["output"])
        result["response"] = llm_result

    result["context_summary"] = context.get_summary()
    return result


async def handle_context_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List all active contexts."""
    return {"contexts": context_manager.list_contexts()}


async def handle_context_clear(params: Dict[str, Any]) -> Dict[str, Any]:
    """Clear messages from a context."""
    session_id = params.get("session_id")
    if not session_id:
        raise ValueError("'session_id' is required")

    context = context_manager.get_context(session_id)
    if not context:
        raise ValueError(f"Context '{session_id}' not found")

    context.clear()
    return {"session_id": session_id, "cleared": True}


# =============================================================================
# NEW: Prompt Library Handlers
# =============================================================================

async def handle_prompts_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List available prompt templates."""
    templates = prompt_library.list_templates()
    return {
        "templates": [
            prompt_library.get_template_info(t)
            for t in templates
        ]
    }


async def handle_prompts_render(params: Dict[str, Any]) -> Dict[str, Any]:
    """Render a prompt template with variables."""
    name = params.get("name")
    variables = params.get("variables", {})

    if not name:
        raise ValueError("'name' is required")

    rendered = prompt_library.render(name, **variables)
    return {"name": name, "rendered": rendered}


async def handle_prompts_add(params: Dict[str, Any]) -> Dict[str, Any]:
    """Add a custom prompt template."""
    name = params.get("name")
    template = params.get("template")

    if not name or not template:
        raise ValueError("'name' and 'template' are required")

    prompt_library.add_template(name, template)
    return {"name": name, "added": True}


# =============================================================================
# NEW: Workflow Orchestration Handlers
# =============================================================================

async def handle_workflows_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List workflow templates and active workflows."""
    return {
        "templates": workflow_manager.list_templates(),
        "active": workflow_manager.list_workflows()
    }


async def handle_workflows_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new workflow from a template."""
    template_name = params.get("template")
    workflow_id = params.get("workflow_id")
    initial_context = params.get("context", {})

    if not template_name:
        raise ValueError("'template' is required")

    workflow = workflow_manager.create_workflow(
        template_name, workflow_id, initial_context
    )
    return workflow.to_dict()


async def handle_workflows_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get workflow status."""
    workflow_id = params.get("workflow_id")
    if not workflow_id:
        raise ValueError("'workflow_id' is required")

    workflow = workflow_manager.get_workflow(workflow_id)
    if not workflow:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    return workflow.to_dict()


@router.get("/mcp/status", tags=["MCP"], summary="Health check for MCP subsystem")
async def mcp_status() -> Dict[str, Any]:
    try:
        models = await registry.list_models()
        status = "ok"
        model_count = len(models)
        payload: Dict[str, Any] = {}
    except Exception as exc:  # pragma: no cover - defensive fallback
        status = "degraded"
        model_count = 0
        payload = {"error": str(exc)}

    payload.update(
        {
            "status": status,
            "methods": sorted(MCP_HANDLERS.keys()),
            "model_count": model_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    return payload


# ============================================================================
# INIT ENDPOINTS - Shortcode Documentation & Auto-Decode
# Für CLI Coding Agents: /v1/init, /mcp/init, /triforce/init
# ============================================================================

@router.get("/init", tags=["Init"], summary="Init endpoint with shortcode documentation")
@router.get("/mcp/init", tags=["Init"], summary="MCP Init endpoint")
async def mcp_init_endpoint(
    agent_id: Optional[str] = None,
    include_docs: bool = True,
    include_tools: bool = True,
    decode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Initialisierungs-Endpoint für CLI Coding Agents.

    Liefert:
    - Shortcode Protocol v2.0 Dokumentation
    - Agent-spezifischen System-Prompt
    - Verfügbare Tools und Endpoints
    - Loadbalancer-Empfehlung
    - Optional: Shortcode-Decodierung

    Alle CLI Agents sollten beim Start /init aufrufen um:
    1. Die Shortcode-Syntax zu lernen
    2. Verfügbare Tools zu kennen
    3. Den besten Endpoint für Lastverteilung zu erfahren

    Beispiel:
        GET /mcp/init?agent_id=claude-mcp
        GET /v1/init?decode=@g>>@c !code "test"
    """
    return await init_service.get_init_response(
        endpoint="mcp",
        agent_id=agent_id,
        include_docs=include_docs,
        include_tools=include_tools,
        decode_shortcode=decode,
    )


@router.post("/init/decode", tags=["Init"], summary="Decode a shortcode")
async def decode_shortcode_endpoint(
    shortcode: str,
) -> Dict[str, Any]:
    """
    Decodiert einen Shortcode in menschenlesbare Form.

    Beispiel:
        POST /init/decode
        {"shortcode": "@gemini>!generate[claudeprompt]@mcp>@claude>[outputtoken]"}

    Returns:
        - raw: Original Shortcode
        - decoded: Strukturierte Pipeline
        - human_readable: Menschenlesbare Beschreibung
        - is_valid: Validierungsstatus
    """
    from ..services.tristar.shortcodes import auto_decode_shortcode
    return auto_decode_shortcode(shortcode)


@router.post("/init/execute", tags=["Init"], summary="Decode and execute a shortcode")
async def execute_shortcode_endpoint(
    shortcode: str,
    source_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Decodiert und führt einen Shortcode aus.

    Der Shortcode wird:
    1. Geparst und validiert
    2. In einzelne Pipeline-Steps zerlegt
    3. Über die entsprechenden Agents/MCP geroutet
    4. Ergebnisse werden zurückgegeben

    Beispiel:
        POST /init/execute
        {"shortcode": "@g>>@c !code 'hello world'", "source_agent": "nova-mcp"}
    """
    return await init_service.decode_and_execute(
        shortcode=shortcode,
        source_agent=source_agent,
    )


@router.get("/init/loadbalancer", tags=["Init"], summary="Get loadbalancer recommendation")
async def loadbalancer_endpoint() -> Dict[str, Any]:
    """
    Gibt Loadbalancer-Statistiken und Empfehlung zurück.

    Hilft CLI Agents den optimalen Endpoint zu wählen:
    - /v1/ für REST API
    - /mcp/ für MCP Protocol
    - /triforce/ für TriForce Integration

    Die Empfehlung basiert auf:
    - Aktuelle Latenz
    - Fehlerrate
    - Queue-Länge
    """
    return {
        "recommended": await loadbalancer.get_best_endpoint(),
        "stats": loadbalancer.get_stats(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/init/brain", tags=["Init"], summary="MCP Server Brain status")
async def mcp_brain_endpoint() -> Dict[str, Any]:
    """
    Status des MCP Server "Brain" (Mitdenk-Funktion).

    Der MCP Server sammelt Änderungen und sendet
    regelmäßig Updates an Gemini Lead.

    Dies ermöglicht:
    - Proaktive Koordination
    - System-Überblick für Gemini
    - Automatische Sync-Updates
    """
    return {
        "brain_status": mcp_brain.get_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/init/models", tags=["Init"], summary="Gemini initializes all models")
async def gemini_init_models_endpoint(
    include_ollama: bool = True,
    include_cloud: bool = True,
    include_cli: bool = True,
) -> Dict[str, Any]:
    """
    Gemini Lead initialisiert alle Modelle und CLI Agents.

    Reihenfolge:
    1. CLI Agents (claude-mcp, codex-mcp, mistral-mcp)
    2. Ollama-Modelle (qwen, deepseek, mistral, etc.)
    3. Cloud-Modelle (registrieren für Mesh)

    Dies ermöglicht:
    - Einheitliche System-Prompts für alle Modelle
    - Shortcode-Protokoll v2.0 Kommunikation
    - Koordinierte Multi-LLM Orchestrierung
    """
    return await gemini_model_init.init_all(
        include_ollama=include_ollama,
        include_cloud=include_cloud,
        include_cli=include_cli,
    )


@router.get("/init/models", tags=["Init"], summary="Get initialized models")
async def get_initialized_models_endpoint() -> Dict[str, Any]:
    """
    Gibt alle initialisierten Modelle zurück.

    Zeigt:
    - CLI Agents mit Status
    - Ollama-Modelle mit Rollen
    - Cloud-Modelle mit Capabilities
    """
    return {
        "models": gemini_model_init.get_initialized_models(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/init/model/{model_name}", tags=["Init"], summary="Initialize specific model")
async def init_specific_model_endpoint(
    model_name: str,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Initialisiert ein spezifisches Modell.

    Unterstützt:
    - Ollama-Modelle (z.B. qwen2.5:14b)
    - Cloud-Modelle (z.B. gemini/gemini-2.0-flash)
    - CLI Agents (z.B. claude-mcp)
    """
    from ..services.gemini_model_init import OLLAMA_MODELS, CLOUD_MODELS

    if model_name in OLLAMA_MODELS:
        return await gemini_model_init.init_ollama_model(model_name, system_prompt)
    elif model_name in CLOUD_MODELS:
        return await gemini_model_init.init_cloud_model(model_name, system_prompt)
    elif model_name.endswith("-mcp"):
        return await gemini_model_init.init_cli_agent(model_name)
    else:
        return {
            "success": False,
            "error": f"Unknown model: {model_name}",
            "available_ollama": list(OLLAMA_MODELS.keys()),
            "available_cloud": list(CLOUD_MODELS.keys()),
        }


# ============================================================================
# Bootstrap Endpoints
# ============================================================================

@router.post("/bootstrap", tags=["Bootstrap"], summary="Bootstrap all CLI agents")
async def bootstrap_agents_endpoint(
    sequential_lead: bool = True,
) -> Dict[str, Any]:
    """
    Startet alle CLI Agents und pusht /init.

    Bootstrap-Reihenfolge:
    1. gemini-mcp (Lead) - zuerst
    2. claude-mcp, codex-mcp, opencode-mcp (parallel)

    Features:
    - Shortcode Protocol v2.0 wird gepusht
    - Agents werden mit /init initialisiert
    - Rate Limiting aktiviert
    """
    return await bootstrap_service.bootstrap_all(sequential_lead=sequential_lead)


@router.post("/bootstrap/{agent_id}", tags=["Bootstrap"], summary="Wakeup single agent")
async def wakeup_agent_endpoint(agent_id: str) -> Dict[str, Any]:
    """
    Weckt einen einzelnen Agent auf.

    Startet den Agent und pusht /init.
    """
    return await bootstrap_service.wakeup_agent(agent_id)


@router.get("/bootstrap/status", tags=["Bootstrap"], summary="Bootstrap status")
async def bootstrap_status_endpoint() -> Dict[str, Any]:
    """
    Gibt den aktuellen Bootstrap-Status zurück.

    Zeigt:
    - Initialisierte Agents
    - Ausstehende Agents
    - Boot-Dauer
    """
    return bootstrap_service.get_status()


@router.post("/agents/{agent_id}/initialized", tags=["Bootstrap"], summary="Agent initialized callback")
async def agent_initialized_callback(agent_id: str, request: Request) -> Dict[str, Any]:
    """
    Callback-Endpoint für CLI Agents um ihre Initialisierung zu melden.
    
    Wird von den TriForce Wrapper-Scripts aufgerufen wenn ein Agent startet.
    Aktualisiert den Bootstrap-Status auf 'initialized'.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    # Agent als initialisiert markieren
    bootstrap_service._initialized_agents.add(agent_id)
    if agent_id in bootstrap_service._init_results:
        bootstrap_service._init_results[agent_id]["status"] = "initialized"
        bootstrap_service._init_results[agent_id]["init_pushed"] = True
    
    logger.info(f"Agent {agent_id} reported as initialized")
    
    return {
        "status": "ok",
        "agent_id": agent_id,
        "message": f"Agent {agent_id} marked as initialized",
        "initialized_agents": list(bootstrap_service._initialized_agents)
    }


@router.post("/agent/output/process", tags=["Bootstrap"], summary="Process agent output")
async def process_agent_output_endpoint(
    agent_id: str,
    output: str,
) -> Dict[str, Any]:
    """
    Verarbeitet Agent Output und führt Shortcode Commands aus.

    Features:
    - Extrahiert Shortcodes aus Output
    - Validiert gegen Whitelist
    - Rate Limiting
    - Führt Commands aus
    """
    return await chat_processor.process_output(agent_id, output)


@router.get("/agent/ratelimit", tags=["Bootstrap"], summary="Rate limit stats")
async def rate_limit_stats_endpoint(
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Gibt Rate Limit Statistiken zurück.

    Zeigt pro Agent:
    - Verbleibende Tokens
    - Commands pro Minute/Stunde
    - Violations
    """
    from ..services.agent_bootstrap import rate_limiter
    return rate_limiter.get_stats(agent_id)


@router.post("/shortcode/extract", tags=["Bootstrap"], summary="Extract shortcodes from text")
async def extract_shortcodes_endpoint(text: str) -> Dict[str, Any]:
    """
    Extrahiert Shortcodes aus Text ohne Ausführung.

    Nützlich zum Testen des Shortcode-Parsers.
    """
    commands = shortcode_filter.extract_commands(text)
    return {
        "text_length": len(text),
        "commands_found": len(commands),
        "commands": [
            {
                "raw": cmd.raw,
                "source": cmd.source_agent,
                "target": cmd.target_agent,
                "action": cmd.action,
                "content": cmd.content,
                "flow": cmd.flow,
                "priority": cmd.priority,
                "is_blocked": cmd.is_blocked,
                "requires_confirmation": cmd.requires_confirmation,
            }
            for cmd in commands
        ],
    }


# ============================================================================
# Codebase REST Endpoints - Direct access to codebase edit functions
# ============================================================================

@router.get("/codebase/structure", tags=["Codebase"], summary="Get backend codebase structure")
async def codebase_structure_endpoint(
    path: str = "app",
    include_files: bool = True,
    max_depth: int = 4,
) -> Dict[str, Any]:
    """Returns directory structure of the backend codebase."""
    return await handle_codebase_structure({
        "path": path,
        "include_files": include_files,
        "max_depth": max_depth,
    })


@router.get("/codebase/file", tags=["Codebase"], summary="Read a file from codebase")
async def codebase_file_endpoint(path: str) -> Dict[str, Any]:
    """Reads a specific file from the backend codebase."""
    return await handle_codebase_file({"path": path})


@router.post("/codebase/search", tags=["Codebase"], summary="Search in codebase")
async def codebase_search_endpoint(
    query: str,
    path: str = "app",
    file_pattern: str = "*.py",
    max_results: int = 50,
    context_lines: int = 2,
) -> Dict[str, Any]:
    """Search for patterns in the backend codebase."""
    return await handle_codebase_search({
        "query": query,
        "path": path,
        "file_pattern": file_pattern,
        "max_results": max_results,
        "context_lines": context_lines,
    })


@router.post("/codebase/edit", tags=["Codebase"], summary="Edit a file in codebase")
async def codebase_edit_endpoint(
    path: str,
    mode: str,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    line_number: Optional[int] = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    create_backup: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Edit a file in the backend codebase.

    Modes:
    - replace: Find old_text and replace with new_text
    - insert: Insert new_text at line_number
    - append: Append new_text to end of file
    - delete_lines: Delete lines from start_line to end_line

    Creates automatic backup and validates Python syntax for .py files.
    """
    return await handle_codebase_edit({
        "path": path,
        "mode": mode,
        "old_text": old_text,
        "new_text": new_text,
        "line_number": line_number,
        "start_line": start_line,
        "end_line": end_line,
        "create_backup": create_backup,
        "dry_run": dry_run,
    })


@router.post("/codebase/create", tags=["Codebase"], summary="Create a new file in codebase")
async def codebase_create_endpoint(
    path: str,
    content: Optional[str] = None,
    template: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new file in the backend codebase.

    Templates:
    - empty: Empty file
    - python_module: Basic Python module
    - fastapi_route: FastAPI route template
    - service_class: Service class template
    """
    return await handle_codebase_create({
        "path": path,
        "content": content,
        "template": template,
    })


@router.post("/codebase/backup", tags=["Codebase"], summary="Manage codebase backups")
async def codebase_backup_endpoint(
    path: str,
    action: str,
) -> Dict[str, Any]:
    """
    Manage backups of codebase files.

    Actions:
    - create: Create a new backup
    - restore: Restore from latest backup
    - list: List available backups
    - diff: Show diff between current and backup
    """
    return await handle_codebase_backup({
        "path": path,
        "action": action,
    })


@router.get("/codebase/routes", tags=["Codebase"], summary="Get all API routes")
async def codebase_routes_endpoint() -> Dict[str, Any]:
    """Returns all API routes with methods and handlers."""
    return await handle_codebase_routes({})


@router.get("/codebase/services", tags=["Codebase"], summary="Get all services")
async def codebase_services_endpoint() -> Dict[str, Any]:
    """Returns all service modules with classes and functions."""
    return await handle_codebase_services({})


# ============================================================================
# Session Handshake Handler
# ============================================================================

async def handle_acknowledge_policy(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mandatory handshake tool.
    The AI must call this to confirm it has read the system prompt.
    """
    confirmation = params.get("confirmation", "")
    agent_id = params.get("agent_id", "unknown")

    if len(confirmation) < 5:
        raise ValueError("Confirmation too short. Please explicitly confirm you read the protocols.")

    # Log the handshake
    mcp_logger.info(f"HANDSHAKE | Agent: {agent_id} | Confirmed: {confirmation}")

    return {
        "status": "session_active",
        "message": "Policy acknowledged. Tools unlocked.",
        "session_context": {
            "rules": "No external HTTP, use MCP tools only.",
            "mode": "strict_mcp"
        }
    }


# ============================================================================
# Standard MCP Protocol Methods (Codex/Claude compatible)
# ============================================================================

async def handle_initialize(params: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    """
    MCP initialize method - returns server info and capabilities.
    Simple, stateless - no confirmation needed (API key already validated).
    """
    from ..services.tristar.model_init import model_init_service

    # Get TriStar model count
    stats = await model_init_service.get_stats()

    # Extract client info for logging
    client_info = params.get("clientInfo", {})
    client_name = client_info.get("name", "unknown")
    client_version = client_info.get("version", "0.0.0")
    client_ip = request.client.host if request and request.client else "unknown"

    mcp_logger.info(f"MCP_INITIALIZE | Client: {client_name} v{client_version} | IP: {client_ip}")

    return {
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": "ailinux-mcp-server",
            "version": VERSION,
            "tristar": {
                "enabled": True,
                "total_models": stats.get("total_models", 0),
                "initialized_models": stats.get("initialized", 0),
            },
        },
        "capabilities": {
            "tools": {
                "tristar": True,
                "memory": True,
                "mesh": True,
            },
            "prompts": {},
            "resources": {},
        },
        "instructions": (_mcp_instructions_for_request(request) if request is not None else build_mcp_instructions()),
    }


async def handle_tools_list(params: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    """MCP tools/list with a minimal default and canonical inventory profiles.

    Legacy handlers remain callable for compatibility, but duplicate/dead tools
    are intentionally not advertised to models.
    """
    inventory = str(params.get("inventory", "core"))
    # Check if client wants legacy (v3) tools
    use_legacy = inventory in {"legacy", "v3"} or params.get("legacy", False) or params.get("v3", False)
    
    if use_legacy:
        # Return all 134 tools from v3 for backwards compatibility
        tools = registry_v3_get_all_tools()
        for tool in tools:
            if isinstance(tool, dict) and "outputSchema" not in tool:
                tool["outputSchema"] = {"type": "object", "additionalProperties": True}
        return _finish_tools_list(tools, "v3", request)
    
    # Primary: unified inventory (Pre-Killer style, full toolbox with handlers)
    try:
        from ..mcp.tool_registry_unified import get_unified_tools, filter_tools_for_profile
        from ..mcp.handlers_wordpress import WORDPRESS_TOOL_SCHEMAS
        from ..mcp.handlers_browser import BROWSER_TOOL_SCHEMAS
        
        tools = get_unified_tools(
            extra_tools=(WORDPRESS_TOOL_SCHEMAS + BROWSER_TOOL_SCHEMAS + N8N_TOOLS)
        )
        existing = {t.get("name") for t in tools}
        if "nova_chat_agent" not in existing:
            tools.append({
                "name": "nova_chat_agent",
                "description": "Chat through Nova's configured account-backed agent bridge.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string", "default": "auto"},
                        "message": {"type": "string"},
                        "messages": {"type": "array", "items": {"type": "object"}},
                        "system": {"type": "string"},
                        "model": {"type": "string"},
                        "temperature": {"type": "number"},
                        "max_tokens": {"type": "integer"},
                    },
                    "required": ["message"],
                },
            })
            existing.add("nova_chat_agent")
        
        tools = filter_tools_for_profile(tools, inventory)

        for tool in tools:
            if isinstance(tool, dict) and "outputSchema" not in tool:
                tool["outputSchema"] = {"type": "object", "additionalProperties": True}

        return _finish_tools_list(
            tools,
            "unified",
            request,
            note=f"canonical MCP surface profile={inventory}",
        )
    except Exception as e:
        logger.warning(f"Unified tools failed, falling back to v4: {e}")
    
    # Fallback: v4 only (52 tools)
    tools = registry_v4_get_all_tools()
    return _finish_tools_list(
        tools,
        "v4-fallback",
        request,
        note="Fallback to v4 due to unified registry failure - check logs",
    )


async def _handle_tools_list_LEGACY(params: Dict[str, Any]) -> Dict[str, Any]:
    """LEGACY: Old manual tool list - kept for reference."""
    tools = [
        {
            "name": "acknowledge_policy",
            "description": "CRITICAL: Must be called FIRST. Confirms you have read the system prompt and session rules.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "confirmation": {"type": "string", "description": "Text confirming you read the protocols (e.g., 'I have read and understood the session rules')."},
                    "agent_id": {"type": "string", "description": "Your Agent ID"},
                },
                "required": ["confirmation"],
            },
        },
        {
            "name": "chat",
            "description": "Send a message to an AI model. Supports Ollama, Gemini, Mistral, Anthropic Claude, and GPT-OSS.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message to send to the AI model"},
                    "model": {"type": "string", "description": "Model ID (e.g., 'anthropic/claude-sonnet-4', 'gemini/gemini-2.0-flash')"},
                    "system_prompt": {"type": "string", "description": "Optional system prompt"},
                    "temperature": {"type": "number", "description": "Sampling temperature (0.0-2.0)"},
                },
                "required": ["message"],
            },
        },
        {
            "name": "list_models",
            "description": "List all available AI models with their capabilities",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "nova_chat_agent",
            "description": "Chat through Nova's configured account-backed agent bridge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "default": "auto"},
                    "message": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}},
                    "system": {"type": "string"},
                    "model": {"type": "string"},
                    "temperature": {"type": "number"},
                    "max_tokens": {"type": "integer"},
                },
                "required": ["message"],
            },
        },
        {
            "name": "ask_specialist",
            "description": "Route a task to the best specialist model",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task description for routing"},
                    "message": {"type": "string", "description": "The actual message/prompt"},
                    "preferred_speed": {"type": "string", "enum": ["fast", "medium", "slow"]},
                },
                "required": ["task", "message"],
            },
        },
        {
            "name": "crawl_url",
            "description": "Crawl a website and extract content",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to crawl"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords for filtering"},
                    "max_pages": {"type": "integer", "description": "Maximum pages to crawl"},
                },
                "required": ["url"],
            },
        },
        {
            "name": "web_search",
            "description": "Search the web for information using AI-powered search",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
        # Extended Multi-Search Tools (v3.0)
        {
            "name": "multi_search",
            "description": "Multi-Search v2.1: SearXNG (9 Engines: Google, Bing, DDG, Brave, GitHub, arXiv) + Wikipedia, Grokipedia, AILinux News, Wiby",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 50, "description": "Maximum results"},
                    "lang": {"type": "string", "default": "de", "description": "Language code (de, en, etc.)"},
                    "use_searxng": {"type": "boolean", "default": True},
                    "use_ddg": {"type": "boolean", "default": True},
                    "use_wiby": {"type": "boolean", "default": True},
                    "use_wikipedia": {"type": "boolean", "default": True},
                    "use_grokipedia": {"type": "boolean", "default": True},
                    "use_ailinux_news": {"type": "boolean", "default": True},
                },
                "required": ["query"],
            },
        },
        # Smart Search Tools (v4.0 - LLM-Powered)
        {
            "name": "smart_search",
            "description": "🚀 AI-Powered Smart Search with LLM enhancement. Uses Cerebras (20x faster) for query expansion & ranking, Groq for summarization. Target latency: <1s",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 30, "description": "Maximum results"},
                    "lang": {"type": "string", "default": "de", "description": "Language code"},
                    "use_searxng": {"type": "boolean", "default": True},
                    "use_ddg": {"type": "boolean", "default": True},
                    "use_wikipedia": {"type": "boolean", "default": True},
                    "use_grokipedia": {"type": "boolean", "default": True},
                    "use_ailinux_news": {"type": "boolean", "default": True},
                    "expand_query": {"type": "boolean", "default": True, "description": "Enable LLM query expansion (~50ms)"},
                    "detect_intent": {"type": "boolean", "default": True, "description": "Enable intent detection (~30ms)"},
                    "summarize": {"type": "boolean", "default": True, "description": "Enable result summarization (~300ms)"},
                    "smart_rank": {"type": "boolean", "default": True, "description": "Enable LLM re-ranking (~80ms)"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "quick_smart_search",
            "description": "⚡ Quick Smart Search - Speed-optimized for <500ms. Query expansion only, fewer sources, no summarization.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 15, "description": "Maximum results"},
                    "lang": {"type": "string", "default": "de", "description": "Language code"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_llm_config",
            "description": "Configure or view LLM settings for smart search. Models: Cerebras (fast), Groq (quality), Gemini (fallback).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["view", "configure"], "default": "view"},
                    "fast_model": {"type": "string", "description": "Fast model ID (e.g., cerebras/llama-3.3-70b)"},
                    "quality_model": {"type": "string", "description": "Quality model ID (e.g., groq/llama-3.3-70b-versatile)"},
                    "enable_expansion": {"type": "boolean", "description": "Enable query expansion"},
                    "enable_summary": {"type": "boolean", "description": "Enable summarization"},
                    "enable_ranking": {"type": "boolean", "description": "Enable smart ranking"},
                },
            },
        },
        {
            "name": "search_health",
            "description": "Health-Check aller Suchprovider: SearXNG (9 Engines), Wikipedia, Grokipedia, AILinux News, Wiby",
            "inputSchema": {"type": "object", "properties": {}},
        },
        # === NEW WIDGET TOOLS ===
        {
            "name": "weather",
            "description": "Get current weather from Open-Meteo API (free, no key). Returns temperature, humidity, wind, weather code and icon.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "default": 52.28, "description": "Latitude (default: Rheine)"},
                    "lon": {"type": "number", "default": 7.44, "description": "Longitude"},
                    "location": {"type": "string", "default": "Rheine", "description": "Location name"}
                }
            },
        },
        {
            "name": "crypto_prices",
            "description": "Get cryptocurrency prices from CoinGecko API (free). Returns USD/EUR prices and 24h change.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "coins": {"type": "array", "items": {"type": "string"}, "default": ["bitcoin", "ethereum", "solana"], "description": "Coin IDs"}
                }
            },
        },
        {
            "name": "stock_indices",
            "description": "Get major stock indices (DAX, S&P500, NASDAQ) from Yahoo Finance.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "market_overview",
            "description": "Combined market data: crypto prices + stock indices in one call.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "google_deep_search",
            "description": "Deep Google search with up to 150 results using googlesearch-python.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "default": 150, "description": "Max results (up to 200)"},
                    "lang": {"type": "string", "default": "de", "description": "Language code"}
                },
                "required": ["query"]
            },
        },
        {
            "name": "current_time",
            "description": "Get current time from the local IANA timezone database. Returns date, time, weekday in DE/EN.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "default": "Europe/Berlin", "description": "IANA timezone (e.g., Europe/Berlin, America/New_York)"},
                    "location": {"type": "string", "description": "Optional location name for display"}
                }
            },
        },
        {
            "name": "list_timezones",
            "description": "List available IANA timezones from the local timezone database.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Filter by region (e.g., Europe, America, Asia)"}
                }
            },
        },
        {
            "name": "ailinux_search",
            "description": "Search AILinux.me News Archive (71+ pages, Tech/Media/Games) via WordPress REST API",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "default": 20, "description": "Number of results"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "grokipedia_search",
            "description": "Search Grokipedia.com - xAI's Wikipedia-style knowledge base with 885K+ articles",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "num_results": {"type": "integer", "default": 5, "description": "Number of results"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "image_search",
            "description": "Bildersuche via SearXNG (Google, Bing, DuckDuckGo Images). Liefert Bild-URLs, Thumbnails und Titel.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Image search query"},
                    "num_results": {"type": "integer", "default": 20, "description": "Number of images (max 50)"},
                    "lang": {"type": "string", "default": "de", "description": "Language code (de, en)"},
                },
                "required": ["query"],
            },
        },
        # TriStar Tools
        {
            "name": "tristar_models",
            "description": "Get all registered TriStar LLM models with their roles and capabilities",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["admin", "lead", "worker", "reviewer"], "description": "Filter by role"},
                    "capability": {"type": "string", "description": "Filter by capability (code, math, reasoning, etc.)"},
                    "provider": {"type": "string", "description": "Filter by provider (ollama, gemini, anthropic, mistral)"},
                },
            },
        },
        {
            "name": "tristar_init",
            "description": "Initialize (impfen) a model with system prompt and configuration",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_id": {"type": "string", "description": "Model ID to initialize"},
                },
                "required": ["model_id"],
            },
        },
        {
            "name": "tristar_memory_store",
            "description": "Store a memory entry in TriStar shared memory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Memory content to store"},
                    "memory_type": {"type": "string", "enum": ["fact", "decision", "code", "summary", "context", "todo"], "description": "Type of memory"},
                    "llm_id": {"type": "string", "description": "ID of the LLM storing the memory"},
                    "initial_confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "Initial confidence score"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "tristar_memory_search",
            "description": "Search TriStar shared memory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "min_confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "Minimum confidence score"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                    "memory_type": {"type": "string", "description": "Filter by memory type"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum results"},
                },
            },
        },
        # Codebase Access Tools
        {
            "name": "codebase_structure",
            "description": "Get the backend codebase directory structure (app/, routes/, services/, etc.)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to scan (default: 'app')"},
                    "include_files": {"type": "boolean", "description": "Include files in output (default: true)"},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Maximum directory depth (default: 4)"},
                },
            },
        },
        {
            "name": "codebase_file",
            "description": "Read a specific file from the backend codebase",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path (e.g., 'app/routes/mcp.py')"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "codebase_search",
            "description": "Search for patterns/text in the codebase",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search pattern (regex supported)"},
                    "path": {"type": "string", "description": "Relative path to search in (default: 'app')"},
                    "file_pattern": {"type": "string", "description": "File glob pattern (default: '*.py')"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum results (default: 50)"},
                    "context_lines": {"type": "integer", "minimum": 0, "maximum": 5, "description": "Context lines around match (default: 2)"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "codebase_routes",
            "description": "Get all API routes with their HTTP methods, paths, and handlers",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "codebase_services",
            "description": "Get all service modules with their classes and functions",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "codebase_edit",
            "description": "Edit a file in the backend codebase. Creates automatic backup. Validates Python syntax for .py files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path (e.g., 'app/routes/mcp.py')"},
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "insert", "append", "delete_lines"],
                        "description": "Edit mode: replace (old_text→new_text), insert (at line), append (to end), delete_lines (line range)"
                    },
                    "old_text": {"type": "string", "description": "Text to find and replace (for mode=replace)"},
                    "new_text": {"type": "string", "description": "New text to insert (for replace/insert/append)"},
                    "line_number": {"type": "integer", "description": "Line number for insert mode"},
                    "start_line": {"type": "integer", "description": "Start line for delete_lines mode"},
                    "end_line": {"type": "integer", "description": "End line for delete_lines mode"},
                    "create_backup": {"type": "boolean", "default": True, "description": "Create .bak backup file"},
                    "dry_run": {"type": "boolean", "default": False, "description": "Preview changes without writing"},
                },
                "required": ["path", "mode"],
            },
        },
        {
            "name": "codebase_create",
            "description": "Create a new file in the backend codebase. Will not overwrite existing files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path for new file"},
                    "content": {"type": "string", "description": "File content"},
                    "template": {
                        "type": "string",
                        "enum": ["empty", "python_module", "fastapi_route", "service_class"],
                        "description": "Use a template instead of content"
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "codebase_backup",
            "description": "Create or restore backups of codebase files",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "action": {
                        "type": "string",
                        "enum": ["create", "restore", "list", "diff"],
                        "description": "Backup action"
                    },
                },
                "required": ["path", "action"],
            },
        },
        # CLI Agent Tools - Subprocess Management for Claude, Codex, Gemini
        {
            "name": "cli-agents_list",
            "description": "List all CLI agents (Claude, Codex, Gemini subprocesses) with their status",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "cli-agents_get",
            "description": "Get details for a specific CLI agent including output buffer",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID (e.g., 'claude-mcp', 'codex-mcp', 'gemini-mcp')"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "cli-agents_start",
            "description": "Start a CLI agent subprocess (fetches system prompt from TriForce)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to start"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "cli-agents_stop",
            "description": "Stop a CLI agent subprocess",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to stop"},
                    "force": {"type": "boolean", "description": "Force kill (default: false)"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "cli-agents_restart",
            "description": "Restart a CLI agent (stop + start)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to restart"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "cli-agents_call",
            "description": "Send a message to a CLI agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to call"},
                    "message": {"type": "string", "description": "Message to send"},
                    "timeout": {"type": "integer", "minimum": 10, "maximum": 600, "description": "Timeout in seconds (default: 120)"},
                },
                "required": ["agent_id", "message"],
            },
        },
        {
            "name": "cli-agents_broadcast",
            "description": "Broadcast a message to multiple or all CLI agents",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to broadcast"},
                    "agent_ids": {"type": "array", "items": {"type": "string"}, "description": "Specific agent IDs (omit for all)"},
                },
                "required": ["message"],
            },
        },
        {
            "name": "cli-agents_output",
            "description": "Get output buffer for a CLI agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID"},
                    "lines": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Number of lines (default: 50)"},
                },
                "required": ["agent_id"],
            },
        },
        {
            "name": "cli-agents_stats",
            "description": "Get statistics for CLI agents (count by status and type)",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    # Add Ollama, TriStar, Gemini Access, and Queue tools dynamically
    tools.extend(OLLAMA_TOOLS)
    tools.extend(TRISTAR_TOOLS)
    tools.extend(GEMINI_ACCESS_TOOLS)
    tools.extend(QUEUE_TOOLS)
    tools.extend(MESH_TOOLS)
    tools.extend(MESH_FILTER_TOOLS)
    tools.extend(INIT_TOOLS)
    tools.extend(MODEL_INIT_TOOLS)
    tools.extend(BOOTSTRAP_TOOLS)
    tools.extend(ADAPTIVE_CODE_TOOLS)
    tools.extend(ADAPTIVE_CODE_V4_TOOLS)  # Enhanced: LRU Cache, Async I/O, Delta Sync, Agent-Aware

    # Add System & Compatibility Tools
    tools.extend([
        {
            "name": "check_compatibility",
            "description": "Checks compatibility of all MCP tools with OpenAI, Gemini and Anthropic",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "debug_mcp_request",
            "description": "Traces an MCP request without executing it",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "params": {"type": "object"}
                },
                "required": ["method"]
            }
        },
        {
            "name": "restart_backend",
            "description": "Restarts the entire backend service",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "delay": {"type": "integer", "default": 2}
                }
            }
        },
        {
            "name": "restart_agent",
            "description": "Restarts a specific CLI agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"}
                },
                "required": ["agent_id"]
            }
        }
    ])


    tools.append({
        "name": "mcp_write_fallback",
        "description": "Store denied MCP write attempts for safe manual review and retry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Original MCP tool name"},
                "arguments": {"type": "object", "description": "Original MCP tool arguments"},
                "reason": {"type": "string", "description": "Reason the write path was denied"},
                "caller": {"type": "string", "description": "Optional caller identity"},
            },
            "required": ["tool_name"],
        },
    })

    return {"tools": tools}


async def handle_check_compatibility(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle check_compatibility tool."""
    tools_response = await handle_tools_list({})
    tools = tools_response["tools"]
    return compatibility_layer.check_compatibility(tools)

async def handle_debug_mcp_request(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle debug_mcp_request tool."""
    return await mcp_debugger.debug_mcp_request(
        params.get("method", ""), params.get("params", {})
    )

async def handle_restart_backend(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle restart_backend tool."""
    return await system_control.restart_backend(params.get("delay", 2))

async def handle_restart_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle restart_agent tool."""
    return await system_control.restart_agent(params.get("agent_id", ""))



_MCP_WRITE_FALLBACK_STORE: List[Dict[str, Any]] = []


async def _store_mcp_write_fallback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Store a denied MCP write attempt for later manual review."""
    entry = {
        "id": f"mcp-write-fallback-{len(_MCP_WRITE_FALLBACK_STORE) + 1}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool_name": payload.get("tool_name") or payload.get("name") or "unknown",
        "arguments": payload.get("arguments") or payload.get("params") or {},
        "reason": payload.get("reason", "write path denied"),
        "caller": payload.get("caller", "mcp"),
    }
    _MCP_WRITE_FALLBACK_STORE.append(entry)
    mcp_logger.warning("MCP_WRITE_FALLBACK_STORED | %s", entry["tool_name"])
    return {"ok": True, "stored": True, "fallback": entry}


async def handle_mcp_write_fallback(params: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tool handler for write-fallback queueing."""
    return await _store_mcp_write_fallback(params)


async def handle_execute_mcp_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    EXPERIMENTAL: Execute any MCP tool dynamically.
    Allows AI to chain tools or execute tools determined at runtime.
    """
    tool_name = params.get("tool_name")
    tool_params = params.get("params", {})

    # Resolve v4 short names to internal names
    tool_name = resolve_alias_reverse(tool_name) if tool_name else tool_name

    if not tool_name:
        raise ValueError("'tool_name' is required")

    # Access the global handler map
    # Note: We need to access MCP_HANDLERS, but it's defined below.
    # We can reconstruct the lookup logic or use a lazy import/lookup pattern.
    
    # Re-using the logic from handle_tools_call but for internal use
    handler = MCP_HANDLERS.get(tool_name)
    
    # Compatibility fallback
    if not handler and "." in tool_name:
        handler = MCP_HANDLERS.get(tool_name.replace(".", "_"))
    if not handler and "_" in tool_name:
        handler = MCP_HANDLERS.get(tool_name.replace("_", "."))

    # Try v4 handlers first
    if not handler:
        try:
            # Bug-Fix 2026-04-28: was `arguments` (undefined in this scope), now tool_params
            v4_result = await call_v4_tool(tool_name, tool_params)
            if v4_result:
                return {"content": [{"type": "text", "text": json.dumps(v4_result, separators=(chr(44), chr(58)))}], "isError": False}
        except Exception:
            pass

    if not handler:
        raise ValueError(f"Unknown tool: {tool_name}")

    mcp_logger.warning(f"EXPERIMENTAL: Dynamic tool execution of '{tool_name}'")
    return await handler(tool_params)


async def handle_tools_call(params: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    """MCP tools/call method - executes a tool."""
    requested_tool_name = params.get("name")
    tool_name = requested_tool_name
    arguments = params.get("arguments", {})

    if request is not None and tool_name:
        from app.services.mcp_workspace_bridge import LOCAL_TOOL_NAMES, is_public_guest, call_public_local_tool
        if is_public_guest(request) and str(tool_name) in LOCAL_TOOL_NAMES:
            return await call_public_local_tool(
                request, str(tool_name), arguments if isinstance(arguments, dict) else {}
            )

    # Resolve unified registry aliases before legacy/v4 normalization.
    from ..mcp.tool_registry_unified import resolve_tool_name_for_call
    tool_name = resolve_tool_name_for_call(tool_name) if tool_name else tool_name

    # Resolve v4 short names to internal names
    tool_name = resolve_alias_reverse(tool_name) if tool_name else tool_name

    if not tool_name:
        raise ValueError("'name' parameter is required for tools/call")

    blocked = _maybe_block_write_tool(tool_name, arguments, request, requested_tool_name)
    if blocked is not None:
        return blocked

    # Map tool names to internal handlers
    tool_map = {
        "acknowledge_policy": handle_acknowledge_policy,
        "chat": handle_llm_invoke,
        "list_models": handle_models_list,
        "nova_chat_agent": handle_nova_chat_agent,
        "ask_specialist": handle_specialists_invoke,
        "crawl": handle_crawl_url,
        "crawl_url": handle_crawl_url,
        "web_search": handle_web_search,
        # Extended Multi-Search (v3.0 - Grokipedia + AILinux News)
        "multi_search": handle_multi_search,
        # Smart Search (v4.0 - LLM-Powered with Cerebras/Groq)
        "smart_search": handle_smart_search,
        "quick_smart_search": handle_quick_smart_search,
        "search_llm_config": handle_search_llm_config,
        "search_health": handle_search_health,
        "weather": handle_weather,
        "crypto_prices": handle_crypto_prices,
        "stock_indices": handle_stock_indices,
        "market_overview": handle_market_overview,
        "google_deep_search": handle_google_deep_search,
        "current_time": handle_current_time,
        "list_timezones": handle_list_timezones,
        "ailinux_search": handle_ailinux_search,
        "grokipedia_search": handle_grokipedia_search,
        "image_search": handle_image_search,
        # TriStar Integration
        "tristar_models": handle_tristar_models,
        "tristar_init": handle_tristar_init,
        "tristar_memory_store": handle_tristar_memory_store,
        "tristar_memory_search": handle_tristar_memory_search,
        # Codebase Access
        "codebase_structure": handle_codebase_structure,
        "codebase_file": handle_codebase_file,
        "codebase_search": handle_codebase_search,
        "codebase_routes": handle_codebase_routes,
        "codebase_services": handle_codebase_services,
        "codebase_edit": handle_codebase_edit,
        "codebase_create": handle_codebase_create,
        "codebase_backup": handle_codebase_backup,
        # CLI Agents
        "cli-agents_list": handle_cli_agents_list,
        "cli-agents_get": handle_cli_agents_get,
        "cli-agents_start": handle_cli_agents_start,
        "cli-agents_stop": handle_cli_agents_stop,
        "cli-agents_restart": handle_cli_agents_restart,
        "cli-agents_call": handle_cli_agents_call,
        "cli-agents_broadcast": handle_cli_agents_broadcast,
        "cli-agents_output": handle_cli_agents_output,
        "cli-agents_stats": handle_cli_agents_stats,
        # V5 canonical agent tool names. Keep these direct so tools listed
        # from V5 are not dependent on the v4 fallback path.
        "agents": handle_cli_agents_list,
        "agent_call": handle_cli_agents_call,
        "agent_broadcast": handle_cli_agents_broadcast,
        "agent_start": handle_cli_agents_start,
        "agent_stop": handle_cli_agents_stop,
        # System & Compatibility
        "check_compatibility": handle_check_compatibility,
        "debug_mcp_request": handle_debug_mcp_request,
            "restart_backend": handle_restart_backend,
            "restart_agent": handle_restart_agent,
            "execute_mcp_tool": handle_execute_mcp_tool,
            "mcp_write_fallback": handle_mcp_write_fallback,
        }
    # Merge with dynamic handlers from services
    tool_map.update(OLLAMA_HANDLERS)
    tool_map.update(TRISTAR_HANDLERS)
    tool_map.update(GEMINI_ACCESS_HANDLERS)
    tool_map.update(QUEUE_HANDLERS)
    tool_map.update(MESH_HANDLERS)
    tool_map.update(MESH_FILTER_HANDLERS)
    tool_map.update(INIT_HANDLERS)
    tool_map.update(MODEL_INIT_HANDLERS)
    tool_map.update(BOOTSTRAP_HANDLERS)
    tool_map.update(ADAPTIVE_CODE_HANDLERS)
    tool_map.update(ADAPTIVE_CODE_V4_HANDLERS)  # Enhanced V4 handlers
    tool_map.update(LLM_COMPAT_HANDLERS)
    tool_map.update(HOTRELOAD_HANDLERS)
    tool_map.update(MEMORY_INDEX_HANDLERS)
    # === NEW CLIENT-SERVER ARCHITECTURE HANDLERS ===
    tool_map.update(VAULT_HANDLERS)
    tool_map.update(CHAT_ROUTER_HANDLERS)
    tool_map.update(TASK_SPAWNER_HANDLERS)
    tool_map.update(N8N_HANDLERS)
    tool_map.update(GROUP_CHAT_HANDLERS)
    tool_map.update(TELEGRAM_AGENT_HANDLERS)
    tool_map.update(SWARM_HANDLERS)
    tool_map.update(MCP_HANDLERS)

    handler = tool_map.get(tool_name)
    # Compatibility fallback
    if not handler and "." in tool_name:
        handler = tool_map.get(tool_name.replace(".", "_"))
    if not handler and "_" in tool_name:
        handler = tool_map.get(tool_name.replace("_", "."))

    # Try v4 handlers first
    if not handler:
        try:
            v4_result = await call_v4_tool(tool_name, arguments)
            if v4_result is not None:
                return _build_tool_result(v4_result)
        except Exception as e:
            mcp_logger.error(f"v4 handler failed for {tool_name}: {e}")
            return _build_tool_result(
                {"error": str(e), "tool_name": tool_name, "source": "v4"},
                is_error=True,
            )

    if not handler:
        raise ValueError(f"Unknown tool: {tool_name}")

    result = await _call_tool_handler(handler, arguments, request)
    return _build_tool_result(result)


# ============================================================================
# TriStar Integration Handlers
# ============================================================================

async def handle_tristar_models(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all registered TriStar models."""
    from ..services.tristar.model_init import model_init_service

    role = params.get("role")
    capability = params.get("capability")
    provider = params.get("provider")

    models = await model_init_service.list_models()

    # Filter by parameters
    if role:
        models = [m for m in models if m.role.value == role]
    if capability:
        models = [m for m in models if any(c.value == capability for c in m.capabilities)]
    if provider:
        models = [m for m in models if m.provider == provider]

    return {
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.model_name,
                "provider": m.provider,
                "role": m.role.value,
                "capabilities": [c.value for c in m.capabilities],
                "initialized": m.initialized,
                "healthy": m.healthy,
            }
            for m in models
        ],
        "count": len(models),
    }


async def handle_tristar_init(params: Dict[str, Any]) -> Dict[str, Any]:
    """Initialize (impfen) a model with system prompt."""
    from ..services.tristar.model_init import model_init_service

    model_id = params.get("model_id")
    if not model_id:
        raise ValueError("'model_id' parameter is required")

    try:
        init_data = await model_init_service.init_model(model_id)
        return {
            "status": "initialized",
            "model_id": model_id,
            "init_data": init_data,
        }
    except ValueError as e:
        raise ValueError(str(e))


async def handle_tristar_memory_store(params: Dict[str, Any]) -> Dict[str, Any]:
    """Store a memory entry in TriStar memory."""
    from ..services.tristar.memory_controller import memory_controller

    content = params.get("content")
    if not content:
        raise ValueError("'content' parameter is required")

    entry = await memory_controller.store(
        content=content,
        memory_type=params.get("memory_type", "fact"),
        llm_id=params.get("llm_id", "mcp-client"),
        initial_confidence=params.get("initial_confidence", 0.8),
        ttl_seconds=params.get("ttl_seconds", 86400),
        tags=params.get("tags", []),
        project_id=params.get("project_id"),
    )

    return {
        "entry_id": entry.entry_id,
        "content_hash": entry.content_hash,
        "aggregate_confidence": entry.aggregate_confidence,
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
        "status": "stored",
    }


async def handle_tristar_memory_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search TriStar memory."""
    from ..services.tristar.memory_controller import memory_controller

    query = params.get("query", "")
    results = await memory_controller.search(
        query=query,
        min_confidence=params.get("min_confidence", 0.0),
        tags=params.get("tags"),
        memory_type=params.get("memory_type"),
        limit=params.get("limit", 10),
    )

    return {
        "results": [e.to_dict() for e in results],
        "count": len(results),
    }


# ============================================================================
# Codebase Access Handlers
# ============================================================================

import unicodedata
from pathlib import Path
import logging

_mcp_logger = logging.getLogger("ailinux.mcp.security")

from app.paths import PROJECT_ROOT
BACKEND_ROOT = PROJECT_ROOT
ALLOWED_EXTENSIONS = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".env.example"}

# Sensitive paths that should never be accessed
BLOCKED_PATHS = {
    ".env", ".git", ".ssh", "secrets", "credentials",
    "__pycache__", ".venv", "node_modules", ".claude",
}
SAFE_HIDDEN_PATHS = {".github", ".env.example"}


def _safe_path(relative_path: str) -> Optional[Path]:
    """Validates and returns safe path within backend root.

    Security measures:
    - Path traversal prevention via is_relative_to() (Python 3.9+)
    - Null byte injection prevention
    - Unicode normalization to prevent homograph attacks
    - Symlink attack prevention
    - Sensitive path blocking
    """
    try:
        # 1. Null byte injection check
        if "\x00" in relative_path or "\0" in relative_path:
            _mcp_logger.warning(f"Null byte injection attempt: {repr(relative_path[:50])}")
            return None

        # 2. Unicode normalization (NFC) to prevent homograph attacks
        normalized_path = unicodedata.normalize("NFC", relative_path)

        # 3. Block suspicious Unicode characters
        if any(ord(c) > 0xFFFF for c in normalized_path):
            _mcp_logger.warning(f"Suspicious Unicode in path: {repr(relative_path[:50])}")
            return None

        # 4. Check for blocked sensitive paths
        path_parts = normalized_path.replace("\\", "/").split("/")
        for part in path_parts:
            lower_part = part.lower()
            if lower_part in BLOCKED_PATHS or (
                part.startswith(".")
                and part not in {".", ".."}
                and lower_part not in SAFE_HIDDEN_PATHS
            ):
                _mcp_logger.warning(f"Blocked path component: {part}")
                return None

        # 5. Resolve path and check containment
        full_path = (BACKEND_ROOT / normalized_path).resolve()

        # 6. Symlink attack prevention - check if resolved path differs significantly
        # This detects symlinks pointing outside the allowed directory
        if full_path.is_symlink():
            real_target = full_path.resolve()
            if not real_target.is_relative_to(BACKEND_ROOT):
                _mcp_logger.warning(f"Symlink escape attempt: {normalized_path} -> {real_target}")
                return None

        # 7. Final containment check using is_relative_to
        if not full_path.is_relative_to(BACKEND_ROOT):
            _mcp_logger.warning(f"Path traversal attempt: {normalized_path}")
            return None

        return full_path

    except Exception as e:
        _mcp_logger.warning(f"Path validation error for {repr(relative_path[:50])}: {e}")
        return None


async def handle_codebase_structure(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get the codebase directory structure as a concise tree string."""
    include_files = params.get("include_files", True)
    max_depth = params.get("max_depth", 4)
    path = params.get("path", "app")

    safe_root = _safe_path(path)
    if not safe_root or not safe_root.exists():
        raise ValueError(f"Path not found: {path}")

    lines = []

    def build_tree(dir_path: Path, prefix: str = "", current_depth: int = 0):
        if current_depth > max_depth:
            lines.append(f"{prefix}└── ... (max depth)")
            return

        try:
            # Sort: directories first, then files
            items = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            
            # Filter items
            filtered_items = []
            for item in items:
                if item.name.startswith(".") or item.name == "__pycache__":
                    continue
                if item.is_file() and not include_files:
                    continue
                if item.is_file() and item.suffix not in ALLOWED_EXTENSIONS:
                    continue
                filtered_items.append(item)

            for i, item in enumerate(filtered_items):
                is_last = (i == len(filtered_items) - 1)
                connector = "└── " if is_last else "├── "
                
                if item.is_dir():
                    lines.append(f"{prefix}{connector}{item.name}/")
                    new_prefix = prefix + ("    " if is_last else "│   ")
                    build_tree(item, new_prefix, current_depth + 1)
                else:
                    lines.append(f"{prefix}{connector}{item.name}")

        except PermissionError:
            lines.append(f"{prefix}└── (access denied)")

    lines.append(f"{path}/")
    build_tree(safe_root)
    
    return {
        "root": path,
        "structure": "\n".join(lines),
        "backend_version": f"{VERSION} (Optimized Tree)",
    }


async def handle_codebase_file(params: Dict[str, Any]) -> Dict[str, Any]:
    """Read a specific file from the codebase."""
    file_path = params.get("path")
    if not file_path:
        raise ValueError("'path' parameter is required")

    safe_path = _safe_path(file_path)
    if not safe_path or not safe_path.exists():
        raise ValueError(f"File not found: {file_path}")

    if safe_path.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed: {safe_path.suffix}")

    if safe_path.stat().st_size > 500_000:  # 500KB limit
        raise ValueError("File too large (max 500KB)")

    try:
        content = safe_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError("File is not valid UTF-8 text")

    return {
        "path": file_path,
        "content": content,
        "size": len(content),
        "lines": content.count("\n") + 1,
    }


# =============================================================================
# CODEBASE EDIT TOOLS - Self-modification capability with safety measures
# =============================================================================

BACKUP_DIR = BACKEND_ROOT / ".backups"
EDIT_LOG_FILE = BACKEND_ROOT / ".edit_log.jsonl"

# Edit-specific sensitive paths
EDIT_FORBIDDEN_PATHS = {
    ".env", ".env.local", ".env.production",
    "credentials.json", "secrets.py",
}


def _is_edit_forbidden_path(file_path: str) -> bool:
    normalized = unicodedata.normalize("NFC", file_path).replace("\\", "/")
    parts = {part.lower() for part in normalized.split("/") if part}
    return any(name.lower() in parts for name in EDIT_FORBIDDEN_PATHS)


def _validate_python_syntax(content: str) -> tuple[bool, Optional[str]]:
    """Validates Python syntax without executing."""
    import ast
    try:
        ast.parse(content)
        return True, None
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"


def _create_backup(file_path: Path) -> Optional[Path]:
    """Creates timestamped backup of file."""
    from datetime import datetime

    if not file_path.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rel_path = file_path.relative_to(BACKEND_ROOT)
    backup_name = f"{rel_path.as_posix().replace('/', '_')}_{timestamp}.bak"
    backup_path = BACKUP_DIR / backup_name

    import shutil
    shutil.copy2(file_path, backup_path)
    return backup_path


def _log_edit(action: str, path: str, details: Dict[str, Any]):
    """Logs edit operations for audit trail."""
    import json
    from datetime import datetime

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "path": path,
        "details": details,
    }

    with open(EDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")


async def handle_codebase_edit(params: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a file in the codebase with safety checks."""
    file_path = params.get("path")
    mode = params.get("mode")

    if not file_path:
        raise ValueError("'path' parameter is required")
    if not mode:
        raise ValueError("'mode' parameter is required")

    # Security checks
    safe_path = _safe_path(file_path)
    if not safe_path:
        raise ValueError(f"Invalid path: {file_path}")

    if _is_edit_forbidden_path(file_path):
        raise ValueError(f"Editing forbidden for security-sensitive files: {file_path}")

    if not safe_path.exists():
        raise ValueError(f"File not found: {file_path}")

    if safe_path.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed for editing: {safe_path.suffix}")

    # Read current content
    try:
        original_content = safe_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError("File is not valid UTF-8 text")

    original_lines = original_content.splitlines(keepends=True)
    dry_run = params.get("dry_run", False)
    create_backup = params.get("create_backup", True)

    # Process edit based on mode
    if mode == "replace":
        old_text = params.get("old_text")
        new_text = params.get("new_text", "")

        if not old_text:
            raise ValueError("'old_text' parameter required for replace mode")

        if old_text not in original_content:
            raise ValueError(f"old_text not found in file. File has {len(original_content)} chars.")

        # Count occurrences
        occurrences = original_content.count(old_text)
        if occurrences > 1:
            raise ValueError(f"old_text found {occurrences} times. Please provide more unique text.")

        new_content = original_content.replace(old_text, new_text, 1)

    elif mode == "insert":
        line_number = params.get("line_number")
        new_text = params.get("new_text", "")

        if not line_number or line_number < 1:
            raise ValueError("'line_number' (>= 1) required for insert mode")

        lines = original_lines.copy()
        insert_idx = min(line_number - 1, len(lines))

        # Ensure new_text ends with newline
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"

        lines.insert(insert_idx, new_text)
        new_content = "".join(lines)

    elif mode == "append":
        new_text = params.get("new_text", "")

        if not new_text:
            raise ValueError("'new_text' parameter required for append mode")

        new_content = original_content
        if not new_content.endswith("\n"):
            new_content += "\n"
        new_content += new_text
        if not new_content.endswith("\n"):
            new_content += "\n"

    elif mode == "delete_lines":
        start_line = params.get("start_line")
        end_line = params.get("end_line")

        if not start_line or not end_line:
            raise ValueError("'start_line' and 'end_line' required for delete_lines mode")
        if start_line < 1 or end_line < start_line:
            raise ValueError("Invalid line range")

        lines = original_lines.copy()
        del lines[start_line - 1:end_line]
        new_content = "".join(lines)

    else:
        raise ValueError(f"Unknown mode: {mode}")

    # Validate Python syntax for .py files
    if safe_path.suffix == ".py":
        is_valid, error_msg = _validate_python_syntax(new_content)
        if not is_valid:
            raise ValueError(f"Python syntax error in modified content: {error_msg}")

    # Calculate diff summary
    original_line_count = len(original_lines)
    new_line_count = new_content.count("\n") + (0 if new_content.endswith("\n") else 1)
    lines_changed = abs(new_line_count - original_line_count)

    result = {
        "path": file_path,
        "mode": mode,
        "dry_run": dry_run,
        "original_lines": original_line_count,
        "new_lines": new_line_count,
        "lines_changed": lines_changed,
        "syntax_valid": True if safe_path.suffix == ".py" else None,
    }

    if dry_run:
        result["preview"] = new_content[:2000] + ("..." if len(new_content) > 2000 else "")
        result["message"] = "Dry run - no changes written"
    else:
        # Create backup
        if create_backup:
            backup_path = _create_backup(safe_path)
            result["backup"] = str(backup_path.relative_to(BACKEND_ROOT)) if backup_path else None

        # Write new content
        safe_path.write_text(new_content, encoding="utf-8")

        # Log the edit
        _log_edit("edit", file_path, {
            "mode": mode,
            "original_lines": original_line_count,
            "new_lines": new_line_count,
            "backup": result.get("backup"),
        })

        result["message"] = f"File edited successfully ({mode})"

    return result


async def handle_codebase_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new file in the codebase."""
    file_path = params.get("path")
    content = params.get("content", "")
    template = params.get("template")

    if not file_path:
        raise ValueError("'path' parameter is required")

    # Security checks
    safe_path = _safe_path(file_path)
    if not safe_path:
        raise ValueError(f"Invalid path: {file_path}")

    if _is_edit_forbidden_path(file_path):
        raise ValueError(f"Creating forbidden for security-sensitive file: {file_path}")

    if safe_path.exists():
        raise ValueError(f"File already exists: {file_path}. Use codebase.edit to modify.")

    if safe_path.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed: {safe_path.suffix}")

    # Use template if specified
    if template:
        templates = {
            "empty": "",
            "python_module": '''"""
Module description.
"""


def main():
    pass


if __name__ == "__main__":
    main()
''',
            "fastapi_route": '''"""
API routes for {name}.
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

router = APIRouter()


@router.get("/")
async def get_items() -> Dict[str, Any]:
    """Get all items."""
    return {{"items": []}}


@router.post("/")
async def create_item(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new item."""
    return {{"created": True, "data": data}}
''',
            "service_class": '''"""
Service for {name}.
"""
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class {ClassName}Service:
    """Service class for {name} operations."""

    def __init__(self):
        self.logger = logger

    async def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process the data."""
        self.logger.info(f"Processing: {{data}}")
        return {{"status": "processed", "data": data}}


# Singleton instance
{instance_name}_service = {ClassName}Service()
''',
        }

        if template not in templates:
            raise ValueError(f"Unknown template: {template}")

        # Generate names from path
        name = safe_path.stem
        class_name = "".join(word.capitalize() for word in name.split("_"))

        content = templates[template].format(
            name=name,
            ClassName=class_name,
            instance_name=name.lower(),
        )

    # Validate Python syntax for .py files
    if safe_path.suffix == ".py" and content:
        is_valid, error_msg = _validate_python_syntax(content)
        if not is_valid:
            raise ValueError(f"Python syntax error: {error_msg}")

    # Ensure parent directory exists
    safe_path.parent.mkdir(parents=True, exist_ok=True)

    # Write file
    safe_path.write_text(content, encoding="utf-8")

    # Log the creation
    _log_edit("create", file_path, {
        "template": template,
        "size": len(content),
    })

    return {
        "path": file_path,
        "created": True,
        "size": len(content),
        "lines": content.count("\n") + 1,
        "template": template,
    }


async def handle_codebase_backup(params: Dict[str, Any]) -> Dict[str, Any]:
    """Manage backups of codebase files."""
    file_path = params.get("path")
    action = params.get("action")

    if not file_path:
        raise ValueError("'path' parameter is required")
    if not action:
        raise ValueError("'action' parameter is required")

    safe_path = _safe_path(file_path)
    if not safe_path:
        raise ValueError(f"Invalid path: {file_path}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Get backup pattern for this file
    rel_path = file_path.replace("/", "_")
    backup_pattern = f"{rel_path}_*.bak"

    if action == "create":
        if not safe_path.exists():
            raise ValueError(f"File not found: {file_path}")

        backup_path = _create_backup(safe_path)
        _log_edit("backup_create", file_path, {"backup": str(backup_path)})

        return {
            "action": "create",
            "path": file_path,
            "backup": str(backup_path.relative_to(BACKEND_ROOT)),
        }

    elif action == "list":
        backups = sorted(BACKUP_DIR.glob(backup_pattern), reverse=True)
        backup_list = []

        for bp in backups[:20]:  # Limit to 20 most recent
            stat = bp.stat()
            backup_list.append({
                "name": bp.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

        return {
            "action": "list",
            "path": file_path,
            "backups": backup_list,
            "count": len(backup_list),
        }

    elif action == "restore":
        backups = sorted(BACKUP_DIR.glob(backup_pattern), reverse=True)

        if not backups:
            raise ValueError(f"No backups found for: {file_path}")

        latest_backup = backups[0]

        # Create backup of current file before restore
        if safe_path.exists():
            _create_backup(safe_path)

        # Restore from backup
        import shutil
        shutil.copy2(latest_backup, safe_path)

        _log_edit("backup_restore", file_path, {
            "restored_from": latest_backup.name,
        })

        return {
            "action": "restore",
            "path": file_path,
            "restored_from": latest_backup.name,
            "size": latest_backup.stat().st_size,
        }

    elif action == "diff":
        if not safe_path.exists():
            raise ValueError(f"File not found: {file_path}")

        backups = sorted(BACKUP_DIR.glob(backup_pattern), reverse=True)

        if not backups:
            return {
                "action": "diff",
                "path": file_path,
                "has_backup": False,
                "message": "No backups to compare",
            }

        import difflib

        current_content = safe_path.read_text(encoding="utf-8")
        backup_content = backups[0].read_text(encoding="utf-8")

        diff = list(difflib.unified_diff(
            backup_content.splitlines(keepends=True),
            current_content.splitlines(keepends=True),
            fromfile=f"backup/{backups[0].name}",
            tofile=file_path,
            lineterm=""
        ))

        return {
            "action": "diff",
            "path": file_path,
            "has_backup": True,
            "backup": backups[0].name,
            "diff": "".join(diff)[:5000],  # Limit diff output
            "lines_changed": len([d for d in diff if d.startswith("+") or d.startswith("-")]),
        }

    else:
        raise ValueError(f"Unknown action: {action}")


async def handle_codebase_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search for patterns in the codebase."""
    import re

    query = params.get("query")
    if not query:
        raise ValueError("'query' parameter is required")

    path = params.get("path", "app")
    file_pattern = params.get("file_pattern", "*.py")
    max_results = min(params.get("max_results", 50), 100)
    context_lines = min(params.get("context_lines", 2), 5)

    safe_root = _safe_path(path)
    if not safe_root or not safe_root.exists():
        raise ValueError(f"Path not found: {path}")

    results = []
    pattern = re.compile(query, re.IGNORECASE)

    for py_file in safe_root.rglob(file_pattern):
        if "__pycache__" in str(py_file):
            continue
        if py_file.suffix not in ALLOWED_EXTENSIONS:
            continue

        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if pattern.search(line):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    results.append({
                        "file": str(py_file.relative_to(BACKEND_ROOT)),
                        "line": i + 1,
                        "match": line.strip(),
                        "context": lines[start:end],
                    })
                    if len(results) >= max_results:
                        break
        except (PermissionError, UnicodeDecodeError):
            continue

        if len(results) >= max_results:
            break

    return {
        "query": query,
        "results": results,
        "count": len(results),
        "truncated": len(results) >= max_results,
    }


async def handle_codebase_routes(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all API routes with their handlers."""
    import re

    routes_dir = BACKEND_ROOT / "app" / "routes"
    routes = []

    # Route pattern: @router.(get|post|put|delete|patch)("path")
    route_pattern = re.compile(
        r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE
    )

    for py_file in routes_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            lines = content.splitlines()

            for i, line in enumerate(lines):
                match = route_pattern.search(line)
                if match:
                    method = match.group(1).upper()
                    path = match.group(2)

                    # Find function name (next line with 'async def')
                    func_name = None
                    for j in range(i + 1, min(i + 5, len(lines))):
                        if "async def " in lines[j] or "def " in lines[j]:
                            func_match = re.search(r"def\s+(\w+)", lines[j])
                            if func_match:
                                func_name = func_match.group(1)
                            break

                    routes.append({
                        "file": py_file.name,
                        "method": method,
                        "path": path,
                        "handler": func_name,
                        "line": i + 1,
                    })
        except (PermissionError, UnicodeDecodeError):
            continue

    # Group by file
    by_file = {}
    for route in routes:
        fname = route["file"]
        if fname not in by_file:
            by_file[fname] = []
        by_file[fname].append(route)

    return {
        "routes": routes,
        "count": len(routes),
        "by_file": by_file,
        "files": list(by_file.keys()),
    }


async def handle_codebase_services(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all service modules with their classes and functions."""
    import re

    services_dir = BACKEND_ROOT / "app" / "services"
    services = []

    class_pattern = re.compile(r"^class\s+(\w+)")
    func_pattern = re.compile(r"^(?:async\s+)?def\s+(\w+)")

    def scan_service(service_path: Path, prefix: str = "") -> List[Dict[str, Any]]:
        results = []

        for item in sorted(service_path.iterdir()):
            if item.name.startswith("_") and item.name != "__init__.py":
                continue

            if item.is_dir():
                results.extend(scan_service(item, f"{prefix}{item.name}/"))
            elif item.suffix == ".py":
                try:
                    content = item.read_text(encoding="utf-8")
                    lines = content.splitlines()

                    classes = []
                    functions = []

                    for i, line in enumerate(lines):
                        class_match = class_pattern.match(line)
                        if class_match:
                            classes.append({
                                "name": class_match.group(1),
                                "line": i + 1,
                            })

                        func_match = func_pattern.match(line)
                        if func_match and not line.strip().startswith("#"):
                            functions.append({
                                "name": func_match.group(1),
                                "line": i + 1,
                                "async": "async def" in line,
                            })

                    if classes or functions:
                        results.append({
                            "file": f"{prefix}{item.name}",
                            "path": str(item.relative_to(BACKEND_ROOT)),
                            "classes": classes,
                            "functions": functions,
                            "lines": len(lines),
                        })
                except (PermissionError, UnicodeDecodeError):
                    continue

        return results

    services = scan_service(services_dir)

    # Summary statistics
    total_classes = sum(len(s["classes"]) for s in services)
    total_functions = sum(len(s["functions"]) for s in services)

    return {
        "services": services,
        "count": len(services),
        "total_classes": total_classes,
        "total_functions": total_functions,
    }


# ============================================================================
# CLI Agent MCP Handlers (v2.80)
# ============================================================================

async def handle_cli_agents_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """List all CLI agents (Claude, Codex, Gemini subprocesses) - Optimized Summary."""
    from ..services.tristar.agent_controller import agent_controller
    agents = await agent_controller.list_agents()
    
    summary = []
    for a in agents:
        agent_id = a.get("agent_id") or a.get("id", "unknown")
        agent_status = a.get("status", "unknown")
        pid = a.get("pid", "-")
        summary.append(f"{agent_id}: {agent_status} (pid={pid})")

    return {
        "summary": summary,
        "count": len(agents),
    }


async def handle_cli_agents_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get details for a specific CLI agent."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id") or params.get("agent")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    agent = await agent_controller.get_agent(agent_id)
    if not agent:
        raise ValueError(f"CLI agent not found: {agent_id}")

    return agent


async def handle_cli_agents_start(params: Dict[str, Any]) -> Dict[str, Any]:
    """Start a CLI agent subprocess."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id") or params.get("agent")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    try:
        result = await agent_controller.start_agent(agent_id)
        return result
    except ValueError as e:
        raise ValueError(str(e))


async def handle_cli_agents_stop(params: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a CLI agent subprocess."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id") or params.get("agent")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    force = params.get("force", False)

    try:
        result = await agent_controller.stop_agent(agent_id, force=force)
        return result
    except ValueError as e:
        raise ValueError(str(e))


async def handle_cli_agents_restart(params: Dict[str, Any]) -> Dict[str, Any]:
    """Restart a CLI agent."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id") or params.get("agent")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    try:
        result = await agent_controller.restart_agent(agent_id)
        return result
    except ValueError as e:
        raise ValueError(str(e))


async def handle_cli_agents_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send a message to a CLI agent."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id") or params.get("agent")
    message = params.get("message") or params.get("command")

    if not agent_id:
        raise ValueError("'agent_id' parameter is required")
    if not message:
        raise ValueError("'message' parameter is required")

    timeout = params.get("timeout", 120)

    try:
        result = await agent_controller.call_agent(agent_id, message, timeout=timeout)
        return result
    except ValueError as e:
        raise ValueError(str(e))


async def handle_cli_agents_broadcast(params: Dict[str, Any]) -> Dict[str, Any]:
    """Broadcast a message to multiple CLI agents."""
    from ..services.tristar.agent_controller import agent_controller

    message = params.get("message") or params.get("command")
    if not message:
        raise ValueError("'message' parameter is required")

    agent_ids = params.get("agent_ids") or params.get("agents")  # Optional, None = all agents

    result = await agent_controller.broadcast(message, agent_ids=agent_ids)
    return result


async def handle_cli_agents_output(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get output buffer for a CLI agent."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id") or params.get("agent")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    lines = params.get("lines", 50)
    output = await agent_controller.get_agent_output(agent_id, lines)

    return {
        "agent_id": agent_id,
        "output": output,
        "lines": len(output),
    }


async def handle_cli_agents_stats(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get CLI agent statistics."""
    from ..services.tristar.agent_controller import agent_controller
    return await agent_controller.get_stats()


async def handle_cli_agents_update_prompt(params: Dict[str, Any]) -> Dict[str, Any]:
    """Update the system prompt for a CLI agent."""
    from ..services.tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    prompt = params.get("prompt")

    if not agent_id:
        raise ValueError("'agent_id' parameter is required")
    if not prompt:
        raise ValueError("'prompt' parameter is required")

    try:
        result = await agent_controller.update_system_prompt(agent_id, prompt)
        return result
    except ValueError as e:
        raise ValueError(str(e))


async def handle_cli_agents_reload_prompts(params: Dict[str, Any]) -> Dict[str, Any]:
    """Reload system prompts from TriForce for all agents."""
    from ..services.tristar.agent_controller import agent_controller
    return await agent_controller.reload_system_prompts()


# ============================================================================
# MCP Node Handlers - Connected WebSocket Clients
# ============================================================================

async def handle_mcp_node_clients(params: Dict[str, Any]) -> Dict[str, Any]:
    """List all connected MCP Node clients (WebSocket connections) with telemetry data."""
    from ..routes.mcp_node import CONNECTED_CLIENTS
    
    clients = []
    for client_id, conn in CONNECTED_CLIENTS.items():
        client_data = {
            "client_id": client_id,
            "user_id": conn.user_id,
            "tier": conn.tier.value,
            "connected_at": conn.connected_at.isoformat(),
            "last_seen": conn.last_seen.isoformat(),
            "supported_tools": conn.supported_tools,
            "client_info": conn.client_info,
        }
        
        # Telemetrie-Daten hinzufügen (falls vorhanden)
        if hasattr(conn, 'mode'):
            client_data["mode"] = conn.mode  # "full" oder "telemetry_only"
        if hasattr(conn, 'total_tool_calls'):
            client_data["telemetry"] = {
                "total_tool_calls": conn.total_tool_calls,
                "successful": conn.successful_tool_calls,
                "failed": conn.failed_tool_calls,
                "recent_tools": conn.tool_usage[-10:] if conn.tool_usage else []
            }
        
        clients.append(client_data)
    
    return {
        "clients": clients,
        "count": len(clients),
        "note": "Telemetry-only mode: Server kann nur Status sehen, KEINE Remote-Execution"
    }


async def handle_prompts_list_mcp(_: Dict[str, Any]) -> Dict[str, Any]:
    """MCP prompts/list method - returns available prompts (standard MCP protocol)."""
    templates = prompt_library.list_templates()
    return {
        "prompts": [
            {
                "name": t,
                "description": prompt_library.get_template_info(t).get("description", ""),
            }
            for t in templates
        ]
    }


async def handle_resources_list_mcp(_: Dict[str, Any]) -> Dict[str, Any]:
    """MCP resources/list method - returns available resources (standard MCP protocol)."""
    # Our server doesn't expose static resources, return empty list
    return {"resources": []}


async def handle_resources_read_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    """MCP resources/read method - read a specific resource."""
    uri = params.get("uri")
    if not uri:
        raise ValueError("'uri' parameter is required")
    # Our server doesn't expose static resources
    raise ValueError(f"Resource not found: {uri}")


Handler = Callable[..., Awaitable[Any]]
MCP_HANDLERS: Dict[str, Handler] = {
    # Standard MCP Protocol Methods (Slash notation - required for Gemini/Claude/Codex compatibility)
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "nova_chat_agent": handle_nova_chat_agent,
    "prompts/list": handle_prompts_list_mcp,
    "prompts/get": handle_prompts_render,  # prompts/get maps to render
    "resources/list": handle_resources_list_mcp,
    "resources/read": handle_resources_read_mcp,

    # Original handlers
    "crawl_url": handle_crawl_url,
    "crawl_site": handle_crawl_site,
    "crawl_status": handle_crawl_status,
    "posts_create": handle_posts_create,
    "media_upload": handle_media_upload,
    "llm_invoke": handle_llm_invoke,
    "admin_crawler_control": handle_admin_control,
    "admin.crawler.config.get": handle_admin_config_get,
    "admin.crawler.config.set": handle_admin_config_set,

    # API Documentation (for Claude Code integration)
    "api_docs": handle_api_docs,
    "api_search": handle_api_search,

    # Translation Layer (API ↔ MCP)
    "translate": handle_translate,
    "translate_api_to_mcp": handle_api_to_mcp,
    "translate_mcp_to_api": handle_mcp_to_api,

    # Model Specialists
    "specialists_list": handle_specialists_list,
    "specialists_route": handle_specialists_route,
    "specialists_invoke": handle_specialists_invoke,

    # Context Management
    "context_create": handle_context_create,
    "context_get": handle_context_get,
    "context_message": handle_context_message,
    "context_list": handle_context_list,
    "context_clear": handle_context_clear,

    # Prompt Library
    "prompts_list": handle_prompts_list,
    "prompts_render": handle_prompts_render,
    "prompts_add": handle_prompts_add,

    # Workflow Orchestration
    "workflows_list": handle_workflows_list,
    "workflows_create": handle_workflows_create,
    "workflows_status": handle_workflows_status,

    # TriStar Integration (v2.80)
    "tristar_models": handle_tristar_models,
    "tristar_models_list": handle_tristar_models,
    "tristar_init": handle_tristar_init,
    "tristar_memory_store": handle_tristar_memory_store,
    "tristar_memory_search": handle_tristar_memory_search,

    # Codebase Access (v2.80)
    "codebase_structure": handle_codebase_structure,
    "codebase_file": handle_codebase_file,
    "codebase_search": handle_codebase_search,
    "codebase_routes": handle_codebase_routes,
    "codebase_services": handle_codebase_services,
    # Codebase Edit (v2.90) - Self-modification capability
    "codebase_edit": handle_codebase_edit,
    "codebase_create": handle_codebase_create,
    "codebase_backup": handle_codebase_backup,

    # CLI Agents (v2.80) - Claude, Codex, Gemini Subprocess Management
    "cli-agents_list": handle_cli_agents_list,
    "cli-agents_get": handle_cli_agents_get,
    "cli-agents_start": handle_cli_agents_start,
    "cli-agents_stop": handle_cli_agents_stop,
    "cli-agents_restart": handle_cli_agents_restart,
    "cli-agents_call": handle_cli_agents_call,
    "cli-agents_broadcast": handle_cli_agents_broadcast,
    "cli-agents_output": handle_cli_agents_output,
    "cli-agents_stats": handle_cli_agents_stats,
    "cli-agents_update-prompt": handle_cli_agents_update_prompt,
    "cli-agents_reload-prompts": handle_cli_agents_reload_prompts,
    # V5 canonical agent tool names
    "agents": handle_cli_agents_list,
    "agent_call": handle_cli_agents_call,
    "agent_broadcast": handle_cli_agents_broadcast,
    "agent_start": handle_cli_agents_start,
    "agent_stop": handle_cli_agents_stop,
    
    # MCP Node Clients (WebSocket connections)
    "mcp_node_clients": handle_mcp_node_clients,
}

# Merge with dynamic handlers from services
MCP_HANDLERS.update(OLLAMA_HANDLERS)
MCP_HANDLERS.update(TRISTAR_HANDLERS)
MCP_HANDLERS.update(GEMINI_ACCESS_HANDLERS)
MCP_HANDLERS.update(QUEUE_HANDLERS)
MCP_HANDLERS.update(MESH_HANDLERS)
MCP_HANDLERS.update(MESH_FILTER_HANDLERS)
# New Client-Server Architecture Handlers
MCP_HANDLERS.update(VAULT_HANDLERS)
MCP_HANDLERS.update(CHAT_ROUTER_HANDLERS)
MCP_HANDLERS.update(TASK_SPAWNER_HANDLERS)
MCP_HANDLERS.update(INIT_HANDLERS)
MCP_HANDLERS.update(MODEL_INIT_HANDLERS)
MCP_HANDLERS.update(BOOTSTRAP_HANDLERS)
MCP_HANDLERS.update(ADAPTIVE_CODE_HANDLERS)
MCP_HANDLERS.update(ADAPTIVE_CODE_V4_HANDLERS)
MCP_HANDLERS.update(LLM_COMPAT_HANDLERS)
MCP_HANDLERS.update(HOTRELOAD_HANDLERS)
MCP_HANDLERS.update(MEMORY_INDEX_HANDLERS)
from ..mcp.handlers_memory_history import HISTORY_HANDLERS
MCP_HANDLERS.update(HISTORY_HANDLERS)
MCP_HANDLERS |= FLARUM_TOOL_HANDLERS
MCP_HANDLERS.update(N8N_HANDLERS)
MCP_HANDLERS.update(GROUP_CHAT_HANDLERS)
MCP_HANDLERS.update(TELEGRAM_AGENT_HANDLERS)
MCP_HANDLERS.update(SWARM_HANDLERS)

# Register all handlers with the tool_registry_v3
register_handlers_from_dict(MCP_HANDLERS)

mcp_logger.info(f"MCP Handlers registered: {len(MCP_HANDLERS)} handlers, {registry_v3_tool_count()} tools in registry v3")

# Initialize v4 consolidated handlers (52 optimized tools)
try:
    init_v4_handlers()
    mcp_logger.info(f"MCP v4 Handlers initialized: {registry_v4_tool_count()} optimized tools")
except Exception as e:
    mcp_logger.warning(f"v4 handler init failed (non-critical): {e}")


from fastapi.responses import StreamingResponse
from ..mcp.structured_admin import record_mcp_call
import uuid
import asyncio
from typing import Dict, Any as TypingAny
from datetime import datetime as dt_datetime

# ============================================================================
# MCP Session Management for Cursor-compatible SSE Transport
# ============================================================================

# In-memory session store with response queues
_mcp_sessions: Dict[str, Dict[str, TypingAny]] = {}


def _get_session(session_id: str) -> Dict[str, TypingAny]:
    """Get or create a session"""
    if session_id not in _mcp_sessions:
        _mcp_sessions[session_id] = {
            "created": dt_datetime.now(),
            "queue": asyncio.Queue(),
            "initialized": False,
        }
    return _mcp_sessions[session_id]


def _store_session_request_state(session: Dict[str, TypingAny], request: Request) -> None:
    state = request.state
    session["auth_user"] = getattr(state, "mcp_auth_user", None)
    session["auth_method"] = getattr(state, "mcp_auth_method", None)
    session["auth_full_access"] = bool(getattr(state, "mcp_auth_full_access", False))
    session["auth_client_id"] = getattr(state, "mcp_auth_client_id", None)


def _restore_session_request_state(session: Dict[str, TypingAny], request: Request) -> None:
    request.state.mcp_auth_user = session.get("auth_user")
    request.state.mcp_auth_method = session.get("auth_method")
    request.state.mcp_auth_full_access = bool(session.get("auth_full_access", False))
    request.state.mcp_auth_client_id = session.get("auth_client_id")


def _clear_mcp_session(session_id: str) -> None:
    _mcp_sessions.pop(session_id, None)
    try:
        from app.services.mcp_workspace_sessions import clear_session
        clear_session(session_id)
    except Exception:
        pass


def _cleanup_old_sessions():
    """Remove sessions older than 1 hour"""
    now = dt_datetime.now()
    expired = [
        sid for sid, data in _mcp_sessions.items()
        if (now - data["created"]).total_seconds() > 3600
    ]
    for sid in expired:
        _clear_mcp_session(sid)


# ============================================================================
# MCP Health Check Endpoint (GET /mcp without SSE)
# ============================================================================

@router.get("/mcp", tags=["MCP"], summary="MCP health check or SSE endpoint")
@router.get("/mcp/", tags=["MCP"], summary="MCP health check or SSE endpoint")
async def mcp_health_or_sse(request: Request):
    """MCP transport for machines, setup page for ordinary browser GET requests."""
    await require_mcp_auth(request)

    accept_header = request.headers.get("Accept", "")
    if "text/html" in accept_header and "text/event-stream" not in accept_header:
        return HTMLResponse(_workspace_setup_html(), headers={"Cache-Control": "no-store"})
    client_ip = request.client.host if request.client else "unknown"

    if "text/event-stream" in accept_header:
        # Streamable HTTP compatibility:
        # Some web MCP connectors open GET on the canonical MCP endpoint itself.
        # Return SSE directly on /v1/mcp instead of redirecting to /v1/mcp/sse.
        session_id = request.headers.get("Mcp-Session-Id") or request.headers.get("mcp-session-id")
        if not session_id:
            session_id = str(uuid.uuid4()).replace("-", "")

        session = _get_session(session_id)
        mcp_logger.info(f"MCP_STREAMABLE_GET | IP: {client_ip} | Session: {session_id}")

        async def streamable_mcp_events():
            yield f": ailinux-mcp session={session_id}\n\n"
            ping_counter = 0

            while True:
                try:
                    response = await asyncio.wait_for(session["queue"].get(), timeout=30.0)
                    yield f"event: message\ndata: {json.dumps(response)}\n\n"
                except asyncio.TimeoutError:
                    ping_counter += 1
                    yield f": keepalive {ping_counter}\n\n"

        return StreamingResponse(
            streamable_mcp_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Mcp-Session-Id": session_id,
            },
        )

    mcp_logger.info(f"MCP_HEALTH_CHECK | IP: {client_ip}")
    protocol_version = (
        request.query_params.get("protocolVersion")
        or request.headers.get("Mcp-Protocol-Version")
        or "2024-11-05"
    )

    return JSONResponse({
        "jsonrpc": "2.0",
        "result": {
            "protocolVersion": protocol_version,
            "serverInfo": {
                "name": "ailinux-mcp-server",
                "version": VERSION,
                "description": "AILinux TriForce MCP Server"
            },
            "capabilities": {
                "tools": True,
                "prompts": True,
                "resources": True
            },
            "transports": ["http", "sse"],
            "endpoints": {
                "sse": "/v1/mcp/sse",
                "messages": "/v1/mcp/messages"
            },
            "instructions": _mcp_instructions_for_request(request)
        }
    })


# ============================================================================
# Cursor-Compatible SSE Endpoint (GET /mcp/sse)
# Following MCP SSE Transport Specification
# ============================================================================

# POST handler for /sse (Streamable HTTP fallback - Cursor tries this first)
@router.post("/mcp/sse", tags=["MCP"], summary="Streamable HTTP on SSE endpoint")
@router.post("/mcp/sse/", tags=["MCP"], summary="Streamable HTTP on SSE endpoint")
@router.post("/sse", tags=["MCP"], summary="Streamable HTTP on SSE endpoint (alias)")
@router.post("/sse/", tags=["MCP"], summary="Streamable HTTP on SSE endpoint (alias)")
async def mcp_sse_post(request: Request):
    """
    Handle POST requests on SSE endpoint.
    Cursor tries Streamable HTTP first, falls back to SSE on failure.
    This allows the streamable HTTP to work on /sse URL.
    """
    # Redirect to unified MCP endpoint
    return await mcp_unified_endpoint(request)


@router.get("/mcp/sse", tags=["MCP"], summary="SSE endpoint for Cursor/MCP clients")
@router.get("/mcp/sse/", tags=["MCP"], summary="SSE endpoint for Cursor/MCP clients")
@router.get("/sse", tags=["MCP"], summary="SSE endpoint (alias)")
@router.get("/sse/", tags=["MCP"], summary="SSE endpoint (alias)")
async def mcp_sse_connect(request: Request):
    """
    SSE Connection Endpoint for Cursor and other MCP clients.

    Protocol:
    1. Client connects to GET /sse
    2. Server sends: event: endpoint\ndata: /messages/?session_id=xxx
    3. Client POSTs JSON-RPC to /messages/?session_id=xxx
    4. Server streams responses back via this SSE connection

    This follows the MCP SSE Transport specification.
    """
    await require_mcp_auth(request)

    client_ip = request.client.host if request.client else "unknown"
    session_id = str(uuid.uuid4()).replace("-", "")

    # Create session with response queue
    session = _get_session(session_id)
    _store_session_request_state(session, request)
    session["authenticated"] = True
    session["last_seen"] = datetime.now(timezone.utc)
    request.state.mcp_session_id = session_id

    mcp_logger.info(f"SSE_CONNECT | IP: {client_ip} | Session: {session_id}")

    # Cleanup old sessions periodically
    _cleanup_old_sessions()

    async def event_generator():
        try:
            # First message: Tell client where to POST messages
            # This is the critical message Cursor expects!
            messages_endpoint = f"/v1/mcp/messages/?session_id={session_id}"
            access_token = None
            if os.environ.get("ALLOW_QUERY_TOKEN_AUTH") == "1":
                access_token = request.query_params.get("access_token")
            if access_token:
                messages_endpoint += f"&access_token={access_token}"
            yield f"event: endpoint\ndata: {messages_endpoint}\n\n"

            mcp_logger.info(f"SSE_ENDPOINT_SENT | Session: {session_id} | Endpoint: {messages_endpoint}")

            # Send initial ping
            yield f": ping - {dt_datetime.now().isoformat()}\n\n"

            # Keep connection alive and send queued responses
            ping_counter = 0
            while True:
                try:
                    # Check for queued responses (non-blocking with timeout)
                    try:
                        response = await asyncio.wait_for(
                            session["queue"].get(),
                            timeout=15.0
                        )
                        # Send response as SSE message
                        yield f"event: message\ndata: {json.dumps(response)}\n\n"
                        mcp_logger.debug(f"SSE_RESPONSE | Session: {session_id} | Response sent")
                    except asyncio.TimeoutError:
                        # Send keepalive ping
                        ping_counter += 1
                        yield f": ping - {dt_datetime.now().isoformat()} - {ping_counter}\n\n"

                except Exception as e:
                    mcp_logger.error(f"SSE_ERROR | Session: {session_id} | Error: {e}")
                    break

        except asyncio.CancelledError:
            mcp_logger.info(f"SSE_DISCONNECT | Session: {session_id}")
        finally:
            # Cleanup session on disconnect
            if session_id in _mcp_sessions:
                _clear_mcp_session(session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )


# ============================================================================
# MCP Messages Endpoint (POST /mcp/messages)
# Handles JSON-RPC requests from Cursor
# ============================================================================

@public_router.post("/mcp/messages", tags=["MCP"], summary="MCP messages endpoint for JSON-RPC (session-aware)")
@public_router.post("/mcp/messages/", tags=["MCP"], summary="MCP messages endpoint for JSON-RPC (session-aware)")
@public_router.post("/messages", tags=["MCP"], summary="MCP messages alias (session-aware)")
@public_router.post("/messages/", tags=["MCP"], summary="MCP messages alias (session-aware)")
@router.post("/mcp/messages", tags=["MCP"], summary="MCP messages endpoint for JSON-RPC")
@router.post("/mcp/messages/", tags=["MCP"], summary="MCP messages endpoint for JSON-RPC")
@router.post("/messages", tags=["MCP"], summary="MCP messages (alias)")
@router.post("/messages/", tags=["MCP"], summary="MCP messages (alias)")
async def mcp_messages_handler(request: Request, session_id: Optional[str] = None):
    """
    Handle JSON-RPC messages from MCP clients.

    Cursor sends requests here after connecting to /sse.
    Responses are either:
    1. Returned directly as JSON (simple requests)
    2. Queued to the SSE stream (for streaming)
    """
    import time as _time
    from ..utils.triforce_logging import multi_logger

    client_ip = request.client.host if request.client else "unknown"
    start_time = _time.time()
    method = None
    params = None
    error_msg = None

    # Get session if exists
    session = _mcp_sessions.get(session_id) if session_id else None

    # Legacy SSE transport:
    # The initial GET /v1/mcp/sse is authenticated and creates the session.
    # Some MCP clients do not resend Authorization on POST /messages.
    if session and session.get("authenticated"):
        _restore_session_request_state(session, request)
        session["last_seen"] = datetime.now(timezone.utc)
    else:
        await require_mcp_auth(request)
    request.state.mcp_session_id = session_id or ""

    mcp_logger.info(f"MCP_MESSAGE | IP: {client_ip} | Session: {session_id or 'none'}")

    try:
        body = await request.json()
        jsonrpc_version = body.get("jsonrpc")
        method = body.get("method")
        params = body.get("params", {})
        req_id = body.get("id")

        if jsonrpc_version != "2.0":
            error_msg = "jsonrpc field must be '2.0'"
            return JSONResponse(
                content={"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request", "data": error_msg}, "id": req_id},
                status_code=400
            )

        if not method:
            error_msg = "method field is required"
            return JSONResponse(
                content={"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request", "data": error_msg}, "id": req_id},
                status_code=400
            )

        mcp_logger.info(f"MCP_METHOD | Session: {session_id} | Method: {method}")

        # Handle initialize specially
        if method == "initialize":
            # Mark session as initialized
            if session:
                session["initialized"] = True

            result = {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "serverInfo": {
                    "name": "ailinux-mcp-server",
                    "version": VERSION
                },
                "capabilities": {
                    "tools": {"listChanged": True},
                    "prompts": {"listChanged": True},
                    "resources": {"listChanged": True}
                },
                "instructions": _mcp_instructions_for_request(request)
            }
            response = {"jsonrpc": "2.0", "result": result, "id": req_id}

            # Queue response for SSE stream if session exists
            if session:
                await session["queue"].put(response)

            latency_ms = (_time.time() - start_time) * 1000
            await multi_logger.log_mcp(method, params, result, latency_ms)

            return JSONResponse(content=response)

        # Handle notifications/initialized (client acknowledgment)
        if method == "notifications/initialized":
            return JSONResponse(content={"jsonrpc": "2.0", "result": {}, "id": req_id})

        # Handle other MCP methods through standard handlers
        handler = MCP_HANDLERS.get(method)
        # NOTE: V4-fallback removed 2026-04-28 (Code Review) — the previous
        # "call_v4_tool(tool_name, arguments)" call referenced undefined names
        # in this scope, raised NameError, and was silently swallowed. Standard
        # MCP methods (tools/list, tools/call, etc.) are handled via MCP_HANDLERS
        # directly; v4 tool dispatch is done inside handle_tools_call.

        if not handler:
            error_msg = f"Method '{method}' not supported"
            return JSONResponse(
                content={"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found", "data": error_msg}, "id": req_id},
                status_code=404
            )

        # Execute handler. Request-aware standard MCP methods enforce
        # client-specific tool visibility and call permissions.
        result = await _call_mcp_method_handler(method, handler, params, request)

        latency_ms = (_time.time() - start_time) * 1000

        # Log successful call
        await multi_logger.log_mcp(method, params, result, latency_ms)
        
        # v2.82: Dedicated tool call logging for tools/call method
        if method == "tools/call":
            tool_name = params.get("name", "unknown")
            tool_args = params.get("arguments", {})
            # Detect caller from request headers
            caller = "unknown"
            if hasattr(request, 'headers'):
                user_agent = request.headers.get("user-agent", "")
                if "claude" in user_agent.lower() or "anthropic" in user_agent.lower():
                    caller = "claude"
                elif "gemini" in user_agent.lower() or "google" in user_agent.lower():
                    caller = "gemini"
                elif "codex" in user_agent.lower():
                    caller = "codex"
            await multi_logger.log_mcp_tool_call(
                tool_name=tool_name,
                params=tool_args,
                result_status="success",
                latency_ms=latency_ms,
                caller=caller,
                result_preview=str(result)[:300] if result else None
            )
            record_mcp_call(tool_name, latency_ms, "success", "mcp_messages_handler", result_size=len(str(result)) if result else 0)

        response = {"jsonrpc": "2.0", "result": result, "id": req_id}

        # Queue response for SSE if session exists
        if session:
            await session["queue"].put(response)

        return JSONResponse(content=response)

    except ValueError as exc:
        error_msg = str(exc)
        tn = params.get("name", method or "unknown") if isinstance(params, dict) else (method or "unknown")
        _err_latency = (_time.time() - start_time) * 1000
        record_mcp_call(tn, _err_latency, "error", "mcp_messages_handler", error=error_msg)
        return JSONResponse(
            content={"jsonrpc": "2.0", "error": {"code": -32000, "message": error_msg}, "id": None},
            status_code=400
        )
    except Exception as exc:
        error_msg = str(exc)
        mcp_logger.error(f"MCP_ERROR | Session: {session_id} | Error: {error_msg}")
        tn = params.get("name", method or "unknown") if isinstance(params, dict) else (method or "unknown")
        _err_latency = (_time.time() - start_time) * 1000
        record_mcp_call(tn, _err_latency, "error", "mcp_messages_handler", error=error_msg)
        return JSONResponse(
            content={"jsonrpc": "2.0", "error": {"code": -32000, "message": "Internal error", "data": error_msg}, "id": None},
            status_code=500
        )


# ============================================================================
# UNIFIED MCP ENDPOINT - Maximum Compatibility
# Supports: Streamable HTTP (2025-03-26), Legacy SSE (2024-11-05), ChatGPT
# ============================================================================

async def _process_mcp_request(
    body: Dict[str, TypingAny],
    request: Request,
    session_id: Optional[str] = None
) -> Dict[str, TypingAny]:
    """Process a single JSON-RPC request and return the response."""
    import time as _time
    from ..utils.triforce_logging import multi_logger

    start_time = _time.time()

    jsonrpc_version = body.get("jsonrpc")
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    # Validate JSON-RPC version
    if jsonrpc_version != "2.0":
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": "Invalid Request", "data": "jsonrpc must be '2.0'"},
            "id": req_id
        }

    # Handle notifications (methods starting with "notifications/")
    # These can come with or without an id depending on client
    if method and method.startswith("notifications/"):
        # Notifications are fire-and-forget, just acknowledge
        # Common notifications: initialized, cancelled, progress, etc.
        if req_id is None:
            return None  # No id = no response expected
        else:
            # Some clients send notifications with id, return empty success
            return {"jsonrpc": "2.0", "result": {}, "id": req_id}

    if not method:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": "Invalid Request", "data": "method is required"},
            "id": req_id
        }

    # Special handling for initialize
    if method == "initialize":
        # Get session if provided
        session = _mcp_sessions.get(session_id) if session_id else None
        if session:
            session["initialized"] = True

        result = {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "serverInfo": {
                "name": "ailinux-mcp-server",
                "version": VERSION
            },
            "capabilities": {
                "tools": {"listChanged": True},
                "prompts": {"listChanged": True},
                "resources": {"listChanged": True}
            },
            "instructions": _mcp_instructions_for_request(request)
        }
        latency_ms = (_time.time() - start_time) * 1000
        await multi_logger.log_mcp(method, params, result, latency_ms)
        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    # Find handler
    handler = MCP_HANDLERS.get(method)
    # Compatibility fallback
    if not handler and "." in method:
        handler = MCP_HANDLERS.get(method.replace(".", "_"))
    if not handler and "_" in method:
        handler = MCP_HANDLERS.get(method.replace("_", "."))

    # NOTE: Dead V4 fallback removed 2026-04-28 (Code Review) — referenced
    # undefined names tool_name/arguments. Tool calls are dispatched through
    # the "tools/call" handler which has correct argument extraction.

    if not handler:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": "Method not found", "data": f"'{method}' not supported"},
            "id": req_id
        }

    # Execute handler
    try:
        result = await _call_mcp_method_handler(method, handler, params, request)
        latency_ms = (_time.time() - start_time) * 1000
        await multi_logger.log_mcp(method, params, result, latency_ms)
        return {"jsonrpc": "2.0", "result": result, "id": req_id}
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32000, "message": str(e)},
            "id": req_id
        }


@router.post("/mcp", tags=["MCP"], summary="Unified MCP endpoint (Streamable HTTP + Legacy)")
@router.post("/mcp/", tags=["MCP"], summary="Unified MCP endpoint (Streamable HTTP + Legacy)")
async def mcp_unified_endpoint(request: Request):
    """
    Unified MCP Endpoint - Maximum Compatibility

    Supports ALL MCP transport types:

    1. **Streamable HTTP (2025-03-26)** - New standard
       - POST with Accept: application/json → JSON response
       - POST with Accept: text/event-stream → SSE stream response
       - Supports batch requests (JSON array)
       - Session management via Mcp-Session-Id header

    2. **Legacy SSE (2024-11-05)** - For Cursor, older clients
       - GET /sse → SSE stream with endpoint event
       - POST /messages → JSON-RPC messages

    3. **ChatGPT** - OpenAI MCP integration
       - POST /mcp with JSON-RPC → JSON response

    Headers:
    - Accept: application/json, text/event-stream (for streaming)
    - Mcp-Session-Id: Session ID (optional, returned in initialize response)
    - Authorization: Bearer <token> or Basic <base64(user:pass)>
    """
    import logging

    await require_mcp_auth(request)

    _log = logging.getLogger("ailinux.mcp.unified")
    client_ip = request.client.host if request.client else "unknown"

    # Get headers
    accept_header = request.headers.get("Accept", "application/json")
    session_id = request.headers.get("Mcp-Session-Id") or request.headers.get("mcp-session-id")

    wants_streaming = "text/event-stream" in accept_header

    _log.info(f"MCP_UNIFIED | IP: {client_ip} | Session: {session_id or 'none'} | Accept: {accept_header}")

    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error", "data": str(e)}, "id": None},
            status_code=400
        )

    # Establish the Streamable HTTP session before initialize is processed so
    # public workspace pairing belongs to this exact ChatGPT/Codex MCP session.
    is_initialize = (
        isinstance(body, dict) and body.get("method") == "initialize"
    ) or (
        isinstance(body, list) and any(isinstance(item, dict) and item.get("method") == "initialize" for item in body)
    )
    response_headers: Dict[str, str] = {}
    if is_initialize and not session_id:
        session_id = str(uuid.uuid4()).replace("-", "")
        session = _get_session(session_id)
        _store_session_request_state(session, request)
        response_headers["Mcp-Session-Id"] = session_id
        _log.info(f"MCP_SESSION_CREATED | Session: {session_id}")
    elif session_id and session_id in _mcp_sessions:
        session = _mcp_sessions[session_id]
        # Public guest requests are credential-less; preserve the original
        # session profile. Authenticated requests keep their freshly validated state.
        if getattr(request.state, "mcp_auth_method", None) == "public_guest" and session.get("auth_method"):
            _restore_session_request_state(session, request)
        session["last_seen"] = datetime.now(timezone.utc)
    request.state.mcp_session_id = session_id or ""

    # Handle batch requests (JSON array)
    if isinstance(body, list):
        responses = []
        for item in body:
            response = await _process_mcp_request(item, request, session_id)
            if response is not None:  # Skip notification responses
                responses.append(response)

        if not responses:
            return JSONResponse(content={"status":"accepted"}, status_code=202)  # All notifications, no response needed

        # Return batch response
        if wants_streaming:
            async def stream_batch():
                for resp in responses:
                    yield f"event: message\ndata: {json.dumps(resp)}\n\n"
            return StreamingResponse(
                stream_batch(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            )

        return JSONResponse(content=responses, headers=response_headers)

    # Single request
    response = await _process_mcp_request(body, request, session_id)

    if response is None:
        return Response(status_code=202)  # Notification acknowledged

    # Return streaming response if requested
    if wants_streaming:
        async def stream_single():
            yield f"event: message\ndata: {json.dumps(response)}\n\n"
        return StreamingResponse(
            stream_single(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                **response_headers
            }
        )

    return JSONResponse(content=response, headers=response_headers)


# ============================================================================
# Streamable HTTP GET Endpoint (2025-03-26 spec)
# For server-initiated messages
# ============================================================================

@router.get("/mcp/stream", tags=["MCP"], summary="Streamable HTTP GET for server messages")
@router.get("/mcp/stream/", tags=["MCP"], summary="Streamable HTTP GET for server messages")
async def mcp_streamable_get(request: Request):
    """
    Streamable HTTP GET endpoint for receiving server-initiated messages.

    Per MCP spec 2025-03-26:
    - Client opens SSE stream
    - Server MAY send JSON-RPC requests/notifications
    - Used for server-to-client communication
    """
    await require_mcp_auth(request)

    session_id = request.headers.get("Mcp-Session-Id") or request.headers.get("mcp-session-id")

    if not session_id or session_id not in _mcp_sessions:
        return JSONResponse(
            content={"error": "Invalid or missing session"},
            status_code=400
        )

    session = _mcp_sessions[session_id]
    client_ip = request.client.host if request.client else "unknown"

    mcp_logger.info(f"MCP_STREAM_GET | IP: {client_ip} | Session: {session_id}")

    async def server_message_stream():
        try:
            ping_counter = 0
            while True:
                try:
                    # Check for queued server messages
                    response = await asyncio.wait_for(
                        session["queue"].get(),
                        timeout=30.0
                    )
                    yield f"event: message\ndata: {json.dumps(response)}\n\n"
                except asyncio.TimeoutError:
                    ping_counter += 1
                    yield f": keepalive {ping_counter}\n\n"
        except asyncio.CancelledError:
            mcp_logger.info(f"MCP_STREAM_CLOSED | Session: {session_id}")

    return StreamingResponse(
        server_message_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ============================================================================
# Session Termination (DELETE)
# ============================================================================

@router.delete("/mcp", tags=["MCP"], summary="Terminate MCP session")
@router.delete("/mcp/", tags=["MCP"], summary="Terminate MCP session")
async def mcp_delete_session(request: Request):
    """
    Terminate an MCP session.

    Per MCP spec 2025-03-26:
    - Client sends DELETE with Mcp-Session-Id header
    - Server removes session
    """
    await require_mcp_auth(request)

    session_id = request.headers.get("Mcp-Session-Id") or request.headers.get("mcp-session-id")

    if not session_id:
        return JSONResponse(
            content={"error": "Missing Mcp-Session-Id header"},
            status_code=400
        )

    if session_id in _mcp_sessions:
        _clear_mcp_session(session_id)
        mcp_logger.info(f"MCP_SESSION_DELETED | Session: {session_id}")
        return JSONResponse(content={"status":"deleted"}, status_code=200)

    return JSONResponse(
        content={"error": "Session not found"},
        status_code=404
    )


# Connection management endpoints removed - stateless API key auth only


@router.post("/mcp_write_fallback", tags=["MCP"], summary="Store MCP write fallback")
async def mcp_write_fallback_endpoint(request: Request):
    """HTTP endpoint for clients that need to queue denied write operations."""
    payload = await request.json()
    result = await handle_mcp_write_fallback(payload)
    result["endpoint"] = "/v1/mcp_write_fallback"
    return JSONResponse(content=result)


@router.post("/mcp/trigger_mcp_write_fallback", tags=["MCP"], summary="Trigger MCP write fallback")
async def trigger_mcp_write_fallback_endpoint(request: Request):
    """Compatibility endpoint for MCP write fallback trigger calls."""
    payload = await request.json()
    result = await handle_mcp_write_fallback(payload)
    result["endpoint"] = "/v1/mcp/trigger_mcp_write_fallback"
    return JSONResponse(content=result)
