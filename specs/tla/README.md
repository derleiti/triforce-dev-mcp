# TriForce TLA+ Specifications

> Specification status: these TLA+ models are formal-design artifacts and do not by themselves certify the current TriForce 2.86.4 runtime. Re-run TLC against the checked-in specs/configs when using them as evidence.

Formal verification specs for TriForce distributed AI system.

## Files

| File | Description |
|------|-------------|
| `ServerFederation.tla` | Node health, heartbeats, auto-failover |
| `CLIAgents.tla` | Agent coordination (parallel/sequential/consensus) |
| `*.cfg` | TLC Model Checker configurations |

## Setup

```bash
# Install TLA+ Toolbox (Ubuntu)
sudo apt install default-jre
wget https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/TLAToolbox-1.8.0-linux.gtk.x86_64.zip
unzip TLAToolbox-*.zip

# Or use VS Code Extension
code --install-extension alygin.vscode-tlaplus
```

## Running Model Checker

```bash
# Command line
java -jar tla2tools.jar -config ServerFederation.cfg ServerFederation.tla

# Or in VS Code: Ctrl+Shift+P -> "TLA+: Check Model"
```

## Properties Verified

### ServerFederation
- **TypeInvariant**: All variables have correct types
- **AlwaysSomeHealthy**: At least one node is healthy (unless all fail)
- **PrimaryIsHealthy**: Primary node never serves while offline
- **EventualFailover**: If hub dies, new primary is eventually elected

### CLIAgents  
- **TypeOK**: Agent states are valid
- **NoDeadlock**: System always makes progress
- **ConsensusReached**: Majority voting works correctly

## Key Concepts

```
Init           Initial state
Next           All possible transitions
[]P            P is always true (safety)
<>P            P is eventually true (liveness)
[]<>P          P happens infinitely often
<>[]P          P becomes permanently true
```

## TriForce Components Modeled

1. **Federation**: Hetzner (HUB) ↔ Backup ↔ Contributors
2. **CLI Agents**: claude-mcp, codex-mcp, gemini-mcp, opencode-mcp
3. **Strategies**: parallel, sequential, consensus

## Next Steps

- [ ] Model Checker run with 3 nodes
- [ ] Add message passing model
- [ ] Model network partitions
- [ ] Add Mesh Brain coordination
