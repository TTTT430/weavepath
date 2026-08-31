# WeavePath Web

React 19 + TypeScript client for WeavePath, a local-first, route-aware conversation workspace. The chat surface is `/`; the independently openable workflow graph is `/graph?workflow=<id>`.

Workflow, conversation, and node names are user data and are never translated. The interface chrome supports Chinese and English, while the product brand remains **WeavePath** in both languages.

## Requirements

- Node.js 22 or newer
- npm with lockfile support
- The WeavePath backend running on `http://localhost:8000`

## Development

```bash
npm ci
npm run dev
```

Vite proxies `/api` to `http://localhost:8000`.

## Verification

```bash
npm test
npm run typecheck
npm run build
```

Conversation instances are concrete route-specific sessions. Shared `topicId` values never imply shared transcripts. The current node shows only its local messages; inherited route memory is loaded separately on demand.

## License

Apache-2.0. See the repository-level license for the full terms.
