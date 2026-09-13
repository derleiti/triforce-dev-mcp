package me.ailinux.workspace;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.DocumentsContract;
import org.json.JSONObject;

import java.io.File;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Android shell backend for the AILinux Loom workspace executor.
 *
 * Android has no generic process API for other apps, so the terminal is served
 * by Termux through its RUN_COMMAND service. Two gates apply, exactly like the
 * desktop backends: the backend must actually be available, and the user must
 * have released the terminal. Capabilities are never advertised otherwise.
 *
 * Requirements on the device:
 *   1. Termux installed (F-Droid / GitHub build, not the Play Store build)
 *   2. allow-external-apps=true in ~/.termux/termux.properties
 *   3. permission com.termux.permission.RUN_COMMAND granted to this app
 */
final class TermuxShell {
    static final String TERMUX_PACKAGE = "com.termux";
    static final String RUN_COMMAND_SERVICE = "com.termux.app.RunCommandService";
    static final String ACTION_RUN_COMMAND = "com.termux.RUN_COMMAND";
    static final String PERMISSION_RUN_COMMAND = "com.termux.permission.RUN_COMMAND";
    private static final String PREFIX = "/data/data/com.termux/files/usr";
    private static final String HOME = "/data/data/com.termux/files/home";
    private static final String RESULT_ACTION = "me.ailinux.workspace.SHELL_RESULT";

    private final Context context;
    private final StateStore state;

    TermuxShell(Context context, StateStore state) {
        this.context = context.getApplicationContext();
        this.state = state;
    }

    boolean termuxInstalled() {
        try {
            context.getPackageManager().getPackageInfo(TERMUX_PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    boolean permissionGranted() {
        return context.checkSelfPermission(PERMISSION_RUN_COMMAND) == PackageManager.PERMISSION_GRANTED;
    }

    boolean available() { return termuxInstalled() && permissionGranted(); }

    boolean released() { return available() && state.shellReleased(); }

    /** Why the shell is or is not usable - shown in the app and reported to the server. */
    String detail() {
        if (!termuxInstalled()) return "Termux is not installed (F-Droid build required)";
        if (!permissionGranted()) return "Termux RUN_COMMAND permission not granted";
        if (!state.shellReleased()) return "terminal not released by the user";
        return "Termux shell released";
    }

    JSONObject status(Uri tree) {
        JSONObject o = new JSONObject();
        try {
            o.put("backend", available() ? "termux" : "");
            o.put("label", "Android (Termux)");
            o.put("available", available());
            o.put("sandboxed", false);
            o.put("released", released());
            o.put("platform", "android");
            o.put("detail", detail());
            o.put("workdir", workdir(tree));
        } catch (Exception ignored) { }
        return o;
    }

    /**
     * Best-effort mapping from the SAF tree to a path Termux can reach. Only
     * primary external storage can be expressed as a real path; anything else
     * falls back to the Termux home directory rather than pretending.
     */
    String workdir(Uri tree) {
        if (tree == null) return HOME;
        try {
            String docId = DocumentsContract.getTreeDocumentId(tree);
            if (docId == null) return HOME;
            int split = docId.indexOf(':');
            String volume = split < 0 ? docId : docId.substring(0, split);
            String relative = split < 0 ? "" : docId.substring(split + 1);
            if (!"primary".equalsIgnoreCase(volume)) return HOME;
            File base = new File("/storage/emulated/0", relative);
            return base.getAbsolutePath();
        } catch (Exception e) {
            return HOME;
        }
    }

    /** Run a command and block until Termux reports back, or the timeout hits. */
    JSONObject run(String command, String cwd, int timeoutSeconds, Uri tree) throws Exception {
        JSONObject result = new JSONObject();
        if (command == null || command.trim().isEmpty()) return fail(result, "command is required");
        if (!termuxInstalled()) return fail(result, "shell unavailable: Termux is not installed");
        if (!permissionGranted()) return fail(result, "shell unavailable: RUN_COMMAND permission not granted");
        if (!state.shellReleased()) return fail(result, "shell not released: enable terminal access in the app first");

        String base = workdir(tree);
        String workdir = (cwd == null || cwd.trim().isEmpty() || ".".equals(cwd.trim()))
                ? base : new File(base, cwd.trim()).getAbsolutePath();
        int timeout = Math.max(1, Math.min(timeoutSeconds <= 0 ? 120 : timeoutSeconds, 300));

        final CountDownLatch done = new CountDownLatch(1);
        final AtomicReference<Bundle> payload = new AtomicReference<>(null);
        BroadcastReceiver receiver = new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent intent) {
                payload.set(intent == null ? null : intent.getBundleExtra("result"));
                done.countDown();
            }
        };
        IntentFilter filter = new IntentFilter(RESULT_ACTION);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            context.registerReceiver(receiver, filter);
        }
        try {
            Intent callback = new Intent(RESULT_ACTION).setPackage(context.getPackageName());
            PendingIntent pending = PendingIntent.getBroadcast(
                    context, (int) (System.nanoTime() & 0x7fffffff), callback,
                    PendingIntent.FLAG_ONE_SHOT | PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

            Intent intent = new Intent(ACTION_RUN_COMMAND);
            intent.setClassName(TERMUX_PACKAGE, RUN_COMMAND_SERVICE);
            intent.putExtra("com.termux.RUN_COMMAND_PATH", PREFIX + "/bin/bash");
            intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-o", "pipefail", "-c", command});
            intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", workdir);
            intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            intent.putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0");
            intent.putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pending);
            context.startService(intent);

            if (!done.await(timeout, TimeUnit.SECONDS)) {
                return fail(result, "command timed out after " + timeout + "s");
            }
            Bundle out = payload.get();
            if (out == null) return fail(result, "Termux returned no result bundle");
            String stdout = nullToEmpty(out.getString("stdout"));
            String stderr = nullToEmpty(out.getString("stderr"));
            String errmsg = nullToEmpty(out.getString("errmsg"));
            int exitCode = out.getInt("exitCode", -1);
            StringBuilder text = new StringBuilder();
            if (!stdout.isEmpty()) text.append("stdout:\n").append(stdout);
            if (!stderr.isEmpty()) text.append(text.length() > 0 ? "\n" : "").append("stderr:\n").append(stderr);
            if (!errmsg.isEmpty()) text.append(text.length() > 0 ? "\n" : "").append("termux:\n").append(errmsg);
            text.append(text.length() > 0 ? "\n" : "")
                .append("exit_code=").append(exitCode)
                .append(" backend=termux sandboxed=false workdir=").append(workdir);
            String body = text.toString();
            if (body.length() > 12000) body = body.substring(0, 12000);
            return text(result, body, exitCode != 0);
        } finally {
            try { context.unregisterReceiver(receiver); } catch (Exception ignored) { }
        }
    }

    private static String nullToEmpty(String value) { return value == null ? "" : value; }

    private static JSONObject text(JSONObject target, String body, boolean error) throws Exception {
        return target.put("content", new org.json.JSONArray()
                        .put(new JSONObject().put("type", "text").put("text", body)))
                .put("isError", error);
    }

    private static JSONObject fail(JSONObject target, String message) throws Exception {
        return text(target, message, true);
    }
}
