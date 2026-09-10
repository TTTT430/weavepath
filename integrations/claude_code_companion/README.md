# WeavePath Claude Code companion

This optional loopback companion discovers Claude Code session transcripts and
maps supported session operations to the WeavePath host-adapter contract.

- Session-head branching uses `claude --resume <session> --fork-session`.
- Transcript inspection reads the matching JSONL under
  `CLAUDE_CONFIG_DIR/projects` (or `~/.claude/projects`).
- Arbitrary historical-turn forks, archive, and rename are intentionally
  reported as unsupported because Claude Code does not expose those operations
  through its documented CLI.

Start it from the repository root with:

```powershell
.\scripts\start-claude-companion.ps1
```

Then start or restart the WeavePath API so it discovers the authenticated local
endpoint. The discovery token is generated per process and the HTTP listener is
bound to `127.0.0.1` only.

References:

- <https://code.claude.com/docs/en/sessions>
- <https://code.claude.com/docs/en/hooks>
