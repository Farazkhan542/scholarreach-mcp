# Demo recording guide (under 3 minutes)

## Before you hit record

1. Terminal 1: `.\.venv\Scripts\python.exe -m scholarreach_mcp` (leave it running).
2. Terminal 2: `npx @modelcontextprotocol/inspector`. In the browser tab: **Transport** `Streamable HTTP`,
   **URL** `http://127.0.0.1:8765/mcp` → **Connect** → **Tools** → **List Tools**.
3. Open a second browser tab on https://scholarreach.faraz-khan.xyz (logged in).
4. Start recording: **Win + Alt + R** (stop with the same keys). Video lands in `Videos\Captures`.

## Shot list

| Time | Do | Inputs | Say |
|---|---|---|---|
| 0:00 | Show the ScholarReach website | | "Applying for a Master's abroad means emailing dozens of professors about their own research. ScholarReach automates that. I made it an Alexa+ agent with an MCP server." |
| 0:20 | Show terminal 1 + the Inspector connected | | "A self-hosted MCP server, spec 2025-11-25, over Streamable HTTP." |
| 0:35 | Run **whats_next** | none | "It remembers earlier sessions: what I've sent, who hasn't replied, and what to do next." Point at `speech`. |
| 0:55 | Run **plan_outreach** | `how_many` = `1` | "One request: find professors, skip anyone I've emailed, find their address, write a personal draft citing their papers. Nothing is sent." |
| 1:35 | Run **review_drafts** | none | "Here are the drafts waiting for me." Scroll the email body. |
| 1:55 | Run **edit_email** | `outreach_id` = the id shown, `subject` = `Master's supervision: agentic AI reliability` | "I can change it by voice." |
| 2:10 | Run **approve_email** | `outreach_id` = the same id | "Sending always needs my yes." The confirmation dialog appears: **Decline** (shows the safety), or Accept only if you really want to email this professor. |
| 2:35 | Show the website's emails page | | "Everything stays in sync with the web app. Open source on GitHub." |

Accepting in step 2:10 sends a **real email** from your Gmail. If you decline, the email stays a draft.

## After recording

1. Upload to YouTube (Public or Unlisted) and copy the link.
2. Put the link on the **Video** line of `SUBMISSION.md`.
3. Paste the SUBMISSION.md sections into the Devpost form, choose the **Alexa+** track, and click **Submit**.
