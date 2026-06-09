# brain-bridge (vendored)

A **self-contained copy** of the BertOS brain MCP bridge, vendored into the body
image so the Dockerized body can spawn it **without mounting the brain repo**
(which would drag a Windows-built `node_modules` into the Linux container).

- **Source of truth:** `~/Documents/bertosV2/scripts/brain-mcp-server.mjs`.
  Edit it there, verify against the running brain, then re-sync here:
  ```bash
  bash deploy/desktop/sync-brain-bridge.sh
  ```
- The bridge is a **thin HTTP client** to the running bertosV2 Next server. It
  imports only `@modelcontextprotocol/sdk` + `zod` (both pure JS), pinned in
  `package.json`, installed at image build (`Dockerfile`). It never imports the
  brain's libs and never drives the daemon directly.
- The body registers it via `_BUILTIN_NODE_SERVERS` in `src/builtin_mcp.py`,
  spawning `node <BERTOS_BRAIN_DIR>/scripts/brain-mcp-server.mjs`. In the
  container set `BERTOS_BRAIN_DIR=/app/brain-bridge` (the deploy overlay does
  this). On a native run it defaults to `~/Documents/bertosV2`.

How it reaches the brain: `BERTOS_BRAIN_BASE_URL` (default
`http://host.docker.internal:3000`) → the native bertosV2 on the host. Subscription
power is gated by `BRAIN_ALLOW_PAID` (the overlay maps `BERTOS_BRAIN_ALLOW_PAID`).
