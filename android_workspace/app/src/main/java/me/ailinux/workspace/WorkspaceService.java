package me.ailinux.workspace;

import android.app.*;
import android.content.*;
import android.os.*;
import androidx.core.app.NotificationCompat;

public class WorkspaceService extends Service implements ProtocolClient.Listener {
    public static final String ACTION_START="me.ailinux.workspace.START",ACTION_STOP="me.ailinux.workspace.STOP",EXTRA_HANDOFF="handoff_code";
    private static final String CHANNEL="workspace_executor"; private ProtocolClient client;
    @Override public void onCreate(){super.onCreate();createChannel();client=new ProtocolClient(this,this);}
    @Override public int onStartCommand(Intent intent,int flags,int startId){if(intent!=null&&ACTION_STOP.equals(intent.getAction())){client.stop(true);stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();return START_NOT_STICKY;}String handoff=intent==null?null:intent.getStringExtra(EXTRA_HANDOFF);if(handoff!=null&&!handoff.isEmpty())client.setHandoffCode(handoff);startForeground(8606,notification("Starting workspace executor…"));client.start();return START_STICKY;}
    @Override public void onDestroy(){if(client!=null)client.stop(false);super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
    @Override public void onState(String state){getSystemService(NotificationManager.class).notify(8606,notification(state));sendBroadcast(new Intent("me.ailinux.workspace.STATE").setPackage(getPackageName()).putExtra("state",state));}
    @Override public void onResumeToken(String token){}
    private Notification notification(String text){Intent open=new Intent(this,MainActivity.class);PendingIntent pi=PendingIntent.getActivity(this,0,open,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);Intent stop=new Intent(this,WorkspaceService.class).setAction(ACTION_STOP);PendingIntent si=PendingIntent.getService(this,1,stop,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);return new NotificationCompat.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_upload_done).setContentTitle("AILinux Workspace").setContentText(text).setOngoing(true).setContentIntent(pi).addAction(0,"Disconnect",si).build();}
    private void createChannel(){if(Build.VERSION.SDK_INT>=26){NotificationChannel c=new NotificationChannel(CHANNEL,"AILinux Workspace Executor",NotificationManager.IMPORTANCE_LOW);c.setDescription("Persistent local MCP workspace connection");getSystemService(NotificationManager.class).createNotificationChannel(c);}}
}
