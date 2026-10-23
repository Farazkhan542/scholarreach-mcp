"""End-to-end tests: a real MCP client talks to the server in-process; ScholarReach is faked."""

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from mcp import Client
import mcp_types as types

from scholarreach_mcp.api import ScholarReachAPI
from scholarreach_mcp.server import build_server

NOW = datetime.now(timezone.utc)


def prof(i, email=None):
    return {"id": f"A{i}", "name": f"Prof {i}", "institution_name": f"Uni {i}", "country_code": "DE",
            "h_index": 30, "topics": ["federated learning"], "email": email, "email_source": None,
            "email_source_url": None, "email_confidence": 0.9 if email else None, "email_checked_at": None}


class FakeScholarReach:
    """In-memory stand-in for the ScholarReach REST API."""

    def __init__(self):
        self.logins = 0
        self.leads = {
            1: {"prof": prof(1, "p1@uni1.de"), "score": 90.0, "outreach_id": 10},  # emailed in an earlier session
            2: {"prof": prof(2), "score": 80.0, "outreach_id": None},  # email gets discovered
            3: {"prof": prof(3), "score": 70.0, "outreach_id": None},  # no email anywhere
            4: {"prof": prof(4, "p4@uni4.de"), "score": 60.0, "outreach_id": None},
        }
        self.discoverable = {2: "p2@uni2.de"}
        self.outreach = {
            10: self._email(10, 1, "sent", sent_at=(NOW - timedelta(days=12)).isoformat()),
        }
        self.gmail = True

    def _email(self, oid, lead_id, status, sent_at=None):
        lead = self.leads[lead_id]
        return {"id": oid, "lead_id": lead_id, "professor": lead["prof"], "to_email": lead["prof"]["email"],
                "subject": f"Research fit with {lead['prof']['name']}", "body": "Dear Professor, " + "x" * 60,
                "generated_by": "gemini", "status": status, "error": None, "approved_at": None,
                "sent_at": sent_at, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}

    def lead_out(self, lid):
        lead = self.leads[lid]
        o = self.outreach.get(lead["outreach_id"]) if lead["outreach_id"] else None
        return {"id": lid, "professor": lead["prof"], "score": lead["score"], "matched_interests": ["federated learning"],
                "reasons": ["3 recent papers on federated learning"], "relevant_papers": [{"title": "FL at scale"}],
                "suggested_ask": "Ask about MS thesis supervision", "manual_email": None,
                "contact_email": lead["prof"]["email"], "outreach_id": lead["outreach_id"],
                "outreach_status": o["status"] if o else None, "created_at": NOW.isoformat()}

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        if path == "/api/v1/auth/login":
            self.logins += 1
            return httpx.Response(200, json={"access_token": "t", "token_type": "bearer"})
        if req.headers.get("authorization") != "Bearer t":
            return httpx.Response(401, json={"detail": "Not authenticated"})
        if path == "/api/v1/outreach/stats":
            by = {}
            for o in self.outreach.values():
                by[o["status"]] = by.get(o["status"], 0) + 1
            return httpx.Response(200, json={"by_status": by, "sent_today": 0, "daily_limit": 10,
                                             "gmail_connected": self.gmail})
        if path == "/api/v1/outreach":
            return httpx.Response(200, json=list(self.outreach.values()))
        if path in ("/api/v1/leads", "/api/v1/leads/search"):
            return httpx.Response(200, json=[self.lead_out(i) for i in self.leads] if method == "GET"
                                  else {"leads": [self.lead_out(i) for i in self.leads], "warnings": [], "notes": []})
        if m := re.fullmatch(r"/api/v1/leads/(\d+)/find-email", path):
            lid = int(m[1])
            self.leads[lid]["prof"]["email"] = self.discoverable.get(lid)
            return httpx.Response(200, json=self.lead_out(lid))
        if m := re.fullmatch(r"/api/v1/leads/(\d+)/draft", path):
            lid = int(m[1])
            if self.leads[lid]["outreach_id"]:
                return httpx.Response(409, json={"detail": {"message": "You already have an email for this professor."}})
            oid = 100 + lid
            self.outreach[oid] = self._email(oid, lid, "draft")
            self.leads[lid]["outreach_id"] = oid
            return httpx.Response(201, json=self.outreach[oid])
        if m := re.fullmatch(r"/api/v1/outreach/(\d+)(?:/(approve|unapprove|regenerate|mark-replied))?", path):
            o = self.outreach.get(int(m[1]))
            if o is None:
                return httpx.Response(404, json={"detail": "Email not found."})
            if m[2] == "approve":
                if not self.gmail:
                    return httpx.Response(400, json={"detail": "Connect your Gmail before approving emails."})
                o["status"] = "approved"
            elif method == "PUT":
                o.update(json.loads(req.content))
            return httpx.Response(200, json=o)
        return httpx.Response(404, json={"detail": f"no fake for {method} {path}"})


@pytest.fixture
def fake():
    return FakeScholarReach()


def make_client(fake, **kw):
    api = ScholarReachAPI(base_url="http://fake", email="s@x.com", password="pw",
                          transport=httpx.MockTransport(fake.handle))
    return Client(build_server(api), **kw)


def data(result: types.CallToolResult) -> dict:
    assert not result.is_error, result.content
    sc = result.structured_content
    return sc.get("result", sc)


async def test_lists_tools_with_safety_annotations(fake):
    async with make_client(fake) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"whats_next", "plan_outreach", "review_drafts", "approve_email", "approve_from_card"} <= set(tools)
    assert tools["approve_email"].annotations.destructive_hint is True
    assert tools["whats_next"].annotations.read_only_hint is True
    assert tools["review_drafts"].meta["ui"]["resourceUri"] == "ui://scholarreach/review.html"
    assert tools["approve_from_card"].meta["ui"]["visibility"] == ["app"]  # button only, hidden from the model


async def test_whats_next_remembers_earlier_sessions(fake):
    async with make_client(fake) as c:
        d = data(await c.call_tool("whats_next", {}))
    assert d["by_status"] == {"sent": 1}
    assert d["follow_up"][0]["professor"] == "Prof 1 at Uni 1"  # sent 12 days ago, no reply
    assert d["uncontacted_leads"] == 3
    assert "sent 1 emails" in d["speech"]


async def test_plan_outreach_skips_contacted_finds_emails_and_drafts(fake):
    progress = []

    async def on_progress(p, total, msg):
        progress.append(msg)

    async with make_client(fake) as c:
        r = await c.call_tool("plan_outreach", {"research_interests": ["federated learning"], "how_many": 2},
                              progress_callback=on_progress)
    d = data(r)
    assert [x["professor"] for x in d["drafts"]] == ["Prof 2", "Prof 4"]
    assert d["drafts"][0]["to"] == "p2@uni2.de"  # discovered during the run
    assert d["skipped_already_contacted"] == ["Prof 1"]
    assert d["no_email_found"] == ["Prof 3"]
    assert "Nothing is sent yet" in d["speech"]
    assert all(o["status"] != "approved" for o in fake.outreach.values())
    assert "Finding Prof 2's email" in progress


async def test_approve_asks_for_confirmation_and_respects_no(fake):
    fake.outreach[104] = fake._email(104, 4, "draft")
    asked = []

    async def decline(ctx, params):
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"send": False})

    async with make_client(fake, elicitation_callback=decline) as c:
        d = data(await c.call_tool("approve_email", {"outreach_id": 104}))
    assert "Prof 4" in asked[0] and "p4@uni4.de" in asked[0]
    assert fake.outreach[104]["status"] == "draft"
    assert "won't send" in d["speech"]


async def test_approve_after_yes(fake):
    fake.outreach[104] = fake._email(104, 4, "draft")

    async def accept(ctx, params):
        return types.ElicitResult(action="accept", content={"send": True})

    async with make_client(fake, elicitation_callback=accept) as c:
        d = data(await c.call_tool("approve_email", {"outreach_id": 104}))
    assert fake.outreach[104]["status"] == "approved"
    assert d["email"]["status"] == "approved"


async def test_approve_without_elicitation_needs_spoken_yes(fake):
    fake.outreach[104] = fake._email(104, 4, "draft")
    async with make_client(fake) as c:  # client has no confirmation dialog
        d = data(await c.call_tool("approve_email", {"outreach_id": 104}))
        assert d["needs_confirmation"] is True and fake.outreach[104]["status"] == "draft"
        d = data(await c.call_tool("approve_email", {"outreach_id": 104, "student_said_yes": True}))
    assert fake.outreach[104]["status"] == "approved"


async def test_api_errors_come_back_readable(fake):
    fake.gmail = False
    fake.outreach[104] = fake._email(104, 4, "draft")
    async with make_client(fake) as c:
        r = await c.call_tool("approve_from_card", {"outreach_id": 104})
    assert r.is_error
    assert "Connect your Gmail" in r.content[0].text


async def test_review_ui_and_pipeline_resource(fake):
    fake.outreach[104] = fake._email(104, 4, "draft")
    async with make_client(fake) as c:
        d = data(await c.call_tool("review_drafts", {}))
        ui = await c.read_resource("ui://scholarreach/review.html")
        pipe = await c.read_resource("scholarreach://pipeline")
    assert [e["outreach_id"] for e in d["emails"]] == [104]
    assert ui.contents[0].mime_type == "text/html;profile=mcp-app"
    assert "approve_from_card" in ui.contents[0].text
    assert json.loads(pipe.contents[0].text)["drafts_waiting"][0]["outreach_id"] == 104
    assert fake.logins == 1  # signed in once, token reused
