package me.ailinux.workspace;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.widget.*;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int PICK_TREE=2401,NOTIFY=2402; private StateStore state; private TextView status,folder; private EditText pair; private CheckBox write;
    private final BroadcastReceiver receiver=new BroadcastReceiver(){@Override public void onReceive(Context c,Intent i){String s=i.getStringExtra("state");if(s!=null)status.setText(s);}};
    @Override public void onCreate(Bundle b){super.onCreate(b);state=new StateStore(this);buildUi();handleIntent(getIntent());refresh();if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},NOTIFY);}
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);handleIntent(intent);}
    @Override protected void onResume(){super.onResume();registerReceiver(receiver,new IntentFilter("me.ailinux.workspace.STATE"),Build.VERSION.SDK_INT>=33?Context.RECEIVER_NOT_EXPORTED:0);refresh();}
    @Override protected void onPause(){try{unregisterReceiver(receiver);}catch(Exception ignored){}super.onPause();}
    private void buildUi(){ScrollView scroll=new ScrollView(this);LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);l.setPadding(40,40,40,40);scroll.addView(l);TextView title=new TextView(this);title.setText("AILinux Workspace");title.setTextSize(28);title.setTextColor(Color.WHITE);l.addView(title);TextView desc=new TextView(this);desc.setText("Native Android MCP workspace executor · api.ailinux.me");desc.setTextColor(0xffb8c1cc);desc.setPadding(0,8,0,24);l.addView(desc);folder=new TextView(this);folder.setTextColor(Color.WHITE);l.addView(folder);Button choose=button("Choose workspace folder");choose.setOnClickListener(v->chooseFolder());l.addView(choose);write=new CheckBox(this);write.setText("Read / Write mode");write.setTextColor(Color.WHITE);write.setChecked("write".equals(state.mode()));l.addView(write);pair=new EditText(this);pair.setHint("Pair code (optional)");pair.setTextColor(Color.WHITE);pair.setHintTextColor(0xff8b949e);l.addView(pair);Button start=button("Start / reconnect executor");start.setOnClickListener(v->startExecutor("") );l.addView(start);Button stop=button("Disconnect and revoke lease");stop.setOnClickListener(v->stopService(new Intent(this,WorkspaceService.class).setAction(WorkspaceService.ACTION_STOP)));l.addView(stop);Button web=button("Open api.ailinux.me/v1/mcp");web.setOnClickListener(v->startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse("https://api.ailinux.me/v1/mcp"))));l.addView(web);status=new TextView(this);status.setTextColor(0xff63d471);status.setPadding(0,28,0,0);l.addView(status);setContentView(scroll);scroll.setBackgroundColor(0xff0d1117);}
    private Button button(String text){Button b=new Button(this);b.setText(text);return b;}
    private void chooseFolder(){Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION|Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION|Intent.FLAG_GRANT_PREFIX_URI_PERMISSION);startActivityForResult(i,PICK_TREE);}
    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data){super.onActivityResult(requestCode,resultCode,data);if(requestCode==PICK_TREE&&resultCode==RESULT_OK&&data!=null&&data.getData()!=null){Uri uri=data.getData();int flags=data.getFlags()&(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION);try{getContentResolver().takePersistableUriPermission(uri,flags);}catch(SecurityException e){status.setText("Persistent folder permission failed: "+e.getMessage());return;}state.setTree(uri);state.setMode(write.isChecked()?"write":"read_only");refresh();startExecutor("");}}
    private void handleIntent(Intent intent){if(intent==null||intent.getData()==null)return;Uri u=intent.getData();String handoff="";if("ailinux-workspace".equals(u.getScheme()))handoff=u.getQueryParameter("code");else if("api.ailinux.me".equals(u.getHost())){String p=u.getQueryParameter("pair_code");if(p!=null)state.setPairCode(p);}if(handoff!=null&&!handoff.isEmpty())startExecutor(handoff);}
    private void startExecutor(String handoff){state.setMode(write.isChecked()?"write":"read_only");String p=pair.getText().toString().trim().toUpperCase(Locale.ROOT);if(!p.isEmpty())state.setPairCode(p);Intent i=new Intent(this,WorkspaceService.class).setAction(WorkspaceService.ACTION_START);if(handoff!=null&&!handoff.isEmpty())i.putExtra(WorkspaceService.EXTRA_HANDOFF,handoff);if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);status.setText("Executor requested…");}
    private void refresh(){Uri tree=state.tree();folder.setText(tree==null?"No folder shared":"Workspace: "+tree);pair.setText(state.pairCode());write.setChecked("write".equals(state.mode()));if(status.getText().length()==0)status.setText(tree==null?"Choose a folder once; Android keeps the URI grant.":"Ready. Start executor or open a pair/handoff link.");}
}
