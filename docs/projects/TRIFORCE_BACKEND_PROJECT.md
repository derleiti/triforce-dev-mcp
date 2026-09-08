# Project TriForce Backend (AILinux)

Backend version: `2.81`
Production branch: `master`
Episodic-memory integration baseline: `65eec52f`

Diese Seite ist als Einstieg für Menschen gedacht, die nicht nur "API nutzen", sondern verstehen wollen, wie der TriForce-Backend-Stack in der Praxis aufgebaut ist.

## Was TriForce im Kern ist

TriForce ist ein FastAPI-basiertes Backend, das vier Ebenen gleichzeitig verbindet:

- API-Layer fuer Web/Client-Apps (`app/main.py`, `app/routes/*`)
- Orchestrierung mehrerer KI-Provider und lokaler Compute-Backends (`app/services/*`)
- Tooling-Layer ueber MCP fuer Agenten, Automationen und Admin-Aufgaben (`app/routes/mcp.py`, `app/mcp/*`)
- Native Memory-Fabric fuer kuratiertes Wissen, episodische Agent-Historie und kontrollierten Runtime-Kontext (`app/services/memory_*.py`, `app/services/episodic_memory.py`)

Das Projekt ist damit nicht nur "Chat-API", sondern eine Betriebsplattform fuer AI-Workloads.

## Architektur in der Implementierung

Die konkrete Runtime startet ueber `app/main.py` und initialisiert unter anderem:

- Redis-basiertes Rate-Limiting
- Model-Registry-Refresh
- TriStar Services (curated Memory, Model Init, Agent Controller, Settings)
- Episodic Memory Provider + `MemoryTriggerEngine` fuer task/file/failure/retry/handoff/resume Recall
- Mesh Coordinator
- Federation Manager mit Locking
- Distributed Compute Manager
- MCP Brain + MCP WebSocket Server

Das ist bewusst als zusammengesetztes System gebaut: ein einzelner Prozess stellt API, Tooling, Agent-Orchestrierung und Monitoring bereit.

## Wichtige Schnittstellen

- `POST /v1/chat/completions`: OpenAI-kompatible Chat-Route
- `POST /v1/mcp`: interner MCP JSON-RPC Einstieg
- `GET/POST /mcp`: Remote-Connector/OAuth-Pfad
- `/v1/client/*`: Endpunkte fuer Desktop-/Mobile-Clients
- `/v1/mesh/*`, `/v1/federation/*`: Knoten- und Clustersteuerung
- `/v1/agents/*`, `/v1/tristar/*`: Agenten- und Workflow-Steuerung

## Implementierte Software-Bausteine

- Multi-Provider Router (Cloud + lokal)
- MCP Tool Registry und Handler-Schicht
- Agent Router / Tasking / Skill-Mapping
- Memory-Index und persistente kuratierte Wissensfunktionen
- Persistente episodische Agent-Historie ueber `EpisodicMemoryProvider` (aktuell Claude-Mem 13.24.1)
- Kontrollierte Memory-Promotion: nur verifizierte Observations mit Evidence
- Remote-Admin und Federation-Control
- Notification-, Mail- und WordPress-Integrationen
- Performance-/Telemetry-Layer fuer MCP Calls


## Native Agent Memory Fabric

TriForce speichert jetzt nicht mehr nur bewusst kuratiertes Wissen, sondern kann sich auch an **Agentenerfahrungen** erinnern. Diese Ebenen werden absichtlich nicht vermischt:

- **Runtime State**: aktueller Task, Run, Retry und aktive Arbeit.
- **Episodic Memory**: was Agenten in frueheren Runs getan, versucht, geaendert oder als Fehler erlebt haben.
- **Curated Memory**: verifizierte Facts, Entscheidungen, Codewissen und dauerhafte Regeln.

Der aktuelle episodische Provider ist Claude-Mem 13.24.1 hinter einem TriForce-eigenen Adapter. Dadurch ist die Funktion nicht Claude-spezifisch: der Recall-Kontext kann jedem ueber TriForce laufenden Modell zur Verfuegung gestellt werden.

Die Runtime kann bei Task-Start/Resume, erstem File-Zugriff, Fehlern, Retries, Handoffs sowie Merge-/Commit-Pruefpunkten Recall ausloesen. Resultate werden projektgescoped, dedupliziert, auf Relevanz/Staleness bewertet, auf ein Context-Budget begrenzt und als **historisch/untrusted** markiert. Der Worker ist kein Single Point of Failure; bei Ausfall arbeitet TriForce ohne episodischen Recall weiter.

Automatisches Hochstufen in kuratiertes Memory gibt es bewusst nicht. Promotion ist explizit, standardmaessig deaktiviert und verlangt `verification_state=verified` plus Verification Evidence.

Der reale Worker-E2E wurde mit isolierten synthetischen Daten erfolgreich gegen Claude-Mem 13.24.1 fuer Health, Store und anschliessende Search verifiziert.

Details: [Episodic Agent Memory Architecture](../architecture/episodic-memory.md).

## Warum diese Architektur sinnvoll ist

TriForce kombiniert drei Anforderungen, die in vielen Projekten getrennt enden:

- Produktive API fuer Apps
- Operatives Steuerzentrum fuer Infrastruktur
- Agent-freundliche Tool-Schnittstelle

Die Vereinigung dieser Ebenen macht TriForce komplexer als einen reinen LLM-Proxy, erlaubt aber konsistente Authentifizierung, Tool-Kontrolle, Agent-Orchestrierung, Betriebssteuerung und persistente Memory-Nutzung in einer gemeinsamen Runtime.

## Weiterlesen

- [System Architecture](../ARCHITECTURE.md)
- [REST API](../api/REST.md)
- [MCP Tools](../api/MCP.md)
- [AILinux Client Project](./AILINUX_CLIENT_PROJECT.md)
- [Episodic Agent Memory](../architecture/episodic-memory.md)
