"""ScholarReach MCP server: lets Alexa+ (or any MCP client) run a student's professor outreach.

Every tool returns a short `speech` line written to be read aloud, plus structured data for
screens. Sending email always needs the student's explicit yes: `approve_email` asks through MCP
elicitation, and the review card's Approve button calls an app-only tool the model cannot see.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from importlib.resources import files
from typing import Annotated, Any

from pydantic import BaseModel, Field

from mcp.server.apps import Apps
from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .api import ScholarReachAPI, ScholarReachError

REVIEW_UI = "ui://scholarreach/review.html"
FOLLOW_UP_AFTER_DAYS = 10
DISCOVERY_BUDGET_S = 150  # stop starting new email lookups after this long
DISCOVERY_PARALLEL = 3  # email lookups run in small parallel batches
OPEN_STATUSES = ("draft", "failed")

INSTRUCTIONS = """\
ScholarReach helps a student applying abroad (BS research, MS, PhD) reach professors who work on
their research topics. It finds professors from their recent papers, finds their email addresses,
writes a personal email citing the professor's own papers, and sends it from the student's Gmail.

Typical voice flow:
1. `whats_next` to see where the student's outreach stands (it remembers past sessions).
2. `plan_outreach` with a topic and countries: finds professors, skips ones already contacted,
   finds emails and writes drafts in one step.
3. `review_drafts` to show the drafts. Read the subject and the first lines aloud.
4. `edit_email` or `regenerate_email` if the student wants changes.
5. `approve_email` only when the student says to send. It asks them to confirm first.

Keep spoken answers short. Never approve an email the student has not explicitly agreed to send.
"""

ReadOnly = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
Writes = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
Sends = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)


class SendConfirmation(BaseModel):
    send: bool = Field(description="Yes, send this email from my Gmail.")


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {plural or word + 's'}"


def _prof(o: dict) -> str:
    p = o.get("professor") or {}
    inst = p.get("institution_name")
    return f"{p.get('name', 'the professor')}" + (f" at {inst}" if inst else "")


def lead_card(lead: dict) -> dict:
    p = lead["professor"]
    return {
        "lead_id": lead["id"],
        "professor": p["name"],
        "institution": p.get("institution_name"),
        "country": p.get("country_code"),
        "score": round(lead["score"], 1),
        "matched_interests": lead.get("matched_interests", []),
        "why": lead.get("reasons", [])[:3],
        "papers": [pp.get("title") for pp in lead.get("relevant_papers", [])[:3]],
        "email": lead.get("contact_email"),
        "outreach_id": lead.get("outreach_id"),
        "outreach_status": lead.get("outreach_status"),
    }


def email_card(o: dict) -> dict:
    p = o.get("professor") or {}
    return {
        "outreach_id": o["id"],
        "lead_id": o["lead_id"],
        "professor": p.get("name"),
        "institution": p.get("institution_name"),
        "to": o.get("to_email"),
        "email_confidence": p.get("email_confidence"),
        "subject": o["subject"],
        "body": o["body"],
        "status": o["status"],
        "generated_by": o.get("generated_by"),
        "error": o.get("error"),
        "sent_at": o.get("sent_at"),
    }


def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


async def pipeline_summary(api: ScholarReachAPI) -> dict[str, Any]:
    """Where the student's outreach stands, built from what ScholarReach has saved."""
    stats = await api.stats()
    emails = await api.outreach()
    leads = await api.leads()
    by = stats["by_status"]
    drafts = [e for e in emails if e["status"] in OPEN_STATUSES]
    needs_follow_up = [
        e for e in emails
        if e["status"] == "sent" and (_days_since(e.get("sent_at")) or 0) >= FOLLOW_UP_AFTER_DAYS
    ]
    uncontacted = [lead for lead in leads if not lead.get("outreach_id")]

    next_steps: list[str] = []
    if not stats["gmail_connected"]:
        next_steps.append("Connect Gmail in the ScholarReach web app so approved emails can be sent.")
    if drafts:
        next_steps.append(f"Review {_plural(len(drafts), 'draft')} waiting for you.")
    if by.get("failed"):
        next_steps.append(f"{_plural(by['failed'], 'email')} failed to send: fix the address and approve again.")
    if needs_follow_up:
        n = len(needs_follow_up)
        next_steps.append(
            f"{_plural(n, 'professor')} {'hasn' if n == 1 else 'haven'}'t replied in {FOLLOW_UP_AFTER_DAYS}+ days. "
            "Mark any replies, or reach out to new professors."
        )
    if uncontacted:
        next_steps.append(f"{_plural(len(uncontacted), 'matched professor')} not contacted yet.")
    if not next_steps:
        next_steps.append("Find new professors with plan_outreach.")

    speech = (
        f"You've sent {_plural(by.get('sent', 0) + by.get('replied', 0), 'email')} and got "
        f"{_plural(by.get('replied', 0), 'reply', 'replies')}. "
        f"{_plural(len(drafts), 'draft')} waiting, {by.get('approved', 0)} queued to send today "
        f"({stats['sent_today']} of {stats['daily_limit']} sent). Next: {next_steps[0]}"
    )
    return {
        "speech": speech,
        "by_status": by,
        "sent_today": stats["sent_today"],
        "daily_limit": stats["daily_limit"],
        "gmail_connected": stats["gmail_connected"],
        "drafts_waiting": [email_card(e) for e in drafts],
        "follow_up": [
            {"outreach_id": e["id"], "professor": _prof(e), "days_since_sent": _days_since(e.get("sent_at"))}
            for e in needs_follow_up
        ],
        "uncontacted_leads": len(uncontacted),
        "next_steps": next_steps,
    }


def build_server(api: ScholarReachAPI | None = None) -> MCPServer:
    api = api or ScholarReachAPI.from_env()
    apps = Apps()

    async def _call(coro):
        try:
            return await coro
        except ScholarReachError as e:
            raise ToolError(e.message) from e  # becomes an isError tool result the assistant can read out

    # ---------- where things stand ----------

    async def whats_next() -> dict[str, Any]:
        """Summarize the student's outreach so far and say what to do next: drafts waiting for
        review, emails queued or failed, professors who haven't replied, Gmail connection.
        Start here at the beginning of a session."""
        return await _call(pipeline_summary(api))

    async def get_profile() -> dict[str, Any]:
        """The student's saved profile: degree, research interests, target program and countries."""
        p = await _call(api.profile())
        interests = ", ".join(p.get("research_interests") or []) or "no research interests yet"
        speech = (
            f"Looking for a {p.get('target_program') or 'program'} in "
            f"{', '.join(p.get('target_countries') or []) or 'any country'}, interested in {interests}."
        )
        if p.get("missing_for_search"):
            speech += " To search, I still need: " + ", ".join(p["missing_for_search"]) + "."
        return {"speech": speech, "profile": p}

    async def update_interests(
        research_interests: Annotated[
            list[str] | None, Field(description="Replaces the saved research interests (max 8).", max_length=8)
        ] = None,
        target_countries: Annotated[
            list[str] | None, Field(description="ISO country codes like DE, NL, or EU for all of Europe.")
        ] = None,
        target_program: Annotated[str | None, Field(description="bs, ms or phd")] = None,
    ) -> dict[str, Any]:
        """Change the student's research interests, target countries or target program."""
        changes = {
            k: v for k, v in {
                "research_interests": research_interests,
                "target_countries": target_countries,
                "target_program": target_program,
            }.items() if v is not None
        }
        if not changes:
            raise ToolError("Tell me what to change: research interests, countries or program.")
        p = await _call(api.update_profile(changes))
        return {"speech": "Saved. " + (await get_profile())["speech"], "profile": p}

    # ---------- the agentic workflow ----------

    async def plan_outreach(
        ctx: Context,
        research_interests: Annotated[
            list[str] | None,
            Field(description="Topics to search, e.g. ['federated learning']. Defaults to the saved profile."),
        ] = None,
        countries: Annotated[
            list[str] | None, Field(description="ISO codes like DE, NL, or EU. Defaults to the saved profile.")
        ] = None,
        how_many: Annotated[int, Field(ge=1, le=10, description="How many new drafts to write.")] = 3,
    ) -> dict[str, Any]:
        """Do the whole outreach prep in one step: find professors whose recent papers match the
        topics, skip anyone the student already emailed (in any earlier session), find each
        professor's email address, and write a personal draft citing their papers.
        Nothing is sent: the drafts wait for the student's review."""
        await ctx.report_progress(0, how_many, "Searching recent papers for matching professors")
        found = await _call(api.search_leads(
            limit=min(50, max(10, how_many * 3)), research_interests=research_interests, countries=countries
        ))
        already = [lead for lead in found["leads"] if lead.get("outreach_id")]
        # Also consider matches saved in earlier sessions (on the same topics), so known addresses get used.
        wanted = {t.lower() for t in research_interests or []}
        pool = {lead["id"]: lead for lead in await _call(api.leads())
                if not wanted or wanted & {m.lower() for m in lead.get("matched_interests", [])}}
        pool.update({lead["id"]: lead for lead in found["leads"]})
        fresh = sorted((lead for lead in pool.values() if not lead.get("outreach_id")),
                       key=lambda lead: lead["score"], reverse=True)
        known = [lead for lead in fresh if lead.get("contact_email")]
        unknown = [lead for lead in fresh if not lead.get("contact_email")]

        drafts: list[dict] = []
        no_email: list[str] = []
        problems: list[str] = []
        tried: set[int] = set()

        async def write(lead: dict) -> None:
            name = lead["professor"]["name"]
            await ctx.report_progress(len(drafts), how_many, f"Writing to {name}")
            try:
                drafts.append(email_card(await api.draft(lead["id"])))
            except ScholarReachError as e:
                problems.append(f"{name}: {e.message}")

        async def discover(lead: dict) -> dict | None:
            try:
                found_lead = await api.find_email(lead["id"])
            except ScholarReachError as e:
                problems.append(f"{lead['professor']['name']}: {e.message}")
                return None
            if not found_lead.get("contact_email"):
                no_email.append(lead["professor"]["name"])
                return None
            return found_lead

        # 1. Professors whose address is already known: no slow lookup needed.
        for lead in known:
            if len(drafts) >= how_many:
                break
            tried.add(lead["id"])
            await write(lead)

        # 2. Look up the rest a few at a time, within a time budget so a voice request stays responsive.
        started = time.monotonic()
        while len(drafts) < how_many and unknown and len(tried) < how_many * 8 + len(known):
            if time.monotonic() - started > DISCOVERY_BUDGET_S:
                break
            batch, unknown = unknown[:DISCOVERY_PARALLEL], unknown[DISCOVERY_PARALLEL:]
            tried.update(lead["id"] for lead in batch)
            names = ", ".join(lead["professor"]["name"] for lead in batch)
            await ctx.report_progress(len(drafts), how_many, f"Finding emails for {names}")
            for lead in await asyncio.gather(*(discover(lead) for lead in batch)):
                if lead and len(drafts) < how_many:
                    await write(lead)
        await ctx.report_progress(how_many, how_many, "Done")

        if drafts:
            names = ", ".join(f"{d['professor']}" + (f" at {d['institution']}" if d["institution"] else "")
                              for d in drafts)
            speech = f"I wrote {_plural(len(drafts), 'draft')}: {names}. Nothing is sent yet. Want to review {'it' if len(drafts) == 1 else 'them'}?"
        else:
            speech = "I couldn't write any new drafts this time."
        if already:
            speech += f" I skipped {_plural(len(already), 'professor')} you've already emailed."
        if no_email:
            speech += f" No email found for {_plural(len(no_email), 'professor')}."
        return {
            "speech": speech,
            "drafts": drafts,
            "skipped_already_contacted": [lead["professor"]["name"] for lead in already],
            "no_email_found": no_email,
            "problems": problems,
            "other_matches": [lead_card(lead) for lead in fresh if lead["id"] not in tried][:5],
            "warnings": found.get("warnings", []),
            "notes": found.get("notes", []),
        }

    async def list_professors(
        include_contacted: Annotated[bool, Field(description="Also list professors already emailed.")] = False,
    ) -> dict[str, Any]:
        """The professors ScholarReach has matched for the student, best match first."""
        leads = await _call(api.leads())
        if not include_contacted:
            leads = [lead for lead in leads if not lead.get("outreach_id")]
        cards = [lead_card(lead) for lead in leads]
        top = ", ".join(c["professor"] for c in cards[:3])
        speech = f"{_plural(len(cards), 'professor')}" + (f". Top matches: {top}." if top else ".")
        return {"speech": speech, "professors": cards}

    async def draft_email(lead_id: Annotated[int, Field(description="lead_id from list_professors")]) -> dict[str, Any]:
        """Find the email address (if needed) and write a draft to one matched professor."""
        lead = next((x for x in await _call(api.leads()) if x["id"] == lead_id), None)
        if lead is None:
            raise ToolError("I can't find that professor in your matches.")
        if not lead.get("contact_email"):
            lead = await _call(api.find_email(lead_id))
        d = email_card(await _call(api.draft(lead_id)))
        to = f" to {d['to']}" if d["to"] else ", but I have no email address for them yet"
        return {"speech": f"Draft written for {d['professor']}{to}. Subject: {d['subject']}.", "email": d}

    # ---------- review and send ----------

    @apps.tool(
        resource_uri=REVIEW_UI,
        title="Review drafts",
        annotations=ReadOnly,
    )
    async def review_drafts() -> dict[str, Any]:
        """Show the drafts waiting for the student's review (with a review card on screens).
        For voice, read each professor's name and subject, and offer to read the body."""
        emails = [email_card(e) for e in await _call(api.outreach()) if e["status"] in OPEN_STATUSES]
        if not emails:
            return {"speech": "No drafts waiting. Want me to find more professors?", "emails": []}
        first = emails[0]
        speech = (
            f"{_plural(len(emails), 'draft')} waiting. First, to {first['professor']}: \"{first['subject']}\". "
            "Shall I read it, change it, or send it?"
        )
        return {"speech": speech, "emails": emails}

    async def read_email(outreach_id: int) -> dict[str, Any]:
        """The full text of one email, to read aloud."""
        d = email_card(await _call(api.outreach_one(outreach_id)))
        return {"speech": f"To {d['professor']}. Subject: {d['subject']}. {d['body']}", "email": d}

    async def edit_email(
        outreach_id: int,
        subject: Annotated[str | None, Field(min_length=3, max_length=300)] = None,
        body: Annotated[str | None, Field(min_length=50, max_length=5000, description="The full new body.")] = None,
        to_email: Annotated[str | None, Field(description="Correct recipient address.")] = None,
    ) -> dict[str, Any]:
        """Change a draft's subject, body or recipient (only drafts and failed emails can change)."""
        changes = {k: v for k, v in {"subject": subject, "body": body, "to_email": to_email}.items() if v}
        if not changes:
            raise ToolError("Nothing to change.")
        d = email_card(await _call(api.edit_outreach(outreach_id, changes)))
        return {"speech": f"Updated the email to {d['professor']}.", "email": d}

    async def regenerate_email(outreach_id: int) -> dict[str, Any]:
        """Write a fresh version of a draft."""
        d = email_card(await _call(api.outreach_action(outreach_id, "regenerate")))
        return {"speech": f"New version for {d['professor']}. Subject: {d['subject']}.", "email": d}

    async def _approve(outreach_id: int) -> dict[str, Any]:
        d = email_card(await _call(api.outreach_action(outreach_id, "approve")))
        return {
            "speech": f"Approved. Your email to {d['professor']} will go out from your Gmail shortly.",
            "email": d,
        }

    async def ask_to_send(ctx: Context, outreach_id: int) -> Elicit[SendConfirmation] | None:
        """Ask the student through the client's confirmation dialog, when it has one.
        Works on both protocol eras: a mid-call request (2025-11-25) or InputRequiredResult (2026-07-28)."""
        caps = ctx.client_capabilities
        if caps is None or caps.elicitation is None:
            return None  # no dialog: approve_email falls back to the spoken yes
        o = email_card(await _call(api.outreach_one(outreach_id)))
        if o["status"] not in OPEN_STATUSES:
            return None
        return Elicit(
            f"Send this email to {o['professor']} <{o['to'] or 'no address yet'}> from your Gmail?\n\n"
            f"Subject: {o['subject']}",
            SendConfirmation,
        )

    async def approve_email(
        outreach_id: int,
        confirmation: Annotated[ElicitationResult[SendConfirmation], Resolve(ask_to_send)],
        student_said_yes: Annotated[
            bool,
            Field(description="Only for clients without confirmation dialogs: true only after you read the "
                              "email to the student and they explicitly said to send it."),
        ] = False,
    ) -> dict[str, Any]:
        """Queue one email to be sent from the student's Gmail (within their daily limit).
        The student is asked to confirm before anything is queued."""
        o = email_card(await _call(api.outreach_one(outreach_id)))
        if o["status"] not in OPEN_STATUSES:
            return {"speech": f"That email is already {o['status']}.", "email": o}
        asked = not (isinstance(confirmation, AcceptedElicitation) and confirmation.data is None)
        if asked:
            if not (isinstance(confirmation, AcceptedElicitation) and confirmation.data.send):
                return {"speech": "Okay, I won't send it. It stays in your drafts.", "email": o}
        elif not student_said_yes:
            return {
                "speech": f"Before I send it: to {o['professor']}, subject \"{o['subject']}\". Should I send it?",
                "needs_confirmation": True,
                "email": o,
            }
        return await _approve(outreach_id)

    @apps.tool(resource_uri=REVIEW_UI, visibility=["app"], annotations=Sends)
    async def approve_from_card(outreach_id: int) -> dict[str, Any]:
        """Approve button on the review card (the student clicked it). Not callable by the model."""
        return await _approve(outreach_id)

    async def cancel_send(outreach_id: int) -> dict[str, Any]:
        """Take an approved email out of the send queue and back to drafts."""
        d = email_card(await _call(api.outreach_action(outreach_id, "unapprove")))
        return {"speech": f"Stopped. The email to {d['professor']} is back in your drafts.", "email": d}

    async def mark_replied(outreach_id: int) -> dict[str, Any]:
        """Record that a professor replied."""
        d = email_card(await _call(api.outreach_action(outreach_id, "mark-replied")))
        return {"speech": f"Great news! Marked {d['professor']} as replied.", "email": d}

    apps.add_html_resource(
        REVIEW_UI,
        files("scholarreach_mcp").joinpath("review.html").read_text(encoding="utf-8"),
        title="ScholarReach draft review",
        prefers_border=True,
    )

    server = MCPServer(
        "scholarreach",
        title="ScholarReach",
        description="Find professors, write personal outreach and send it from your Gmail.",
        instructions=INSTRUCTIONS,
        website_url="https://scholarreach.faraz-khan.xyz",
        version="1.0.0",
        extensions=[apps],
    )
    for fn, ann, title in (
        (whats_next, ReadOnly, "What's next"),
        (get_profile, ReadOnly, "Student profile"),
        (update_interests, Writes, "Update interests"),
        (plan_outreach, Writes, "Plan outreach"),
        (list_professors, ReadOnly, "Matched professors"),
        (draft_email, Writes, "Draft an email"),
        (read_email, ReadOnly, "Read an email"),
        (edit_email, Writes, "Edit an email"),
        (regenerate_email, Writes, "Rewrite an email"),
        (approve_email, Sends, "Approve and send"),
        (cancel_send, Writes, "Cancel sending"),
        (mark_replied, Writes, "Mark replied"),
    ):
        server.add_tool(fn, title=title, annotations=ann)

    @server.resource("scholarreach://pipeline", mime_type="application/json", title="Outreach pipeline")
    async def pipeline() -> str:
        """Counts by status, drafts waiting, follow-ups due and next steps."""
        return json.dumps(await _call(pipeline_summary(api)), default=str)

    @server.prompt(title="Outreach session")
    def outreach_session(topic: str = "", countries: str = "") -> str:
        """A guided session: check status, find professors, review and send."""
        ask = f" on {topic}" if topic else ""
        where = f" in {countries}" if countries else ""
        return (
            "Run a ScholarReach outreach session for me. First call whats_next and tell me where I stand. "
            f"Then call plan_outreach for professors{ask}{where} and show me the drafts with review_drafts. "
            "Read each one to me and wait for my decision before approving anything."
        )

    return server
