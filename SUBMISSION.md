# Devpost submission: ScholarReach MCP

**Primary track:** Alexa+ (self-hosted MCP server, spec 2025-11-25, Streamable HTTP)
**Mini challenges:** none
**Repo:** https://github.com/Farazkhan542/scholarreach-mcp (MIT)
**Video:** <YouTube link>

---

## Tagline

Apply to grad school abroad by talking: Alexa finds professors who work on your research, writes
to each about their own papers, and sends from your Gmail only when you say yes.

## What it does

Students applying for an MS or PhD abroad have to email dozens of professors, and each email has to
show they've read the professor's work. Finding the right professors, finding their email addresses
and writing each email takes weeks, and most students give up after a handful.

ScholarReach (my web app, built during the hackathon window) already automates the hard parts. This
project makes it an **Alexa+ agent** through a self-hosted MCP server:

- **"Where am I?"**: `whats_next` remembers every earlier session: sent, replied, drafts waiting,
  professors with no reply after 10 days, Gmail status. It answers with the next best step.
- **"Find three professors in Germany working on federated learning and write to them."**:
  `plan_outreach` runs a multi-step workflow in one call: searches recent papers, ranks professors
  by fit, **skips anyone already emailed in past sessions**, discovers each email address (paper
  affiliations → ORCID → homepages → web search), and writes a personal draft citing the
  professor's papers. It streams progress while it works.
- **"Read me the first one" / "Make it shorter" / "Send it."**: review, edit, regenerate, approve.
  On screens, drafts render as an interactive **MCP App** card with an Approve button.
- **Safe by design:** sending needs an explicit yes through **MCP elicitation**. The card's button
  calls an **app-only tool the model can't see**. The backend blocks duplicates and honors
  do-not-contact.

## What's new during the hackathon

ScholarReach is a live web app (https://scholarreach.faraz-khan.xyz). Before this project it was
usable only through its website. Everything in this repository was built during the submission
period, and it is the significant update:

- **A new Alexa+ interface:** a self-hosted MCP server (spec 2025-11-25, Streamable HTTP) that
  exposes ScholarReach as voice-first tools.
- **A new agentic workflow:** `plan_outreach` chains search → skip already-contacted → email
  discovery → drafting in one request. In the web app those are separate manual steps per professor.
- **New cross-session awareness:** `whats_next` turns saved state into spoken next steps (drafts
  waiting, failed sends, no reply after 10 days).
- **New confirm-before-send safety layer:** MCP elicitation plus an app-only Approve button.
- **A new MCP App review card** for screens.

The demo video shows these new features. The website is shown only as the place where emails and
status end up.

## How I built it

- Python, **MCP Python SDK 2.3** (`MCPServer`), Streamable HTTP on uvicorn, negotiates **2025-11-25**
  (verified with a raw `initialize` handshake) and also serves 2026-07-28 clients.
- MCP features used: tools with annotations (read-only / destructive hints), progress
  notifications, **elicitation** via the SDK's `Resolve`/`Elicit` (works on both protocol eras),
  **MCP Apps** (`ui://` resource + app-only tool visibility), resources, prompts, server instructions.
- Every tool returns a short `speech` line for voice and structured data for screens.
- Backend: ScholarReach (FastAPI, PostgreSQL/Neon, OpenAlex, ORCID, Europe PMC, CSRankings, Gemini,
  Gmail API), hosted on Vercel.
- End-to-end tests drive the server with the SDK's MCP client against a fake API.

## Challenges

See the friction log below. The biggest: MCP SDK 2.x changes (FastMCP → MCPServer, and `ctx.elicit()`
failing on 2026-07-28 connections), which I solved with the resolver-based elicitation that works on
both eras.

## What's next

- OAuth for multi-student hosting (today it's self-hosted per student).
- Reply detection from Gmail so "who replied?" needs no manual marking.
- Weekly proactive check-ins ("2 professors replied, 3 drafts waiting").

---

## Product feedback

**Tools/SDKs used and for what**
- **Model Context Protocol (spec 2025-11-25) + MCP Python SDK 2.3.0**: the whole Alexa+ integration:
  Streamable HTTP server, tools, elicitation, progress, MCP Apps, resources, prompts.
- **MCP Inspector**: manual testing and the demo.
- **Claude Code**: pair-programming the server, tests and docs.

**What worked well**
- The SDK's in-process `Client(server)` made true end-to-end tests easy: no ports, no mocks of MCP.
- Dual-era serving: one server answered both a 2025-11-25 `initialize` and 2026-07-28 clients with
  no extra code.
- `Resolve` + `Elicit` is a clean way to ask for confirmation once and have it work on both eras.
- MCP Apps in core (`mcp.server.apps`) with tool `visibility: ["app"]` is exactly the primitive you
  need for "only a human click can do this".
- Tool annotations (`destructiveHint`) map naturally to "this sends a real email".

**What needs work**
- v1 → v2 migration: almost every tutorial (including the getting-started docs most people land on)
  still uses `FastMCP`, which now raises `ModuleNotFoundError`.
- `ctx.elicit()` raises `NoBackChannelError` on 2026-07-28 connections; the fix (resolvers) isn't
  discoverable from the error message.
- Resolvers break under `from __future__ import annotations` when defined inside a factory function.
- No official JS helper shown for the MCP App side in Python examples; I wrote the postMessage
  JSON-RPC bridge by hand.
- Hackathon rules say "2025-11-25 (or a later version, once confirmed)" while the current SDK
  defaults to 2026-07-28. It's unclear which one judges will test with.

**Onboarding (zero to hello world)**
About 30 minutes, most of it spent finding out that v2 renamed FastMCP and choosing between the
two protocol eras. After that, adding tools was fast.

**Would I build with it again?**
Yes. One small server made my existing web app usable from any MCP host, and the newer features
(elicitation, MCP Apps, progress) are what make an agent safe enough to send real email.

---

## Friction log

| # | Task | Steps taken | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| 1 | Hello-world MCP server in Python | `pip install mcp`, followed getting-started (`from mcp.server.fastmcp import FastMCP`) | Server starts | `ModuleNotFoundError`: FastMCP renamed to `MCPServer` in 2.x | Medium | `from mcp.server.mcpserver import MCPServer` | Update the getting-started pages to v2 or show both side by side |
| 2 | Ask the user to confirm before sending an email | `await ctx.elicit(msg, Schema)` inside the tool | Client shows a confirmation | `NoBackChannelError` on 2026-07-28 connections (the SDK client's default) | High | `Annotated[ElicitationResult[T], Resolve(fn)]` resolver returning `Elicit(...)` | Make `ctx.elicit()` work on both eras, or have the error point to `Resolve`/`Elicit` |
| 3 | Use a resolver defined inside `build_server()` | Module had `from __future__ import annotations` | Tool registers | `InvalidSignature: NameError: 'ask_to_send' is not defined` | Medium | Removed the future import | Resolve annotations with the function's closure, or document the limitation |
| 4 | Return a readable error from a tool | `raise ValueError("Connect your Gmail…")` | Assistant can read the message out | Wrapped as "Error executing tool …" via `UnexpectedToolError`; test lost the message | Low | `raise ToolError(...)` | Document `ToolError` next to the first tool example |
| 5 | Confirm my server meets the track's spec version | Read rules ("2025-11-25 or later once confirmed"), SDK `LATEST_PROTOCOL_VERSION` = 2026-07-28 | Clear target | Ambiguous which era judges/Alexa+ use | Medium | Raw `curl` `initialize` with `protocolVersion: 2025-11-25`: negotiated 2025-11-25 | State the exact version Alexa+ negotiates; provide a conformance check |
| 6 | Show drafts as an interactive card (MCP Apps) | `Apps().tool(resource_uri=…)`, `add_html_resource` | Example of the iframe side | Server API documented; the HTML/JS bridge isn't, in Python docs | Low | Hand-wrote `ui/initialize`, `tools/call`, `ui/notifications/tool-result` handling | Ship a tiny vanilla-JS bridge snippet with the Python Apps docs |
| 7 | Test with a real Alexa+ device | Not attempted within the deadline | A documented way to point Alexa+ at a self-hosted MCP URL from the getting-started docs | The track docs link only to the generic MCP getting-started guide; no Alexa+-specific connection steps there | Medium | Demoed with MCP Inspector over Streamable HTTP | Add an "Alexa+ ← your MCP server" quick start (URL, auth, test console) to the track resources |
