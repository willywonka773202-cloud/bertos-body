# Bert's AI — Operating Principles

Canonical framing for anyone (human or agent) building on Bert's AI. Mined from the
AIOS research brief; grounded against what the OS already does. Read this once; it's
the *why* behind the architecture. Don't inject it into every prompt — that would
violate the progressive-disclosure rule it describes.

---

## Three Ms — Mindset / Method / Machine (the *why*)

- **Mindset — default shift.** Before any task ask: *"how could AI do 30–100% of
  this?"* The answer is rarely 0%. Bert's AI exists to make that the reflex.
- **Method — function breakdown.** Will's role is a tree of tasks. Automate one
  leaf at a time; leaves are reusable across processes. Each BertOS automation rule
  / skill = one automated leaf.
- **Machine — curiosity rule.** Never accept output blind ("no dark code"). Treat
  the model as a mentor, not a vending machine. This is the same discipline as the
  build+check HANDOFF protocol: **evidence before assertions.**
- **Productivity dips before it climbs.** ~20% dip during the switch, then
  exponential payoff. Don't quit in the valley.

## Four Cs — Context / Connections / Capabilities / Cadence (the *what*, built in order)

1. **Context** — what the OS knows about Will / his projects / his voice / his money.
   *(Obsidian shared brain + auto-memory + per-project frontmatter. Hot-cache via
   `/api/brain/memory-hot`.)*
2. **Connections** — what data it can reach (APIs / MCP / CLI). *(10+ engines,
   cabinet escalation, Lane A/B, Hermes, email/calendar.)*
3. **Capabilities** — what it can *produce* (skills = reusable SOPs; Deep Build).
4. **Cadence** — when it acts on its own (automation engine + safety partition).

Each builds on the last. `/api/brain/audit` scores all four; `/api/brain/level-up`
turns the gaps into a ranked build backlog. Run them as the recurring meta-loop.

---

## Progressive-disclosure rule (memory / context)

The vault is a wiki, not a RAG dump. **Don't crawl the whole memory graph unless the
query needs it.** The OS already enforces this in code (`chat_processor.build_context`):
pinned facts are always present; extended memory is RAG-retrieved only above a
similarity threshold and flagged "do not reference unless the user asks."

**Cheap-path questions that NEVER need a full crawl** — answer from the hot-cache or
a single index read:

- "What am I working on right now / lately?" → `/api/brain/memory-hot` (hot-cache).
- "What's the state of <project>?" → that project's frontmatter / `HANDOFF.md`.
- "What did the last build/audit/loop do?" → the latest run artifact, not the graph.
- "What engines / limits do I have?" → `/api/brain/limits`, `/api/brain/status`.

Only fan out across the graph for genuinely cross-cutting questions ("where have I
seen X before across all projects?"). Wiki beats RAG up to ~hundreds of pages; past
millions of docs, go back to real embeddings.

---

## Connection discipline (API-over-MCP)

- Prefer **API endpoints over MCP** — MCP loads every endpoint and eats context.
- Research a new connector's API **once**, save the endpoint catalog to
  `references/<service>.md`, then read that cheap markdown forever.
- **Scoped key per connector**, least privilege, read-only where possible. Keys live
  in env vars with placeholders; **never paste a secret into chat or a public repo.**

---

## Non-negotiable safety invariant

Keep the `automations/starter-rules.json` safety partition. **Do not relax it.**

- **Unattended cadence = read-only / validation-only.** A loop running while Will
  sleeps may read, score, lint, and draft — never send, patch, deploy, spend, or
  write memory on its own.
- **Side-effectful actions = approval-gated.** Drafts/patches, memory writes,
  connector actions, paid calls, git/deploy require a human click. The click *is* the
  approval — user-initiated builds are fine; that's why the build launcher confirms.
- **Free-first.** New AI features resolve a free model first
  (`resolve_endpoint(..., free_only=True)`) and fail soft, never silently spending.
- **Never fake evidence.** "Verified" means it actually ran. This holds inside
  routines too. If a free chat model isn't available, say so (`ai_unavailable`) rather
  than inventing output.
