#!/usr/bin/env python3
"""
TriForce Evolution Stresstest
=============================

Stresstest für das Auto-Evolution System.
Befragt alle CLI-Agents und LLM-Modelle parallel.

Usage:
    python scripts/evolve_stresstest.py [--mode analyze|suggest|implement]
    python scripts/evolve_stresstest.py --full   # Full multi-model test
"""

import asyncio
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_evolution_service(mode: str = "analyze") -> Dict[str, Any]:
    """Test the auto-evolution service."""
    from app.services.auto_evolve import AutoEvolveService, EvolutionMode

    print(f"\n{'='*60}")
    print(f"EVOLUTION SERVICE TEST - Mode: {mode}")
    print(f"{'='*60}")

    service = AutoEvolveService()

    start = time.perf_counter()
    result = await service.run_evolution(
        mode=EvolutionMode(mode),
        max_findings=30,
    )
    elapsed = time.perf_counter() - start

    print(f"\n[RESULT] Evolution ID: {result.evolution_id}")
    print(f"[RESULT] Duration: {elapsed:.2f}s")
    print(f"[RESULT] Agents consulted: {result.agents_consulted}")
    print(f"[RESULT] Findings: {len(result.findings)}")
    print(f"[RESULT] Memory stored: {result.memory_stored}")

    if result.findings:
        print(f"\n[TOP FINDINGS]")
        for i, f in enumerate(result.findings[:5], 1):
            print(f"  {i}. [{f.severity.value.upper()}] {f.file}:{f.line or '?'}")
            print(f"     Issue: {f.current[:60]}...")
            print(f"     Fix: {f.suggested[:60]}...")

    if result.error:
        print(f"\n[ERROR] {result.error}")

    return result.to_dict()


async def test_multi_model_stress() -> Dict[str, Any]:
    """Stress test with multiple LLM models in parallel."""
    print(f"\n{'='*60}")
    print("MULTI-MODEL STRESS TEST")
    print(f"{'='*60}")

    # Models to test
    models = [
        ("gemini/gemini-2.5-flash", "Gemini Flash"),
        ("gemini/gemini-2.5-pro", "Gemini Pro"),
        ("mistral/mistral-large-latest", "Mistral Large"),
        ("gpt-oss:20b-cloud", "GPT-OSS 20B"),
    ]

    test_prompt = """Analyze this Python code and suggest one improvement:

```python
def fetch_data(url):
    import requests
    response = requests.get(url)
    data = response.json()
    return data
```

Respond with JSON: {"issue": "...", "fix": "..."}"""

    results = {}

    async def test_model(model_id: str, name: str) -> Dict[str, Any]:
        """Test a single model."""
        try:
            # Try to import and use the chat service
            from app.services.chat import generate_response

            start = time.perf_counter()
            response = await asyncio.wait_for(
                generate_response(
                    model=model_id,
                    prompt=test_prompt,
                    temperature=0.3,
                ),
                timeout=60,
            )
            elapsed = time.perf_counter() - start

            return {
                "model": model_id,
                "name": name,
                "status": "success",
                "response_length": len(response) if response else 0,
                "latency_ms": int(elapsed * 1000),
            }
        except asyncio.TimeoutError:
            return {"model": model_id, "name": name, "status": "timeout"}
        except Exception as e:
            return {"model": model_id, "name": name, "status": "error", "error": str(e)}

    # Run all models in parallel
    print(f"\nTesting {len(models)} models in parallel...")
    start = time.perf_counter()

    tasks = [test_model(model_id, name) for model_id, name in models]
    model_results = await asyncio.gather(*tasks, return_exceptions=True)

    total_elapsed = time.perf_counter() - start

    # Process results
    success_count = 0
    for r in model_results:
        if isinstance(r, dict):
            results[r.get("model", "unknown")] = r
            if r.get("status") == "success":
                success_count += 1
                print(f"  ✓ {r['name']}: {r['latency_ms']}ms ({r['response_length']} chars)")
            else:
                print(f"  ✗ {r['name']}: {r.get('status')} - {r.get('error', '')[:50]}")

    print(f"\n[SUMMARY]")
    print(f"  Total duration: {total_elapsed:.2f}s")
    print(f"  Models tested: {len(models)}")
    print(f"  Success: {success_count}/{len(models)}")

    return {
        "test": "multi_model_stress",
        "models_tested": len(models),
        "success_count": success_count,
        "total_duration_ms": int(total_elapsed * 1000),
        "results": results,
    }


async def test_agent_broadcast() -> Dict[str, Any]:
    """Test broadcasting to all CLI agents."""
    print(f"\n{'='*60}")
    print("AGENT BROADCAST TEST")
    print(f"{'='*60}")

    try:
        from app.services.tristar.agent_controller import AgentController

        controller = AgentController()
        agents = await controller.list_agents()

        print(f"\nFound {len(agents)} agents:")
        for agent in agents:
            status = agent.get("status", "unknown")
            icon = "🟢" if status == "running" else "⚪"
            print(f"  {icon} {agent.get('agent_id')}: {status}")

        # Try to send a simple message to each agent
        broadcast_prompt = "PING: Respond with 'PONG' and your agent ID"

        results = {}
        for agent in agents:
            agent_id = agent.get("agent_id")
            try:
                response = await asyncio.wait_for(
                    controller.call_agent(agent_id, broadcast_prompt),
                    timeout=30,
                )
                results[agent_id] = {
                    "status": "success",
                    "response_length": len(str(response)),
                }
                print(f"  ✓ {agent_id}: responded")
            except asyncio.TimeoutError:
                results[agent_id] = {"status": "timeout"}
                print(f"  ⏱ {agent_id}: timeout")
            except Exception as e:
                results[agent_id] = {"status": "error", "error": str(e)[:50]}
                print(f"  ✗ {agent_id}: {str(e)[:50]}")

        return {
            "test": "agent_broadcast",
            "agents_found": len(agents),
            "results": results,
        }

    except ImportError as e:
        print(f"  Agent controller not available: {e}")
        return {"test": "agent_broadcast", "status": "unavailable", "error": str(e)}


async def test_mcp_tools() -> Dict[str, Any]:
    """Test MCP tool availability."""
    print(f"\n{'='*60}")
    print("MCP TOOLS TEST")
    print(f"{'='*60}")

    try:
        from app.services.tristar_mcp import TRISTAR_TOOLS, TRISTAR_HANDLERS

        # Check for evolution tools
        evolve_tools = [t for t in TRISTAR_TOOLS if t["name"].startswith("evolve_")]

        print(f"\nTotal MCP tools: {len(TRISTAR_TOOLS)}")
        print(f"Total handlers: {len(TRISTAR_HANDLERS)}")
        print(f"Evolution tools: {len(evolve_tools)}")

        for tool in evolve_tools:
            print(f"  - {tool['name']}: {tool['description'][:50]}...")

        return {
            "test": "mcp_tools",
            "total_tools": len(TRISTAR_TOOLS),
            "total_handlers": len(TRISTAR_HANDLERS),
            "evolution_tools": [t["name"] for t in evolve_tools],
        }

    except ImportError as e:
        print(f"  MCP tools not available: {e}")
        return {"test": "mcp_tools", "status": "unavailable", "error": str(e)}


async def run_full_stresstest() -> Dict[str, Any]:
    """Run all stress tests."""
    print(f"\n{'#'*60}")
    print("# TRIFORCE EVOLUTION STRESSTEST")
    print(f"# Started: {datetime.now().isoformat()}")
    print(f"{'#'*60}")

    results = {
        "started_at": datetime.now().isoformat(),
        "tests": {},
    }

    # Test 1: MCP Tools
    try:
        results["tests"]["mcp_tools"] = await test_mcp_tools()
    except Exception as e:
        results["tests"]["mcp_tools"] = {"status": "error", "error": str(e)}

    # Test 2: Evolution Service
    try:
        results["tests"]["evolution"] = await test_evolution_service("analyze")
    except Exception as e:
        results["tests"]["evolution"] = {"status": "error", "error": str(e)}

    # Test 3: Agent Broadcast
    try:
        results["tests"]["agent_broadcast"] = await test_agent_broadcast()
    except Exception as e:
        results["tests"]["agent_broadcast"] = {"status": "error", "error": str(e)}

    # Test 4: Multi-Model Stress
    try:
        results["tests"]["multi_model"] = await test_multi_model_stress()
    except Exception as e:
        results["tests"]["multi_model"] = {"status": "error", "error": str(e)}

    results["completed_at"] = datetime.now().isoformat()

    # Save results
    output_file = Path("/var/tristar/logs/evolution/stresstest_result.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(results, indent=2))

    print(f"\n{'#'*60}")
    print(f"# STRESSTEST COMPLETE")
    print(f"# Results saved to: {output_file}")
    print(f"{'#'*60}")

    return results


def main():
    parser = argparse.ArgumentParser(description="TriForce Evolution Stresstest")
    parser.add_argument(
        "--mode",
        choices=["analyze", "suggest", "implement"],
        default="analyze",
        help="Evolution mode (default: analyze)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run full stresstest with all components",
    )
    parser.add_argument(
        "--mcp-only",
        action="store_true",
        help="Only test MCP tools",
    )
    parser.add_argument(
        "--agents-only",
        action="store_true",
        help="Only test agent broadcast",
    )
    args = parser.parse_args()

    if args.full:
        asyncio.run(run_full_stresstest())
    elif args.mcp_only:
        asyncio.run(test_mcp_tools())
    elif args.agents_only:
        asyncio.run(test_agent_broadcast())
    else:
        asyncio.run(test_evolution_service(args.mode))


if __name__ == "__main__":
    main()
