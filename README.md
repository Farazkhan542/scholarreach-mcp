# ScholarReach MCP: professor outreach by voice

> *"Alexa, find me three professors in Germany working on federated learning and write to them."*

**Demo video:** https://youtu.be/S8FSP5ovAfE

An **Alexa+ MCP server** (MCP spec **2025-11-25**, **Streamable HTTP**) for
[ScholarReach](https://scholarreach.faraz-khan.xyz), a web app that helps students apply abroad by
finding professors whose recent papers match their research, finding the professors' email addresses,
and sending personal, reviewed emails from the student's own Gmail.

Applying for an MS or PhD abroad means emailing dozens of professors, each email citing their own work.
Students spend weeks on it. This server turns that into a short conversation with an assistant:

1. **"Where am I?"**: `whats_next` remembers every earlier session: emails sent, replies, drafts
   waiting, professors who haven't replied in 10+ days, and whether Gmail is connected.
2. **"Find professors and write to them."**: `plan_outreach` runs the whole workflow in one call:
   searches recent papers (OpenAlex), ranks professors, **skips anyone already emailed**, discovers
   each professor's email address (papers → ORCID → homepages → web search), and writes a personal
   draft that cites the professor's papers. It streams progress ("Finding Prof. Müller's email…").
3. **"Read me the first one."**: `review_drafts` / `read_email`. On a screen, the drafts appear
   in an interactive **MCP App** review card.
4. **"Make it shorter" / "Send it."**: `edit_email`, `regenerate_email`, `approve_email`.
   Sending **always** needs the student's explicit yes (see *Safety*).

## Quick start

Needs Python 3.11+ and a ScholarReach account (free: register at https://scholarreach.faraz-khan.xyz,
fill in your profile, and connect Gmail if you want to send).

```powershell
git clone https://github.com/Farazkhan542/scholarreach-mcp
cd scholarreach-mcp
python -m venv .venv
.\.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env            # macOS/Linux: cp .env.example .env
# edit .env: SCHOLARREACH_EMAIL and SCHOLARREACH_PASSWORD
python -m scholarreach_mcp        # → ScholarReach MCP server on http://127.0.0.1:8765/mcp
```

### Connect a client

| Client | How |
|---|---|
| **MCP Inspector** | `npx.cmd @modelcontextprotocol/inspector` (PowerShell; `npx` elsewhere) → Transport *Streamable HTTP* → URL `http://127.0.0.1:8765/mcp` → Connect |
| **Claude Code** | `claude mcp add --transport http scholarreach http://127.0.0.1:8765/mcp` |
| **Any MCP host / Alexa+** | Streamable HTTP endpoint `https://<your-host>/mcp`. Set `MCP_AUTH_TOKEN` and send `Authorization: Bearer <token>` |

To reach it from the internet, run it behind HTTPS (e.g. `cloudflared tunnel --url http://127.0.0.1:8765`)
and **set `MCP_AUTH_TOKEN`**.

### Tests

```powershell
pytest
```

The tests drive the real MCP server with the SDK's MCP client against an in-memory fake of the
ScholarReach API, so they need no network or accounts. They cover the full workflow, skipping
contacted professors, email discovery, progress notifications, the confirm-before-send flow (yes,
no, and clients without dialogs), error messages, the MCP App resource and token reuse.

## Tools, resources, prompts

| Tool | What it does | Hints |
|---|---|---|
| `whats_next` | Status across sessions + the next best step | read-only |
| `plan_outreach` | Search → skip contacted → find emails → write drafts (with progress) | writes, never sends |
| `list_professors` | Matched professors, best first | read-only |
| `draft_email` | Find email + draft for one professor | writes |
| `review_drafts` | Drafts waiting, rendered as an **MCP App** card on screens | read-only |
| `read_email` | Full text, ready to read aloud | read-only |
| `edit_email` / `regenerate_email` | Change or rewrite a draft | writes |
| `approve_email` | Queue for sending from Gmail **after confirmation** | destructive |
| `cancel_send` / `mark_replied` | Pull back from the queue / record a reply | writes |
| `get_profile` / `update_interests` | Read or change research interests, countries, program | |
| `approve_from_card` | The review card's button. `visibility: ["app"]`, hidden from the model | destructive |

- **Resource** `scholarreach://pipeline`: the outreach state as JSON (for context-aware assistants).
- **Resource** `ui://scholarreach/review.html`: the MCP App (`text/html;profile=mcp-app`).
- **Prompt** `outreach_session(topic, countries)`: a guided session.

## Design for voice

- Every tool returns a one- or two-sentence **`speech`** field written to be read aloud ("I wrote
  3 drafts: Prof. A at TU Munich… Nothing is sent yet. Want to review them?"), plus structured data
  for screens. Errors come back as short sentences ("Connect your Gmail before approving emails.").
- One utterance does a lot: `plan_outreach` replaces ~15 clicks in the web app.
- It remembers you: state lives in ScholarReach, so "already emailed" and "no reply in 10 days" carry
  across sessions and devices.

## Safety

Emails go out from the student's real Gmail, so sending is guarded three ways:

1. `approve_email` asks through **MCP elicitation** ("Send this email to Prof. X <x@uni.de>?") on
   both protocol eras: a mid-call `elicitation/create` on 2025-11-25 and an `InputRequiredResult`
   round trip on 2026-07-28. If the client has no confirmation dialog, the tool refuses until the
   assistant has read the email back and the student said yes.
2. The card's Approve button calls `approve_from_card`, which is **app-only**: the model never sees it.
3. ScholarReach itself refuses duplicates per professor, honors do-not-contact, and sends in small
   daily batches within the student's limit.

## How it works

```
Alexa+ / MCP host ──Streamable HTTP (MCP 2025-11-25)──▶ scholarreach-mcp (this repo, mcp SDK 2.3)
                                                          │  bearer token, auto re-login
                                                          ▼
                                     ScholarReach API (FastAPI): OpenAlex, ORCID, Europe PMC,
                                     CSRankings, web search, Gemini drafting, Gmail API sending
```

| File | |
|---|---|
| [scholarreach_mcp/server.py](scholarreach_mcp/server.py) | MCP tools, resource, prompt, MCP App, elicitation |
| [scholarreach_mcp/api.py](scholarreach_mcp/api.py) | ScholarReach REST client |
| [scholarreach_mcp/review.html](scholarreach_mcp/review.html) | MCP App review card (postMessage JSON-RPC bridge) |
| [scholarreach_mcp/\_\_main\_\_.py](scholarreach_mcp/__main__.py) | Streamable HTTP server (uvicorn) + optional bearer auth |
| [tests/test_server.py](tests/test_server.py) | End-to-end MCP tests |
| [DEMO.md](DEMO.md) | Demo recording guide |

## License

[MIT](LICENSE)
