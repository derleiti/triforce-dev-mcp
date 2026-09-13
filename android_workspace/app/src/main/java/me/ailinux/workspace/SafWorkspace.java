package me.ailinux.workspace;

import android.content.Context;
import android.net.Uri;
import androidx.documentfile.provider.DocumentFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Pattern;

final class SafWorkspace {
    private static final long MAX_TEXT = 2L * 1024L * 1024L;
    private static final Set<String> IGNORE = new HashSet<>(Arrays.asList(".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"));
    private final Context context;
    private final DocumentFile root;
    private final boolean writable;

    SafWorkspace(Context context, Uri treeUri, boolean writable) {
        this.context = context.getApplicationContext();
        this.root = DocumentFile.fromTreeUri(context, treeUri);
        this.writable = writable;
        if (root == null || !root.isDirectory()) throw new IllegalArgumentException("Invalid workspace tree URI");
    }

    JSONObject info(JSONArray caps) throws Exception {
        JSONObject o = new JSONObject();
        o.put("workspace", root.getName() == null ? "android" : root.getName());
        o.put("mode", writable ? "write" : "read_only");
        o.put("access", "android-saf-persisted-tree");
        o.put("capabilities", caps);
        o.put("top_level", tree(new JSONObject().put("path", "").put("max_depth", 1).put("max_entries", 100)).getJSONArray("entries"));
        return o;
    }

    static String cleanPath(String raw) {
        String p = raw == null ? "" : raw.replace('\\', '/');
        while (p.startsWith("./")) p = p.substring(2);
        if (p.equals(".")) return "";
        if (p.startsWith("/") || p.indexOf('\0') >= 0) throw new IllegalArgumentException("absolute or invalid path rejected");
        ArrayList<String> parts = new ArrayList<>();
        for (String part : p.split("/")) {
            if (part.isEmpty() || part.equals(".")) continue;
            if (part.equals("..")) throw new IllegalArgumentException("path traversal rejected");
            parts.add(part);
        }
        return String.join("/", parts);
    }

    private DocumentFile resolve(String raw, boolean createDirs, boolean wantFile, boolean createFile) throws Exception {
        String p = cleanPath(raw);
        if (p.isEmpty()) return root;
        String[] parts = p.split("/");
        DocumentFile cur = root;
        int last = parts.length - 1;
        for (int i=0;i<parts.length;i++) {
            boolean finalPart = i == last;
            DocumentFile next = cur.findFile(parts[i]);
            if (next == null && (!finalPart || !wantFile) && createDirs) next = cur.createDirectory(parts[i]);
            if (next == null && finalPart && wantFile && createFile) next = cur.createFile("text/plain", parts[i]);
            if (next == null) throw new FileNotFoundException(p);
            if (!finalPart && !next.isDirectory()) throw new IOException("not a directory: " + parts[i]);
            cur = next;
        }
        return cur;
    }

    private String readText(String path) throws Exception {
        DocumentFile file = resolve(path, false, true, false);
        if (!file.isFile()) throw new IOException("not a file");
        if (file.length() > MAX_TEXT) throw new IOException("file exceeds 2 MiB text limit");
        try (InputStream in = context.getContentResolver().openInputStream(file.getUri()); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            if (in == null) throw new IOException("cannot open file");
            byte[] buf = new byte[8192]; int n; while ((n = in.read(buf)) >= 0) { out.write(buf,0,n); if (out.size() > MAX_TEXT) throw new IOException("file exceeds 2 MiB text limit"); }
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private void writeText(String path, String text, boolean create) throws Exception {
        if (!writable) throw new SecurityException("workspace is read-only");
        DocumentFile file = resolve(path, true, true, create);
        try (OutputStream out = context.getContentResolver().openOutputStream(file.getUri(), "wt")) {
            if (out == null) throw new IOException("cannot open file for writing");
            out.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    JSONObject read(JSONObject args) throws Exception {
        String path = cleanPath(args.optString("path", ""));
        String text = readText(path); String[] lines = text.split("\\r?\\n", -1);
        int start = Math.max(1, args.optInt("start_line", 1));
        int end = args.has("end_line") ? Math.min(lines.length, args.optInt("end_line", lines.length)) : lines.length;
        StringBuilder b = new StringBuilder(); for (int i=start-1;i<end;i++) { if (i>start-1) b.append('\n'); b.append(lines[i]); }
        return new JSONObject().put("path", path).put("text", b.toString()).put("total_lines", lines.length).put("start_line", start).put("end_line", end);
    }

    JSONObject tree(JSONObject args) throws Exception {
        String base = cleanPath(args.optString("path", ""));
        int maxDepth = Math.max(1, Math.min(8, args.optInt("max_depth", args.optInt("depth", 3))));
        int maxEntries = Math.max(1, Math.min(1000, args.optInt("max_entries", 300)));
        JSONArray out = new JSONArray(); DocumentFile start = resolve(base, false, false, false);
        walkTree(start, base, 1, maxDepth, maxEntries, out);
        return new JSONObject().put("path", base.isEmpty()?".":base).put("entries", out).put("truncated", out.length() >= maxEntries);
    }

    private void walkTree(DocumentFile dir, String prefix, int depth, int maxDepth, int maxEntries, JSONArray out) {
        if (!dir.isDirectory()) return;
        for (DocumentFile child : dir.listFiles()) {
            if (out.length() >= maxEntries) return;
            String name = child.getName(); if (name == null || IGNORE.contains(name)) continue;
            String path = prefix.isEmpty()?name:prefix+"/"+name;
            out.put(path + (child.isDirectory()?"/":""));
            if (child.isDirectory() && depth < maxDepth) walkTree(child, path, depth+1, maxDepth, maxEntries, out);
        }
    }

    JSONObject search(JSONObject args, boolean forceRegex) throws Exception {
        String base = cleanPath(args.optString("path", "")); String query = forceRegex ? args.optString("pattern", "") : args.optString("query", "");
        if (query.isEmpty()) throw new IllegalArgumentException("search pattern required");
        int max = Math.max(1, Math.min(500,args.optInt("max_results",100))); boolean sensitive = args.optBoolean("case_sensitive",false);
        Pattern regex = (forceRegex || args.optBoolean("regex",false)) ? Pattern.compile(query, sensitive?0:Pattern.CASE_INSENSITIVE) : null;
        JSONArray hits = new JSONArray(); ArrayList<String> files = new ArrayList<>(); collectFiles(resolve(base,false,false,false),base,files,20000);
        String needle = sensitive?query:query.toLowerCase(Locale.ROOT);
        for (String path: files) { String text; try { text=readText(path); } catch(Exception ignored){ continue; } String[] lines=text.split("\\r?\\n",-1); for(int i=0;i<lines.length;i++){String line=lines[i]; boolean ok=regex!=null?regex.matcher(line).find():(sensitive?line:line.toLowerCase(Locale.ROOT)).contains(needle); if(ok){hits.put(new JSONObject().put("path",path).put("line",i+1).put("text",line.substring(0,Math.min(500,line.length())))); if(hits.length()>=max)return new JSONObject().put("results",hits).put("truncated",true);}}}
        return new JSONObject().put("results",hits).put("truncated",false);
    }

    private void collectFiles(DocumentFile dir,String prefix,List<String> out,int max){ if(out.size()>=max)return; if(dir.isFile()){out.add(prefix);return;} for(DocumentFile c:dir.listFiles()){if(out.size()>=max)return;String n=c.getName();if(n==null||IGNORE.contains(n))continue;String p=prefix.isEmpty()?n:prefix+"/"+n;if(c.isDirectory())collectFiles(c,p,out,max);else out.add(p);} }

    JSONObject edit(JSONObject args) throws Exception {
        String path=cleanPath(args.optString("path","")), op=args.optString("operation","write"); String old="";
        try{old=readText(path);}catch(Exception e){if(!op.equals("create")&&!op.equals("write"))throw e;}
        String next; if(op.equals("create")||op.equals("write"))next=args.optString("content",""); else if(op.equals("append"))next=old+args.optString("content",""); else if(op.equals("replace")){String needle=args.optString("old_text","");if(needle.isEmpty()||old.indexOf(needle)!=old.lastIndexOf(needle))throw new IllegalArgumentException("old_text must occur exactly once");next=old.replace(needle,args.optString("new_text",""));}else throw new IllegalArgumentException("unknown edit operation");
        writeText(path,next,true); return new JSONObject().put("path",path).put("operation",op).put("bytes",next.getBytes(StandardCharsets.UTF_8).length);
    }

    JSONObject codeEdit(JSONObject args) throws Exception {
        String path=cleanPath(args.optString("path","")), mode=args.optString("mode","replace"), old=readText(path), next=old;
        if(mode.equals("replace")){String needle=args.optString("old_text","");if(needle.isEmpty()||old.indexOf(needle)!=old.lastIndexOf(needle))throw new IllegalArgumentException("old_text must occur exactly once");next=old.replace(needle,args.optString("new_text",""));}
        else if(mode.equals("append"))next=old+args.optString("new_text","");
        else if(mode.equals("insert")){ArrayList<String> lines=new ArrayList<>(Arrays.asList(old.split("\\r?\\n",-1)));int line=Math.max(1,args.optInt("line",1));lines.add(Math.min(lines.size(),line-1),args.optString("new_text",""));next=String.join("\n",lines);}
        else if(mode.equals("delete")){ArrayList<String> lines=new ArrayList<>(Arrays.asList(old.split("\\r?\\n",-1)));int line=Math.max(1,args.optInt("line",1));if(line>lines.size())throw new IllegalArgumentException("line outside file");lines.remove(line-1);next=String.join("\n",lines);} else throw new IllegalArgumentException("unsupported code_edit mode");
        if(args.optBoolean("dry_run",false))return new JSONObject().put("ok",true).put("dry_run",true).put("path",path).put("mode",mode).put("changed",!next.equals(old)).put("preview",next.substring(0,Math.min(20000,next.length())));
        writeText(path,next,false);return new JSONObject().put("ok",true).put("path",path).put("mode",mode).put("bytes",next.getBytes(StandardCharsets.UTF_8).length);
    }

    JSONObject createDir(JSONObject args) throws Exception { if(!writable)throw new SecurityException("workspace is read-only");String p=cleanPath(args.optString("path",""));resolve(p,true,false,false);return new JSONObject().put("path",p).put("created",true); }
    JSONObject fileOps(JSONObject args) throws Exception { String action=args.optString("action","read").toLowerCase(Locale.ROOT),path=cleanPath(args.optString("path","")); if(action.equals("read"))return read(args); if(action.equals("size"))return new JSONObject().put("path",path).put("size",resolve(path,false,true,false).length()); if(action.equals("list"))return tree(new JSONObject().put("path",path).put("max_depth",1).put("max_entries",args.optInt("max_entries",300))); if(action.equals("find"))return search(new JSONObject().put("query",args.optString("pattern",args.optString("query",""))).put("path",path).put("max_results",args.optInt("max_results",100)),false); if(action.equals("write")||action.equals("append"))return edit(new JSONObject().put("path",path).put("operation",action).put("content",args.optString("content",""))); if(action.equals("delete")||action.equals("remove")){if(!writable)throw new SecurityException("workspace is read-only");if(path.isEmpty())throw new IllegalArgumentException("cannot delete workspace root");DocumentFile f=resolve(path,false,false,false);boolean ok=f.delete();return new JSONObject().put("ok",ok).put("path",path).put("deleted",ok);} throw new IllegalArgumentException("unsupported file_ops action: "+action); }
    JSONObject clear(JSONObject args) throws Exception { if(!writable)throw new SecurityException("workspace is read-only");if(!"DELETE_ALL".equals(args.optString("confirm","")))throw new IllegalArgumentException("workspace_clear requires confirm=DELETE_ALL");int removed=0;JSONArray failed=new JSONArray();for(DocumentFile c:root.listFiles()){if(c.delete())removed++;else failed.put(c.getName());}return new JSONObject().put("ok",failed.length()==0).put("removed_entries",removed).put("failed",failed).put("root_preserved",true); }
}
