"""Lumora AG: a fictional software company run the way Knovas runs itself (Client Zero).

Lumora builds *Lumora Desk*, document search for Swiss Treuhand firms, and the *Lumora API*
for software partners. It has Knovas's seven processes, roles, value streams and spines;
everything in this file is invented: people, customers, plans, decisions, risks, releases,
deals and experiments. ``build.py`` turns it into ``graph.json`` the way KnowledgeBase's
loader writes a tenant (definition nodes with their facts, instances with ``instance_of``,
``in_state`` and a dated ``current_step``).

Some records are left incomplete on purpose, so the views have something to show: the
e-mail intake plan has no invariant or Alloy check yet, one risk is past its review date,
the SSO plan has no documentation record, one decision names nobody who decided it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from graph_views.offline import OfflineGraph

TENANT = "5e0d1a7c-4b2f-4c3e-9a61-1d2b3c4d5e6f"
LABEL = {"key": "LUMORA", "name": "Lumora AG", "kind": "software company (fictional demo)"}
REPO = "lumora/handbook"            # where Lumora keeps its plans and decisions (named in `source`)

# -- organisation --------------------------------------------------------------------------------

PEOPLE = {
    "PERS-lea": ("Lea Brunner", "CEO; holds the commercial spine and co-owns decisions."),
    "PERS-marco": ("Marco Keller", "CTO; owns the engine and the feature lifecycle."),
    "PERS-nina": ("Nina Hofer", "Head of Product; ontology steward, runs the experiments."),
    "PERS-daniel": ("Daniel Frei", "Account executive for Treuhand firms."),
    "PERS-sara": ("Sara Meier", "Senior engineer, search and ingest."),
}
FUNCTIONS = {
    "FN-ENGINEERING": "Engineering", "FN-PRODUCT": "Product", "FN-SALES": "Sales", "FN-FINANCE": "Finance",
}
ROLES = {   # key: (name, function, holders)
    "ROLE-CTO": ("CTO", "FN-ENGINEERING", ["PERS-marco"]),
    "ROLE-ENGINE-OWNER": ("Engine owner", "FN-ENGINEERING", ["PERS-marco"]),
    "ROLE-DECISION-OWNER": ("Decision and risk owner", "FN-PRODUCT", ["PERS-lea", "PERS-marco"]),
    "ROLE-COMMERCIAL-OWNER": ("Commercial and finance spine owner", "FN-SALES", ["PERS-lea"]),
    "ROLE-ONTOLOGY-STEWARD": ("Ontology steward", "FN-PRODUCT", ["PERS-nina"]),
}
PERSON_FUNCTION = {"PERS-lea": "FN-FINANCE", "PERS-marco": "FN-ENGINEERING", "PERS-nina": "FN-PRODUCT",
                   "PERS-daniel": "FN-SALES", "PERS-sara": "FN-ENGINEERING"}
AGENTS = {"AGENT-ci": ("CI", "Lumora's build pipeline; records test runs and Alloy checks.")}

# -- product spine -------------------------------------------------------------------------------

PRODUCTS = {"PROD-DESK": ("Lumora Desk", "Treuhand firms and their staff"),
            "PROD-API": ("Lumora API", "software partners")}
TIERS = {"TIER-START": ("Start", "PROD-DESK", "CHF 29"), "TIER-TEAM": ("Team", "PROD-DESK", "CHF 49"),
         "TIER-SCALE": ("Scale", "PROD-DESK", "CHF 79")}
API_GROUPS = {"API-INGEST": ("Ingest API", "PROD-API"), "API-SEARCH": ("Search API", "PROD-API")}
SYSTEM = ("SYS-LUMORA-CORE", "Lumora Core", "The software behind Lumora Desk and the Lumora API.")
CONTEXTS = {
    "L1-INTAKE": ("Intake", "Receives documents: upload, e-mail, scanner, OCR."),
    "L1-SEARCH": ("Search", "Finds documents and passages; ranking and filters."),
    "L1-ACCOUNTS": ("Accounts", "Firms, users, sign-in and tenant isolation."),
    "L1-BILLING": ("Billing", "Usage metering and invoices."),
}
CAPABILITIES = {   # key: (name, status, tier, offered by, realised by)
    "CAP-01": ("Upload and scan documents", "live", "Start", ["TIER-START", "API-INGEST"], "L1-INTAKE"),
    "CAP-02": ("Find a document by client, year or name", "live", "Start", ["TIER-START", "API-SEARCH"], "L1-SEARCH"),
    "CAP-03": ("Receive documents by e-mail", "planned", "Team", ["TIER-TEAM"], "L1-INTAKE"),
    "CAP-04": ("Read tables in scanned statements", "research", "Team", ["TIER-TEAM"], "L1-INTAKE"),
    "CAP-05": ("Sign in with the firm's Microsoft account", "live-on-request", "Team", ["TIER-TEAM"], "L1-ACCOUNTS"),
    "CAP-06": ("Pay per document processed", "built-not-activated", "Scale", ["TIER-SCALE"], "L1-BILLING"),
}
COMPONENTS = {   # key: (name, context or parent component, alloy_status, docs_status, description)
    "L2-INBOX": ("Inbox", "L1-INTAKE", "modelled", "complete", "Accepts uploads and forwards them to OCR."),
    "L2-OCR": ("OCR", "L1-INTAKE", "modelled", "partial", "Turns scanned pages into text and tables."),
    "L3-OCR-TABLES": ("Table reader", "L2-OCR", "none", "missing", "Finds tables on a scanned page."),
    "L2-SEARCH-INDEX": ("Search index", "L1-SEARCH", "modelled", "complete", "Keeps the passages each firm may search."),
    "L2-QUERY": ("Query", "L1-SEARCH", "modelled", "complete", "Answers a search: names, filters, ranking."),
    "L2-AUTH": ("Sign-in", "L1-ACCOUNTS", "modelled", "partial", "Passwords and Microsoft sign-in."),
    "L2-TENANTS": ("Tenants", "L1-ACCOUNTS", "verified", "complete", "One firm's data never reaches another."),
    "L2-USAGE": ("Usage meter", "L1-BILLING", "modelled", "partial", "Counts processed documents per firm."),
    "L2-INVOICING": ("Invoicing", "L1-BILLING", "none", "missing", "Monthly invoice from the usage meter."),
}
INVARIANTS = {   # key: (name, area, kept by)
    "GI-TENANT-01": ("A query reads one firm's documents only", "Accounts", ["L2-TENANTS", "L2-QUERY", "L2-SEARCH-INDEX"]),
    "GI-INTAKE-01": ("Every accepted document is searchable within ten minutes", "Intake", ["L2-INBOX", "L2-OCR"]),
    "GI-SEARCH-01": ("A result names the document it came from", "Search", ["L2-QUERY"]),
    "GI-BILLING-01": ("A processed document is counted exactly once", "Billing", ["L2-USAGE"]),
    "GI-AUTH-01": ("A removed user cannot sign in", "Accounts", ["L2-AUTH"]),
}
CONTRACTS = {   # component: [(suffix, kind, text, invariant satisfied)]
    "L2-INBOX": [("PRE-SIGNED-IN", "precondition", "The uploader is signed in to the firm.", None),
                 ("RESP-ACCEPT", "responsibility", "Accepts PDF, JPEG and TIFF up to 50 MB.", "GI-INTAKE-01")],
    "L2-OCR": [("POST-TEXT", "postcondition", "Every page has text or is flagged unreadable.", "GI-INTAKE-01"),
               ("RESP-ROUTE", "responsibility", "Sends every page through OCR once.", None)],
    "L2-SEARCH-INDEX": [("RESP-PER-FIRM", "responsibility", "Keeps one index per firm.", "GI-TENANT-01"),
                        ("POST-REMOVED", "postcondition", "A deleted document leaves the index within a minute.", None)],
    "L2-QUERY": [("PRE-TENANT", "precondition", "The query carries the firm of the signed-in user.", "GI-TENANT-01"),
                 ("POST-SOURCE", "postcondition", "Each result names its document.", "GI-SEARCH-01"),
                 ("RESP-RANK", "responsibility", "Ranks the firm's own client first.", None)],
    "L2-AUTH": [("RESP-REVOKE", "responsibility", "Ends the sessions of a removed user at once.", "GI-AUTH-01"),
                ("PRE-ACTIVE", "precondition", "Only an active user gets a session.", "GI-AUTH-01")],
    "L2-TENANTS": [("RESP-ISOLATE", "responsibility", "Gives every read the firm it belongs to.", "GI-TENANT-01"),
                   ("PRE-KNOWN", "precondition", "A request names a known firm.", None)],
    "L2-USAGE": [("POST-ONCE", "postcondition", "A retried upload is not counted again.", "GI-BILLING-01")],   # no responsibility yet
}
CALLS = [("L2-INBOX", "L2-OCR"), ("L2-OCR", "L2-SEARCH-INDEX"), ("L2-INBOX", "L2-USAGE"), ("L2-QUERY", "L2-SEARCH-INDEX"),
         ("L2-QUERY", "L2-TENANTS"), ("L2-AUTH", "L2-TENANTS"), ("L2-SEARCH-INDEX", "L2-TENANTS")]
SOURCE_FILES = {"L2-INBOX": ["intake/inbox.py", "intake/formats.py"], "L2-OCR": ["intake/ocr.py"],
                "L2-SEARCH-INDEX": ["search/index.py"], "L2-QUERY": ["search/query.py", "search/ranking.py"],
                "L2-AUTH": ["accounts/auth.py", "accounts/microsoft.py"], "L2-TENANTS": ["accounts/tenants.py"],
                "L2-USAGE": ["billing/usage.py"]}

# -- goals and market ----------------------------------------------------------------------------

GOALS = {   # key: (name, period, status, owner)
    "GOAL-2026-Q4-first-ten-treuhand": ("Ten Treuhand firms search their files with Lumora Desk", "quarter", "active", "ROLE-COMMERCIAL-OWNER"),
    "GOAL-2026-Q4-search-under-two-seconds": ("Every search answers within two seconds", "quarter", "active", "ROLE-ENGINE-OWNER"),
    "GOAL-2027-01-api-partner": ("First software partner live on the Lumora API", "month", "draft", "ROLE-COMMERCIAL-OWNER"),
}
MARKET = ("MKT-01", "Switzerland \u2014 document search for fiduciaries")
SEGMENTS = {"ICP-01": ("Treuhand firms (CH)", "CH"), "ICP-02": ("Tax advisers (DE)", "DE"),
            "ICP-03": ("Accounting software vendors (DACH)", "DACH")}
PERSONAS = {"P-01": ("Treuhand \u2014 Owner", "ICP-01", "Economic buyer"),
            "P-02": ("Treuhand \u2014 Mandate leader", "ICP-01", "Champion and pilot lead"),
            "P-03": ("Tax adviser \u2014 Partner", "ICP-02", "Economic buyer"),
            "P-04": ("Software vendor \u2014 Head of Development", "ICP-03", "Technical buyer")}
CHANNELS = {"CH-WEBINAR": "Webinar", "CH-LINKEDIN": "LinkedIn", "CH-REFERRAL": "Referral"}
HYPOTHESES = {   # key: (name, status, importance, targets)
    "ICP-01.H01": ("Treuhand firms lose an hour a day looking for documents", "validated", "critical", ["ICP-01", "P-02"]),
    "ICP-01.H02": ("A setup checklist doubles the share of firms that reach their first search", "tested", "high", ["ICP-01"]),
    "ICP-02.H01": ("German tax advisers buy through their software vendor", "asserted", "high", ["ICP-02", "P-03"]),
    "ICP-01.H03": ("Firms pick the Team tier when the pricing page shows the e-mail inbox", "validated", "high", ["ICP-01", "P-01"]),
}
METRICS = {"MET-first-search-share": ("Share of new firms with a search in week one", "%", "up"),
           "MET-webinar-signups": ("Webinar sign-ups", "count", "up"),
           "MET-team-tier-share": ("Share of new firms on the Team tier", "%", "up")}

# -- process instances ---------------------------------------------------------------------------
# Feature plans: (slug, date, name, status, since, owner, size, components, capability, goal or hypothesis)
PLANS = [
    ("email-intake", "2026-10-21", "E-mail intake for client documents", "draft", "2026-10-21", "ROLE-ENGINE-OWNER", "13 sp",
     ["L2-INBOX"], "CAP-03", "GOAL-2026-Q4-first-ten-treuhand"),
    ("ocr-tables", "2026-10-02", "Tables from scanned bank statements", "review", "2026-10-09", "PERS-sara", "21 sp",
     ["L2-OCR", "L3-OCR-TABLES"], "CAP-04", "ICP-01.H01"),
    ("usage-billing", "2026-09-15", "Pay per processed document", "approved", "2026-10-14", "ROLE-ENGINE-OWNER", "34 sp",
     ["L2-USAGE", "L2-INVOICING"], "CAP-06", "GOAL-2027-01-api-partner"),
    ("fuzzy-name-search", "2026-09-01", "Find clients despite misspelt names", "implementing", "2026-10-06", "PERS-sara", "13 sp",
     ["L2-QUERY", "L2-SEARCH-INDEX"], "CAP-02", "GOAL-2026-Q4-search-under-two-seconds"),
    ("tenant-export", "2026-08-18", "A firm exports all its documents", "verified", "2026-10-23", "ROLE-ENGINE-OWNER", "8 sp",
     ["L2-TENANTS"], "CAP-02", "GOAL-2026-Q4-first-ten-treuhand"),
    ("sso-login", "2026-08-04", "Sign in with Microsoft", "implemented", "2026-10-16", "PERS-marco", "13 sp",
     ["L2-AUTH"], "CAP-05", "GOAL-2026-Q4-first-ten-treuhand"),
    ("search-ranking-v2", "2026-07-07", "Ranking that prefers the client's own documents", "released", "2026-09-29", "PERS-sara", "21 sp",
     ["L2-QUERY"], "CAP-02", "GOAL-2026-Q4-search-under-two-seconds"),
    ("desktop-sync", "2026-06-23", "Desktop folder sync", "killed", "2026-09-08", "ROLE-DECISION-OWNER", "34 sp",
     ["L2-INBOX"], "CAP-01", "ICP-01.H01"),
]
PLAN_STEP = {"draft": "brief", "review": "brief", "approved": "approved", "implementing": "implementing",
             "verified": "verified", "implemented": "implemented", "released": "released", "killed": "killed"}

# Decisions: (key, name, status, since, decided_on, reversibility, decided_by, affects, owner)
DECISIONS = [
    ("DEC-0001", "Run OCR on our own GPU, not a cloud service", "decided", "2026-08-12", "2026-08-12", "hard",
     "PERS-marco", ["L2-OCR"], "ROLE-DECISION-OWNER"),
    ("DEC-0002", "Desktop sync is out of scope for 2026", "decided", "2026-09-08", "2026-09-08", "easy",
     "ROLE-DECISION-OWNER", ["FEAT-2026-06-23-desktop-sync"], "ROLE-DECISION-OWNER"),
    ("DEC-0003", "Bill per processed document, not per seat", "reviewed", "2026-10-20", None, "hard",
     None, ["FEAT-2026-09-15-usage-billing", "L2-USAGE"], "ROLE-COMMERCIAL-OWNER"),
    ("DEC-0004", "Microsoft sign-in only, no Google for now", "proposed", "2026-10-27", None, "easy",
     None, ["FEAT-2026-08-04-sso-login"], "ROLE-DECISION-OWNER"),
    ("DEC-0005", "Rank by client match before recency", "superseded", "2026-09-22", "2026-07-15", "easy",
     "PERS-sara", ["L2-QUERY"], "ROLE-ENGINE-OWNER"),
    ("DEC-0006", "Rank by client match, then recency within the client", "decided", "2026-09-22", "2026-09-22", "easy",
     "PERS-sara", ["L2-QUERY", "FEAT-2026-07-07-search-ranking-v2"], "ROLE-ENGINE-OWNER"),
    ("DEC-0007", "Store documents outside Switzerland", "rejected", "2026-08-30", "2026-08-30", "one-way",
     "ROLE-DECISION-OWNER", ["L2-TENANTS"], "ROLE-DECISION-OWNER"),
]
DECISION_SUPERSEDES = [("DEC-0006", "DEC-0005")]

# Risks: (key, name, status, since, likelihood, impact, review_date, treatment, mitigation, affects/threatens, mitigated by)
RISKS = [
    ("RISK-1", "OCR misreads amounts in low-quality scans", "mitigating", "2026-10-09", "medium", "high", "2026-11-12", "modify",
     "Flag pages under 200 dpi and ask for a rescan.", [("affects", "FEAT-2026-10-02-ocr-tables")], "FEAT-2026-10-02-ocr-tables"),
    ("RISK-2", "Firms do not finish setup and never search", "owned", "2026-10-01", "high", "high", "2026-11-05", "modify",
     "Setup checklist experiment.", [("threatens", "GOAL-2026-Q4-first-ten-treuhand")], None),
    ("RISK-3", "Usage counted twice after a retry", "assessed", "2026-10-15", "medium", "medium", "2026-10-22", None,
     None, [("affects", "FEAT-2026-09-15-usage-billing")], None),   # review date passed: P-review fails
    ("RISK-4", "A Microsoft outage locks firms out", "accepted", "2026-10-16", "low", "medium", "2027-01-15", "retain",
     "Password sign-in stays available.", [("affects", "FEAT-2026-08-04-sso-login")], None),
    ("RISK-5", "Search slower than two seconds for large firms", "closed", "2026-09-29", "medium", "high", "2026-12-01", "modify",
     "Ranking v2 shipped with a per-client index.", [("threatens", "GOAL-2026-Q4-search-under-two-seconds")],
     "FEAT-2026-07-07-search-ranking-v2"),
]
RISK_ACCEPTED_BY = {"RISK-4": "ROLE-DECISION-OWNER"}

# Releases: (key, version, status, since, environment, released_on, components)
RELEASES = [
    ("REL-2026.09.2", "2026.09.2", "announced", "2026-09-30", "prod", "2026-09-29", ["L2-QUERY"]),
    ("REL-2026.10.0", "2026.10.0", "deployed", "2026-10-17", "prod", "2026-10-16", ["L2-AUTH", "L2-TENANTS"]),
    ("REL-2026.10.1", "2026.10.1", "candidate", "2026-10-28", "dev", None, ["L2-QUERY", "L2-SEARCH-INDEX"]),
]

# Accounts and customers: account key -> (name, region); customers: (key, account, state, since, status, tier, seats, started)
ACCOUNTS = {"ACC-aebi-treuhand": ("Aebi Treuhand AG", "Bern"), "ACC-zollinger-partner": ("Zollinger & Partner", "Z\u00fcrich"),
            "ACC-berg-fiduciaire": ("Berg Fiduciaire SA", "Lausanne"), "ACC-wyss-steuer": ("Steuerb\u00fcro Wyss", "Luzern"),
            "ACC-kanzlei-roth": ("Steuerkanzlei Roth", "M\u00fcnchen"), "ACC-tessin-fidu": ("Fiduciaria Ticino SA", "Lugano"),
            "ACC-ledgerline": ("Ledgerline GmbH", "Wien")}
CUSTOMERS = [
    ("CUST-aebi-treuhand", "ACC-aebi-treuhand", "first-query", "2026-09-18", "active", "Team", "12", "2026-09-10"),
    ("CUST-zollinger-partner", "ACC-zollinger-partner", "certificate-issued", "2026-10-22", "onboarding", "Team", "8", "2026-10-15"),
    ("CUST-berg-fiduciaire", "ACC-berg-fiduciaire", "client-created", "2026-10-26", "onboarding", "Start", "4", "2026-10-24"),
    ("CUST-wyss-steuer", "ACC-wyss-steuer", "organisation-created", "2026-10-29", "onboarding", "Start", "3", "2026-10-29"),
]
# Opportunities: (key, account, stage, since, value, next step, next on, meddpicc confirmed of 8, persona, converts to)
OPPORTUNITIES = [
    ("OPP-aebi-team", "ACC-aebi-treuhand", "won", "2026-09-05", "CHF 7'056 per year", "", None, 8, "P-01", "CUST-aebi-treuhand"),
    ("OPP-zollinger-pilot", "ACC-zollinger-partner", "won", "2026-10-13", "CHF 4'704 per year", "", None, 7, "P-02", "CUST-zollinger-partner"),
    ("OPP-berg-start", "ACC-berg-fiduciaire", "contract", "2026-10-21", "CHF 1'392 per year", "Signature from both partners", "2026-11-04", 6, "P-01", None),
    ("OPP-tessin-fidu", "ACC-tessin-fidu", "proof-of-concept", "2026-10-12", "CHF 9'408 per year", "Pilot review with the mandate leaders", "2026-11-10", 4, "P-02", None),
    ("OPP-kanzlei-roth", "ACC-kanzlei-roth", "demo", "2026-10-24", "EUR 5'200 per year", "Demo on their own files", "2026-11-03", 2, "P-03", None),
    ("OPP-ledgerline-api", "ACC-ledgerline", "qualified", "2026-10-08", "EUR 24'000 per year", "Technical call on the Search API", "2026-11-06", 3, "P-04", None),
    ("OPP-wyss-start", "ACC-wyss-steuer", "won", "2026-10-28", "CHF 1'044 per year", "", None, 6, "P-01", "CUST-wyss-steuer"),
    ("OPP-old-lead-bucher", "ACC-kanzlei-roth", "lost", "2026-08-20", "EUR 3'000 per year", "", None, 1, "P-03", None),
]
MEDDPICC = ("metrics", "economic_buyer", "decision_criteria", "decision_process", "paper_process", "pain", "champion", "competition")

# Experiments and campaigns
EXPERIMENTS = [   # (key, name, status, since, keep, kill, ends_on, hypothesis, result, decision)
    ("EXP-setup-checklist", "Setup checklist for new firms", "running", "2026-10-05", "first-search share >= 60 %",
     "first-search share < 35 %", "2026-11-30", "ICP-01.H02", None, None),
    ("EXP-pricing-inbox", "Pricing page shows the e-mail inbox in Team", "decided-keep", "2026-10-19", "Team share >= 40 %",
     "Team share < 25 %", "2026-10-15", "ICP-01.H03", "RES-pricing-inbox", "DEC-0003"),
]
CAMPAIGNS = [   # (key, name, status, since, metric, minimum sample, keep, kill, starts, ends, hypothesis, segment, channel)
    ("CMP-treuhand-webinar", "Webinar: Belege finden in Sekunden", "running", "2026-10-10", "MET-webinar-signups", "60 sign-ups",
     "40 sign-ups", "15 sign-ups", "2026-10-10", "2026-11-20", "ICP-01.H01", "ICP-01", "CH-WEBINAR"),
    ("CMP-de-vendor-outreach", "Outreach to German tax software vendors", "planned", "2026-10-27", "MET-team-tier-share", "20 replies",
     "5 meetings", "1 meeting", "2026-11-09", "2026-12-18", "ICP-02.H01", "ICP-02", "CH-LINKEDIN"),
]
SAMPLES = [("SAMPLE-webinar-signups-2026-10-24", "MET-webinar-signups", "CMP-treuhand-webinar", "23", "2026-10-24"),
           ("SAMPLE-webinar-signups-2026-10-29", "MET-webinar-signups", "CMP-treuhand-webinar", "31", "2026-10-29"),
           ("SAMPLE-first-search-share-2026-10-26", "MET-first-search-share", None, "52 %", "2026-10-26")]


def _src(path: str) -> str:
    return f"{REPO}/{path}"


def build_definitions(g: OfflineGraph, processes: Dict[str, Any], spines: Dict[str, Any]) -> Dict[str, str]:
    """Process, ProcessStep, ValueStream, Spine and SpineLevel nodes as KnowledgeBase's loader writes them
    (seed + definitions section, ``scripts/client_zero/providers/definitions.py``)."""
    import json

    def j(x: Any) -> str:
        return json.dumps(x, separators=(",", ":"), ensure_ascii=False)

    rules = {r["id"]: r for r in processes.get("rules", [])}
    streams = {"VS-build": ("Build", "From an idea to software in production."),
               "VS-sell": ("Sell", "From a first contact to a firm searching its files.")}
    for key, (name, desc) in streams.items():
        g.node("ValueStream", name, key=key, description=desc, facts={"source": _src("handbook/value-streams.md")})
    steps_of: Dict[str, str] = {}
    for p in processes["processes"]:
        slug = p["key"].removeprefix("PROC-")
        facts = {"owner_role": p["owner_role"],
                 "instance_types": ", ".join(p["instance_types"]), "governing_fact": p["governing_fact"],
                 "status_map": j(p["status_map"]), "transitions": j(p["transitions"]),
                 "change_classes": j(p["change_classes"]), "source": _src("handbook/processes.yaml")}
        used = sorted({c for st in p["steps"] for c in st["exit_criteria"] if c in rules})
        if used:
            facts["rules"] = j([rules[c] for c in used])
        for opt in ("exists_today", "pack"):
            if p.get(opt):
                facts[opt] = p[opt]
        if p["context"]:
            facts["context"] = j(p["context"])
        g.node("Process", p["name"], key=p["key"], description=p["description"][:2000], facts=facts)
        g.edge(p["key"], "part_of", p["value_stream"])
        g.edge(p["key"], "owned_by", p["owner_role"])
        n = len(p["steps"])
        for st in p["steps"]:
            sf: Dict[str, Any] = {"order": str(st["order"]), "resting": "yes" if st["resting"] else "no",
                                  "source": _src("handbook/processes.yaml")}
            for k in ("owner_role", "description"):
                if st.get(k):
                    sf[k] = st[k]
            for k in ("produces", "requires"):
                if st[k]:
                    sf[k] = j(st[k])
            if st["exit_criteria"]:
                sf["exit_criteria"] = ", ".join(st["exit_criteria"])
            summary = f"{p['name']}: {st['name']} (Step {st['order']} of {n} in the {p['name']} process.)"
            g.node("ProcessStep", f"{p['name']}: {st['id']}", key=st["key"], description=summary, facts=sf)
            g.edge(p["key"], "has_step", st["key"])
            steps_of[f"{p['key']}/{st['id']}"] = st["key"]
        for t in p["transitions"]:
            g.edge(f"STEP-{slug}-{t['from']}", "reworks_to" if t["rework"] else "transitions_to", f"STEP-{slug}-{t['to']}")
    for sp in spines["spines"]:
        sf = {"levels": j(sp["levels"]), "source": _src("handbook/spines.yaml")}
        if sp.get("branches"):
            sf["branches"] = j(sp["branches"])
        g.node("Spine", sp["name"], key=sp["key"], description=sp["description"][:2000], facts=sf)
        for chain in [sp] + sp.get("branches", []):
            for lv in chain["levels"]:
                lkey = f"{sp['key']}/{lv['id']}"
                lf = {"order": lv["id"], "types": ", ".join(lv["types"]), "branch": "main" if chain is sp else chain["key"],
                      "verified": "yes" if lv.get("verified", True) else "no", "source": _src("handbook/spines.yaml")}
                g.node("SpineLevel", f"{sp['name']}: {lv['name']}"[:500], key=lkey,
                       description=f"Level {lv['id']} of the {sp['name']}: {', '.join(lv['types'])}.", facts=lf)
                g.edge(sp["key"], "has_level", lkey)
    return steps_of


def _instance(g: OfflineGraph, key: str, process: str, step: str, since: str) -> None:
    slug = process.removeprefix("PROC-")
    g.fact(g.id_of(key), "current_step", step, valid_from=since)
    g.edge(key, "instance_of", process)
    g.edge(key, "in_state", f"STEP-{slug}-{step}")


def build_company(g: OfflineGraph) -> None:
    """Lumora's organisation, product, goals and market (before the definitions, which name its roles)."""
    company = g.node("Company", "Lumora AG", key="COMP-lumora", description="Document search for Swiss Treuhand firms, Z\u00fcrich.",
                     facts={"source": _src("handbook/company.md")})
    for key, name in FUNCTIONS.items():
        g.node("Function", name, key=key, description=f"The {name.lower()} function of Lumora.", facts={"source": _src("handbook/company.md")})
        g.edge(key, "part_of", company)
    for key, (name, desc) in PEOPLE.items():
        g.node("Person", name, key=key, description=desc, facts={"source": _src("handbook/people.md")})
        g.edge(key, "member_of", PERSON_FUNCTION[key])
    for key, (name, fn, holders) in ROLES.items():
        g.node("Role", name, key=key, description=f"{name}, held by {', '.join(PEOPLE[h][0] for h in holders)}.",
               facts={"holder": "; ".join(holders), "source": _src("handbook/roles.md")})
        g.edge(key, "member_of", fn)
        for h in holders:
            g.edge(key, "held_by", h)
    for key, (name, desc) in AGENTS.items():
        g.node("Agent", name, key=key, description=desc, facts={"source": _src("handbook/tools.md")})

    # product spine
    for key, (name, audience) in PRODUCTS.items():
        g.node("Product", name, key=key, description=f"{name}, for {audience}.", facts={"audience": audience, "source": _src("product/README.md")})
    for key, (name, product, price) in TIERS.items():
        g.node("PlatformTier", f"Lumora Desk {name}", key=key, description=f"The {name} plan of Lumora Desk, {price} per user and month.",
               facts={"price_per_user_month": price, "source": _src("product/pricing.md")})
        g.edge(key, "part_of", product)
    for key, (name, product) in API_GROUPS.items():
        g.node("ApiServiceGroup", name, key=key, description=f"The {name} of the Lumora API.", facts={"source": _src("product/api.md")})
        g.edge(key, "part_of", product)
    g.node("System", SYSTEM[1], key=SYSTEM[0], description=SYSTEM[2], facts={"source": _src("engineering/README.md")})
    for key, (name, desc) in CONTEXTS.items():
        g.node("BoundedContext", name, key=key, description=desc, facts={"source": _src(f"engineering/{name.lower()}/README.md")})
        g.edge(key, "part_of", SYSTEM[0])
    for key, (name, status, tier, offered, context) in CAPABILITIES.items():
        g.node("Capability", name, key=key, description=f"{name}.", facts={"status": status, "tier": tier,
               "owner": "ROLE-ENGINE-OWNER", "source": _src("product/capabilities.md")})
        for o in offered:
            g.edge(o, "offers", key)
        g.edge(context, "realises", key)
    for key, (name, parent, alloy, docs, desc) in COMPONENTS.items():
        g.node("Component", f"{key} \u2014 {name}", key=key, description=desc,
               facts={"display_name": name, "alloy_status": alloy, "docs_status": docs, "owner": "ROLE-ENGINE-OWNER",
                      "source_paths": f"src/{name.lower().replace(' ', '_')}/", "source": _src(f"engineering/components/{key}.md")})
        g.edge(key, "part_of", parent)
    for key, (name, area, keepers) in INVARIANTS.items():
        g.node("Invariant", name, key=key, description=f"{name}.", facts={"catalog_status": "active", "area": area,
               "source": _src("engineering/invariants.md")})
        for c in keepers:
            g.edge(c, "keeps", key)
    for comp, rows in CONTRACTS.items():
        for suffix, kind, text, inv in rows:
            ckey = f"{comp}/{suffix}"
            g.node("ComponentContract", text, key=ckey, description=text, facts={"kind": kind,
                   "source": _src(f"engineering/components/{comp}.md")})
            g.edge(ckey, "governs", comp)
            if inv:
                g.edge(ckey, "satisfies", inv)
    for a, b in CALLS:
        g.edge(a, "calls", b)
    for comp, paths in SOURCE_FILES.items():
        for path in paths:
            sk = f"SRC-lumora-core/{path}"
            g.node("SourceFile", path, key=sk, description=f"{path} in Lumora Core.", facts={"module": path[:-3].replace("/", "."),
                   "last_commit": "2026-10-24", "source": "git"})
            g.edge(sk, "part_of", comp)
    for key, (name, *_rest) in COMPONENTS.items():
        if COMPONENTS[key][3] != "missing":
            rec = f"REC-component-{key.lower()}"
            g.node("Record", f"{name}: reference", key=rec, description=f"Reference page of the {name} component.",
                   facts={"kind": "reference", "updated_on": "2026-10-12", "stale": "no", "path": f"engineering/components/{key}.md",
                          "source": _src(f"engineering/components/{key}.md")})
            g.edge(rec, "describes", key)

    # goals and market
    for key, (name, period, status, owner) in GOALS.items():
        g.node("Goal", name, key=key, description=f"{name}.", facts={"period": period, "status": status, "owner": owner,
               "source": _src("handbook/goals.md")})
        g.edge(key, "owned_by", owner)
    g.node("Market", MARKET[1], key=MARKET[0], description="Where Lumora sells Lumora Desk.", facts={"source": _src("sales/market.md")})
    for key, (name, region) in SEGMENTS.items():
        g.node("Segment", name, key=key, description=f"{name}: a kind of firm Lumora sells to.", facts={"region": region,
               "source": _src("sales/segments.md")})
        g.edge(key, "in_market", MARKET[0])
    for key, (name, segment, role) in PERSONAS.items():
        g.node("Persona", name, key=key, description=f"{name}; usually the {role.lower()}.", facts={"role_in_deal": role,
               "version": "v1 2026-09-01", "source": _src("sales/personas.md")})
        g.edge(key, "member_of", segment)
    for key, name in CHANNELS.items():
        g.node("Channel", name, key=key, description=f"{name} as a marketing channel.", facts={"source": _src("marketing/channels.md")})
    for key, (name, status, importance, targets) in HYPOTHESES.items():
        g.node("Hypothesis", name, key=key, description=f"{name}.", facts={"status": status, "importance": importance,
               "how_we_know": "interviews with 9 firms (2026-08)" if status != "asserted" else "", "source": _src("sales/hypotheses.md")})
        for t in targets:
            g.edge(key, "targets", t)
    for key, (name, unit, direction) in METRICS.items():
        g.node("Metric", name, key=key, description=f"{name}.", facts={"unit": unit, "direction": direction, "definition": name,
               "owner": "ROLE-ONTOLOGY-STEWARD", "source": _src("marketing/metrics.md")})



def build_instances(g: OfflineGraph) -> None:
    """The process instances and what they produce (after the definitions: they point at the steps)."""
    _feature_plans(g)
    _decisions(g)
    _risks(g)
    _releases(g)
    _customers_and_sales(g)
    _experiments(g)


def _feature_plans(g: OfflineGraph) -> None:
    for i, (slug, date, name, status, since, owner, size, comps, cap, why) in enumerate(PLANS):
        key = f"FEAT-{date}-{slug}"
        g.node("FeaturePlan", name, key=key, description=f"{name}: the plan, its components and its checks.",
               facts={"status": status, "owner": owner, "date": date, "size_points": size, "source": _src(f"plans/{date}-{slug}.md")})
        _instance(g, key, "PROC-feature-lifecycle", PLAN_STEP[status], since)
        g.edge(key, "owned_by", owner)
        for c in comps:
            g.edge(key, "affects", c)
        g.edge(key, "serves", cap)
        g.edge(key, "justified_by" if why.startswith("ICP-") else "motivated_by", why)
        if slug == "email-intake":              # a fresh brief: no invariant, no Alloy check, no record yet
            continue
        for inv in sorted({inv for inv, (_n, _a, keepers) in INVARIANTS.items() if set(keepers) & set(comps)}):
            g.edge(key, "keeps", inv)
        check = f"ALLOY-{slug.replace('-', '_')}.als#check-01"
        g.node("AlloyCheck", f"{name}: model check", key=check, description=f"Bounded Alloy check of the {name.lower()} plan.",
               facts={"outcome": "failed" if slug == "fuzzy-name-search" else "passed", "expected": "no_counterexample",
                      "model": f"models/{slug}.als", "source": "ci"})
        g.edge(check, "verifies", key)
        g.edge(check, "verifies", comps[0])
        if slug != "sso-login":                 # SSO has no plan page: rule R5 reports it
            rec = f"REC-plan-{slug}"
            g.node("Record", f"{name}: explanation", key=rec, description=f"Why and how: {name.lower()}.",
                   facts={"kind": "explanation", "updated_on": since, "stale": "no", "path": f"plans/{date}-{slug}.md",
                          "source": _src(f"plans/{date}-{slug}.md")})
            g.edge(rec, "describes", key)
        if PLAN_STEP[status] in ("implementing", "verified", "implemented", "released"):
            for n in range(2 if slug != "fuzzy-name-search" else 1):
                sha = f"{(i * 7 + n * 3 + 11):x}a{slug[:3].encode().hex()}"[:7].ljust(7, "0")
                commit = f"COMMIT-{sha}"
                g.node("Commit", f"{name} ({n + 1})", key=commit, description=f"Part {n + 1} of {name.lower()}.",
                       facts={"committed_on": since, "author": "ROLE-ENGINE-OWNER", "message": f"{slug}: part {n + 1}", "source": "git"})
                g.edge(commit, "implements", key)
                g.edge(commit, "changes", comps[0])
        if PLAN_STEP[status] in ("verified", "implemented", "released"):
            run = f"TESTRUN-ci-{slug}@{since}"
            g.node("TestRun", f"CI {slug} {since}", key=run, description=f"Test run for {name.lower()}.",
                   facts={"outcome": "passed", "run_date": since, "tests": "214", "failures": "0", "source": "ci"})
            g.edge(run, "verifies", key)
            g.edge(run, "performed_by", "AGENT-ci")
    pm = "PM-usage-billing"
    g.node("PreMortem", "Pre-mortem: pay per processed document", key=pm, description="What would make usage billing fail.",
           facts={"held_on": "2026-10-08", "participants": "PERS-lea; PERS-marco; PERS-sara", "source": _src("plans/pre-mortems.md")})
    g.edge(pm, "examines", "FEAT-2026-09-15-usage-billing")


def _decisions(g: OfflineGraph) -> None:
    for key, name, status, since, decided_on, rev, by, affects, owner in DECISIONS:
        facts = {"status": status, "reversibility": rev, "owner": owner, "source": _src(f"decisions/{key}.md")}
        if decided_on:
            facts["decided_on"] = decided_on
        g.node("Decision", name, key=key, description=f"{name}.", facts=facts)
        _instance(g, key, "PROC-decision", status, since)
        g.edge(key, "owned_by", owner)
        if by:
            g.edge(key, "decided_by", by)
        for a in affects:
            g.edge(key, "affects", a)
    for new, old in DECISION_SUPERSEDES:
        g.edge(new, "supersedes", old)
    g.edge("PM-usage-billing", "examines", "DEC-0003")


def _risks(g: OfflineGraph) -> None:
    for key, name, status, since, lik, imp, review, treat, mitig, links, by in RISKS:
        facts = {"status": status, "likelihood": lik, "impact": imp, "likelihood_impact": f"{lik}/{imp}", "review_date": review,
                 "owner": "ROLE-DECISION-OWNER", "source": _src("handbook/risks.md")}
        if treat:
            facts["treatment"] = treat
        if mitig:
            facts["mitigation"] = mitig
        g.node("Risk", name, key=key, description=f"{name}.", facts=facts)
        _instance(g, key, "PROC-risk-review", status, since)
        g.edge(key, "owned_by", "ROLE-DECISION-OWNER")
        for rel, target in links:
            g.edge(key, rel, target)
        if by:
            g.edge(key, "mitigated_by", by)
        if key in RISK_ACCEPTED_BY:
            g.edge(key, "accepted_by", RISK_ACCEPTED_BY[key])
    g.edge("RISK-3", "raised_in", "PM-usage-billing")


def _releases(g: OfflineGraph) -> None:
    for key, version, status, since, env, on, comps in RELEASES:
        facts = {"version": version, "status": status, "environment": env, "owner": "ROLE-ENGINE-OWNER",
                 "source": _src(f"releases/{version}.md")}
        if on:
            facts["released_on"] = on
        g.node("Release", f"Release {version}", key=key, description=f"Lumora release {version}.", facts=facts)
        _instance(g, key, "PROC-release", status, since)
        g.edge(key, "owned_by", "ROLE-ENGINE-OWNER")
        for c in comps:
            g.edge(key, "changes", c)


def _customers_and_sales(g: OfflineGraph) -> None:
    for key, (name, region) in ACCOUNTS.items():
        g.node("Account", name, key=key, description=f"{name}, {region}.", facts={"region": region, "source": _src("sales/accounts.yaml")})
        g.edge(key, "in_market", MARKET[0])
    for key, acc, state, since, status, tier, seats, started in CUSTOMERS:
        g.node("Customer", ACCOUNTS[acc][0], key=key, description=f"{ACCOUNTS[acc][0]} as a Lumora Desk customer.",
               facts={"status": status, "onboarding_state": state, "account_key": acc, "tier": tier, "seats": seats,
                      "started_on": started, "owner": "ROLE-COMMERCIAL-OWNER", "source": _src("sales/customers.yaml")})
        _instance(g, key, "PROC-customer-onboarding", state, since)
        g.edge(key, "for_account", acc)
        g.edge(key, "owned_by", "ROLE-COMMERCIAL-OWNER")
    for key, acc, stage, since, value, nxt, nxt_on, confirmed, persona, converts in OPPORTUNITIES:
        facts: Dict[str, Any] = {"stage": stage, "account_key": acc, "value": value, "owner": "PERS-daniel",
                                 "source": _src("sales/pipeline.yaml")}
        for i, m in enumerate(MEDDPICC):
            facts[f"meddpicc_{m}"] = "confirmed" if i < confirmed else ("partial" if i == confirmed else "unknown")
        if nxt:
            facts["next_step"], facts["next_step_on"] = nxt, nxt_on
        g.node("Opportunity", f"{ACCOUNTS[acc][0]} \u2014 {stage}", key=key, description=f"Deal with {ACCOUNTS[acc][0]}.", facts=facts)
        _instance(g, key, "PROC-sales", stage, since)
        g.edge(key, "for_account", acc)
        g.edge(key, "targets", persona)
        g.edge(key, "owned_by", "PERS-daniel")
        if converts:
            g.edge(key, "converts_to", converts)


def _experiments(g: OfflineGraph) -> None:
    step = {"registered": "design", "exploration": "design", "running": "measure", "decided-keep": "keep",
            "decided-kill": "kill", "decided-iterate": "iterate", "planned": "design"}
    g.node("Result", "Team share rose to 46 %", key="RES-pricing-inbox", description="46 % of new firms chose Team (n = 26).",
           facts={"verdict": "keep", "sample": "26 firms", "value": "46 %", "measured_on": "2026-10-15", "source": _src("experiments/pricing-inbox.md")})
    for key, name, status, since, keep, kill, ends, hyp, result, decision in EXPERIMENTS:
        g.node("Experiment", name, key=key, description=f"{name}.", facts={"status": status, "keep_threshold": keep,
               "kill_threshold": kill, "minimum_sample": "20 firms", "ends_on": ends, "owner": "ROLE-ONTOLOGY-STEWARD",
               "source": _src(f"experiments/{key.removeprefix('EXP-')}.md")})
        _instance(g, key, "PROC-experiment", step[status], since)
        g.edge(key, "tests", hyp)
        g.edge(key, "performed_by", "PERS-nina")
        if result:
            g.edge(key, "results_in", result)
            g.edge("RES-pricing-inbox", "about", "GOAL-2026-Q4-first-ten-treuhand")
        if decision:
            g.edge(key, "results_in", decision)
    for key, name, status, since, metric, sample, keep, kill, starts, ends, hyp, segment, channel in CAMPAIGNS:
        g.node("Campaign", name, key=key, description=f"{name}.", facts={"status": status, "metric_key": metric,
               "minimum_sample": sample, "keep_threshold": keep, "kill_threshold": kill, "starts_on": starts, "ends_on": ends,
               "owner": "ROLE-ONTOLOGY-STEWARD", "source": _src(f"marketing/{key.removeprefix('CMP-')}.md")})
        _instance(g, key, "PROC-experiment", step[status], since)
        g.edge(key, "tests", hyp)
        g.edge(key, "targets", segment)
        g.edge(key, "measured_by", metric)
        g.edge(key, "via", channel)
        g.edge(key, "owned_by", "ROLE-ONTOLOGY-STEWARD")
    for key, metric, about, value, on in SAMPLES:
        g.node("MetricSample", f"{METRICS[metric][0]} on {on}", key=key, description=f"{METRICS[metric][0]}: {value} on {on}.",
               facts={"value": value, "measured_on": on, "source": _src("marketing/metrics.md")})
        g.edge(key, "measures", metric)
        if about:
            g.edge(key, "about", about)


def events(g: OfflineGraph) -> List[Tuple[str, str]]:
    """A few dated business events (the flow view's timeline)."""
    rows = [("EVT-2026-10-20-decision-review", "Decision review", "2026-10-20", "ROLE-DECISION-OWNER",
             ["DEC-0003", "FEAT-2026-09-15-usage-billing"]),
            ("EVT-2026-10-23-export-verified", "Tenant export verified in staging", "2026-10-23", "PERS-marco",
             ["FEAT-2026-08-18-tenant-export", "L2-TENANTS"]),
            ("EVT-2026-10-28-aebi-pilot-call", "Pilot call with Aebi Treuhand", "2026-10-28", "PERS-daniel", ["OPP-aebi-team"])]
    for key, name, on, actor, touches in rows:
        g.node("Event", name, key=key, description=f"{name}.", facts={"occurred_at": on, "actor": actor, "source": _src("handbook/log.md")})
        g.edge(key, "performed_by", actor)
        for t in touches:
            g.edge(key, "touches", t)
    return [(r[0], r[2]) for r in rows]
