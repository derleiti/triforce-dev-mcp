"""
MCP Tools for Federation Vault Management
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Tool definitions for MCP
FEDERATION_TOOLS = [
    {
        "name": "federation_nodes",
        "description": "List registered federation nodes with status",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "federation_register",
        "description": "Register a new federation node. Returns auth token (shown once!)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Unique node identifier"},
                "role": {"type": "string", "enum": ["hub", "node"], "default": "node"}
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "federation_revoke",
        "description": "Revoke a node's access to the federation",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node to revoke"}
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "federation_rotate",
        "description": "Rotate a node's auth token. Returns new token.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node to rotate token for"}
            },
            "required": ["node_id"]
        }
    },
    {
        "name": "federation_verify",
        "description": "Verify a node's auth token",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Node ID"},
                "token": {"type": "string", "description": "Auth token to verify"}
            },
            "required": ["node_id", "token"]
        }
    }
]


async def handle_federation_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle federation vault tool calls"""
    try:
        # Import here to avoid circular imports
        from app.services.federation_vault import get_federation_vault
        vault = get_federation_vault()
        
        if name == "federation_nodes":
            nodes = vault.list_nodes()
            return {
                "nodes": nodes,
                "count": len(nodes),
                "active_count": sum(1 for n in nodes if n["active"])
            }
        
        elif name == "federation_register":
            node_id = args.get("node_id")
            role = args.get("role", "node")
            
            if not node_id:
                return {"error": "node_id required"}
            
            if vault.get_node(node_id):
                return {"error": f"Node {node_id} already registered"}
            
            token = vault.register_node(node_id, role)
            return {
                "success": True,
                "node_id": node_id,
                "role": role,
                "token": token,
                "note": "Save this token! It will not be shown again.",
                "env_line": f"FEDERATION_TOKEN={token}"
            }
        
        elif name == "federation_revoke":
            node_id = args.get("node_id")
            if vault.revoke_node(node_id):
                return {"success": True, "node_id": node_id, "status": "revoked"}
            return {"error": f"Node {node_id} not found"}
        
        elif name == "federation_rotate":
            node_id = args.get("node_id")
            token = vault.rotate_token(node_id)
            if token:
                return {
                    "success": True,
                    "node_id": node_id,
                    "new_token": token,
                    "note": "Update the node's FEDERATION_TOKEN env var",
                    "env_line": f"FEDERATION_TOKEN={token}"
                }
            return {"error": f"Node {node_id} not found"}
        
        elif name == "federation_verify":
            node_id = args.get("node_id")
            token = args.get("token")
            valid = vault.verify_token(node_id, token)
            return {
                "valid": valid,
                "node_id": node_id
            }
        
        else:
            return {"error": f"Unknown tool: {name}"}
            
    except Exception as e:
        logger.error(f"Federation tool error: {e}")
        return {"error": str(e)}


def get_federation_tools() -> List[Dict]:
    """Return tool definitions for registration"""
    return FEDERATION_TOOLS


# Additional tool for node updates
FEDERATION_TOOLS.append({
    "name": "federation_update_nodes",
    "description": "Sync code from hub to all federation nodes and restart services",
    "inputSchema": {
        "type": "object",
        "properties": {
            "restart": {"type": "boolean", "default": True, "description": "Restart services after sync"}
        }
    }
})

async def handle_node_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute node update via script"""
    import subprocess
    try:
        result = subprocess.run(
            [str(Path(__file__).resolve().parents[2] / "scripts" / "update-nodes.sh")],
            capture_output=True,
            text=True,
            timeout=120
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout[-2000:] if result.stdout else "",
            "errors": result.stderr[-500:] if result.stderr else ""
        }
    except Exception as e:
        return {"error": str(e)}
