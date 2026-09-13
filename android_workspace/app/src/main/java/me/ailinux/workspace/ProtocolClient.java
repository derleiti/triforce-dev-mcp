package me.ailinux.workspace;

import android.content.Context;
import android.os.Build;
import android.util.Log;
import org.json.JSONArray;
import org.json.JSONObject;
import okhttp3.*;
import java.io.IOException;
import java.net.URLEncoder;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

final class ProtocolClient extends WebSocketListener {
    interface Listener { void onState(String state); void onResumeToken(String token); void onPairCode(String code); }
    static final String VERSION="2.86.7-android";
    static final String BASE="https://api.ailinux.me";
    private static final String TAG="AILinuxWorkspace";
    private final Context context; private final StateStore state; private final Listener listener;
    private final OkHttpClient http=new OkHttpClient.Builder().pingInterval(25, TimeUnit.SECONDS).retryOnConnectionFailure(true).build();
    private final ScheduledExecutorService timer=Executors.newSingleThreadScheduledExecutor(); private final ExecutorService tools=Executors.newSingleThreadExecutor();
    private volatile WebSocket ws; private volatile String handoffCode=""; private volatile int reconnectAttempt=0; private volatile ScheduledFuture<?> reconnectFuture; private final AtomicBoolean stopped=new AtomicBoolean(false); private final AtomicBoolean connecting=new AtomicBoolean(false);
    private SafWorkspace workspace;
    private final JSONArray readCaps=new JSONArray().put("workspace_info").put("file_read").put("file_tree").put("code_read").put("code_tree").put("code_search").put("code_grep").put("file_ops");

    ProtocolClient(Context context, Listener listener){this.context=context.getApplicationContext();this.state=new StateStore(context);this.listener=listener;}
    void setHandoffCode(String code){handoffCode=code==null?"":code.trim().toUpperCase();}
    void start(){stopped.set(false);cancelReconnect();WebSocket current=ws;if(current!=null)return;connect();}
    void stop(boolean revoke){stopped.set(true);cancelReconnect();connecting.set(false);WebSocket s=ws;if(s!=null){if(revoke){try{s.send(new JSONObject().put("jsonrpc","2.0").put("method","workspace/revoke").put("params",new JSONObject()).toString());}catch(Exception ignored){}}s.close(1000,"user disconnect");}ws=null;if(revoke)state.clearCredentials();listener.onState("Disconnected");}

    private JSONArray capabilities(){JSONArray out=new JSONArray();for(int i=0;i<readCaps.length();i++)out.put(readCaps.optString(i));if("write".equals(state.mode())){out.put("file_edit").put("directory_create").put("workspace_clear").put("code_edit");}return out;}
    private void connect(){
        if(stopped.get()||!connecting.compareAndSet(false,true))return;
        if(state.tree()==null){connecting.set(false);listener.onState("Choose a workspace folder first");return;}
        try{workspace=new SafWorkspace(context,state.tree(),"write".equals(state.mode()));}
        catch(Exception e){connecting.set(false);listener.onState("Workspace unavailable: "+e.getMessage());return;}
        listener.onState("Connecting executor…");
        if(!handoffCode.isEmpty()){openSocket("handoff_code",handoffCode);return;}
        String resume=state.resumeToken();
        if(resume!=null&&!resume.isEmpty()){
            final RequestBody body;
            try{body=RequestBody.create(new JSONObject().put("resume_token",resume).toString(),MediaType.parse("application/json"));}
            catch(Exception e){connecting.set(false);listener.onState("Could not prepare resume request");return;}
            http.newCall(new Request.Builder().url(BASE+"/v1/mcp/workspace/resume-ticket").post(body).build()).enqueue(new Callback(){
                public void onFailure(Call c,IOException e){scheduleReconnect("Resume ticket failed");}
                public void onResponse(Call c,Response r)throws IOException{
                    try(Response x=r){
                        if(x.code()==403){state.clearCredentials();listener.onState("Saved workspace lease expired · open a fresh pair link");return;}
                        if(!x.isSuccessful()){scheduleReconnect("Resume rejected: "+x.code());return;}
                        ResponseBody responseBody=x.body();
                        if(responseBody==null){scheduleReconnect("Resume returned no body");return;}
                        String code=new JSONObject(responseBody.string()).optString("pair_code","");
                        if(code.isEmpty())scheduleReconnect("Resume returned no ticket");else openSocket("pair_code",code);
                    }catch(Exception e){scheduleReconnect("Resume error");}
                }
            });
            return;
        }
        String pair=state.pairCode();
        if(pair!=null&&!pair.isEmpty()){listener.onPairCode(pair);openSocket("pair_code",pair);return;}
        createPairTicket();
    }
    private void createPairTicket(){
        listener.onState("Creating one-time workspace pair code…");
        http.newCall(new Request.Builder().url(BASE+"/v1/mcp/workspace/pair-ticket").post(RequestBody.create(new byte[0],null)).build()).enqueue(new Callback(){
            public void onFailure(Call c,IOException e){scheduleReconnect("Pair ticket failed");}
            public void onResponse(Call c,Response r)throws IOException{
                try(Response x=r){
                    if(!x.isSuccessful()){scheduleReconnect("Pair ticket rejected: "+x.code());return;}
                    ResponseBody responseBody=x.body();
                    if(responseBody==null){scheduleReconnect("Pair ticket returned no body");return;}
                    String code=new JSONObject(responseBody.string()).optString("pair_code","").trim().toUpperCase();
                    if(code.isEmpty()){scheduleReconnect("Pair ticket returned no code");return;}
                    state.setPairCode(code);
                    listener.onPairCode(code);
                    listener.onState("Waiting for AI pairing · "+code);
                    openSocket("pair_code",code);
                }catch(Exception e){scheduleReconnect("Pair ticket error");}
            }
        });
    }
    private static String enc(String value){try{return URLEncoder.encode(value,"UTF-8").replace("+","%20");}catch(Exception e){throw new IllegalArgumentException("URL encoding failed",e);}}
    private void openSocket(String key,String code){String url="wss://api.ailinux.me/v1/mcp/node/connect?mode=workspace&"+key+"="+enc(code)+"&machine_id=android&client_version="+enc(VERSION);WebSocket previous=ws;WebSocket next=http.newWebSocket(new Request.Builder().url(url).build(),this);ws=next;if(previous!=null&&previous!=next)previous.cancel();}
    @Override public void onOpen(WebSocket socket,Response response){if(socket!=ws){socket.cancel();return;}connecting.set(false);cancelReconnect();reconnectAttempt=0;listener.onState("Transport connected");}
    @Override public void onMessage(WebSocket socket,String text){try{JSONObject msg=new JSONObject(text);String method=msg.optString("method","");if("connected".equals(method)){sendHello(socket);return;}if("workspace/shared".equals(method)||"workspace/paired".equals(method)){JSONObject p=msg.optJSONObject("params");if(p!=null&&p.optBoolean("ok",true)){String token=p.optString("resume_token","");if(!token.isEmpty()){state.setResumeToken(token);state.setPairCode("");handoffCode="";listener.onResumeToken(token);}listener.onState("Workspace connected · "+state.mode());}return;}if("workspace/detached".equals(method)){listener.onState("AI detached · lease retained");return;}if("ping".equals(method)){socket.send(new JSONObject().put("jsonrpc","2.0").put("method","pong").put("params",msg.optJSONObject("params")==null?new JSONObject():msg.optJSONObject("params")).toString());return;}if("tools/call".equals(method)){tools.submit(()->handleToolCall(socket,msg));}}
        catch(Exception e){Log.e(TAG,"protocol message",e);listener.onState("Protocol error: "+e.getMessage());}}
    private void sendHello(WebSocket socket)throws Exception{socket.send(new JSONObject().put("jsonrpc","2.0").put("method","client/info").put("params",new JSONObject().put("client","ailinux-android-workspace").put("platform","Android "+Build.VERSION.RELEASE).put("hostname",Build.MODEL).put("server_version",VERSION).put("mode","workspace").put("workspace",workspace.info(capabilities()).optString("workspace","android")).put("access_mode",state.mode()).put("remote_profile",state.mode())).toString());socket.send(new JSONObject().put("jsonrpc","2.0").put("method","tools/list").put("params",new JSONObject().put("tools",new JSONArray().put("client_workspace_tool"))).toString());socket.send(new JSONObject().put("jsonrpc","2.0").put("method","workspace/share").put("params",new JSONObject().put("task","").put("access_mode",state.mode()).put("mode",state.mode()).put("capabilities",capabilities())).toString());}
    private void handleToolCall(WebSocket socket,JSONObject msg){String id=String.valueOf(msg.opt("id"));String tool="";try{JSONObject outer=msg.getJSONObject("params").getJSONObject("arguments");tool=outer.optString("tool","");stage(socket,id,tool,"started");JSONObject args=outer.optJSONObject("arguments");if(args==null)args=new JSONObject();JSONObject data=execute(tool,args);stage(socket,id,tool,"finished");socket.send(resultMessage(msg.opt("id"),data,false).toString());}catch(Exception e){try{stage(socket,id,tool,"failed");socket.send(resultMessage(msg.opt("id"),new JSONObject().put("ok",false).put("error",String.valueOf(e.getMessage())),true).toString());}catch(Exception ignored){}}}
    private JSONObject execute(String tool,JSONObject args)throws Exception{switch(tool){case"workspace_info":return workspace.info(capabilities());case"file_read":case"code_read":return workspace.read(args);case"file_tree":case"code_tree":return workspace.tree(args);case"code_search":return workspace.search(args,false);case"code_grep":return workspace.search(args,true);case"file_edit":return workspace.edit(args);case"directory_create":return workspace.createDir(args);case"workspace_clear":return workspace.clear(args);case"file_ops":return workspace.fileOps(args);case"code_edit":return workspace.codeEdit(args);default:throw new IllegalArgumentException("unsupported Android workspace tool: "+tool);}}
    private void stage(WebSocket socket,String id,String tool,String stage)throws Exception{socket.send(new JSONObject().put("jsonrpc","2.0").put("method","workspace/tool_stage").put("params",new JSONObject().put("request_id",id).put("tool",tool).put("stage",stage)).toString());}
    private JSONObject resultMessage(Object id,JSONObject data,boolean error)throws Exception{JSONObject r=new JSONObject().put("content",new JSONArray().put(new JSONObject().put("type","text").put("text",data.toString()))).put("structuredContent",data).put("isError",error);return new JSONObject().put("jsonrpc","2.0").put("id",id==null?JSONObject.NULL:id).put("result",r);}
    @Override public void onClosed(WebSocket socket,int code,String reason){if(socket!=ws)return;ws=null;connecting.set(false);if(!stopped.get())scheduleReconnect("Disconnected ("+code+")");}
    @Override public void onFailure(WebSocket socket,Throwable t,Response response){if(socket!=ws)return;ws=null;connecting.set(false);if(!stopped.get())scheduleReconnect("Connection lost");}
    private synchronized void cancelReconnect(){ScheduledFuture<?> f=reconnectFuture;if(f!=null)f.cancel(false);reconnectFuture=null;}
    private synchronized void scheduleReconnect(String message){if(stopped.get())return;connecting.set(false);ScheduledFuture<?> f=reconnectFuture;if(f!=null&&!f.isDone())return;listener.onState(message+" · reconnecting");long delay=Math.min(30,1L<<Math.min(5,reconnectAttempt++));reconnectFuture=timer.schedule(()->{synchronized(ProtocolClient.this){reconnectFuture=null;}connect();},delay,TimeUnit.SECONDS);}
}
