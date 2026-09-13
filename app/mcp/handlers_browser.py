"""
MCP Browser Service — Web Browsing via Playwright
Allows AI agents to browse the web, read pages, fill forms, and take screenshots.
Brumo kann damit auch Akazienhonig bestellen.
"""
from __future__ import annotations
import asyncio, json, base64, os, shutil, time
from typing import Any, Dict, Optional

# Lazy-load playwright
_browser = None
_page = None

async def _ensure_browser():
    """Launch browser if not running."""
    global _browser, _page
    if _page and not _page.is_closed():
        return _page
    
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        launch_args = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        try:
            _browser = await pw.chromium.launch(headless=True, args=launch_args)
        except Exception as exc:
            # The Python package can be present while the Playwright-managed browser
            # payload is not. Reuse an already installed system Chromium/Chrome
            # instead of mutating the host or downloading packages at request time.
            if "Executable doesn't exist" not in str(exc):
                raise
            executable = (
                os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                or shutil.which("google-chrome")
                or shutil.which("chromium")
                or shutil.which("chromium-browser")
            )
            if not executable:
                raise
            _browser = await pw.chromium.launch(
                headless=True, executable_path=executable, args=launch_args
            )
        context = await _browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 AILinux-Nova/1.0"
        )
        _page = await context.new_page()
        return _page
    except ImportError:
        # Auto-install playwright
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "playwright", "--break-system-packages",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        proc2 = await asyncio.create_subprocess_exec(
            "playwright", "install", "chromium", "--with-deps",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc2.communicate()
        # Retry
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await _browser.new_context(viewport={"width": 1280, "height": 800})
        _page = await context.new_page()
        return _page


async def handle_browser_navigate(arguments: Dict[str, Any]) -> str:
    """Navigate to URL and return page content + screenshot."""
    url = arguments.get("url", "")
    if not url:
        return json.dumps({"error": "url required"})
    
    wait_for = arguments.get("wait_for", "load")  # load, domcontentloaded, networkidle
    screenshot = arguments.get("screenshot", True)
    extract_text = arguments.get("extract_text", True)
    
    try:
        page = await _ensure_browser()
        await page.goto(url, wait_until=wait_for, timeout=30000)
        
        result = {
            "url": page.url,
            "title": await page.title(),
            "status": "loaded"
        }
        
        if extract_text:
            # Get main text content
            text = await page.evaluate("""() => {
                const el = document.querySelector('main') || document.querySelector('article') || document.body;
                return el ? el.innerText.substring(0, 5000) : '';
            }""")
            result["text"] = text
        
        if screenshot:
            img_bytes = await page.screenshot(type="png", full_page=False)
            result["screenshot_base64"] = base64.b64encode(img_bytes).decode()
            result["screenshot_size"] = len(img_bytes)
        
        # Get links
        links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]'))
                .slice(0, 20)
                .map(a => ({text: a.innerText.trim().substring(0, 50), href: a.href}))
                .filter(l => l.text && l.href.startsWith('http'));
        }""")
        result["links"] = links
        
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


async def handle_browser_click(arguments: Dict[str, Any]) -> str:
    """Click an element on the current page."""
    selector = arguments.get("selector", "")
    text = arguments.get("text", "")
    
    if not selector and not text:
        return json.dumps({"error": "selector or text required"})
    
    try:
        page = await _ensure_browser()
        
        if text:
            await page.click(f"text={text}", timeout=10000)
        else:
            await page.click(selector, timeout=10000)
        
        await page.wait_for_load_state("load", timeout=15000)
        
        title = await page.title()
        return json.dumps({
            "clicked": text or selector,
            "new_url": page.url,
            "new_title": title
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_browser_type(arguments: Dict[str, Any]) -> str:
    """Type text into an input field."""
    selector = arguments.get("selector", "")
    text = arguments.get("text", "")
    submit = arguments.get("submit", False)
    
    if not selector or not text:
        return json.dumps({"error": "selector and text required"})
    
    try:
        page = await _ensure_browser()
        await page.fill(selector, text)
        
        if submit:
            await page.press(selector, "Enter")
            await page.wait_for_load_state("load", timeout=15000)
        
        return json.dumps({
            "typed": text[:50],
            "selector": selector,
            "submitted": submit,
            "current_url": page.url
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_browser_screenshot(arguments: Dict[str, Any]) -> str:
    """Take a screenshot of the current page."""
    full_page = arguments.get("full_page", False)
    
    try:
        page = await _ensure_browser()
        img_bytes = await page.screenshot(type="png", full_page=full_page)
        
        return json.dumps({
            "url": page.url,
            "title": await page.title(),
            "screenshot_base64": base64.b64encode(img_bytes).decode(),
            "size_bytes": len(img_bytes)
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_browser_search(arguments: Dict[str, Any]) -> str:
    """Search the web — routes through SearXNG multi_search for reliability.
    Playwright browser search is unreliable (Google CAPTCHA, outdated selectors).
    """
    query = arguments.get("query", "")
    if not query:
        return json.dumps({"error": "query required"})

    # Route directly through SearXNG multi_search (fast, reliable, no CAPTCHA)
    try:
        from ..services.multi_search import multi_search
        result = await multi_search(query=query, max_results=10, lang="de")
        return json.dumps({
            "query": query,
            "engine": "searxng",
            "results": result.get("results", [])[:10],
            "total": result.get("total", 0),
            "sources": result.get("sources", {}),
        })
    except Exception as e:
        return json.dumps({"error": str(e), "query": query})


async def handle_browser_close(arguments: Dict[str, Any]) -> str:
    """Close the browser."""
    global _browser, _page
    try:
        if _page and not _page.is_closed():
            await _page.close()
        if _browser:
            await _browser.close()
        _browser = None
        _page = None
        return json.dumps({"status": "closed"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# Handler registry
BROWSER_HANDLERS = {
    "browser_navigate": handle_browser_navigate,
    "browser_click": handle_browser_click,
    "browser_type": handle_browser_type,
    "browser_screenshot": handle_browser_screenshot,
    "browser_search": handle_browser_search,
    "browser_close": handle_browser_close,
}

# Tool schemas
BROWSER_TOOL_SCHEMAS = [
    {
        "name": "browser_navigate",
        "description": "Navigate to a URL and extract page content + screenshot. The connected AI sees the page and can interact with it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"},
                "wait_for": {"type": "string", "enum": ["load", "domcontentloaded", "networkidle"], "default": "load"},
                "screenshot": {"type": "boolean", "default": True},
                "extract_text": {"type": "boolean", "default": True}
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_click",
        "description": "Click an element on the current page by CSS selector or visible text",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector"},
                "text": {"type": "string", "description": "Visible text to click"}
            }
        }
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field. Can submit with Enter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for input field"},
                "text": {"type": "string", "description": "Text to type"},
                "submit": {"type": "boolean", "default": False, "description": "Press Enter after typing"}
            },
            "required": ["selector", "text"]
        }
    },
    {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current browser page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "full_page": {"type": "boolean", "default": False}
            }
        }
    },
    {
        "name": "browser_search",
        "description": "Search the web and return structured results",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "engine": {"type": "string", "enum": ["google", "duckduckgo", "searxng"], "default": "duckduckgo"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "browser_close",
        "description": "Close the browser session",
        "inputSchema": {"type": "object", "properties": {}}
    }
]
