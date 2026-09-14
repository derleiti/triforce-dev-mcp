#!/usr/bin/env python3
"""
Replicate Integration v2 — Full rewrite based on API docs.
- SSE streaming for text models via urls.stream
- Image generation (FLUX, SDXL)
- Vision analysis (LLaVA)
- Proper prompt formatting
"""
import shutil, ast
from datetime import datetime

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
BASE = "/home/zombie/workspace/triforce"

def safe_replace(filepath, old, new, label=""):
    with open(filepath, "r") as f:
        content = f.read()
    if old not in content:
        print(f"  ⚠ SKIP {label}: pattern not found in {filepath.split('/')[-1]}")
        return content
    shutil.copy2(filepath, f"{filepath}.bak.replv2.{TS}")
    content = content.replace(old, new, 1)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  ✓ {label}")
    return content

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. REWRITE _stream_replicate in chat.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n[1/4] Rewriting _stream_replicate in chat.py...")

fp = f"{BASE}/app/services/chat.py"
with open(fp, "r") as f:
    content = f.read()

# Find and remove old _stream_replicate
marker = "async def _stream_replicate("
if marker in content:
    idx = content.index(marker)
    # Find the next top-level function or EOF
    rest = content[idx:]
    # Look for next "async def _stream_" or "async def _" at same indent level or EOF
    lines = rest.split("\n")
    end_line = len(lines)
    for i, line in enumerate(lines):
        if i == 0:
            continue
        # Top-level async def or module-level function = end of our function
        if (line.startswith("async def ") or line.startswith("def ")) and i > 2:
            end_line = i
            break
    old_fn = "\n".join(lines[:end_line])
    
    new_fn = '''async def _stream_replicate(
    model: str,
    messages: List[dict[str, str]],
    *,
    api_key: str,
    temperature: Optional[float],
    timeout: float = 120.0,
) -> AsyncGenerator[str, None]:
    """Stream text from Replicate via SSE (urls.stream) with sync+poll fallback."""
    import aiohttp, asyncio

    target_model = strip_provider_prefix(model)

    # Convert chat messages to Replicate prompt/system_prompt format
    system_prompt = ""
    prompt_parts = []
    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_prompt = text
        elif role == "assistant":
            prompt_parts.append(f"Assistant: {text}")
        else:
            prompt_parts.append(text if len(messages) <= 2 else f"User: {text}")
    prompt = "\\n".join(prompt_parts)

    payload = {
        "input": {
            "prompt": prompt,
            "max_tokens": 2048,
            "temperature": temperature if temperature is not None else 0.7,
        }
    }
    if system_prompt:
        payload["input"]["system_prompt"] = system_prompt

    url = f"https://api.replicate.com/v1/models/{target_model}/predictions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",
    }

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        # Create prediction
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                error = await resp.text()
                raise RuntimeError(f"Replicate API error ({resp.status}): {error}")
            data = await resp.json()

        stream_url = data.get("urls", {}).get("stream")
        status = data.get("status", "")

        # ━━ Path A: SSE Streaming (preferred for LLMs) ━━
        if stream_url and status in ("starting", "processing"):
            try:
                async with session.get(
                    stream_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "text/event-stream",
                    },
                    timeout=aiohttp.ClientTimeout(total=120, sock_read=35),
                ) as sse_resp:
                    event_type = ""
                    async for raw_line in sse_resp.content:
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\\n\\r")
                        if line.startswith("event: "):
                            event_type = line[7:].strip()
                        elif line.startswith("data: "):
                            payload_str = line[6:]
                            if event_type == "output":
                                yield payload_str
                            elif event_type == "error":
                                raise RuntimeError(f"Replicate SSE error: {payload_str}")
                            elif event_type == "done":
                                return
                        # empty line = end of event block, reset
                        elif line == "":
                            event_type = ""
                return  # SSE done
            except Exception as sse_err:
                logger.warning("Replicate SSE failed, trying poll fallback: %s", sse_err)
                # Fall through to poll

        # ━━ Path B: Sync completed or poll fallback ━━
        if status == "succeeded":
            output = data.get("output", "")
            if isinstance(output, list):
                for token in output:
                    yield str(token)
            else:
                yield str(output)
            return

        # Poll for completion
        get_url = data.get("urls", {}).get("get", "")
        if get_url:
            for _ in range(60):
                await asyncio.sleep(1)
                async with session.get(get_url, headers={"Authorization": f"Bearer {api_key}"}) as poll:
                    poll_data = await poll.json()
                    ps = poll_data.get("status")
                    if ps == "succeeded":
                        output = poll_data.get("output", "")
                        if isinstance(output, list):
                            for token in output:
                                yield str(token)
                        else:
                            yield str(output)
                        return
                    elif ps == "failed":
                        raise RuntimeError(f"Replicate failed: {poll_data.get('error')}")
                    elif ps == "canceled":
                        raise RuntimeError("Replicate prediction canceled")
        raise RuntimeError("Replicate prediction timed out")
'''

    shutil.copy2(fp, f"{fp}.bak.replv2.{TS}")
    content = content.replace(old_fn, new_fn)
    with open(fp, "w") as f:
        f.write(content)
    print("  ✓ _stream_replicate rewritten with SSE streaming")
else:
    print("  ⚠ _stream_replicate not found — check manually")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. REWRITE _replicate_chat in chat_router.py 
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n[2/4] Rewriting _replicate_chat in chat_router.py...")

fp2 = f"{BASE}/app/services/chat_router.py"
with open(fp2, "r") as f:
    content2 = f.read()

marker2 = '    async def _replicate_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:'
if marker2 in content2:
    idx2 = content2.index(marker2)
    rest2 = content2[idx2:]
    lines2 = rest2.split("\n")
    end2 = len(lines2)
    for i, line in enumerate(lines2):
        if i == 0:
            continue
        if (line.startswith("    async def ") or line.startswith("    def ")) and not line.strip().startswith("#") and i > 3:
            end2 = i
            break
    old_fn2 = "\n".join(lines2[:end2])

    new_fn2 = '''    async def _replicate_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """Replicate Prediction API — SSE streaming with sync fallback."""
        import asyncio
        
        # Build prompt/system_prompt from messages
        system_prompt = ""
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_prompt = content
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
            else:
                prompt_parts.append(content if len(messages) <= 2 else f"User: {content}")
        prompt = "\\n".join(prompt_parts)

        model_id = model.strip()
        payload = {
            "input": {
                "prompt": prompt,
                "max_tokens": max_tokens or 2048,
                "temperature": temp if temp is not None else 0.7,
            }
        }
        if system_prompt:
            payload["input"]["system_prompt"] = system_prompt

        url = f"https://api.replicate.com/v1/models/{model_id}/predictions"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Prefer": "wait=60",
                },
                json=payload
            ) as resp:
                if resp.status not in (200, 201):
                    error = await resp.text()
                    raise RuntimeError(f"Replicate API error ({resp.status}): {error}")
                data = await resp.json()

            stream_url = data.get("urls", {}).get("stream")
            status = data.get("status", "")

            # Try SSE streaming and collect full response
            if stream_url and status in ("starting", "processing"):
                chunks = []
                try:
                    async with session.get(
                        stream_url,
                        headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
                        timeout=aiohttp.ClientTimeout(total=120, sock_read=35),
                    ) as sse:
                        event_type = ""
                        async for raw_line in sse.content:
                            line = raw_line.decode("utf-8", errors="replace").rstrip("\\n\\r")
                            if line.startswith("event: "):
                                event_type = line[7:].strip()
                            elif line.startswith("data: "):
                                if event_type == "output":
                                    chunks.append(line[6:])
                                elif event_type == "done":
                                    break
                            elif line == "":
                                event_type = ""
                    if chunks:
                        return "".join(chunks)
                except Exception:
                    pass  # fall through to poll

            # Sync completed
            if data.get("status") == "succeeded":
                output = data.get("output", "")
                return "".join(output) if isinstance(output, list) else str(output)

            # Poll fallback
            get_url = data.get("urls", {}).get("get", "")
            if get_url:
                for _ in range(60):
                    await asyncio.sleep(1)
                    async with session.get(get_url, headers={"Authorization": f"Bearer {api_key}"}) as poll:
                        pd = await poll.json()
                        if pd.get("status") == "succeeded":
                            output = pd.get("output", "")
                            return "".join(output) if isinstance(output, list) else str(output)
                        elif pd.get("status") in ("failed", "canceled"):
                            raise RuntimeError(f"Replicate {pd['status']}: {pd.get('error')}")
            raise RuntimeError("Replicate prediction timed out")

'''
    shutil.copy2(fp2, f"{fp2}.bak.replv2.{TS}")
    content2 = content2.replace(old_fn2, new_fn2)
    with open(fp2, "w") as f:
        f.write(content2)
    print("  ✓ _replicate_chat rewritten with SSE streaming")
else:
    print("  ⚠ _replicate_chat not found")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. ADD Replicate Image Generation to nova_frontend.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n[3/4] Adding Replicate image generation to nova_frontend.py...")

fp3 = f"{BASE}/app/routes/nova_frontend.py"

# Insert replicate image handler before the Gemini section
old_img = '    # Gemini native image generation (gemini-2.5-flash-image'
new_img = '''    # ── Replicate image generation (FLUX, SDXL) ──
    if m.startswith("replicate/"):
        from ..config import get_settings as _gs
        _s = _gs()
        rep_key = _s.replicate_api_key
        if not rep_key:
            raise HTTPException(status_code=503, detail="Replicate API key not configured")
        rep_model = m.replace("replicate/", "", 1)
        # Map size to aspect_ratio for FLUX models
        _SIZE_TO_AR = {"1024x1024": "1:1", "1792x1024": "16:9", "1024x1792": "9:16", "512x512": "1:1"}
        rep_payload: dict = {"input": {"prompt": req.prompt}}
        if "flux" in rep_model.lower():
            rep_payload["input"]["aspect_ratio"] = _SIZE_TO_AR.get(req.size, "1:1")
            rep_payload["input"]["num_outputs"] = min(req.n, 4)
            rep_payload["input"]["output_format"] = "webp"
            rep_payload["input"]["output_quality"] = 90 if req.quality == "hd" else 80
        else:
            # SDXL / other models use width/height
            parts = req.size.split("x")
            if len(parts) == 2:
                rep_payload["input"]["width"] = int(parts[0])
                rep_payload["input"]["height"] = int(parts[1])
            rep_payload["input"]["num_outputs"] = min(req.n, 4)
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"https://api.replicate.com/v1/models/{rep_model}/predictions",
                headers={"Authorization": f"Bearer {rep_key}", "Content-Type": "application/json", "Prefer": "wait=60"},
                json=rep_payload,
            )
            if r.status_code not in (200, 201):
                raise HTTPException(status_code=r.status_code, detail=r.text)
            rdata = r.json()
            # Poll if not yet done
            if rdata.get("status") in ("starting", "processing"):
                import asyncio
                get_url = rdata.get("urls", {}).get("get", "")
                for _ in range(60):
                    await asyncio.sleep(2)
                    pr = await client.get(get_url, headers={"Authorization": f"Bearer {rep_key}"})
                    pd = pr.json()
                    if pd.get("status") == "succeeded":
                        rdata = pd
                        break
                    elif pd.get("status") in ("failed", "canceled"):
                        raise HTTPException(status_code=500, detail=f"Replicate {pd['status']}: {pd.get('error')}")
            output = rdata.get("output", [])
            if isinstance(output, str):
                output = [output]
            images = [{"url": u} for u in output if isinstance(u, str) and u.startswith("http")]
            return {"ok": True, "mode": "media_image", "provider": "replicate", "result": {"data": images}}

    # Gemini native image generation (gemini-2.5-flash-image'''

safe_replace(fp3, old_img, new_img, "Replicate image generation added to nova_frontend.py")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. ADD Replicate Vision to _vision_proxy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n[4/4] Adding Replicate vision to nova_frontend.py...")

# Find the vision_proxy function and add replicate handling
# Look for the first provider check inside _vision_proxy
with open(fp3, "r") as f:
    vp_content = f.read()

# Add replicate vision before the fallback/else in _vision_proxy
old_vision = '    raise api_error("Selected model does not support vision analysis", status_code=400, code="unsupported_provider")'
if old_vision not in vp_content:
    # Check vision.py instead
    fp4 = f"{BASE}/app/services/vision.py"
    with open(fp4, "r") as f:
        v_content = f.read()
    if old_vision in v_content:
        new_vision = '''    # ── Replicate vision models (LLaVA, Moondream, Qwen-Omni) ──
    if provider == "replicate":
        from ..config import get_settings as _gs
        _s = _gs()
        rep_key = _s.replicate_api_key
        if not rep_key:
            raise api_error("Replicate API key not configured", status_code=503, code="replicate_unavailable")
        rep_model = model_id.replace("replicate/", "", 1) if model_id.startswith("replicate/") else model_id
        rep_input = {"prompt": prompt or "Describe this image in detail."}
        if image_url:
            rep_input["image"] = image_url
        elif image_base64:
            rep_input["image"] = f"data:{mime_type or 'image/jpeg'};base64,{image_base64}"
        rep_input["max_tokens"] = 1024
        import httpx as _httpx, asyncio
        async with _httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"https://api.replicate.com/v1/models/{rep_model}/predictions",
                headers={"Authorization": f"Bearer {rep_key}", "Content-Type": "application/json", "Prefer": "wait=60"},
                json={"input": rep_input},
            )
            if r.status_code not in (200, 201):
                raise api_error(f"Replicate vision error: {r.text}", status_code=r.status_code, code="replicate_error")
            rdata = r.json()
            if rdata.get("status") in ("starting", "processing"):
                get_url = rdata.get("urls", {}).get("get", "")
                for _ in range(60):
                    await asyncio.sleep(1)
                    pr = await client.get(get_url, headers={"Authorization": f"Bearer {rep_key}"})
                    pd = pr.json()
                    if pd.get("status") == "succeeded":
                        rdata = pd
                        break
                    elif pd.get("status") in ("failed", "canceled"):
                        raise api_error(f"Replicate vision {pd['status']}", status_code=500, code="replicate_error")
            output = rdata.get("output", "")
            if isinstance(output, list):
                output = "".join(output)
            return {"text": str(output), "provider": "replicate", "model": model_id}

    raise api_error("Selected model does not support vision analysis", status_code=400, code="unsupported_provider")'''

        safe_replace(fp4, old_vision, new_vision, "Replicate vision added to vision.py")
    else:
        print("  ⚠ Vision unsupported_provider pattern not found in either file")
else:
    print("  ⚠ Vision pattern found in nova_frontend.py — skipping (would need different approach)")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYNTAX CHECK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n[VERIFY] Syntax checking...")
files = [
    f"{BASE}/app/services/chat.py",
    f"{BASE}/app/services/chat_router.py",
    f"{BASE}/app/routes/nova_frontend.py",
    f"{BASE}/app/services/vision.py",
]
all_ok = True
for fpath in files:
    try:
        with open(fpath) as fh:
            ast.parse(fh.read())
        print(f"  ✓ {fpath.split('/')[-1]}")
    except SyntaxError as e:
        print(f"  ✗ {fpath.split('/')[-1]}: {e}")
        all_ok = False

if all_ok:
    print("\n✅ All patches applied and syntax verified.")
else:
    print("\n❌ Syntax errors — check above.")
