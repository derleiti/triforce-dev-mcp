"""
MCP Tool Builder v1.0
======================
Meta-Tool: Erstellt, editiert und optimiert andere MCP-Tools zur Laufzeit.
Mit persistentem Evolution-Log fuer KI-Lernfaehigkeit ueber Sessions hinweg.

Actions:
  create   - Neues MCP-Tool aus Beschreibung generieren
  edit     - Bestehendes Tool-File editieren
  optimize - Tool analysieren + verbesserte Version deployen
  delete   - Tool aus user_tools/ entfernen
  list     - Alle verfuegbaren Tools mit Status
  log      - Evolution-Log anzeigen/durchsuchen
  deploy   - Explizit deployen + warten bis Backend oben
"""

import os
import re
import ast
import json
import time
import hashlib
import datetime
import subprocess
import importlib.util
import logging
from pathlib import Path

from app.paths import PROJECT_ROOT as TRIFORCE_PROJECT_ROOT, STATE_DIR
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ailinux.mcp.tool_builder")

PROJECT_ROOT   = TRIFORCE_PROJECT_ROOT
USER_TOOLS_DIR = PROJECT_ROOT / "app" / "mcp" / "user_tools"
EVOLUTION_LOG  = PROJECT_ROOT / "triforce" / "memory" / "mcp_evolution.jsonl"
VENV_PYTHON    = PROJECT_ROOT / ".venv" / "bin" / "python"

# Sicherstellen dass Verzeichnisse existieren
USER_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
EVOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Evolution Log — persistent KI-Gedaechtnis ueber Sessions hinweg
# =============================================================================

def _evo_write(event_type: str, data: Dict) -> None:
    """Schreibt einen Eintrag in den Evolution-Log."""
    entry = {
        "ts": datetime.datetime.now().isoformat(),
        "type": event_type,
        **data,
    }
    try:
        with open(EVOLUTION_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"evo_write failed: {e}")


def _evo_read(limit: int = 50, filter_type: str = None,
              filter_tool: str = None) -> List[Dict]:
    """Liest letzte N Eintraege aus dem Evolution-Log."""
    if not EVOLUTION_LOG.exists():
        return []
    entries = []
    try:
        lines = EVOLUTION_LOG.read_text(errors='replace').splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if filter_type and e.get("type") != filter_type:
                    continue
                if filter_tool and e.get("tool") != filter_tool:
                    continue
                entries.append(e)
                if len(entries) >= limit:
                    break
            except Exception:
                continue
    except Exception:
        pass
    return entries


def _evo_summary_for_tool(tool_name: str) -> Dict:
    """Kompakte Zusammenfassung der Evolution eines Tools."""
    entries = _evo_read(limit=200, filter_tool=tool_name)
    if not entries:
        return {"tool": tool_name, "history": [], "generations": 0}

    creates   = [e for e in entries if e["type"] == "create"]
    optimizes = [e for e in entries if e["type"] == "optimize"]
    deploys   = [e for e in entries if e["type"] == "deploy"]
    errors    = [e for e in entries if e["type"] == "error" and e.get("tool") == tool_name]

    return {
        "tool": tool_name,
        "generations": len(creates) + len(optimizes),
        "total_deploys": len(deploys),
        "total_errors": len(errors),
        "last_action": entries[0].get("type") if entries else None,
        "last_ts": entries[0].get("ts") if entries else None,
        "last_notes": entries[0].get("notes", "") if entries else "",
        "optimization_history": [
            {"ts": e["ts"], "reason": e.get("reason", ""), "result": e.get("result", "")}
            for e in optimizes[:5]
        ],
    }


# =============================================================================
# Code-Generierung
# =============================================================================

HANDLER_TEMPLATE = '''"""
MCP Tool: {tool_name}
Generated: {timestamp}
Description: {description}
"""

import logging
from typing import Any, Dict

logger = logging.getLogger("ailinux.mcp.user_tools.{tool_name}")


async def handle_{tool_name}(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    {description}
    """
    # --- IMPLEMENTATION ---
{implementation}
    # --- END ---


# Tool-Schema fuer MCP-Registry
TOOL_SCHEMA = {{
    "name": "{tool_name}",
    "description": "{description}",
    "inputSchema": {{
        "type": "object",
        "properties": {schema_properties},
        "required": {required_fields},
    }},
}}

# Handler-Map — wird von Auto-Discovery geladen
HANDLERS = {{
    "{tool_name}": handle_{tool_name},
}}
'''


def _generate_implementation(description: str, params_hint: Dict) -> str:
    """Generiert Minimal-Implementierung basierend auf Beschreibung."""
    lines = []
    lines.append(f'    # Auto-generated from: {description[:60]}')
    lines.append( '    import subprocess, json')
    lines.append( '    result = {}')
    lines.append( '    ')

    # Erkenne Muster in der Beschreibung
    desc_lower = description.lower()
    if any(k in desc_lower for k in ["shell", "command", "exec", "run"]):
        lines.append('    cmd = params.get("command", "")')
        lines.append('    if not cmd:')
        lines.append('        return {"error": "command parameter required"}')
        lines.append('    r = subprocess.run(cmd, shell=True, capture_output=True,')
        lines.append('                       text=True, timeout=30)')
        lines.append('    result = {"stdout": r.stdout, "stderr": r.stderr,')
        lines.append('              "exit_code": r.returncode, "success": r.returncode == 0}')
    elif any(k in desc_lower for k in ["file", "read", "write", "path"]):
        lines.append('    from pathlib import Path')
        lines.append('    path = params.get("path", "")')
        lines.append('    if not path:')
        lines.append('        return {"error": "path parameter required"}')
        lines.append('    p = Path(path)')
        lines.append('    if not p.exists():')
        lines.append('        return {"error": f"Path not found: {path}"}')
        lines.append('    result = {"path": str(p), "exists": True,')
        lines.append('              "size": p.stat().st_size if p.is_file() else None}')
    elif any(k in desc_lower for k in ["api", "http", "request", "fetch", "url"]):
        lines.append('    import urllib.request')
        lines.append('    url = params.get("url", "")')
        lines.append('    if not url:')
        lines.append('        return {"error": "url parameter required"}')
        lines.append('    try:')
        lines.append('        with urllib.request.urlopen(url, timeout=10) as resp:')
        lines.append('            result = {"url": url, "status": resp.status,')
        lines.append('                      "content_type": resp.headers.get("Content-Type", "")}')
        lines.append('    except Exception as e:')
        lines.append('        result = {"url": url, "error": str(e)}')
    else:
        # Generic
        lines.append('    input_data = params.get("input", "")')
        lines.append('    result = {')
        lines.append('        "status": "ok",')
        lines.append('        "tool": logger.name.split(".")[-1],')
        lines.append('        "input": input_data,')
        lines.append('        "note": "Implement me in app/mcp/user_tools/",')
        lines.append('    }')

    lines.append('    return result')
    return "\n".join(lines)


def _build_tool_code(tool_name: str, description: str,
                     params: Dict = None, implementation: str = None) -> str:
    """Erstellt vollstaendigen Tool-Code."""
    params = params or {}
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    if not implementation:
        implementation = _generate_implementation(description, params)

    schema_props = json.dumps(params or {
        "input": {"type": "string", "description": "Input data"}
    }, indent=8)
    required = json.dumps(list(params.keys()) if params else [])

    return HANDLER_TEMPLATE.format(
        tool_name=tool_name,
        timestamp=ts,
        description=description,
        implementation=implementation,
        schema_properties=schema_props,
        required_fields=required,
    )


# =============================================================================
# Syntax-Validierung
# =============================================================================

def _validate_python(code: str) -> Dict[str, Any]:
    """Prueft Python-Syntax und Pflichtfelder."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError line {e.lineno}: {e.msg}"}

    # Pflichtfelder pruefen
    if "HANDLERS" not in code:
        return {"ok": False, "error": "HANDLERS dict fehlt im Code"}
    if "handle_" not in code:
        return {"ok": False, "error": "Kein handle_* Handler gefunden"}

    return {"ok": True}


# =============================================================================
# Backend-Restart mit Health-Polling
# =============================================================================

def _restart_and_wait(timeout: int = 45) -> Dict[str, Any]:
    """Restart TriForce-Backend, pollt bis healthy."""
    t0 = time.time()

    # Restart triggern (sudo required for systemctl)
    try:
        r = subprocess.run(
            ["sudo", "systemctl", "restart", "triforce"],
            timeout=15, capture_output=True
        )
        if r.returncode != 0:
            raise RuntimeError(f"systemctl exit {r.returncode}: {r.stderr.decode()[:100]}")
    except Exception as e:
        logger.warning(f"systemctl restart failed: {e}, trying script fallback")
        # Fallback: start-triforce.sh direkt
        subprocess.Popen(
            ["sudo", "systemctl", "restart", "triforce"],
            start_new_session=True
        )

    # Polling bis healthy
    last_code = None
    attempts = 0
    while time.time() - t0 < timeout:
        time.sleep(2)
        attempts += 1
        try:
            r = subprocess.run(
                "curl -s -o /dev/null -w '%{http_code}' "
                "http://localhost:9000/health --max-time 3",
                shell=True, capture_output=True, text=True, timeout=6
            )
            code = r.stdout.strip()
            last_code = code
            if code in ("200", "204"):
                elapsed = round(time.time() - t0, 1)
                return {
                    "ok": True,
                    "elapsed_s": elapsed,
                    "attempts": attempts,
                    "http_code": code,
                }
        except Exception:
            pass

    return {
        "ok": False,
        "elapsed_s": round(time.time() - t0, 1),
        "attempts": attempts,
        "last_http_code": last_code,
        "error": f"Backend nicht bereit nach {timeout}s",
    }


# =============================================================================
# Auto-Discovery — laedt alle HANDLERS aus user_tools/
# =============================================================================

def load_user_tools() -> Dict[str, Any]:
    """
    Laedt alle *.py Dateien aus user_tools/ und gibt zusammengefuehrte
    HANDLERS-Dict zurueck. Wird beim MCP-Start aufgerufen.
    """
    all_handlers = {}
    errors = []

    for py_file in sorted(USER_TOOLS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"user_tools.{py_file.stem}", str(py_file)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            handlers = getattr(mod, "HANDLERS", {})
            all_handlers.update(handlers)
            logger.info(f"user_tools: loaded {py_file.name} ({list(handlers.keys())})")
        except Exception as e:
            errors.append({"file": py_file.name, "error": str(e)})
            logger.warning(f"user_tools: failed {py_file.name}: {e}")

    return {"handlers": all_handlers, "errors": errors,
            "loaded": len(all_handlers), "files": len(list(USER_TOOLS_DIR.glob("*.py")))}


def get_user_tool_schemas() -> List[Dict]:
    """Gibt alle TOOL_SCHEMA aus user_tools/ zurueck."""
    schemas = []
    for py_file in sorted(USER_TOOLS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"user_tools_schema.{py_file.stem}", str(py_file)
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            schema = getattr(mod, "TOOL_SCHEMA", None)
            if schema:
                schemas.append(schema)
        except Exception:
            pass
    return schemas


# =============================================================================
# Action: list
# =============================================================================

async def _action_list(params: Dict) -> Dict:
    """Listet alle MCP-Tools auf."""
    # User-Tools
    user_tools = []
    for py_file in sorted(USER_TOOLS_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        size = py_file.stat().st_size
        mtime = datetime.datetime.fromtimestamp(py_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        # Handler-Namen lesen
        try:
            content = py_file.read_text(errors='replace')
            handlers = re.findall(r'"(\w+)":\s*handle_\w+', content)
        except Exception:
            handlers = []
        evo = _evo_summary_for_tool(py_file.stem)
        user_tools.append({
            "file": py_file.name,
            "handlers": handlers,
            "size_kb": round(size / 1024, 1),
            "modified": mtime,
            "generations": evo.get("generations", 0),
            "last_action": evo.get("last_action"),
        })

    # Core-Tools summary
    core_count = 0
    try:
        from app.mcp.tool_registry_v5 import get_all_tools
        core_count = len(get_all_tools())
    except Exception:
        pass

    return {
        "user_tools": user_tools,
        "user_tool_count": len(user_tools),
        "core_tool_count": core_count,
        "user_tools_dir": str(USER_TOOLS_DIR),
        "evolution_log": str(EVOLUTION_LOG),
        "evolution_entries": sum(1 for _ in EVOLUTION_LOG.open()) if EVOLUTION_LOG.exists() else 0,
    }


# =============================================================================
# Action: create
# =============================================================================

async def _action_create(params: Dict) -> Dict:
    """Erstellt ein neues MCP-Tool."""
    tool_name = params.get("tool_name", "").strip().lower().replace("-", "_")
    description = params.get("description", "")
    implementation = params.get("implementation", "")  # Optional: fertiger Code
    tool_params = params.get("params", {})
    auto_deploy = params.get("auto_deploy", True)

    if not tool_name:
        return {"error": "tool_name required"}
    if not description:
        return {"error": "description required"}
    if not re.match(r'^[a-z][a-z0-9_]*$', tool_name):
        return {"error": f"tool_name muss snake_case sein: '{tool_name}'"}

    target = USER_TOOLS_DIR / f"{tool_name}.py"
    if target.exists() and not params.get("overwrite"):
        return {"error": f"Tool '{tool_name}' existiert bereits. overwrite=true zum Ueberschreiben."}

    # Code generieren oder verwenden
    if not implementation:
        code = _build_tool_code(tool_name, description, tool_params)
    else:
        # Custom implementation in Template einbauen
        code = _build_tool_code(tool_name, description, tool_params, implementation)

    # Syntax-Check
    validation = _validate_python(code)
    if not validation["ok"]:
        return {"error": f"Syntax-Fehler im generierten Code: {validation['error']}",
                "code_preview": code[:500]}

    # Schreiben
    target.write_text(code)

    _evo_write("create", {
        "tool": tool_name,
        "file": str(target),
        "description": description,
        "lines": len(code.splitlines()),
        "notes": f"Erstellt mit {len(tool_params)} Params",
    })

    result = {
        "status": "created",
        "tool": tool_name,
        "file": str(target),
        "lines": len(code.splitlines()),
        "code_preview": code[:600],
    }

    if auto_deploy:
        restart_result = _restart_and_wait()
        result["deploy"] = restart_result
        _evo_write("deploy", {
            "tool": tool_name,
            "trigger": "create",
            "result": "ok" if restart_result["ok"] else "timeout",
            "elapsed_s": restart_result.get("elapsed_s"),
        })

    return result


# =============================================================================
# Action: edit
# =============================================================================

async def _action_edit(params: Dict) -> Dict:
    """Editiert ein bestehendes Tool-File."""
    tool_name = params.get("tool_name", "").strip()
    new_code = params.get("code", "")
    patch = params.get("patch", "")  # Alternativ: find+replace patch
    auto_deploy = params.get("auto_deploy", True)

    if not tool_name:
        return {"error": "tool_name required"}

    target = USER_TOOLS_DIR / f"{tool_name}.py"
    if not target.exists():
        # Auch in core MCP suchen
        core_candidates = list(PROJECT_ROOT.rglob(f"app/mcp/{tool_name}.py"))
        if core_candidates:
            target = core_candidates[0]
        else:
            return {"error": f"Tool-File '{tool_name}.py' nicht gefunden"}

    # Backup
    backup = str(target) + f".bak.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    target_backup = Path(backup)
    target_backup.write_text(target.read_text(errors='replace'))

    if new_code:
        # Kompletter Ersatz
        validation = _validate_python(new_code)
        if not validation["ok"]:
            return {"error": f"Syntax-Fehler: {validation['error']}"}
        old_lines = len(target.read_text().splitlines())
        target.write_text(new_code)
        new_lines = len(new_code.splitlines())
    elif patch:
        # Patch: "OLD_STRING|||NEW_STRING" Format
        if "|||" not in patch:
            return {"error": "patch Format: 'alter_text|||neuer_text'"}
        old_str, new_str = patch.split("|||", 1)
        content = target.read_text(errors='replace')
        if old_str not in content:
            return {"error": f"Patch-String nicht gefunden: '{old_str[:60]}'"}
        new_content = content.replace(old_str, new_str, 1)
        validation = _validate_python(new_content)
        if not validation["ok"]:
            return {"error": f"Syntax-Fehler nach Patch: {validation['error']}"}
        old_lines = len(content.splitlines())
        target.write_text(new_content)
        new_code = new_content
        new_lines = len(new_content.splitlines())
    else:
        return {"error": "code oder patch required"}

    _evo_write("edit", {
        "tool": tool_name,
        "file": str(target),
        "backup": backup,
        "lines_before": old_lines,
        "lines_after": new_lines,
        "delta": new_lines - old_lines,
    })

    result = {
        "status": "edited",
        "tool": tool_name,
        "file": str(target),
        "backup": backup,
        "lines_before": old_lines,
        "lines_after": new_lines,
    }

    if auto_deploy:
        restart_result = _restart_and_wait()
        result["deploy"] = restart_result
        _evo_write("deploy", {
            "tool": tool_name,
            "trigger": "edit",
            "result": "ok" if restart_result["ok"] else "timeout",
        })

    return result


# =============================================================================
# Action: optimize
# =============================================================================

async def _action_optimize(params: Dict) -> Dict:
    """
    Analysiert ein Tool, liest seinen Evolutions-Log,
    generiert eine optimierte Version und deployed sie.
    """
    tool_name = params.get("tool_name", "").strip()
    reason = params.get("reason", "general optimization")
    auto_deploy = params.get("auto_deploy", True)
    new_code = params.get("optimized_code", "")  # Optionaler fertiger Code

    if not tool_name:
        return {"error": "tool_name required"}

    # Tool-File finden
    target = USER_TOOLS_DIR / f"{tool_name}.py"
    if not target.exists():
        core_candidates = list((PROJECT_ROOT / "app" / "mcp").glob(f"{tool_name}.py"))
        if core_candidates:
            target = core_candidates[0]
        else:
            return {"error": f"Tool '{tool_name}.py' nicht gefunden"}

    current_code = target.read_text(errors='replace')
    evo_summary = _evo_summary_for_tool(tool_name)
    recent_entries = _evo_read(limit=10, filter_tool=tool_name)

    # Analyse-Report generieren
    analysis = {
        "tool": tool_name,
        "file": str(target),
        "current_lines": len(current_code.splitlines()),
        "current_size_kb": round(len(current_code) / 1024, 2),
        "generations": evo_summary.get("generations", 0),
        "total_errors": evo_summary.get("total_errors", 0),
        "last_action": evo_summary.get("last_action"),
        "recent_history": recent_entries[:5],
        "reason": reason,
    }

    # Wenn kein fertiger Code geliefert: Code-Qualitaets-Check
    quality_notes = []
    try:
        tree = ast.parse(current_code)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in functions:
            fn_lines = (getattr(fn, 'end_lineno', 0) or 0) - fn.lineno
            if fn_lines > 60:
                quality_notes.append(f"Function '{fn.name}' ist {fn_lines} Zeilen -- aufsplitten?")
            if len(fn.args.args) > 7:
                quality_notes.append(f"Function '{fn.name}' hat {len(fn.args.args)} Args -- zu viele")
    except Exception:
        pass

    analysis["quality_notes"] = quality_notes

    if not new_code:
        return {
            "status": "analysis_ready",
            "analysis": analysis,
            "current_code": current_code,
            "instruction": (
                "Liefere optimierten Code als 'optimized_code' Parameter. "
                "Evolution-History und Analyse oben zeigen wo Verbesserungsbedarf ist."
            ),
        }

    # Fertigen Code deployen
    validation = _validate_python(new_code)
    if not validation["ok"]:
        return {"error": f"Syntax-Fehler im optimierten Code: {validation['error']}"}

    # Backup + Deploy
    backup = str(target) + f".bak.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    Path(backup).write_text(current_code)
    target.write_text(new_code)

    old_lines = len(current_code.splitlines())
    new_lines = len(new_code.splitlines())

    _evo_write("optimize", {
        "tool": tool_name,
        "file": str(target),
        "backup": backup,
        "reason": reason,
        "lines_before": old_lines,
        "lines_after": new_lines,
        "delta": new_lines - old_lines,
        "quality_notes_resolved": quality_notes,
        "result": "deployed" if auto_deploy else "written",
    })

    result = {
        "status": "optimized",
        "tool": tool_name,
        "reason": reason,
        "lines_before": old_lines,
        "lines_after": new_lines,
        "delta": f"{new_lines - old_lines:+d} Zeilen",
        "backup": backup,
        "analysis": analysis,
    }

    if auto_deploy:
        restart_result = _restart_and_wait()
        result["deploy"] = restart_result
        _evo_write("deploy", {
            "tool": tool_name,
            "trigger": "optimize",
            "result": "ok" if restart_result["ok"] else "timeout",
            "elapsed_s": restart_result.get("elapsed_s"),
        })

    return result


# =============================================================================
# Action: delete
# =============================================================================

async def _action_delete(params: Dict) -> Dict:
    """Loescht ein User-Tool."""
    tool_name = params.get("tool_name", "").strip()
    if not tool_name:
        return {"error": "tool_name required"}

    target = USER_TOOLS_DIR / f"{tool_name}.py"
    if not target.exists():
        return {"error": f"Tool '{tool_name}.py' nicht in user_tools/ gefunden"}

    # Backup statt hartem Delete
    backup = PROJECT_ROOT / ".backups" / f"user_tool_{tool_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.py.bak"
    backup.parent.mkdir(exist_ok=True)
    backup.write_text(target.read_text(errors='replace'))
    target.unlink()

    _evo_write("delete", {
        "tool": tool_name,
        "backup": str(backup),
    })

    restart_result = _restart_and_wait()
    return {
        "status": "deleted",
        "tool": tool_name,
        "backup": str(backup),
        "deploy": restart_result,
    }


# =============================================================================
# Action: log
# =============================================================================

async def _action_log(params: Dict) -> Dict:
    """Zeigt Evolution-Log an."""
    limit = params.get("limit", 30)
    filter_type = params.get("filter_type")
    filter_tool = params.get("filter_tool")
    stats = params.get("stats", False)

    entries = _evo_read(limit=limit, filter_type=filter_type, filter_tool=filter_tool)

    if stats:
        type_counts: Dict[str, int] = {}
        tool_counts: Dict[str, int] = {}
        for e in _evo_read(limit=1000):
            type_counts[e.get("type", "?")] = type_counts.get(e.get("type", "?"), 0) + 1
            tool = e.get("tool")
            if tool:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
        return {
            "total_entries": sum(1 for _ in open(EVOLUTION_LOG)) if EVOLUTION_LOG.exists() else 0,
            "by_type": dict(sorted(type_counts.items(), key=lambda x: -x[1])),
            "by_tool": dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:20]),
            "log_size_kb": round(EVOLUTION_LOG.stat().st_size / 1024, 1) if EVOLUTION_LOG.exists() else 0,
        }

    return {
        "entries": entries,
        "count": len(entries),
        "log_file": str(EVOLUTION_LOG),
    }


# =============================================================================
# Action: deploy (explizit)
# =============================================================================

async def _action_deploy(params: Dict) -> Dict:
    """Expliziter Restart + Warten."""
    reason = params.get("reason", "manual deploy")
    result = _restart_and_wait()
    _evo_write("deploy", {
        "tool": "_manual",
        "trigger": reason,
        "result": "ok" if result["ok"] else "timeout",
        "elapsed_s": result.get("elapsed_s"),
    })
    return {"status": "deployed" if result["ok"] else "failed", **result}


# =============================================================================
# Main Handler
# =============================================================================

async def handle_mcp_tool_builder(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Meta-Tool: Erstellt, editiert und optimiert MCP-Tools zur Laufzeit.
    Mit Evolution-Log fuer KI-Lernfaehigkeit ueber mehrere Sessions.

    action: create | edit | optimize | delete | list | log | deploy
    """
    action = params.get("action", "list")

    dispatch = {
        "list":     _action_list,
        "create":   _action_create,
        "edit":     _action_edit,
        "optimize": _action_optimize,
        "delete":   _action_delete,
        "log":      _action_log,
        "deploy":   _action_deploy,
    }

    handler_fn = dispatch.get(action)
    if not handler_fn:
        return {
            "error": f"Unbekannte action '{action}'",
            "valid_actions": list(dispatch.keys()),
        }

    try:
        result = await handler_fn(params)
        # Jede erfolgreiche Nutzung loggen (ausser list/log selbst)
        if action not in ("list", "log") and "error" not in result:
            _evo_write("use", {
                "tool": params.get("tool_name", "_"),
                "action": action,
                "success": True,
            })
        return result
    except Exception as e:
        _evo_write("error", {
            "tool": params.get("tool_name", "_"),
            "action": action,
            "error": str(e)[:200],
        })
        logger.exception(f"mcp_tool_builder action={action} failed")
        return {"error": str(e), "action": action}


# =============================================================================
# Handler-Registry
# =============================================================================

TOOL_BUILDER_HANDLERS = {
    "mcp_tool_builder": handle_mcp_tool_builder,
}
