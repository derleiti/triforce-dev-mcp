"""
WordPress MCP Handlers — Full CMS Control via MCP
Tools: wp_publish_post, wp_list_posts, wp_update_post, wp_create_page,
       wp_delete_post, wp_multi_ai_post
"""
from __future__ import annotations
import json, base64, os
from typing import Any, Dict

# Default featured image: Brumo datacenter (Media ID 97318)
DEFAULT_FEATURED_MEDIA = 97318

# Internal WordPress API via curl (bypasses Cloudflare)
async def _wp_api(method: str, endpoint: str, data: dict = None) -> dict:
    """Call WordPress REST API via local HTTPS using httpx."""
    import httpx

    user = os.getenv("WORDPRESS_APP_USER", os.getenv("WORDPRESS_USER", "ailinux-nova-ai"))
    passwd = os.getenv("WORDPRESS_APP_PASSWORD", os.getenv("WORDPRESS_PASSWORD", ""))
    url = f"https://ailinux.me/wp-json/wp/v2/{endpoint}"
    headers = {
        "Host": "ailinux.me",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(verify=True, timeout=30.0) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                auth=(user, passwd),
                json=data if data else None,
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}


async def handle_wp_publish_post(arguments: Dict[str, Any]) -> str:
    """Create and publish a WordPress post."""
    title = arguments.get("title", "")
    content = arguments.get("content", "")
    status = arguments.get("status", "publish")
    excerpt = arguments.get("excerpt", "")
    category = arguments.get("category", "")
    author_name = arguments.get("author_name", "")
    
    featured = arguments.get("featured_media", DEFAULT_FEATURED_MEDIA)
    data = {"title": title, "content": content, "status": status, "featured_media": featured}
    if excerpt:
        data["excerpt"] = excerpt
    
    result = await _wp_api("POST", "posts", data)
    
    if "id" in result:
        return json.dumps({
            "success": True,
            "post_id": result["id"],
            "status": result.get("status"),
            "link": result.get("link"),
            "title": result.get("title", {}).get("rendered", title)
        })
    return json.dumps({"success": False, "error": result})


async def handle_wp_list_posts(arguments: Dict[str, Any]) -> str:
    """List WordPress posts."""
    status = arguments.get("status", "publish")
    per_page = arguments.get("per_page", 10)
    
    result = await _wp_api("GET", f"posts?status={status}&per_page={per_page}")
    
    if isinstance(result, list):
        posts = [{"id": p["id"], "title": p.get("title", {}).get("rendered", ""), 
                  "status": p.get("status"), "link": p.get("link"),
                  "date": p.get("date")} for p in result]
        return json.dumps({"posts": posts, "count": len(posts)})
    return json.dumps({"error": result})


async def handle_wp_update_post(arguments: Dict[str, Any]) -> str:
    """Update an existing WordPress post."""
    post_id = arguments.get("post_id")
    if not post_id:
        return json.dumps({"error": "post_id required"})
    
    data = {}
    for field in ["title", "content", "status", "excerpt"]:
        if field in arguments:
            data[field] = arguments[field]
    
    result = await _wp_api("POST", f"posts/{post_id}", data)
    
    if "id" in result:
        return json.dumps({"success": True, "post_id": result["id"], 
                          "status": result.get("status"), "link": result.get("link")})
    return json.dumps({"success": False, "error": result})


async def handle_wp_delete_post(arguments: Dict[str, Any]) -> str:
    """Delete a WordPress post."""
    post_id = arguments.get("post_id")
    if not post_id:
        return json.dumps({"error": "post_id required"})
    
    result = await _wp_api("DELETE", f"posts/{post_id}?force=true")
    success = False
    if isinstance(result, dict):
        success = bool(result.get("deleted"))
        if not success and result.get("id") is not None:
            success = str(result.get("id")) == str(post_id)
        if not success and isinstance(result.get("previous"), dict):
            success = str(result["previous"].get("id")) == str(post_id)
    return json.dumps({"success": success, "deleted_id": post_id})


async def handle_wp_create_page(arguments: Dict[str, Any]) -> str:
    """Create a WordPress page (not post)."""
    title = arguments.get("title", "")
    content = arguments.get("content", "")
    status = arguments.get("status", "publish")
    
    data = {"title": title, "content": content, "status": status}
    
    result = await _wp_api("POST", "pages", data)
    
    if "id" in result:
        return json.dumps({"success": True, "page_id": result["id"],
                          "link": result.get("link")})
    return json.dumps({"success": False, "error": result})


async def handle_wp_multi_ai_post(arguments: Dict[str, Any]) -> str:
    """Create multiple AI opinion posts — each provider writes their take.
    Uses the Swarm or Group Chat system to get responses from different AIs,
    then publishes individual posts named by provider."""
    topic = arguments.get("topic", "")
    providers = arguments.get("providers", ["claude", "chatgpt", "gemini", "mistral"])
    publish = arguments.get("publish", True)
    
    if not topic:
        return json.dumps({"error": "topic required"})
    
    # Use TriForce internal API to get responses from different providers
    import asyncio
    results = []
    
    provider_models = {
        "claude": "anthropic/claude-sonnet-4-20250514",
        "chatgpt": "openrouter/openai/gpt-4o",
        "gemini": "gemini/gemini-2.5-flash",
        "mistral": "mistral/mistral-small-latest",
        "llama": "groq/llama-3.3-70b-versatile",
        "deepseek": "openrouter/deepseek/deepseek-chat",
        "kimi": "groq/moonshotai/kimi-k2",
        "qwen": "openrouter/qwen/qwen3-235b-a22b"
    }
    
    prompt = f"""Write a blog post about: {topic}
Write 300-500 words. Be opinionated. Share your unique perspective.
Format in HTML with <h2>, <p>, <ul> tags. No intro like "As an AI..."."""
    
    for provider in providers:
        model = provider_models.get(provider, f"openrouter/{provider}")
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "-X", "POST", "http://localhost:9000/v1/chat",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500
                }),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            raw = stdout.decode().strip()
            
            # TriForce /v1/chat returns raw text, not JSON
            try:
                resp = json.loads(raw)
                text = (resp.get("text") or 
                       resp.get("choices", [{}])[0].get("message", {}).get("content") or
                       resp.get("response") or "")
            except (json.JSONDecodeError, ValueError):
                text = raw  # Direct text response
            
            if text and len(text) > 100:
                post_title = f"{provider.upper()}'s Take: {topic}"
                post_content = f'<p><em>This post was written by {provider.upper()} ({model}) as part of an AI multi-perspective series.</em></p>\n{text}\n<p><em>— {provider.upper()} via AILinux Swarm Intelligence</em></p>'
                
                if publish:
                    wp_result = await _wp_api("POST", "posts", {
                        "title": post_title,
                        "content": post_content,
                        "status": "publish",
                        "excerpt": f"{provider.upper()}'s perspective on: {topic}"
                    })
                    results.append({
                        "provider": provider,
                        "model": model,
                        "post_id": wp_result.get("id"),
                        "link": wp_result.get("link"),
                        "words": len(text.split())
                    })
                else:
                    results.append({
                        "provider": provider,
                        "model": model,
                        "preview": text[:200] + "...",
                        "words": len(text.split())
                    })
        except Exception as e:
            results.append({"provider": provider, "error": str(e)})
    
    return json.dumps({
        "topic": topic,
        "posts_created": len([r for r in results if "post_id" in r]),
        "results": results
    })




async def handle_wp_create_draft(arguments: Dict[str, Any]) -> str:
    """Create a WordPress draft post."""
    title = arguments.get("title", "")
    content = arguments.get("content", "")
    categories = arguments.get("categories", [])
    
    featured = arguments.get("featured_media", DEFAULT_FEATURED_MEDIA)
    data = {"title": title, "content": content, "status": "draft", "featured_media": featured}
    if categories:
        data["categories"] = categories
    
    result = await _wp_api("POST", "posts", data)
    
    if "id" in result:
        return json.dumps({
            "success": True,
            "post_id": result["id"],
            "status": "draft",
            "link": result.get("link"),
            "title": result.get("title", {}).get("rendered", title)
        })
    return json.dumps({"success": False, "error": result})


async def handle_wp_list_drafts(arguments: Dict[str, Any]) -> str:
    """List WordPress draft posts."""
    per_page = arguments.get("per_page", 20)
    result = await _wp_api("GET", f"posts?status=draft&per_page={per_page}")
    
    if isinstance(result, list):
        posts = [{"id": p["id"], "title": p.get("title", {}).get("rendered", ""),
                  "date": p.get("date"), "link": p.get("link")} for p in result]
        return json.dumps({"drafts": posts, "count": len(posts)})
    return json.dumps({"error": result})


async def handle_create_post(arguments: Dict[str, Any]) -> str:
    """Create a WordPress post (alias for wp_publish_post with configurable status)."""
    return await handle_wp_publish_post(arguments)


# Handler registry
WORDPRESS_HANDLERS = {
    "wp_publish_post": handle_wp_publish_post,
    "wp_list_posts": handle_wp_list_posts,
    "wp_update_post": handle_wp_update_post,
    "wp_delete_post": handle_wp_delete_post,
    "wp_create_page": handle_wp_create_page,
    "wp_multi_ai_post": handle_wp_multi_ai_post,
    "wp_create_draft": handle_wp_create_draft,
    "wp_list_drafts": handle_wp_list_drafts,
    "create_post": handle_create_post,
}

# Tool schemas
WORDPRESS_TOOL_SCHEMAS = [
    {
        "name": "wp_publish_post",
        "description": "Create and publish a WordPress blog post on ailinux.me",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Post title"},
                "content": {"type": "string", "description": "HTML content"},
                "status": {"type": "string", "enum": ["publish", "draft", "pending"], "default": "publish"},
                "excerpt": {"type": "string", "description": "Optional excerpt"},
                "author_name": {"type": "string", "description": "Display author name"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "wp_list_posts",
        "description": "List WordPress posts by status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["publish", "draft", "pending", "private"], "default": "publish"},
                "per_page": {"type": "integer", "default": 10}
            }
        }
    },
    {
        "name": "wp_update_post",
        "description": "Update an existing WordPress post",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer", "description": "WordPress post ID"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "status": {"type": "string", "enum": ["publish", "draft", "pending"]}
            },
            "required": ["post_id"]
        }
    },
    {
        "name": "wp_delete_post",
        "description": "Delete a WordPress post permanently",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer", "description": "Post ID to delete"}
            },
            "required": ["post_id"]
        }
    },
    {
        "name": "wp_create_page",
        "description": "Create a WordPress page (not post)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "HTML content"},
                "status": {"type": "string", "default": "publish"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "wp_multi_ai_post",
        "description": "Create multiple blog posts — each AI provider writes their perspective on a topic. Creates separate posts named 'CLAUDE's Take', 'GEMINI's Take', etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic for all AIs to write about"},
                "providers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "AI providers to use",
                    "default": ["claude", "chatgpt", "gemini", "mistral"]
                },
                "publish": {"type": "boolean", "default": True, "description": "Publish immediately or preview only"}
            },
            "required": ["topic"]
        }
    }
]
