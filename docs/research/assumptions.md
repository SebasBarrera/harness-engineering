# Provisional assumptions and open decisions

- Python is provisionally selected for the research prototype because it minimizes implementation time while supporting typed models, JSON Schema, CLI, process control and agent integrations.
- SQLite plus a local content-addressed artifact store is sufficient for a single-researcher MVP.
- Git is the first SCM adapter but is not imported by the core domain.
- The first real provider is not selected in this starter; the contract and simulated provider come first.
- Prompts and full model responses are not persisted by default; digests and usage metadata are preferred until retention and privacy rules are approved.
- Strong OS sandboxing is outside the first vertical slice; the MVP must state this limitation clearly.
- A minimal web interface and MCP runtime are post-MVP unless required by an evaluation protocol.
