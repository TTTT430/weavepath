# WeavePath Codex companion

This personal Codex plugin exposes a per-process authenticated loopback bridge
from WeavePath to trusted Codex app tools. It never writes navigation commands
to the composer and does not patch Codex application files.

Repository-local installation:

```powershell
codex plugin marketplace add <repository-root>
codex plugin add weavepath-codex-companion@weavepath-local
```

Open a new Codex task after installation so the MCP server receives the native
app-tools pipe. Then restart the WeavePath API after the discovery file appears.

The current contract supports task listing and transcript inspection, task-head
fork, navigation, rename, and archive. Historical-turn fork is intentionally
reported as unsupported by the current Codex task API.
