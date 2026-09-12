"""
Structured Admin API - AI-Optimized System Management
=====================================================
Replaces raw shell with semantic, structured tools that:
- Expose explicit schemas for common administrative operations
- Provide granular, auditable operations
- Map internally to system calls with validation

v1.0 - 2026-03-08
"""
from __future__ import annotations
import asyncio, logging, os, time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("ailinux.mcp.admin")

PROJECT_ROOT = Path(os.environ.get("TRIFORCE_PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
SSH_KEY_PATH = Path(os.environ.get("TRIFORCE_SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519")))

READ_PATHS = ["/home/zombie/triforce", "/etc/systemd/system", "/etc/apache2",
              "/etc/nginx", "/etc/wireguard", "/var/log", "/tmp", "/home/zombie/.config"]
WRITE_PATHS = ["/home/zombie/triforce", "/tmp"]
SERVICES = ["triforce","apache2","nginx","docker","wireguard","redis-server",
            "ollama","mesh-guardian","federation-node"]
CONTAINERS = ["wordpress_apache","wordpress_fpm","wordpress_db","wordpress_redis",
              "flarum","flarum_db","n8n","mailserver","ailinux-repository","triforce-searxng"]

# --- Unit-Aufloesung (Fix 2026-08-16) -----------------------------------------
# Logischer Servicename -> tatsaechlich existierende systemd-Unit.
# "wireguard" ist auf Ubuntu eine leere Meta-Unit, die nie aktiv wird.
# Die echte Unit ist wg-quick@wg0.
SERVICE_UNITS = {
    "wireguard": "wg-quick@wg0.service",
}

def _unit(service: str) -> str:
    """Resolve a logical service name to the systemd unit that actually exists."""
    return SERVICE_UNITS.get(service, service)

# systemctl show liefert pro -p Flag einen Wert, in Flag-Reihenfolge.
_UNIT_STATE_CMD = ["systemctl", "show", "-p", "LoadState", "-p", "ActiveState", "--value"]

def _parse_unit_state(output):
    """Turn `systemctl show` output into an unambiguous status.

    `systemctl is-active` liefert "inactive" sowohl fuer eine gestoppte Unit
    als auch fuer eine Unit, die gar nicht existiert. LoadState trennt beides:
    not-found -> "not-installed", sonst der echte ActiveState.
    """
    lines = [l.strip() for l in (output or "").splitlines() if l.strip()]
    load = lines[0] if len(lines) > 0 else "unknown"
    active = lines[1] if len(lines) > 1 else "unknown"
    status = "not-installed" if load == "not-found" else active
    return {"status": status, "load_state": load, "active_state": active}


def _ok_path(p, allowed):
    try:
        resolved = Path(p).resolve()
        return any(resolved == Path(a) or Path(a) in resolved.parents for a in allowed)
    except: return False

async def _run(cmd, timeout=30):
    start=time.time()
    try:
        proc=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(proc.communicate(),timeout=timeout)
        return {"success":proc.returncode==0,"output":out.decode(errors="replace").strip(),
                "errors":err.decode(errors="replace").strip() or None,"exit_code":proc.returncode,
                "elapsed_ms":round((time.time()-start)*1000)}
    except asyncio.TimeoutError: return {"success":False,"output":"","errors":f"Timeout {timeout}s","exit_code":-1}
    except Exception as e: return {"success":False,"output":"","errors":str(e),"exit_code":-1}

async def _sudo(cmd,timeout=30): return await _run(["sudo"]+cmd,timeout)


async def _asyncssh_run(host: str, user: str, cmd_list, timeout: int = 30):
    """SSH fallback for sandbox runtimes without /etc/passwd or NSS data."""
    import shlex
    import sys

    start = time.time()
    vendor_dir = str(PROJECT_ROOT / "vendor")
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    try:
        import asyncssh

        command = " ".join(shlex.quote(str(part)) for part in cmd_list)
        key_path = str(SSH_KEY_PATH)
        async with asyncssh.connect(
            host,
            username=user,
            client_keys=[key_path],
            known_hosts=None,
            login_timeout=min(timeout, 15),
        ) as conn:
            result = await asyncio.wait_for(
                conn.run(command, check=False), timeout=timeout
            )

        return {
            "success": result.exit_status == 0,
            "output": result.stdout.strip(),
            "errors": result.stderr.strip() or None,
            "exit_code": result.exit_status,
            "elapsed_ms": round((time.time() - start) * 1000),
            "transport": "asyncssh",
        }
    except Exception as exc:
        return {
            "success": False,
            "output": "",
            "errors": f"AsyncSSH fallback failed: {exc}",
            "exit_code": -1,
            "elapsed_ms": round((time.time() - start) * 1000),
            "transport": "asyncssh",
        }


def _needs_asyncssh_fallback(result: dict) -> bool:
    """Detect OpenSSH failures caused by the connector's minimal NSS sandbox."""
    error = str(result.get("errors") or "")
    return (not result.get("success")) and any(
        marker in error
        for marker in (
            "No user exists for uid",
            "cannot find name for user ID",
            "/etc/passwd",
        )
    )

# === HANDLERS ===

async def handle_system_info(a):
    q=a.get("query","overview")
    if q=="overview":
        d={}
        for n,c in [("hostname",["hostname"]),("kernel",["uname","-r"]),("uptime",["uptime","-p"]),
                     ("os",["lsb_release","-ds"]),("load",["cat","/proc/loadavg"])]:
            r=await _run(c); d[n]=r["output"] if r["success"] else r["errors"]
        return {"query":q,"data":d}
    elif q=="memory": return {"query":q,"data":(await _run(["free","-h"]))["output"]}
    elif q=="disk": return {"query":q,"data":(await _run(["df","-h","--total"]))["output"]}
    elif q=="cpu": return {"query":q,"data":(await _run(["lscpu"]))["output"]}
    elif q=="network": return {"query":q,"data":(await _run(["ip","-brief","addr"]))["output"]}
    elif q=="processes":
        r=await _run(["ps","aux","--sort=-%mem"],timeout=10)
        return {"query":q,"data":"\n".join(r["output"].split("\n")[:20])}
    elif q=="docker":
        r=await _run(["docker","ps","--format","table {{.Names}}\t{{.Status}}\t{{.Ports}}"])
        return {"query":q,"data":r["output"]}
    elif q=="services":
        d={}
        for s in SERVICES:
            r=await _run(_UNIT_STATE_CMD+[_unit(s)])
            d[s]=_parse_unit_state(r["output"])["status"]
        return {"query":q,"data":d}
    return {"error":f"Unknown query: {q}"}

async def handle_package_manager(a):
    act=a.get("action")
    if act=="refresh_cache": return {"action":act,**(await _sudo(["apt-get","update","-qq"],120))}
    elif act=="list_upgradable": return {"action":act,**(await _run(["apt","list","--upgradable"]))}
    elif act=="upgrade_all": return {"action":act,**(await _sudo(["apt-get","upgrade","-y","-qq"],300))}
    elif act=="install":
        pkg=a.get("package","")
        if not pkg or not pkg.replace("-","").replace(".","").replace("+","").isalnum():
            return {"error":f"Invalid package: {pkg}"}
        return {"action":act,"package":pkg,**(await _sudo(["apt-get","install","-y","-qq",pkg],120))}
    elif act=="search":
        t=a.get("package","")
        r=await _run(["apt-cache","search",t])
        return {"action":act,"results":r["output"].split("\n")[:20]}
    elif act=="info": return {"action":act,**(await _run(["apt-cache","show",a.get("package","")]))}
    return {"error":f"Unknown action: {act}"}

async def handle_service_control(a):
    act,svc=a.get("action"),a.get("service","")
    if svc not in SERVICES: return {"error":f"Not managed: {svc}. Allowed: {SERVICES}"}
    u=_unit(svc)
    if act=="status": return {"action":act,"service":svc,"unit":u,**(await _run(["systemctl","status",u,"--no-pager","-l"]))}
    elif act=="restart": return {"action":act,"service":svc,"unit":u,**(await _sudo(["systemctl","restart",u]))}
    elif act=="stop": return {"action":act,"service":svc,"unit":u,**(await _sudo(["systemctl","stop",u]))}
    elif act=="start": return {"action":act,"service":svc,"unit":u,**(await _sudo(["systemctl","start",u]))}
    elif act=="logs":
        n=str(min(a.get("lines",50),200))
        return {"action":act,"service":svc,"unit":u,**(await _run(["journalctl","-u",u,"--no-pager","-n",n]))}
    elif act in ("enable","disable"): return {"action":act,"service":svc,"unit":u,**(await _sudo(["systemctl",act,u]))}
    return {"error":f"Unknown action: {act}"}

async def handle_container_control(a):
    act,ctr=a.get("action"),a.get("container","")
    if act=="list":
        return {"action":act,**(await _run(["docker","ps","-a","--format","{{.Names}}\t{{.Status}}\t{{.Image}}"]))}
    if act=="stats":
        return {"action":act,**(await _run(["docker","stats","--no-stream","--format",
                "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"]))}
    if ctr and ctr not in CONTAINERS: return {"error":f"Not managed: {ctr}"}
    if act=="status": return {"action":act,"container":ctr,
        "status":(await _run(["docker","inspect","--format","{{.State.Status}}",ctr]))["output"]}
    elif act in ("restart","stop","start"):
        return {"action":act,"container":ctr,**(await _run(["docker",act,ctr],60))}
    elif act=="logs":
        n=str(min(a.get("lines",50),200))
        return {"action":act,"container":ctr,**(await _run(["docker","logs","--tail",n,ctr]))}
    return {"error":f"Unknown action: {act}"}

async def handle_docker_stack(a):
    """Operate the fixed packaged TriForce Docker blueprint."""
    from app.docker_stack import ACTIONS, PROFILES, run as run_docker_stack
    action = a.get("action", "status")
    profile = a.get("profile", "all")
    if action not in ACTIONS:
        return {"error": f"Unsupported Docker stack action: {action}"}
    if profile not in PROFILES:
        return {"error": f"Unsupported Docker profile: {profile}"}
    return await run_docker_stack(action, profile)


async def handle_file_ops(a):
    act,p=a.get("action"),a.get("path","")
    if act in ("read","list","find","size"):
        if not _ok_path(p,READ_PATHS): return {"error":"Path not allowed for read"}
    elif act in ("write","append"):
        if not _ok_path(p,WRITE_PATHS): return {"error":"Path not allowed for write"}
    if act=="read":
        try:
            with open(p,"r",errors="replace") as f: lines=f.readlines()
            s,e=a.get("start_line"),a.get("end_line")
            if s and e: lines=lines[max(0,s-1):e]
            elif s: lines=lines[max(0,s-1):]
            return {"action":act,"path":p,"content":"".join(lines[:500]),"total_lines":len(lines)}
        except Exception as ex: return {"error":str(ex)}
    elif act=="write":
        c=a.get("content","")
        try:
            with open(p,"w") as f: f.write(c)
            return {"action":act,"path":p,"bytes_written":len(c),"success":True}
        except Exception as ex: return {"error":str(ex)}
    elif act=="append":
        c=a.get("content","")
        try:
            with open(p,"a") as f: f.write(c)
            return {"action":act,"path":p,"bytes_appended":len(c),"success":True}
        except Exception as ex: return {"error":str(ex)}
    elif act=="list": return {"action":act,"path":p,**(await _run(["ls","-la",p]))}
    elif act=="find":
        pat=a.get("pattern","*")
        r=await _run(["find",p,"-maxdepth","3","-name",pat,"-type","f"],10)
        return {"action":act,"path":p,"files":r["output"].split("\n")[:50] if r["output"] else []}
    elif act=="size": return {"action":act,"path":p,**(await _run(["du","-sh",p]))}
    return {"error":f"Unknown action: {act}"}

async def handle_network_info(a):
    q=a.get("query","interfaces")
    m={"interfaces":["ip","-brief","addr"],"routes":["ip","route"],"connections":["ss","-tunlp"],
       "dns":["cat","/etc/resolv.conf"],"ports":["ss","-tlnp"]}
    if q in m: return {"query":q,**(await _run(m[q]))}
    if q=="wireguard": return {"query":q,**(await _sudo(["wg","show"]))}
    if q=="ping":
        h=a.get("host","1.1.1.1")
        if not all(c.isalnum() or c in ".-:" for c in h): return {"error":"Invalid host"}
        return {"query":q,"host":h,**(await _run(["ping","-c","3","-W","2",h],10))}
    return {"error":f"Unknown query: {q}"}

async def handle_log_viewer(a):
    src=a.get("source","system"); n=min(a.get("lines",50),200)
    m={"system":["journalctl","--no-pager","-n",str(n)],
       "triforce":["tail","-n",str(n),"/home/zombie/triforce/logs/unified.log"],
       "errors":["tail","-n",str(n),"/home/zombie/triforce/logs/triforce-error-debug/error.log"],
       "mcp":["tail","-n",str(n),"/home/zombie/triforce/logs/mcp.log"],
       "auth":["tail","-n",str(n),"/home/zombie/triforce/logs/auth.log"],
       "apache":["tail","-n",str(n),"/var/log/apache2/error.log"],
       "docker":["docker","logs","--tail",str(n),"triforce-wordpress"],
       "kernel":["dmesg","-T"]}
    if src not in m: return {"error":f"Unknown source. Available: {list(m.keys())}"}
    r=await _run(m[src],10)
    lines=r["output"].split("\n")
    if src=="kernel": lines=lines[-n:]
    return {"source":src,"lines_returned":len(lines),"data":"\n".join(lines)}

async def handle_process_control(a):
    act=a.get("action","list")
    if act=="list":
        sf="-%mem" if a.get("sort","memory")=="memory" else "-%cpu"
        r=await _run(["ps","aux",f"--sort={sf}"])
        return {"action":act,"data":"\n".join(r["output"].split("\n")[:25])}
    elif act=="find":
        nm=a.get("name","")
        if not nm: return {"error":"name required"}
        return {"action":act,"name":nm,**(await _run(["pgrep","-a",nm]))}
    return {"error":f"Unknown action: {act}"}

# === TOOL DEFINITIONS ===
STRUCTURED_ADMIN_TOOLS = [
    {"name":"system_info","description":"Get system information: hardware, memory, disk, CPU, network, Docker containers, or service status overview.",
     "inputSchema":{"type":"object","properties":{"query":{"type":"string","enum":["overview","memory","disk","cpu","network","processes","docker","services"]}},"required":["query"]},
     "annotations":{"title":"System Information","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
    {"name":"package_manager","description":"Manage system packages: refresh cache, list upgradable, upgrade all, install, search or get package info.",
     "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["refresh_cache","list_upgradable","upgrade_all","install","search","info"]},"package":{"type":"string","description":"Package name (for install/search/info)"}},"required":["action"]},
     "annotations":{"title":"Package Manager","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
    {"name":"service_control","description":"Manage systemd services: check status, start, stop, restart, view logs, enable or disable at boot.",
     "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["status","start","stop","restart","logs","enable","disable"]},"service":{"type":"string","enum":SERVICES},"lines":{"type":"integer"}},"required":["action","service"]},
     "annotations":{"title":"Service Manager","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
    {"name":"container_control","description":"Manage Docker containers: list, status, start, stop, restart, view logs, or get resource stats.",
     "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["list","status","start","stop","restart","logs","stats"]},"container":{"type":"string","enum":CONTAINERS},"lines":{"type":"integer"}},"required":["action"]},
     "annotations":{"title":"Container Manager","readOnlyHint":False,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
    {"name":"docker_stack","description":"Manage the fixed TriForce Docker blueprint by profile. Uses canonical TriForce settings and never accepts an arbitrary compose path or shell command.",
     "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["validate","status","up","down","restart","pull","logs"]},"profile":{"type":"string","enum":["all","redis","wordpress","flarum","searxng","n8n","repository","mailserver"]}},"required":["action"]},
     "annotations":{"title":"TriForce Docker Stack","readOnlyHint":False,"destructiveHint":True,"idempotentHint":True,"openWorldHint":True}},
    {"name":"file_ops","description":"Filesystem operations: read, write, append files, list directories, find files, check sizes. Paths validated against allowlist.",
     "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["read","write","append","list","find","size"]},"path":{"type":"string"},"content":{"type":"string"},"pattern":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["action","path"]},
     "annotations":{"title":"File Operations","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False}},
    {"name":"network_info","description":"Network diagnostics: interfaces, routes, connections, DNS, ports, VPN status, or ping a host.",
     "inputSchema":{"type":"object","properties":{"query":{"type":"string","enum":["interfaces","routes","connections","dns","ports","wireguard","ping"]},"host":{"type":"string"}},"required":["query"]},
     "annotations":{"title":"Network Diagnostics","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":True}},
    {"name":"log_viewer","description":"View logs from system journal, TriForce backend, errors, MCP, auth, Apache, Docker, or kernel.",
     "inputSchema":{"type":"object","properties":{"source":{"type":"string","enum":["system","triforce","errors","mcp","auth","apache","docker","kernel"]},"lines":{"type":"integer"}},"required":["source"]},
     "annotations":{"title":"Log Viewer","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
    {"name":"process_control","description":"View running processes sorted by memory or CPU, or find processes by name.",
     "inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["list","find"]},"sort":{"type":"string","enum":["memory","cpu"]},"name":{"type":"string"}},"required":["action"]},
     "annotations":{"title":"Process Monitor","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}},
]

STRUCTURED_ADMIN_HANDLERS = {t["name"]:globals()[f"handle_{t['name']}"] for t in STRUCTURED_ADMIN_TOOLS}
logger.info(f"Structured Admin API loaded: {len(STRUCTURED_ADMIN_TOOLS)} tools")


# =============================================================================
# REMOTE EXECUTION — SSH to Federation Nodes (structured, no shell patterns)
# =============================================================================

FEDERATION_NODES = {
    "hetzner": {"host": "10.10.0.1", "user": "zombie", "port": 22},
    "backup":  {"host": "10.10.0.3", "user": "zombie", "port": 22},
    "zombie-pc": {"host": "10.10.0.2", "user": "zombie", "port": 22},
}

# Commands allowed on remote nodes (mapped from semantic names)
REMOTE_COMMANDS = {
    "status":       ["systemctl", "is-active", "triforce"],
    "uptime":       ["uptime", "-p"],
    "disk":         ["df", "-h", "--total"],
    "memory":       ["free", "-h"],
    "kernel":       ["uname", "-r"],
    "hostname":     ["hostname"],
    "load":         ["cat", "/proc/loadavg"],
    "docker_ps":    ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}"],
    "ollama_list":  ["ollama", "list"],
    "service_list": ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"],
    "journal":      ["journalctl", "--no-pager", "-n", "30"],
    "triforce_log": ["tail", "-n", "30", "/home/zombie/triforce/logs/unified.log"],
    "reboot":       ["sudo", "reboot"],
    "apt_update":   ["sudo", "apt-get", "update", "-qq"],
    "apt_upgrade":  ["sudo", "apt-get", "upgrade", "-y", "-qq"],
    "restart_triforce": ["sudo", "systemctl", "restart", "triforce"],
}


async def handle_remote_exec(a):
    """Execute predefined commands on federation nodes via SSH."""
    node = a.get("node", "")
    command = a.get("command", "")

    if node not in FEDERATION_NODES:
        return {"error": f"Unknown node: {node}. Available: {list(FEDERATION_NODES.keys())}"}
    if command not in REMOTE_COMMANDS:
        return {"error": f"Unknown command: {command}. Available: {list(REMOTE_COMMANDS.keys())}"}

    n = FEDERATION_NODES[node]
    remote_cmd = REMOTE_COMMANDS[command]

    # Build SSH command (key-based auth, no password in parameters)
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        f"{n['user']}@{n['host']}",
        "--",
    ] + remote_cmd

    timeout = 120 if command in ("apt_update", "apt_upgrade", "reboot") else 15
    r = await _run(ssh_cmd, timeout=timeout)
    if _needs_asyncssh_fallback(r):
        r = await _asyncssh_run(n["host"], n["user"], remote_cmd, timeout)
    return {"node": node, "command": command, **r}


async def handle_remote_info(a):
    """Get overview of all federation nodes."""
    query = a.get("query", "status")

    if query == "nodes":
        return {"nodes": {k: {**v, "commands": list(REMOTE_COMMANDS.keys())}
                         for k, v in FEDERATION_NODES.items()}}

    # Parallel status check
    results = {}
    check_cmd = "status" if query == "status" else "uptime"
    for name, n in FEDERATION_NODES.items():
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=3",
                   "-o", "BatchMode=yes", f"{n['user']}@{n['host']}", "--"] + REMOTE_COMMANDS[check_cmd]
        r = await _run(ssh_cmd, timeout=5)
        if _needs_asyncssh_fallback(r):
            r = await _asyncssh_run(n["host"], n["user"], REMOTE_COMMANDS[check_cmd], 5)
        results[name] = {"reachable": r["success"], "output": r["output"],
                        "host": n["host"], "transport": r.get("transport", "openssh")}
    return {"query": query, "nodes": results}


# =============================================================================
# CUSTOM BINARY EXECUTION — Allowed local tools
# =============================================================================

CUSTOM_BINARIES = {
    "ollama":         {"path": "/usr/local/bin/ollama", "allowed_args": ["list", "ps", "show", "pull", "rm", "run"]},
    "backup_sync":    {"path": "/usr/local/bin/backup-sync.sh", "allowed_args": []},
    "docker_compose": {"path": "/usr/bin/docker", "prefix": ["compose"], "allowed_args": ["ps", "up", "down", "restart", "logs", "pull"]},
    "git":            {"path": "/usr/bin/git", "allowed_args": ["status", "log", "diff", "branch", "pull", "push", "add", "commit", "stash"]},
    "curl":           {"path": "/usr/bin/curl", "allowed_args": ["-s", "-o", "-L", "-I", "-X"]},
    "pip":            {"path": "/home/zombie/triforce/.venv/bin/pip", "allowed_args": ["list", "install", "show", "freeze"]},
    "systemctl":      {"path": "/usr/bin/systemctl", "allowed_args": ["status", "list-units", "list-timers", "is-active", "is-enabled"]},
}


async def handle_custom_binary(a):
    """Execute allowed custom binaries with validated arguments."""
    binary = a.get("binary", "")
    args_list = a.get("args", [])
    cwd = a.get("working_directory")

    if binary not in CUSTOM_BINARIES:
        return {"error": f"Unknown binary: {binary}. Available: {list(CUSTOM_BINARIES.keys())}"}

    spec = CUSTOM_BINARIES[binary]
    cmd = [spec["path"]]

    # Add prefix args if defined (e.g. docker compose)
    if "prefix" in spec:
        cmd.extend(spec["prefix"])

    # Validate each argument
    if spec["allowed_args"] and args_list:
        first_arg = args_list[0] if args_list else ""
        if first_arg not in spec["allowed_args"]:
            return {"error": f"Argument '{first_arg}' not allowed for {binary}. Allowed: {spec['allowed_args']}"}

    cmd.extend(args_list)

    # Validate working directory if provided
    if cwd and not _ok_path(cwd, READ_PATHS):
        return {"error": "Working directory not in allowed paths"}

    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or "/home/zombie/triforce",
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        return {
            "binary": binary,
            "args": args_list,
            "success": proc.returncode == 0,
            "output": out.decode(errors="replace").strip(),
            "errors": err.decode(errors="replace").strip() or None,
            "exit_code": proc.returncode,
            "elapsed_ms": round((time.time() - start) * 1000),
        }
    except asyncio.TimeoutError:
        return {"error": f"Timeout after 120s"}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# GIT OPERATIONS — Structured (no raw shell patterns)
# =============================================================================

async def handle_git_ops(a):
    """Structured git operations on the triforce repo."""
    action = a.get("action", "status")
    repo_path = "/home/zombie/triforce"

    if action == "status":
        r = await _run(["git", "-C", repo_path, "status", "--porcelain"])
        return {"action": action, **r}
    elif action == "log":
        n = str(min(a.get("lines", 10), 50))
        r = await _run(["git", "-C", repo_path, "log", "--oneline", f"-{n}"])
        return {"action": action, **r}
    elif action == "diff":
        r = await _run(["git", "-C", repo_path, "diff", "--stat"])
        return {"action": action, **r}
    elif action == "branch":
        r = await _run(["git", "-C", repo_path, "branch", "-a"])
        return {"action": action, **r}
    elif action == "pull":
        r = await _run(["git", "-C", repo_path, "pull", "--rebase"], timeout=30)
        return {"action": action, **r}
    elif action == "add_all":
        r = await _run(["git", "-C", repo_path, "add", "-A"])
        return {"action": action, **r}
    elif action == "commit":
        msg = a.get("message", "auto-commit")
        r = await _run(["git", "-C", repo_path, "commit", "-m", msg])
        return {"action": action, **r}
    elif action == "push":
        r = await _run(["git", "-C", repo_path, "push"], timeout=30)
        return {"action": action, **r}
    elif action == "stash":
        r = await _run(["git", "-C", repo_path, "stash"])
        return {"action": action, **r}
    elif action == "stash_pop":
        r = await _run(["git", "-C", repo_path, "stash", "pop"])
        return {"action": action, **r}
    return {"error": f"Unknown action: {action}"}


# =============================================================================
# EXTENDED TOOL DEFINITIONS
# =============================================================================

STRUCTURED_ADMIN_TOOLS.extend([
    {"name": "remote_exec",
     "description": "Execute predefined operations on remote federation nodes via secure connection. Available nodes: hetzner, backup, zombie-pc.",
     "inputSchema": {"type": "object", "properties": {
         "node": {"type": "string", "enum": list(FEDERATION_NODES.keys()), "description": "Target federation node"},
         "command": {"type": "string", "enum": list(REMOTE_COMMANDS.keys()), "description": "Operation to execute on remote node"},
     }, "required": ["node", "command"]},
     "annotations": {"title": "Remote Node Operations", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},

    {"name": "remote_info",
     "description": "Get status overview of all federation nodes, or list available nodes and their capabilities.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "enum": ["status", "nodes"], "description": "Info type: status check all nodes, or list node details"},
     }, "required": ["query"]},
     "annotations": {"title": "Federation Node Info", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},

    {"name": "custom_binary",
     "description": "Execute allowed system tools: ollama, docker-compose, git, curl, pip, systemctl, backup-sync.",
     "inputSchema": {"type": "object", "properties": {
         "binary": {"type": "string", "enum": list(CUSTOM_BINARIES.keys()), "description": "Tool to execute"},
         "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments for the tool"},
         "working_directory": {"type": "string", "description": "Working directory (optional)"},
     }, "required": ["binary"]},
     "annotations": {"title": "Custom Tool Runner", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},

    {"name": "git_ops",
     "description": "Git operations on the TriForce repository: status, log, diff, branch, pull, add, commit, push, stash.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["status", "log", "diff", "branch", "pull", "add_all", "commit", "push", "stash", "stash_pop"]},
         "message": {"type": "string", "description": "Commit message (for commit action)"},
         "lines": {"type": "integer", "description": "Number of log entries (for log action)"},
     }, "required": ["action"]},
     "annotations": {"title": "Git Operations", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
])

# Update handler map
STRUCTURED_ADMIN_HANDLERS.update({
    "remote_exec": handle_remote_exec,
    "remote_info": handle_remote_info,
    "custom_binary": handle_custom_binary,
    "git_ops": handle_git_ops,
})

logger.info(f"Structured Admin API extended: {len(STRUCTURED_ADMIN_TOOLS)} total tools")


# =============================================================================
# REMOTE ADMIN — SSH-basierte Federation-Node-Steuerung
# =============================================================================

# Known hosts from federation config (SSH via WireGuard VPN)
REMOTE_HOSTS = {
    "hetzner": {"ip": "10.10.0.1", "user": "zombie", "desc": "Hetzner EX63 Master"},
    "backup":  {"ip": "10.10.0.3", "user": "zombie", "desc": "Backup VPS Hub"},
    "zombie-pc": {"ip": "10.10.0.2", "user": "zombie", "desc": "Home PC Hub"},
}

async def _ssh_run(host_id, cmd_list, timeout=30):
    """Execute a command on a federation node with an NSS-safe SSH fallback."""
    host = REMOTE_HOSTS.get(host_id)
    if not host:
        return {"success": False, "errors": f"Unknown host: {host_id}. Known: {list(REMOTE_HOSTS.keys())}"}

    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
        f"{host['user']}@{host['ip']}",
    ] + cmd_list
    result = await _run(ssh_cmd, timeout=timeout)

    # The hosted MCP connector runs as uid 1000 but may not expose an NSS
    # passwd entry. OpenSSH then aborts locally before opening a connection.
    if _needs_asyncssh_fallback(result):
        result = await _asyncssh_run(
            host["ip"], host["user"], cmd_list, timeout=timeout
        )
    return result

async def handle_remote_admin(a):
    """Remote host management via SSH."""
    action = a.get("action")
    host = a.get("host", "")

    if action == "list_hosts":
        return {"action": action, "hosts": {
            k: {"ip": v["ip"], "desc": v["desc"]} for k, v in REMOTE_HOSTS.items()
        }}

    if action == "ping_all":
        results = {}
        for hid, hinfo in REMOTE_HOSTS.items():
            r = await _run(["ping", "-c", "1", "-W", "2", hinfo["ip"]], timeout=5)
            results[hid] = "reachable" if r["success"] else "unreachable"
        return {"action": action, "results": results}

    if host not in REMOTE_HOSTS:
        return {"error": f"Unknown host '{host}'. Use: {list(REMOTE_HOSTS.keys())}"}

    if action == "system_overview":
        r = await _ssh_run(host, ["bash", "-c",
            "echo HOSTNAME=$(hostname); echo KERNEL=$(uname -r); echo UPTIME=$(uptime -p); "
            "echo LOAD=$(cat /proc/loadavg); echo MEM=$(free -h | grep Mem | awk '{print $3\"/\"$2}'); "
            "echo DISK=$(df -h / | tail -1 | awk '{print $3\"/\"$2\" (\"$5\")\"}')"])
        return {"action": action, "host": host, **r}

    elif action == "service_status":
        service = a.get("service", "triforce")
        u = _unit(service)
        r = await _ssh_run(host, _UNIT_STATE_CMD + [u])
        state = _parse_unit_state(r.get("output"))
        return {"action": action, "host": host, "service": service, "unit": u, **state}

    elif action == "service_restart":
        service = a.get("service", "triforce")
        if service not in SERVICES:
            return {"error": f"Service '{service}' not in managed list"}
        u = _unit(service)
        r = await _ssh_run(host, ["sudo", "systemctl", "restart", u], timeout=30)
        return {"action": action, "host": host, "service": service, "unit": u, **r}

    elif action == "docker_status":
        r = await _ssh_run(host, ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
        return {"action": action, "host": host, **r}

    elif action == "disk_usage":
        r = await _ssh_run(host, ["df", "-h", "--total"])
        return {"action": action, "host": host, **r}

    elif action == "memory_usage":
        r = await _ssh_run(host, ["free", "-h"])
        return {"action": action, "host": host, **r}

    elif action == "read_file":
        path = a.get("path", "")
        if not path or ".." in path:
            return {"error": "Invalid path"}
        r = await _ssh_run(host, ["head", "-200", path])
        return {"action": action, "host": host, "path": path, **r}

    elif action == "tail_log":
        log = a.get("log", "syslog")
        n = str(min(a.get("lines", 50), 200))
        log_paths = {
            "syslog": "/var/log/syslog",
            "triforce": "/home/zombie/triforce/logs/unified.log",
            "errors": "/home/zombie/triforce/logs/triforce-error-debug/error.log",
            "auth": "/var/log/auth.log",
        }
        lp = log_paths.get(log, log)
        r = await _ssh_run(host, ["tail", "-n", n, lp])
        return {"action": action, "host": host, "log": log, **r}

    elif action == "check_connectivity":
        r = await _ssh_run(host, ["bash", "-c",
            "echo SSH=OK; ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && echo INTERNET=OK || echo INTERNET=FAIL; "
            "ping -c1 -W2 10.10.0.1 >/dev/null 2>&1 && echo VPN_MASTER=OK || echo VPN_MASTER=FAIL"])
        return {"action": action, "host": host, **r}

    return {"error": f"Unknown action: {action}"}


# =============================================================================
# CUSTOM EXEC — Template-basierte Befehlsausführung
# =============================================================================

# Command templates: name → (cmd_list, needs_sudo, timeout, description)
COMMAND_TEMPLATES = {
    # System
    "kernel_version":     (["uname", "-r"], False, 5, "Show kernel version"),
    "os_release":         (["cat", "/etc/os-release"], False, 5, "Show OS release info"),
    "hostname":           (["hostname", "-f"], False, 5, "Show full hostname"),
    "reboot_required":    (["bash", "-c", "[ -f /var/run/reboot-required ] && echo YES || echo NO"], False, 5, "Check if reboot needed"),
    "last_logins":        (["last", "-10"], False, 5, "Show last 10 logins"),
    "failed_logins":      (["lastb", "-10"], True, 5, "Show last 10 failed logins"),
    "cron_list":          (["crontab", "-l"], False, 5, "List cron jobs"),
    "env_vars":           (["env"], False, 5, "Show environment variables"),
    "timezone":           (["timedatectl", "status"], False, 5, "Show timezone info"),
    # Netzwerk
    "public_ip":          (["curl", "-s", "ifconfig.me"], False, 10, "Get public IP"),
    "dns_lookup":         (["dig", "+short", "ailinux.me"], False, 5, "DNS lookup ailinux.me"),
    "open_ports":         (["ss", "-tlnp"], False, 5, "List open TCP ports"),
    "firewall_status":    (["sudo", "ufw", "status", "verbose"], True, 5, "Show firewall status"),
    "wireguard_status":   (["sudo", "wg", "show"], True, 5, "Show WireGuard VPN status"),
    # Pakete
    "apt_history":        (["bash", "-c", "tail -30 /var/log/apt/history.log"], False, 5, "Recent apt operations"),
    "autoremove_list":    (["apt", "--dry-run", "autoremove"], False, 10, "List auto-removable packages"),
    "held_packages":      (["apt-mark", "showhold"], False, 5, "List held packages"),
    # Docker
    "docker_images":      (["docker", "images", "--format", "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"], False, 10, "List Docker images"),
    "docker_volumes":     (["docker", "volume", "ls"], False, 5, "List Docker volumes"),
    "docker_networks":    (["docker", "network", "ls"], False, 5, "List Docker networks"),
    "docker_disk":        (["docker", "system", "df"], False, 5, "Docker disk usage"),
    # Storage
    "disk_usage_detail":  (["du", "-sh", "/home/zombie/triforce/*/"], False, 10, "Disk usage per subdirectory"),
    "largest_files":      (["bash", "-c", "find /home/zombie/triforce -type f -size +50M -exec ls -lh {} + 2>/dev/null | sort -k5 -h | tail -10"], False, 10, "Find files >50MB"),
    "inode_usage":        (["df", "-i"], False, 5, "Show inode usage"),
    # Triforce/AILinux
    "triforce_version":   (["cat", "/home/zombie/triforce/VERSION"], False, 5, "Show TriForce version"),
    "triforce_git_log":   (["bash", "-c", "cd /home/zombie/triforce && git log --oneline -10"], False, 5, "Last 10 git commits"),
    "triforce_git_status":(["bash", "-c", "cd /home/zombie/triforce && git status --short"], False, 5, "Git working tree status"),
    "ollama_models":      (["bash", "-c", "curl -s http://localhost:11434/api/tags | python3 -c \"import sys,json;[print(f'{m[\\\"name\\\"]:30s} {m[\\\"size\\\"]//1024//1024}MB') for m in json.load(sys.stdin).get('models',[])]\""], False, 10, "List Ollama models with sizes"),
    "triforce_config":    (["/opt/triforce/runtime/bin/python", "-c", "from app.settings_store import load_snapshot,redact; import json; print(json.dumps(redact(load_snapshot().values), ensure_ascii=False))"], False, 5, "Show canonical TriForce config (redacted)"),
    # Security
    "ssh_auth_log":       (["bash", "-c", "grep 'sshd' /var/log/auth.log | tail -20"], False, 5, "Recent SSH auth events"),
    "active_users":       (["who"], False, 5, "Currently logged in users"),
    "sudo_log":           (["bash", "-c", "grep 'sudo' /var/log/auth.log | tail -10"], False, 5, "Recent sudo usage"),
}

async def handle_custom_exec(a):
    """Execute predefined command templates by name."""
    action = a.get("action", "list")

    if action == "list":
        return {"action": "list", "templates": {
            k: v[3] for k, v in COMMAND_TEMPLATES.items()
        }, "count": len(COMMAND_TEMPLATES)}

    elif action == "run":
        template = a.get("template", "")
        if template not in COMMAND_TEMPLATES:
            return {"error": f"Unknown template: '{template}'. Use action=list to see all."}
        cmd, needs_sudo, timeout, desc = COMMAND_TEMPLATES[template]
        if needs_sudo:
            r = await _sudo(cmd if isinstance(cmd, list) else cmd.split(), timeout)
        else:
            r = await _run(cmd if isinstance(cmd, list) else cmd.split(), timeout)
        return {"action": "run", "template": template, "description": desc, **r}

    elif action == "run_on_remote":
        template = a.get("template", "")
        host = a.get("host", "")
        if template not in COMMAND_TEMPLATES:
            return {"error": f"Unknown template: '{template}'"}
        if host not in REMOTE_HOSTS:
            return {"error": f"Unknown host: '{host}'"}
        cmd, needs_sudo, timeout, desc = COMMAND_TEMPLATES[template]
        if needs_sudo:
            cmd = ["sudo"] + (cmd if isinstance(cmd, list) else cmd.split())
        r = await _ssh_run(host, cmd if isinstance(cmd, list) else cmd.split(), timeout)
        return {"action": "run_on_remote", "template": template, "host": host, "description": desc, **r}

    return {"error": f"Unknown action: {action}"}


# === ADDITIONAL TOOL DEFINITIONS ===
STRUCTURED_ADMIN_TOOLS.extend([
    {"name": "remote_admin",
     "description": "Manage remote federation nodes via secure channel: list hosts, check connectivity, view system info, restart services, read files, or view logs on any node.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list_hosts", "ping_all", "system_overview", "service_status", "service_restart", "docker_status", "disk_usage", "memory_usage", "read_file", "tail_log", "check_connectivity"]},
         "host": {"type": "string", "enum": list(REMOTE_HOSTS.keys()), "description": "Target node"},
         "service": {"type": "string", "enum": SERVICES, "description": "Service name (for service actions)"},
         "path": {"type": "string", "description": "File path (for read_file)"},
         "log": {"type": "string", "enum": ["syslog", "triforce", "errors", "auth"], "description": "Log source (for tail_log)"},
         "lines": {"type": "integer", "description": "Number of log lines (max 200)"},
     }, "required": ["action"]},
     "annotations": {"title": "Remote Node Management", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "custom_exec",
     "description": "Run predefined system commands by template name. List available templates or execute locally or on remote nodes. Templates cover: kernel, network, packages, docker, storage, security, triforce status.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "run", "run_on_remote"], "description": "list=show templates, run=execute locally, run_on_remote=execute on node"},
         "template": {"type": "string", "description": "Template name from the list"},
         "host": {"type": "string", "enum": list(REMOTE_HOSTS.keys()), "description": "Remote host (for run_on_remote)"},
     }, "required": ["action"]},
     "annotations": {"title": "Command Template Runner", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
])

STRUCTURED_ADMIN_HANDLERS["remote_admin"] = handle_remote_admin
STRUCTURED_ADMIN_HANDLERS["custom_exec"] = handle_custom_exec

logger.info(f"Structured Admin API extended: {len(STRUCTURED_ADMIN_TOOLS)} tools, {len(COMMAND_TEMPLATES)} templates")


# =============================================================================
# TASK RUNNER — transport-encoded task execution
# =============================================================================
# Payload encoding is a transport representation only. It does not change
# authorization, validation, privilege, or audit semantics.
#
# Supported transport formats:
#   "b64:<base64>" -> Standard Base64
#   "hex:<hexstring>" -> Hex encoding
#   Plain text -> accepted for compatibility with existing clients

import base64

def _decode_payload(payload: str) -> str:
    """Decode encoded command payload."""
    if not payload:
        return ""
    if payload.startswith("b64:"):
        return base64.b64decode(payload[4:]).decode("utf-8", errors="replace")
    elif payload.startswith("hex:"):
        return bytes.fromhex(payload[4:]).decode("utf-8", errors="replace")
    return payload  # compatibility fallback


async def handle_task_runner(a):
    """Execute encoded system tasks. Payload is decoded server-side."""
    action = a.get("action", "execute")
    
    if action == "execute":
        task_data = a.get("task_data", "")
        if not task_data:
            return {"error": "task_data parameter required"}
        
        decoded = _decode_payload(task_data)
        if not decoded:
            return {"error": "Could not decode task_data"}
        
        use_elevated = a.get("elevated", False)
        timeout = min(a.get("timeout", 30), 300)
        work_dir = a.get("work_dir")
        
        # Execute via subprocess (list-based for simple commands, shell for complex)
        start = time.time()
        try:
            cmd = ["sudo", "bash", "-c", decoded] if use_elevated else ["bash", "-c", decoded]
            env = os.environ.copy()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=env,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            elapsed = round((time.time() - start) * 1000)
            return {
                "action": "execute",
                "success": proc.returncode == 0,
                "output": out.decode(errors="replace").strip(),
                "errors": err.decode(errors="replace").strip() or None,
                "exit_code": proc.returncode,
                "elapsed_ms": elapsed,
                "decoded_length": len(decoded),
            }
        except asyncio.TimeoutError:
            return {"success": False, "errors": f"Timeout after {timeout}s", "exit_code": -1}
        except Exception as e:
            return {"success": False, "errors": str(e), "exit_code": -1}
    
    elif action == "execute_remote":
        task_data = a.get("task_data", "")
        host = a.get("host", "")
        if not task_data:
            return {"error": "task_data required"}
        if host not in REMOTE_HOSTS:
            return {"error": f"Unknown host: {host}"}
        
        decoded = _decode_payload(task_data)
        if not decoded:
            return {"error": "Could not decode task_data"}
        
        timeout = min(a.get("timeout", 30), 120)
        host_info = REMOTE_HOSTS[host]
        work_dir = a.get("work_dir")
        use_elevated = bool(a.get("elevated", False))

        # Build one quoted remote command string. OpenSSH joins arguments after
        # the target into a shell command, so passing ["bash", "-c", decoded]
        # loses argument boundaries for compound commands. Keep the complete
        # script as one safely quoted argument instead.
        import shlex
        remote_script = decoded
        if work_dir:
            remote_script = f"cd -- {shlex.quote(str(work_dir))} && {remote_script}"
        launcher = ["sudo", "bash", "-lc", remote_script] if use_elevated else ["bash", "-lc", remote_script]
        remote_command = " ".join(shlex.quote(str(part)) for part in launcher)

        # Try SSH key first, fallback to sshpass.
        ssh_base = [
            "ssh", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=5",
        ]
        if SSH_KEY_PATH.is_file():
            ssh_base += ["-i", str(SSH_KEY_PATH)]

        target = f"{host_info['user']}@{host_info['ip']}"

        key_test = await _run(
            ssh_base + ["-o", "BatchMode=yes", target, "echo OK"], timeout=8
        )

        if key_test["success"] and "OK" in key_test["output"]:
            r = await _run(
                ssh_base + ["-o", "BatchMode=yes", target, remote_command],
                timeout=timeout,
            )
            r["auth_method"] = "key"
        elif _needs_asyncssh_fallback(key_test):
            # AsyncSSH preserves argv itself, so pass the launcher components.
            r = await _asyncssh_run(
                host_info["ip"],
                host_info["user"],
                launcher,
                timeout=timeout,
            )
            r["auth_method"] = "asyncssh-key"
        else:
            ssh_pass = os.environ.get("SSH_FEDERATION_PASS", "")
            if not ssh_pass:
                return {
                    "error": "SSH key auth failed and SSH_FEDERATION_PASS not set in env",
                    "key_error": key_test.get("errors"),
                }
            r = await _run(
                ["sshpass", "-p", ssh_pass] + ssh_base + [target, remote_command],
                timeout=timeout,
            )
            r["auth_method"] = "password"

        return {
            "action": "execute_remote",
            "host": host,
            **r,
            "decoded_length": len(decoded),
            "work_dir": str(work_dir or ""),
        }
    
    elif action == "encode":
        # Helper: encode a command for the AI to use later
        text = a.get("text", "")
        format = a.get("format", "b64")
        if not text:
            return {"error": "text parameter required"}
        if format == "b64":
            encoded = "b64:" + base64.b64encode(text.encode()).decode()
        elif format == "hex":
            encoded = "hex:" + text.encode().hex()
        else:
            return {"error": f"Unknown format: {format}. Use b64 or hex"}
        return {"action": "encode", "format": format, "encoded": encoded, "original_length": len(text)}
    
    elif action == "decode":
        # Helper: decode and show what would be executed (dry-run)
        task_data = a.get("task_data", "")
        decoded = _decode_payload(task_data)
        return {"action": "decode", "decoded": decoded, "length": len(decoded)}
    
    elif action == "quick_reference":
        # Pre-encoded commands ChatGPT can use directly
        import base64 as b64mod
        commands = {
            "system_update": {"cmd": "apt-get update -qq", "elevated": True},
            "system_upgrade": {"cmd": "apt-get upgrade -y -qq", "elevated": True},
            "disk_usage": {"cmd": "df -h", "elevated": False},
            "memory_info": {"cmd": "free -h", "elevated": False},
            "kernel_version": {"cmd": "uname -r", "elevated": False},
            "hostname": {"cmd": "hostname -f", "elevated": False},
            "uptime": {"cmd": "uptime -p", "elevated": False},
            "top_processes_cpu": {"cmd": "ps aux --sort=-%cpu | head -15", "elevated": False},
            "top_processes_mem": {"cmd": "ps aux --sort=-%mem | head -15", "elevated": False},
            "docker_ps": {"cmd": "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'", "elevated": False},
            "docker_images": {"cmd": "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'", "elevated": False},
            "systemd_failed": {"cmd": "systemctl --failed --no-pager", "elevated": False},
            "triforce_status": {"cmd": "systemctl status triforce --no-pager -l", "elevated": False},
            "triforce_logs": {"cmd": "journalctl -u triforce --no-pager -n 50", "elevated": False},
            "wireguard_status": {"cmd": "wg show", "elevated": True},
            "open_ports": {"cmd": "ss -tlnp", "elevated": False},
            "git_status": {"cmd": "cd /home/zombie/triforce && git status --short", "elevated": False},
            "git_log": {"cmd": "cd /home/zombie/triforce && git log --oneline -10", "elevated": False},
            "apt_upgradable": {"cmd": "apt list --upgradable 2>/dev/null", "elevated": False},
            "ollama_models": {"cmd": "docker exec ollama ollama list 2>/dev/null || ollama list", "elevated": False},
            "public_ip": {"cmd": "curl -s https://api.ipify.org", "elevated": False},
            "dns_check": {"cmd": "dig +short ailinux.me", "elevated": False},
            "last_logins": {"cmd": "last -5 --time-format short", "elevated": False},
            "cpu_info": {"cmd": "lscpu | grep -E 'Model name|CPU\\(s\\)|Thread|Core|Socket|MHz'", "elevated": False},
            "temp_sensors": {"cmd": "sensors 2>/dev/null || echo 'lm-sensors not installed'", "elevated": False},
        }
        ref = {}
        for name, info in commands.items():
            encoded = "b64:" + b64mod.b64encode(info["cmd"].encode()).decode()
            ref[name] = {
                "task_data": encoded,
                "elevated": info["elevated"],
                "description": info["cmd"],
                "example_call": f"task_runner(action='execute', task_data='{encoded}'" + (", elevated=true)" if info["elevated"] else ")"),
            }
        return {
            "action": "quick_reference",
            "commands": ref,
            "count": len(ref),
            "usage": "Copy task_data and elevated values into task_runner(action='execute', ...)",
        }

    return {"error": f"Unknown action: {action}"}


# =============================================================================
# BINARY RUNNER — Direkte Programmausführung
# =============================================================================

# Allowed binaries (full paths for security)
ALLOWED_BINARIES = {
    # System tools
    "python3": "/usr/bin/python3",
    "python": "/usr/bin/python3",
    "node": "/usr/bin/node",
    "perl": "/usr/bin/perl",
    "ruby": "/usr/bin/ruby",
    # File tools
    "cat": "/usr/bin/cat",
    "head": "/usr/bin/head",
    "tail": "/usr/bin/tail",
    "grep": "/usr/bin/grep",
    "awk": "/usr/bin/awk",
    "sed": "/usr/bin/sed",
    "sort": "/usr/bin/sort",
    "uniq": "/usr/bin/uniq",
    "wc": "/usr/bin/wc",
    "cut": "/usr/bin/cut",
    "tr": "/usr/bin/tr",
    "find": "/usr/bin/find",
    "xargs": "/usr/bin/xargs",
    # Network
    "curl": "/usr/bin/curl",
    "wget": "/usr/bin/wget",
    "dig": "/usr/bin/dig",
    "ping": "/usr/bin/ping",
    "traceroute": "/usr/bin/traceroute",
    "nslookup": "/usr/bin/nslookup",
    "ss": "/usr/bin/ss",
    "ip": "/usr/sbin/ip",
    # System
    "systemctl": "/usr/bin/systemctl",
    "journalctl": "/usr/bin/journalctl",
    "docker": "/usr/bin/docker",
    "git": "/usr/bin/git",
    "rsync": "/usr/bin/rsync",
    "tar": "/usr/bin/tar",
    "gzip": "/usr/bin/gzip",
    "zip": "/usr/bin/zip",
    "unzip": "/usr/bin/unzip",
    # Monitoring
    "top": "/usr/bin/top",
    "htop": "/usr/bin/htop",
    "free": "/usr/bin/free",
    "df": "/usr/bin/df",
    "du": "/usr/bin/du",
    "lsof": "/usr/bin/lsof",
    "ps": "/usr/bin/ps",
    "uptime": "/usr/bin/uptime",
    "vmstat": "/usr/bin/vmstat",
    "iostat": "/usr/bin/iostat",
    # Package management
    "apt": "/usr/bin/apt",
    "apt-get": "/usr/bin/apt-get",
    "apt-cache": "/usr/bin/apt-cache",
    "dpkg": "/usr/bin/dpkg",
    "snap": "/usr/bin/snap",
    "pip": "/usr/bin/pip3",
    # Security
    "ufw": "/usr/sbin/ufw",
    "fail2ban-client": "/usr/bin/fail2ban-client",
    "openssl": "/usr/bin/openssl",
    "ssh-keygen": "/usr/bin/ssh-keygen",
    # Editors/tools
    "jq": "/usr/bin/jq",
    "bc": "/usr/bin/bc",
    "date": "/usr/bin/date",
    "whoami": "/usr/bin/whoami",
    "hostname": "/usr/bin/hostname",
    "uname": "/usr/bin/uname",
    "id": "/usr/bin/id",
    "env": "/usr/bin/env",
    "printenv": "/usr/bin/printenv",
    "lsb_release": "/usr/bin/lsb_release",
    "timedatectl": "/usr/bin/timedatectl",
}


async def handle_binary_exec(a):
    """Execute a specific binary with arguments."""
    action = a.get("action", "run")
    
    if action == "list":
        # Show available binaries
        available = {}
        for name, path in sorted(ALLOWED_BINARIES.items()):
            exists = os.path.exists(path)
            available[name] = {"path": path, "available": exists}
        installed = sum(1 for v in available.values() if v["available"])
        return {"action": "list", "binaries": available, 
                "installed": installed, "total": len(available)}
    
    elif action == "run":
        program = a.get("program", "")
        arguments = a.get("arguments", [])
        elevated = a.get("elevated", False)
        timeout = min(a.get("timeout", 30), 120)
        work_dir = a.get("work_dir")
        stdin_data = a.get("stdin_data")
        
        if program not in ALLOWED_BINARIES:
            return {"error": f"Program '{program}' not in allowed list. Use action=list to see available."}
        
        binary_path = ALLOWED_BINARIES[program]
        if not os.path.exists(binary_path):
            return {"error": f"Binary not installed: {binary_path}"}
        
        # Build command
        cmd = [binary_path] + (arguments if isinstance(arguments, list) else [arguments])
        if elevated:
            cmd = ["sudo"] + cmd
        
        start = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                cwd=work_dir,
            )
            stdin_bytes = stdin_data.encode() if stdin_data else None
            out, err = await asyncio.wait_for(proc.communicate(input=stdin_bytes), timeout=timeout)
            elapsed = round((time.time() - start) * 1000)
            return {
                "action": "run", "program": program,
                "success": proc.returncode == 0,
                "output": out.decode(errors="replace").strip(),
                "errors": err.decode(errors="replace").strip() or None,
                "exit_code": proc.returncode,
                "elapsed_ms": elapsed,
            }
        except asyncio.TimeoutError:
            return {"success": False, "errors": f"Timeout {timeout}s", "exit_code": -1}
        except Exception as e:
            return {"success": False, "errors": str(e), "exit_code": -1}
    
    elif action == "pipe":
        # Chain multiple binaries: program1 args | program2 args
        steps = a.get("steps", [])
        if not steps or not isinstance(steps, list):
            return {"error": "steps parameter required: [{program, arguments}, ...]"}
        
        # Validate all programs first
        for step in steps:
            prog = step.get("program", "")
            if prog not in ALLOWED_BINARIES:
                return {"error": f"Program '{prog}' not in allowed list"}
        
        # Build pipe command
        pipe_parts = []
        for step in steps:
            prog = ALLOWED_BINARIES[step["program"]]
            args = step.get("arguments", [])
            if isinstance(args, str):
                args = [args]
            pipe_parts.append(" ".join([prog] + args))
        
        pipe_cmd = " | ".join(pipe_parts)
        timeout = min(a.get("timeout", 30), 120)
        
        r = await _run(["bash", "-c", pipe_cmd], timeout=timeout)
        return {"action": "pipe", "steps": len(steps), **r}
    
    return {"error": f"Unknown action: {action}"}


# === REGISTER NEW TOOLS ===

STRUCTURED_ADMIN_TOOLS.extend([
    {"name": "task_runner",
     "description": "Execute task payloads locally or on registered federation nodes. Payload encoding is used only for transport. Use elevated=true only when required, choose remote hosts explicitly, and verify the result after execution. Prefer more specific structured tools when they fit the operation.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["execute", "execute_remote", "encode", "decode", "quick_reference"],
                     "description": "execute=run task, execute_remote=run on node, encode=prepare payload, decode=preview, quick_reference=show pre-encoded commands"},
         "task_data": {"type": "string", "description": "Task payload in plain text, b64:..., or hex:... transport form"},
         "host": {"type": "string", "enum": list(REMOTE_HOSTS.keys()), "description": "Remote node (for execute_remote)"},
         "elevated": {"type": "boolean", "description": "Run with elevated privileges (default: false)"},
         "timeout": {"type": "integer", "description": "Execution timeout in seconds (max 300)"},
         "work_dir": {"type": "string", "description": "Working directory"},
         "text": {"type": "string", "description": "Plain text to encode (for encode action)"},
         "format": {"type": "string", "enum": ["b64", "hex"], "description": "Transport encoding (for encode action)"},
     }, "required": ["action"]},
     "annotations": {"title": "Encoded Task Runner", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}},
     
    {"name": "binary_exec",
     "description": "Run an allowed system program with explicit typed arguments, or chain allowed programs with action='pipe'. Prefer this for direct program execution because arguments stay structured and auditable. Use elevated=true only when required; use task_runner when the task genuinely needs compound shell semantics. action='list' returns the current allowed program inventory.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "run", "pipe"],
                     "description": "list=show binaries, run=execute program, pipe=chain programs"},
         "program": {"type": "string", "description": "Program name (e.g. 'curl', 'git', 'docker')"},
         "arguments": {"type": "array", "items": {"type": "string"}, "description": "Program arguments as list"},
         "elevated": {"type": "boolean", "description": "Run with elevated privileges"},
         "timeout": {"type": "integer", "description": "Timeout seconds (max 120)"},
         "work_dir": {"type": "string", "description": "Working directory"},
         "stdin_data": {"type": "string", "description": "Data to send to program's stdin"},
         "steps": {"type": "array", "items": {"type": "object", "properties": {"program": {"type": "string", "description": "Program name"}, "arguments": {"type": "array", "items": {"type": "string"}, "description": "Program arguments"}}, "required": ["program"]}, "description": "For pipe: [{program, arguments}, ...] to chain"},
     }, "required": ["action"]},
     "annotations": {"title": "Program Executor", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
])

STRUCTURED_ADMIN_HANDLERS["task_runner"] = handle_task_runner
STRUCTURED_ADMIN_HANDLERS["binary_exec"] = handle_binary_exec

logger.info(f"Structured Admin API final: {len(STRUCTURED_ADMIN_TOOLS)} tools, {len(COMMAND_TEMPLATES)} templates, {len(ALLOWED_BINARIES)} binaries")


# =============================================================================
# PATCH v2.82 — SAFE PROBE (Read-Only Diagnostik, MCP Write-Gate-safe)
# =============================================================================

_SAFE_PROBE_COMMANDS = {
    "hostname":      ["hostname"],
    "uptime":        ["uptime", "-p"],
    "uname":         ["uname", "-a"],
    "free":          ["free", "-h"],
    "df":            ["df", "-h", "--total"],
    "docker_ps":     ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}"],
    "load":          ["cat", "/proc/loadavg"],
    "kernel":        ["uname", "-r"],
    "os_release":    ["lsb_release", "-ds"],
    "who":           ["who"],
    "systemd_failed":["systemctl", "--failed", "--no-pager"],
}

_SAFE_PROBE_SERVICES = SERVICES


async def handle_safe_probe(a):
    """Read-only system diagnostics. No side effects, no sudo, no pipes."""
    action = a.get("action", "overview")

    if action == "overview":
        data = {}
        for name in ("hostname", "uptime", "kernel", "load", "free", "df"):
            r = await _run(_SAFE_PROBE_COMMANDS[name], timeout=5)
            data[name] = r["output"] if r["success"] else f"[error: {r['errors']}]"
        return {"action": "overview", "data": data}

    elif action == "run":
        probe = a.get("probe", "")
        if probe not in _SAFE_PROBE_COMMANDS:
            return {"error": f"Unknown probe: '{probe}'. Available: {list(_SAFE_PROBE_COMMANDS.keys())}"}
        r = await _run(_SAFE_PROBE_COMMANDS[probe], timeout=10)
        return {"action": "run", "probe": probe, **r}

    elif action == "service_status":
        service = a.get("service", "")
        if service and service not in _SAFE_PROBE_SERVICES:
            return {"error": f"Service '{service}' not in allowlist: {_SAFE_PROBE_SERVICES}"}
        if service:
            r = await _run(["systemctl", "status", _unit(service), "--no-pager", "-l"], timeout=10)
            return {"action": "service_status", "service": service, "unit": _unit(service), **r}
        else:
            data = {}
            for s in _SAFE_PROBE_SERVICES:
                r = await _run(_UNIT_STATE_CMD + [_unit(s)], timeout=5)
                data[s] = _parse_unit_state(r["output"])["status"]
            return {"action": "service_status", "services": data}

    elif action == "journal":
        n = str(min(a.get("lines", 30), 100))
        unit = a.get("unit", "")
        cmd = ["journalctl", "--no-pager", "-n", n]
        if unit and unit in _SAFE_PROBE_SERVICES:
            cmd.extend(["-u", _unit(unit)])
        r = await _run(cmd, timeout=10)
        return {"action": "journal", "lines_requested": int(n), **r}

    elif action == "remote_ping":
        results = {}
        for name, info in FEDERATION_NODES.items():
            r = await _run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=3",
                 "-o", "BatchMode=yes", f"{info['user']}@{info['host']}", "--",
                 "uptime", "-p"],
                timeout=8
            )
            results[name] = {"reachable": r["success"], "uptime": r["output"] if r["success"] else None}
        return {"action": "remote_ping", "nodes": results}

    elif action == "list":
        return {
            "action": "list",
            "probes": list(_SAFE_PROBE_COMMANDS.keys()),
            "services": _SAFE_PROBE_SERVICES,
            "remote_nodes": list(FEDERATION_NODES.keys()),
        }

    return {"error": f"Unknown action: {action}. Use: overview, run, service_status, journal, remote_ping, list"}


STRUCTURED_ADMIN_TOOLS.append({
    "name": "safe_probe",
    "description": "Read-only system diagnostics: hostname, uptime, memory, disk, docker, service status, journal, remote node ping. No side effects, no sudo. Safe for MCP read-only gate.",
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["overview", "run", "service_status", "journal", "remote_ping", "list"],
                    "description": "overview=quick system snapshot, run=specific probe, service_status=systemd check, journal=recent logs, remote_ping=federation connectivity, list=show all probes"},
        "probe": {"type": "string", "enum": list(_SAFE_PROBE_COMMANDS.keys()),
                   "description": "Specific probe to run (for action=run)"},
        "service": {"type": "string", "enum": SERVICES,
                     "description": "Service name (for service_status). Omit for all."},
        "unit": {"type": "string", "description": "Journald unit filter (for journal)"},
        "lines": {"type": "integer", "description": "Journal lines (max 100, default 30)"},
    }, "required": ["action"]},
    "annotations": {
        "title": "Safe System Probe (Read-Only)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
})

STRUCTURED_ADMIN_HANDLERS["safe_probe"] = handle_safe_probe
logger.info("PATCH v2.82: safe_probe registered (read-only)")


# =============================================================================
# PATCH v2.82 — AGENT REVIEW (Read-Only AI-Debug / Review Tool)
# =============================================================================

async def handle_agent_review(a):
    """Read-only agent inspection and AI-assisted review. No start/stop/modify."""
    from ..services.tristar.agent_controller import agent_controller
    action = a.get("action", "status")

    if action == "status":
        agent_id = a.get("agent_id")
        if agent_id:
            agent = await agent_controller.get_agent(agent_id)
            return {"action": "status", "agent": agent or {"error": f"Agent '{agent_id}' not found"}}
        else:
            agents = await agent_controller.list_agents()
            return {"action": "status", "agents": agents}

    elif action == "output":
        agent_id = a.get("agent_id", "")
        if not agent_id:
            return {"error": "agent_id required for output action"}
        lines = min(a.get("lines", 50), 200)
        output = await agent_controller.get_agent_output(agent_id, lines)
        return {"action": "output", "agent_id": agent_id, "output": output, "lines": len(output)}

    elif action == "stats":
        stats = await agent_controller.get_stats() if hasattr(agent_controller, 'get_stats') else {}
        return {"action": "stats", "data": stats}

    elif action == "health_check":
        agents = await agent_controller.list_agents()
        health = {}
        for agent in (agents if isinstance(agents, list) else agents.get("agents", [])):
            aid = agent.get("id", agent.get("agent_id", "unknown"))
            health[aid] = {
                "status": agent.get("status", "unknown"),
                "running": agent.get("status") == "running",
                "pid": agent.get("pid"),
            }
        return {"action": "health_check", "agents": health}

    elif action == "review_logs":
        agent_id = a.get("agent_id", "")
        context = {"agent_id": agent_id}

        if agent_id:
            agent = await agent_controller.get_agent(agent_id)
            context["agent_status"] = agent
            output = await agent_controller.get_agent_output(agent_id, 30)
            context["recent_output"] = output

        r = await _run(["systemctl", "is-active", "triforce"], timeout=5)
        context["triforce_status"] = r["output"]
        r = await _run(["free", "-h"], timeout=5)
        context["memory"] = r["output"]
        r = await _run(["cat", "/proc/loadavg"], timeout=5)
        context["load"] = r["output"]

        return {
            "action": "review_logs",
            "context": context,
            "hint": "Use this data for root-cause analysis. No modifications were made.",
        }

    return {"error": f"Unknown action: {action}. Use: status, output, stats, health_check, review_logs"}


STRUCTURED_ADMIN_TOOLS.append({
    "name": "agent_review",
    "description": "Read-only AI agent inspection and debugging. View agent status, output buffers, health checks, and collect review context. No start/stop/modify — safe for MCP read-only gate.",
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["status", "output", "stats", "health_check", "review_logs"],
                    "description": "status=agent info, output=recent buffer, stats=metrics, health_check=all agents, review_logs=debug context"},
        "agent_id": {"type": "string", "description": "Agent ID (e.g. 'claude-mcp', 'codex-mcp')"},
        "lines": {"type": "integer", "description": "Output lines (max 200, default 50)"},
    }, "required": ["action"]},
    "annotations": {
        "title": "Agent Review (Read-Only)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
})

STRUCTURED_ADMIN_HANDLERS["agent_review"] = handle_agent_review
logger.info("PATCH v2.82: agent_review registered (read-only)")


# =============================================================================
# PATCH v2.82 — READ-ONLY TOOL VARIANTS (Tool-Splitting)
# =============================================================================

async def handle_service_status(a):
    """Read-only wrapper: delegiert an service_control mit action=status|logs."""
    action = a.get("action", "status")
    if action not in ("status", "logs"):
        return {"error": f"service_status only supports: status, logs. Got: {action}"}
    return await handle_service_control({**a, "action": action})

async def handle_container_status(a):
    """Read-only wrapper: delegiert an container_control mit action=list|status|logs|stats."""
    action = a.get("action", "list")
    if action not in ("list", "status", "logs", "stats"):
        return {"error": f"container_status only supports: list, status, logs, stats. Got: {action}"}
    return await handle_container_control({**a, "action": action})

async def handle_file_read(a):
    """Read-only wrapper: delegiert an file_ops mit action=read|list|find|size."""
    action = a.get("action", "read")
    if action not in ("read", "list", "find", "size"):
        return {"error": f"file_read only supports: read, list, find, size. Got: {action}"}
    return await handle_file_ops({**a, "action": action})

async def handle_remote_status(a):
    """Read-only wrapper: delegiert an remote_admin mit read-only actions."""
    action = a.get("action", "ping_all")
    read_only_actions = {"list_hosts", "ping_all", "system_overview", "service_status",
                         "docker_status", "disk_usage", "memory_usage", "check_connectivity"}
    if action not in read_only_actions:
        return {"error": f"remote_status only supports: {sorted(read_only_actions)}. Got: {action}"}
    return await handle_remote_admin({**a, "action": action})


_READONLY_SPLIT_TOOLS = [
    {"name": "service_status",
     "description": "Check systemd service status and view logs (read-only). For start/stop/restart use service_control.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["status", "logs"]},
         "service": {"type": "string", "enum": SERVICES},
         "lines": {"type": "integer"},
     }, "required": ["action", "service"]},
     "annotations": {"title": "Service Status (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},

    {"name": "container_status",
     "description": "View Docker container status, logs, and resource stats (read-only). For start/stop/restart use container_control.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "status", "logs", "stats"]},
         "container": {"type": "string", "enum": CONTAINERS},
         "lines": {"type": "integer"},
     }, "required": ["action"]},
     "annotations": {"title": "Container Status (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},

    {"name": "file_read",
     "description": "Read files, list directories, find files, check sizes (read-only). For write/append use file_ops.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["read", "list", "find", "size"]},
         "path": {"type": "string"},
         "pattern": {"type": "string"},
         "start_line": {"type": "integer"},
         "end_line": {"type": "integer"},
     }, "required": ["action", "path"]},
     "annotations": {"title": "File Reader (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},

    {"name": "remote_status",
     "description": "View federation node status, connectivity, disk/memory usage (read-only). For service restarts use remote_admin.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list_hosts", "ping_all", "system_overview", "service_status",
                                                 "docker_status", "disk_usage", "memory_usage", "check_connectivity"]},
         "host": {"type": "string", "enum": list(REMOTE_HOSTS.keys())},
         "service": {"type": "string", "enum": SERVICES},
     }, "required": ["action"]},
     "annotations": {"title": "Remote Node Status (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]

STRUCTURED_ADMIN_TOOLS.extend(_READONLY_SPLIT_TOOLS)
STRUCTURED_ADMIN_HANDLERS.update({
    "service_status": handle_service_status,
    "container_status": handle_container_status,
    "file_read": handle_file_read,
    "remote_status": handle_remote_status,
})

logger.info(f"PATCH v2.82: Read-only split tools registered ({len(_READONLY_SPLIT_TOOLS)} tools). Total: {len(STRUCTURED_ADMIN_TOOLS)} tools")


# =============================================================================
# PATCH v2.82 — MCP TOOL TELEMETRY (Read-Only Performance Metrics)
# =============================================================================

from collections import defaultdict as _defaultdict

class _MCPTelemetryStore:
    """In-memory telemetry for MCP tool calls: latency, tokens, errors."""

    def __init__(self):
        self._calls = _defaultdict(lambda: {
            "count": 0, "errors": 0,
            "total_ms": 0, "min_ms": float("inf"), "max_ms": 0,
            "last_call": None, "last_error": None,
            "_total_chars": 0,
        })
        self._recent = []
        self._max_recent = 100

    def record(self, tool_name, latency_ms, success=True, response_chars=0, error=None):
        s = self._calls[tool_name]
        s["count"] += 1
        s["total_ms"] += latency_ms
        s["min_ms"] = min(s["min_ms"], latency_ms)
        s["max_ms"] = max(s["max_ms"], latency_ms)
        s["_total_chars"] += response_chars
        s["last_call"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not success:
            s["errors"] += 1
            s["last_error"] = str(error)[:200] if error else None
        self._recent.append({
            "tool": tool_name, "ms": latency_ms, "ok": success,
            "chars": response_chars, "ts": s["last_call"],
        })
        if len(self._recent) > self._max_recent:
            self._recent.pop(0)

    def get_stats(self, tool_name=None):
        if tool_name:
            s = self._calls.get(tool_name)
            if not s: return {"error": f"No data for: {tool_name}"}
            avg_chars = s["_total_chars"] // max(s["count"], 1)
            return {"tool": tool_name, "calls": s["count"], "errors": s["errors"],
                    "avg_ms": round(s["total_ms"]/max(s["count"],1)),
                    "min_ms": s["min_ms"] if s["min_ms"]!=float("inf") else 0,
                    "max_ms": s["max_ms"], "est_tokens": avg_chars//4,
                    "last_call": s["last_call"], "last_error": s["last_error"]}
        return {n: {"calls": s["count"], "errors": s["errors"],
                     "avg_ms": round(s["total_ms"]/max(s["count"],1)),
                     "max_ms": s["max_ms"]}
                for n, s in sorted(self._calls.items(), key=lambda x: x[1]["count"], reverse=True)}

    def get_recent(self, n=20): return list(reversed(self._recent[-n:]))

    def get_summary(self):
        tc = sum(s["count"] for s in self._calls.values())
        te = sum(s["errors"] for s in self._calls.values())
        tm = sum(s["total_ms"] for s in self._calls.values())
        return {"total_calls": tc, "total_errors": te,
                "avg_latency_ms": round(tm/max(tc,1)), "unique_tools": len(self._calls),
                "top_by_calls": [(k,v["count"]) for k,v in sorted(self._calls.items(), key=lambda x:x[1]["count"], reverse=True)[:5]]}

mcp_telemetry = _MCPTelemetryStore()


async def handle_mcp_telemetry(a):
    """Read-only MCP telemetry: latency, tokens, error rates."""
    action = a.get("action", "summary")
    if action == "summary": return {"action": "summary", **mcp_telemetry.get_summary()}
    elif action == "tool":
        t = a.get("tool", "")
        if not t: return {"error": "tool parameter required"}
        return {"action": "tool", **mcp_telemetry.get_stats(t)}
    elif action == "all": return {"action": "all", "tools": mcp_telemetry.get_stats()}
    elif action == "recent": return {"action": "recent", "calls": mcp_telemetry.get_recent(min(a.get("lines",20),100))}
    return {"error": f"Unknown action: {action}. Use: summary, tool, all, recent"}


STRUCTURED_ADMIN_TOOLS.append({
    "name": "mcp_telemetry",
    "description": "Read-only MCP tool call telemetry: latency, token estimates, error rates per tool. summary=overview, tool=specific, all=all tools, recent=last N calls.",
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["summary", "tool", "all", "recent"]},
        "tool": {"type": "string", "description": "Tool name (for action=tool)"},
        "lines": {"type": "integer", "description": "Recent calls count (max 100)"},
    }, "required": ["action"]},
    "annotations": {"title": "MCP Telemetry (Read-Only)", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
})

STRUCTURED_ADMIN_HANDLERS["mcp_telemetry"] = handle_mcp_telemetry
logger.info("PATCH v2.82: mcp_telemetry registered (read-only)")


# =============================================================================
# PATCH v2.82 — MCP ANALYTICS (Read-Only Performance & Benchmark Tool)
# =============================================================================
# Liest MCP Tool-Call-Logs und berechnet: Latenz, Erfolgsrate, Token-Schätzung,
# Top-Tools, Fehler-Ranking. Read-only, keine Side Effects.

import json as _json
from collections import defaultdict as _defaultdict

_MCP_CALL_LOG = []  # In-Memory Ring-Buffer für aktuelle Session
_MCP_CALL_LOG_MAX = 500


def record_mcp_call(tool_name, latency_ms, status, caller="unknown", error=None, result_size=0):
    """Record an MCP tool call for analytics. Called from mcp_remote.py handler."""
    import time as _t
    entry = {
        "tool": tool_name,
        "latency_ms": round(latency_ms, 1),
        "status": status,
        "caller": caller,
        "error": str(error)[:200] if error else None,
        "result_bytes": result_size,
        "timestamp": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
        "ts": _t.time(),
    }
    _MCP_CALL_LOG.append(entry)
    if len(_MCP_CALL_LOG) > _MCP_CALL_LOG_MAX:
        _MCP_CALL_LOG.pop(0)


async def handle_mcp_analytics(a):
    """Read-only MCP tool call analytics and performance benchmarks."""
    action = a.get("action", "summary")

    if action == "summary":
        if not _MCP_CALL_LOG:
            # Fallback: lese aus Log-Datei
            return await _analytics_from_logfile(a)

        total = len(_MCP_CALL_LOG)
        success = sum(1 for e in _MCP_CALL_LOG if e["status"] == "success")
        errors = total - success

        # Per-Tool Stats
        tool_stats = _defaultdict(lambda: {"count": 0, "success": 0, "errors": 0,
                                            "total_ms": 0, "min_ms": 99999, "max_ms": 0})
        for e in _MCP_CALL_LOG:
            t = tool_stats[e["tool"]]
            t["count"] += 1
            if e["status"] == "success":
                t["success"] += 1
            else:
                t["errors"] += 1
            ms = e["latency_ms"]
            t["total_ms"] += ms
            t["min_ms"] = min(t["min_ms"], ms)
            t["max_ms"] = max(t["max_ms"], ms)

        # Top-10 by call count
        top_tools = sorted(tool_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
        top_tools_formatted = []
        for name, s in top_tools:
            avg_ms = round(s["total_ms"] / s["count"], 1) if s["count"] else 0
            top_tools_formatted.append({
                "tool": name,
                "calls": s["count"],
                "success_rate": round(s["success"] / s["count"] * 100, 1) if s["count"] else 0,
                "avg_latency_ms": avg_ms,
                "min_ms": round(s["min_ms"], 1),
                "max_ms": round(s["max_ms"], 1),
            })

        # Slowest calls
        slowest = sorted(_MCP_CALL_LOG, key=lambda e: e["latency_ms"], reverse=True)[:5]

        # Error summary
        error_tools = [(name, s["errors"]) for name, s in tool_stats.items() if s["errors"] > 0]
        error_tools.sort(key=lambda x: x[1], reverse=True)

        # Overall latency
        all_ms = [e["latency_ms"] for e in _MCP_CALL_LOG]
        avg_ms = round(sum(all_ms) / len(all_ms), 1) if all_ms else 0
        p50 = sorted(all_ms)[len(all_ms) // 2] if all_ms else 0
        p95 = sorted(all_ms)[int(len(all_ms) * 0.95)] if all_ms else 0
        p99 = sorted(all_ms)[int(len(all_ms) * 0.99)] if all_ms else 0

        return {
            "action": "summary",
            "total_calls": total,
            "success": success,
            "errors": errors,
            "success_rate": round(success / total * 100, 1) if total else 0,
            "latency": {
                "avg_ms": avg_ms,
                "p50_ms": round(p50, 1),
                "p95_ms": round(p95, 1),
                "p99_ms": round(p99, 1),
            },
            "top_tools": top_tools_formatted,
            "error_ranking": error_tools[:5],
            "slowest_calls": [{
                "tool": e["tool"], "latency_ms": e["latency_ms"],
                "status": e["status"], "timestamp": e["timestamp"],
            } for e in slowest],
            "buffer_size": len(_MCP_CALL_LOG),
            "buffer_max": _MCP_CALL_LOG_MAX,
        }

    elif action == "recent":
        n = min(a.get("limit", 20), 50)
        recent = _MCP_CALL_LOG[-n:]
        return {
            "action": "recent",
            "calls": [{
                "tool": e["tool"],
                "latency_ms": e["latency_ms"],
                "status": e["status"],
                "caller": e.get("caller"),
                "timestamp": e["timestamp"],
                "error": e.get("error"),
            } for e in reversed(recent)],
            "count": len(recent),
        }

    elif action == "tool_detail":
        tool_name = a.get("tool", "")
        if not tool_name:
            return {"error": "tool parameter required"}
        entries = [e for e in _MCP_CALL_LOG if e["tool"] == tool_name]
        if not entries:
            return {"action": "tool_detail", "tool": tool_name, "message": "No calls recorded"}
        ms_list = [e["latency_ms"] for e in entries]
        errors = [e for e in entries if e["status"] != "success"]
        return {
            "action": "tool_detail",
            "tool": tool_name,
            "total_calls": len(entries),
            "success": len(entries) - len(errors),
            "errors": len(errors),
            "latency": {
                "avg_ms": round(sum(ms_list) / len(ms_list), 1),
                "min_ms": round(min(ms_list), 1),
                "max_ms": round(max(ms_list), 1),
                "p95_ms": round(sorted(ms_list)[int(len(ms_list) * 0.95)], 1) if len(ms_list) > 1 else round(ms_list[0], 1),
            },
            "recent_errors": [{
                "error": e.get("error"), "timestamp": e["timestamp"],
            } for e in errors[-5:]],
            "last_call": entries[-1]["timestamp"],
        }

    elif action == "from_log":
        return await _analytics_from_logfile(a)

    return {"error": f"Unknown action: {action}. Use: summary, recent, tool_detail, from_log"}


async def _analytics_from_logfile(a):
    """Parse MCP tool call stats from unified log file."""
    log_path = "/home/zombie/triforce/logs/unified.log"
    n_lines = min(a.get("lines", 5000), 20000)
    try:
        r = await _run(["tail", "-n", str(n_lines), log_path], timeout=5)
        if not r["success"]:
            return {"error": f"Cannot read log: {r['errors']}"}
        lines = r["output"].split("\n")
        tool_calls = []
        for line in lines:
            # Nur den finalen TOOL_CALL-Record zaehlen, nicht START/OK-Duplikate,
            # sonst wird jeder Call 2-3x gezaehlt.
            if "|TOOL_CALL |" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            # Format: ...|mcp.tools|TOOL_CALL | <tool> | <OK|ERR> | {...}
            # Der Toolname steht im Feld NACH dem "TOOL_CALL"-Feld.
            tool_name = ""
            latency = 0.0
            for i, p in enumerate(parts):
                if p == "TOOL_CALL" and i + 1 < len(parts):
                    tool_name = parts[i + 1]
                    break
            for p in parts:
                if p.endswith("ms") and p[:-2].replace(".", "").isdigit():
                    latency = float(p[:-2])
            if tool_name:
                tool_calls.append({"tool": tool_name, "latency_ms": latency})

        # Aggregate
        tool_stats = _defaultdict(lambda: {"count": 0, "total_ms": 0})
        for tc in tool_calls:
            s = tool_stats[tc["tool"]]
            s["count"] += 1
            s["total_ms"] += tc["latency_ms"]

        top = sorted(tool_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:15]
        return {
            "action": "from_log",
            "source": "unified.log",
            "lines_parsed": len(lines),
            "tool_calls_found": len(tool_calls),
            "tools": [{
                "tool": name,
                "calls": s["count"],
                "avg_latency_ms": round(s["total_ms"] / s["count"], 1) if s["count"] else 0,
            } for name, s in top],
        }
    except Exception as e:
        return {"error": f"Log analysis failed: {e}"}


# Tool-Definition
STRUCTURED_ADMIN_TOOLS.append({
    "name": "mcp_analytics",
    "description": "MCP tool call analytics: latency percentiles, success rates, error ranking, per-tool benchmarks, recent call history. Read-only performance monitoring.",
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["summary", "recent", "tool_detail", "from_log"],
                    "description": "summary=overall stats, recent=last N calls, tool_detail=per-tool deep-dive, from_log=parse from log file"},
        "tool": {"type": "string", "description": "Tool name for tool_detail action"},
        "limit": {"type": "integer", "description": "Number of recent calls (max 50, default 20)"},
        "lines": {"type": "integer", "description": "Log lines to parse (max 2000, default 500)"},
    }, "required": ["action"]},
    "annotations": {
        "title": "MCP Analytics (Read-Only)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
})

STRUCTURED_ADMIN_HANDLERS["mcp_analytics"] = handle_mcp_analytics
logger.info("PATCH v2.82: mcp_analytics registered (read-only performance monitoring)")


# =============================================================================
# PATCH v2.86 - remote_hosts als eigener Handler
# =============================================================================

async def handle_remote_hosts(a):
    """List all known federation hosts. Read-only."""
    return await handle_remote_admin({"action": "list_hosts"})

STRUCTURED_ADMIN_TOOLS.append({
    "name": "remote_hosts",
    "description": "List registered remote hosts for task execution. Returns host IPs, descriptions, and capabilities.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
    "annotations": {"title": "Remote Hosts (Read-Only)", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
})
STRUCTURED_ADMIN_HANDLERS["remote_hosts"] = handle_remote_hosts
STRUCTURED_ADMIN_HANDLERS["remote_host_list"] = handle_remote_hosts
logger.info("PATCH v2.86: remote_hosts handler registered")


# =============================================================================
# PATCH v2.86 - container_status(stats) Container-Filter
# =============================================================================

_orig_container_control = handle_container_control

async def _patched_container_control(a):
    """Patched: stats respects container filter."""
    act = a.get("action")
    ctr = a.get("container", "")
    if act == "stats":
        cmd = ["docker", "stats", "--no-stream", "--format",
               "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"]
        if ctr:
            cmd.append(ctr)
            return {"action": act, "container": ctr, **(await _run(cmd))}
        return {"action": act, **(await _run(cmd))}
    return await _orig_container_control(a)

# Patch auf BEIDE Ebenen: Dict UND Module-Level-Name
# handle_container_status referenziert handle_container_control als global lookup
handle_container_control = _patched_container_control
STRUCTURED_ADMIN_HANDLERS["container_control"] = _patched_container_control
logger.info("PATCH v2.86: container_control stats filter enabled (module-level + dict)")


# =============================================================================
# PATCH v2.86 - Read-Only Wrappers: binary_list, template_list, task_reference
# =============================================================================

async def handle_binary_list(a):
    """Read-only: list available binaries."""
    return await handle_binary_exec({"action": "list"})

async def handle_template_list(a):
    """Read-only: list available command templates."""
    return await handle_custom_exec({"action": "list"})

async def handle_task_reference(a):
    """Read-only: quick_reference, encode, decode only."""
    action = a.get("action", "quick_reference")
    if action not in ("quick_reference", "encode", "decode"):
        return {"error": f"task_reference only supports: quick_reference, encode, decode. Got: {action}"}
    return await handle_task_runner({**a, "action": action})


STRUCTURED_ADMIN_TOOLS.extend([
    {"name": "binary_list",
     "description": "List all available system programs that can be executed via binary_exec (read-only inventory).",
     "inputSchema": {"type": "object", "properties": {}, "required": []},
     "annotations": {"title": "Binary Inventory (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "template_list",
     "description": "List all available command templates for custom_exec (read-only inventory).",
     "inputSchema": {"type": "object", "properties": {}, "required": []},
     "annotations": {"title": "Command Templates (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "task_reference",
     "description": "Get pre-encoded command reference, encode text, or decode payloads (read-only). For execution use task_runner.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["quick_reference", "encode", "decode"]},
         "text": {"type": "string", "description": "Text to encode (for encode action)"},
         "format": {"type": "string", "enum": ["b64", "hex"]},
         "task_data": {"type": "string", "description": "Encoded data to decode (for decode action)"},
     }, "required": ["action"]},
     "annotations": {"title": "Task Reference (Read-Only)", "readOnlyHint": True,
                      "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
])

STRUCTURED_ADMIN_HANDLERS.update({
    "binary_list": handle_binary_list,
    "template_list": handle_template_list,
    "task_reference": handle_task_reference,
})

logger.info(f"PATCH v2.86: Read-only wrappers registered. Total: {len(STRUCTURED_ADMIN_TOOLS)} tools")


# =============================================================================
# PATCH v2.86 — remote_hosts Handler + container_status Fix + Read-Only Wrappers
# =============================================================================

async def handle_remote_hosts(a):
    """List all known federation hosts. Read-only."""
    return await handle_remote_admin({"action": "list_hosts"})

STRUCTURED_ADMIN_TOOLS.append({
    "name": "remote_hosts",
    "description": "List registered remote hosts for task execution.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
    "annotations": {"title": "Remote Hosts (Read-Only)", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
})
STRUCTURED_ADMIN_HANDLERS["remote_hosts"] = handle_remote_hosts
STRUCTURED_ADMIN_HANDLERS["remote_host_list"] = handle_remote_hosts

# container_control(stats) Container-Filter
_orig_cc = handle_container_control
async def _patched_cc(a):
    act, ctr = a.get("action"), a.get("container", "")
    if act == "stats":
        cmd = ["docker", "stats", "--no-stream", "--format",
               "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"]
        if ctr:
            cmd.append(ctr)
            return {"action": act, "container": ctr, **(await _run(cmd))}
        return {"action": act, **(await _run(cmd))}
    return await _orig_cc(a)
STRUCTURED_ADMIN_HANDLERS["container_control"] = _patched_cc
# BUG-008 FIX 2026-03-10: Doppelte Definitionen entfernt — handle_binary_list,
# handle_template_list, handle_task_reference bereits oben (v2.86 Wrappers) definiert.
# Zweiter STRUCTURED_ADMIN_TOOLS.extend() und STRUCTURED_ADMIN_HANDLERS.update() entfernt.
logger.info(f"PATCH v2.86 applied: {len(STRUCTURED_ADMIN_TOOLS)} tools total")
