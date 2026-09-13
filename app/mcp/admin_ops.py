"""
Structured Admin Operations for AI Clients
===========================================
Provides structured, parameterized system management tools
optimized for AI agent interaction. Each tool maps to specific
operations with defined inputs and outputs.
"""
import asyncio
import os
import logging
from typing import Any, Dict

logger = logging.getLogger("ailinux.admin_ops")

async def _run(cmd: str, timeout: int = 30) -> Dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd="/home/zombie/triforce")
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {"success": proc.returncode == 0,
                "output": stdout.decode("utf-8", errors="replace").strip(),
                "errors": stderr.decode("utf-8", errors="replace").strip() if stderr else None,
                "code": proc.returncode}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "errors": "Timed out", "code": -1}
    except Exception as e:
        return {"success": False, "output": "", "errors": str(e), "code": -1}

async def _run_sudo(cmd: str, timeout: int = 30) -> Dict[str, Any]:
    return await _run(f"sudo {cmd}", timeout=timeout)

# === 1. SYSTEM INFO ===
async def handle_system_info(params: Dict[str, Any]) -> Dict[str, Any]:
    cat = params.get("category", "overview")
    cmds = {
        "overview": "uname -r && hostname && uptime -p && free -h | head -2 && df -h / | tail -1",
        "kernel": "uname -a",
        "memory": "free -h && echo --- && cat /proc/meminfo | head -5",
        "disk": "df -h && echo --- && lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT 2>/dev/null | head -20",
        "cpu": "lscpu | head -20 && echo --- && cat /proc/loadavg",
        "network": "ip -br addr && echo --- && ip route | head -5 && echo --- && ss -tulnp 2>/dev/null | head -15",
        "uptime": "uptime && who",
        "os": "cat /etc/os-release && echo --- && hostnamectl 2>/dev/null | head -10",
    }
    if cat not in cmds:
        return {"error": f"Unknown: {cat}", "available": list(cmds.keys())}
    r = await _run(cmds[cat])
    return {"category": cat, **r}

# === 2. PACKAGE MANAGER ===
async def handle_package_manager(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action", "status")
    target = params.get("target", "")
    if action == "refresh":
        return await _run_sudo("apt-get update -qq", timeout=60)
    elif action == "status":
        r = await _run("apt list --installed 2>/dev/null | wc -l")
        u = await _run("apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0")
        return {"installed": r["output"], "upgradable": u["output"], "success": True}
    elif action == "list_upgradable":
        return await _run("apt list --upgradable 2>/dev/null")
    elif action == "search":
        if not target: return {"error": "target required"}
        return await _run(f"apt-cache search {target} | head -20")
    elif action == "info":
        if not target: return {"error": "target required"}
        return await _run(f"apt-cache show {target} 2>/dev/null | head -30")
    elif action == "install":
        if not target: return {"error": "target required"}
        if not all(c.isalnum() or c in "-._+" for c in target):
            return {"error": "Invalid package name"}
        return await _run_sudo(f"apt-get install -y {target}", timeout=120)
    elif action == "upgrade":
        return await _run_sudo("apt-get upgrade -y", timeout=300)
    return {"error": f"Unknown: {action}", "available": ["refresh","status","list_upgradable","search","info","install","upgrade"]}

# === 3. SERVICE CONTROL ===
async def handle_service_control(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action", "list")
    target = params.get("target", "")
    lines = min(params.get("lines", 30), 200)
    act_map = {
        "list": "systemctl list-units --type=service --state=running --no-pager | head -30",
        "list_all": "systemctl list-units --type=service --no-pager | head -50",
    }
    if action in act_map:
        return await _run(act_map[action])
    if not target and action == "status":
        return await _run(act_map["list"])
    if not target:
        return {"error": "target required"}
    sudo_acts = {"start","stop","restart","enable","disable"}
    if action == "status":
        return await _run(f"systemctl status {target} --no-pager -l 2>&1 | head -25")
    elif action == "journal":
        return await _run(f"journalctl -u {target} --no-pager -n {lines} 2>&1")
    elif action in sudo_acts:
        return await _run_sudo(f"systemctl {action} {target}")
    return {"error": f"Unknown: {action}", "available": ["list","list_all","status","start","stop","restart","enable","disable","journal"]}

# === 4. CONTAINER OPS ===
async def handle_container_ops(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action", "list")
    target = params.get("target", "")
    lines = min(params.get("lines", 50), 200)
    no_target = {
        "list": "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1",
        "list_all": "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>&1",
        "images": "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' 2>&1",
        "stats": "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>&1",
    }
    if action in no_target:
        return await _run(no_target[action])
    if not target:
        return {"error": "target required"}
    if action == "status":
        return await _run(f"docker inspect {target} --format '{{{{json .State}}}}' 2>&1")
    elif action == "logs":
        return await _run(f"docker logs --tail {lines} {target} 2>&1")
    elif action in ("start","stop","restart"):
        return await _run(f"docker {action} {target}")
    return {"error": f"Unknown: {action}", "available": ["list","list_all","status","logs","start","stop","restart","images","stats"]}

# === 5. FILE OPS ===
_ALLOWED = ["/home/zombie/triforce/","/etc/","/var/log/","/tmp/","/home/zombie/"]

def _ok_path(p):
    return any(os.path.realpath(p).startswith(a) for a in _ALLOWED)

async def handle_file_ops(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action", "read")
    path = params.get("path", "")
    if not path and action not in ("search",):
        return {"error": "path required"}
    if path and not _ok_path(path):
        return {"error": f"Path restricted. Allowed: {_ALLOWED}"}
    if action == "read":
        return await _run(f"head -n {min(params.get('lines',100),500)} '{path}'")
    elif action == "tail":
        return await _run(f"tail -n {min(params.get('lines',50),200)} '{path}'")
    elif action == "write":
        c = params.get("content","")
        if not c: return {"error": "content required"}
        return await _run(f"cat > '{path}' << 'ADMIN_EOF'\n{c}\nADMIN_EOF")
    elif action == "append":
        c = params.get("content","")
        if not c: return {"error": "content required"}
        return await _run(f"cat >> '{path}' << 'ADMIN_EOF'\n{c}\nADMIN_EOF")
    elif action == "list":
        return await _run(f"ls -la '{path}' | head -50")
    elif action == "stat":
        return await _run(f"stat '{path}'")
    elif action == "search":
        q = params.get("query","")
        scope = params.get("scope","/home/zombie/triforce/app")
        if not _ok_path(scope): return {"error": "scope restricted"}
        return await _run(f"grep -rn '{q}' '{scope}' --include='*.py' --include='*.json' | head -30")
    elif action == "tree":
        return await _run(f"find '{path}' -maxdepth {min(params.get('depth',2),4)} -type f | head -100")
    return {"error": f"Unknown: {action}", "available": ["read","tail","write","append","list","stat","search","tree"]}

# === 6. PROCESS MONITOR ===
async def handle_process_monitor(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action", "top")
    target = params.get("target", "")
    n = min(params.get("count", 15), 50)
    cmds = {"top": f"ps aux --sort=-%mem | head -n {n}", "cpu_top": f"ps aux --sort=-%cpu | head -n {n}", "tree": "pstree -p | head -40"}
    if action in cmds:
        return await _run(cmds[action])
    if action == "find":
        return await _run(f"pgrep -la '{target}' 2>/dev/null | head -20") if target else {"error": "target required"}
    if action == "info":
        return await _run(f"ps -p {target} -o pid,ppid,user,%mem,%cpu,stat,start,cmd --no-headers 2>/dev/null") if target else {"error": "target required"}
    return {"error": f"Unknown: {action}", "available": ["top","cpu_top","find","info","tree"]}

# === 7. NETWORK DIAGNOSTICS ===
async def handle_network_diagnostics(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action", "overview")
    target = params.get("target", "")
    cmds = {
        "overview": "ip -br addr && echo --- && ip route | head -5 && echo --- && cat /etc/resolv.conf | grep nameserver",
        "interfaces": "ip -br addr", "routes": "ip route show",
        "connections": "ss -tulnp 2>/dev/null | head -30", "ports": "ss -tlnp 2>/dev/null | head -30",
        "dns": "cat /etc/resolv.conf && echo --- && resolvectl status 2>/dev/null | head -20",
        "wireguard": "sudo wg show 2>/dev/null || echo 'WireGuard not active'",
    }
    if action in cmds:
        return await _run(cmds[action]) if action != "wireguard" else await _run_sudo("wg show 2>/dev/null || echo 'WireGuard not active'")
    if action == "ping":
        return await _run(f"ping -c 3 -W 2 '{target}' 2>&1", timeout=10) if target else {"error": "target required"}
    if action == "resolve":
        return await _run(f"dig +short '{target}' 2>&1") if target else {"error": "target required"}
    return {"error": f"Unknown: {action}", "available": ["overview","interfaces","routes","connections","dns","ports","ping","resolve","wireguard"]}

# === 8. LOG VIEWER ===
async def handle_log_viewer(params: Dict[str, Any]) -> Dict[str, Any]:
    source = params.get("source", "system").lower()
    lines = min(params.get("lines", 50), 200)
    prio = params.get("priority", "")
    unit = params.get("unit", "")
    cmds = {
        "system": f"journalctl --no-pager -n {lines}" + (f" -p {prio}" if prio else "") + (f" -u {unit}" if unit else ""),
        "triforce": f"tail -n {lines} /home/zombie/triforce/logs/unified.log 2>/dev/null",
        "errors": f"journalctl --no-pager -n {lines} -p err 2>&1",
        "auth": f"journalctl --no-pager -n {lines} -t sshd -t sudo 2>&1",
        "docker": f"journalctl --no-pager -n {lines} -u docker 2>&1",
        "kernel": f"dmesg | tail -n {lines}",
        "nginx": f"tail -n {lines} /var/log/nginx/access.log 2>/dev/null || tail -n {lines} /var/log/apache2/access.log 2>/dev/null || echo 'No web logs'",
    }
    if source not in cmds:
        return {"error": f"Unknown: {source}", "available": list(cmds.keys())}
    return await _run(cmds[source])

# === TOOL DEFINITIONS ===
ADMIN_OPS_TOOLS = [
    {"name": "system_info", "description": "Query system information: kernel, memory, disk, CPU, network, OS, uptime.",
     "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "enum": ["overview","kernel","memory","disk","cpu","network","uptime","os"]}}}},
    {"name": "package_manager", "description": "Manage system packages: refresh cache, list upgradable, search, install, upgrade.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["refresh","status","list_upgradable","search","info","install","upgrade"]}, "target": {"type": "string", "description": "Package name"}}, "required": ["action"]}},
    {"name": "service_control", "description": "Manage system services: list, status, start, stop, restart, enable, disable, view journal.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["list","list_all","status","start","stop","restart","enable","disable","journal"]}, "target": {"type": "string", "description": "Service name"}, "lines": {"type": "integer"}}, "required": ["action"]}},
    {"name": "container_ops", "description": "Manage Docker containers: list, start, stop, restart, logs, inspect, images, resource stats.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["list","list_all","status","logs","start","stop","restart","images","stats"]}, "target": {"type": "string", "description": "Container name"}, "lines": {"type": "integer"}}, "required": ["action"]}},
    {"name": "file_ops", "description": "File operations: read, write, append, list directory, search content, stat. Path-restricted.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["read","tail","write","append","list","stat","search","tree"]}, "path": {"type": "string"}, "content": {"type": "string"}, "query": {"type": "string"}, "lines": {"type": "integer"}, "scope": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["action"]}},
    {"name": "process_monitor", "description": "Monitor processes: top by memory/CPU, find by name, details by PID, process tree.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["top","cpu_top","find","info","tree"]}, "target": {"type": "string"}, "count": {"type": "integer"}}, "required": ["action"]}},
    {"name": "network_diagnostics", "description": "Network diagnostics: interfaces, routes, connections, DNS, ports, ping, resolve, WireGuard.",
     "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["overview","interfaces","routes","connections","dns","ports","ping","resolve","wireguard"]}, "target": {"type": "string"}}, "required": ["action"]}},
    {"name": "log_viewer", "description": "View logs: system journal, triforce, errors, auth, docker, kernel. Filter by priority and unit.",
     "inputSchema": {"type": "object", "properties": {"source": {"type": "string", "enum": ["system","triforce","errors","auth","docker","kernel","nginx"]}, "lines": {"type": "integer"}, "priority": {"type": "string", "enum": ["emerg","alert","crit","err","warning","notice","info","debug"]}, "unit": {"type": "string"}}, "required": ["source"]}},
]

ADMIN_OPS_HANDLERS = {
    "system_info": handle_system_info, "package_manager": handle_package_manager,
    "service_control": handle_service_control, "container_ops": handle_container_ops,
    "file_ops": handle_file_ops, "process_monitor": handle_process_monitor,
    "network_diagnostics": handle_network_diagnostics, "log_viewer": handle_log_viewer,
}
