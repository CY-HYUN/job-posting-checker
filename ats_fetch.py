"""Read the target companies' open postings from their careers systems in one pass and keep the leads worth checking.

    python ats_fetch.py targets.csv --out leads.jsonl        # AI/ML/data titles, not internships, placed in France or open to it
    python ats_fetch.py targets.csv --out leads.jsonl --all  # keep every title and place
    python ats_fetch.py targets.csv --drop-senior            # also drop senior, lead and experienced-hire titles
    python ats_fetch.py --selftest                           # prove the parsers and filters on recorded shapes

targets.csv columns: company, ats, slug, site. `site` is only for Workday ("wd3/Criteo_Career_Site": the host label and the
career-site name from the careers URL) and Lever EU accounts ("eu"). The endpoints are the ones in docs/endpoints.md,
each answered a live request on 2026-09-27 or 2026-09-28; the field names below come from those responses.

Each lead is one JSON line: company, ats, id, title, location, remote, posted, url, text (plain). `id` is the ATS's own
posting id or URL, so it is stable across runs, unlike the Indeed connector's per-call ids. A target that fails (HTTP error,
wrong slug, changed shape) is named in the summary and makes the exit code 1; it is never skipped silently.
With --blocks FILE, a board that answers 429 with a long Retry-After (Workable asked for 83,988 s on 2026-09-29) is written
to FILE with the end of its wait, and every run before that time skips the board instead of asking and failing again.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import email.utils
import html
import http.client
import io
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from posting_check import FRANCE_PLACE, eligibility

TIMEOUT, RETRY_WAIT = 25, 3
UA = {"User-Agent": "job-posting-checker/1.0 (+https://github.com/CY-HYUN/job-posting-checker)"}
# per search; Workday caps a page at 20 rows. 100 pages = 2,000 rows, the most a Workday board lists (Thales, NVIDIA);
# at 50, NVIDIA's "data science" search stopped at 1,000 of 1,091 (2026-09-29). A search that still fills them warns.
WORKDAY_PAGE, WORKDAY_MAX_PAGES = 20, 100
WORKDAY_QUERIES = ("machine learning", "data scientist", "data science", "artificial intelligence",
                   "intelligence artificielle", "deep learning", "LLM", "NLP", "computer vision", "research engineer")

# "Member of Technical Staff" is how AI labs title research-engineer roles (Cohere and H Company, Paris, 2026-09-29).
# "A.I." stands outside the group: it ends in a dot, and a dot before the closing \b never matches.
TITLE_KEEP = re.compile(r"\ba\.i\.|\b(ai|ml|mlops|llm|nlp|machine[- ]learning|deep[- ]learning|data[- ]scien\w*|scientist|"
                        r"research (engineer|scientist)|applied (ai|scientist)|computer vision|gen(erative)?[- ]?ai|"
                        r"intelligence artificielle|ia|member of (the )?technical staff)\b", re.I)
# plurals: "AMI Scientist/Interns" reached tier A on 2026-09-29; "early-stage" is a startup, not an internship
TITLE_DROP = re.compile(r"\b(interns?|internships?|(?<!-)stages?|stagiaires?|alternan\w*|apprenti\w*|work[- ]study|ph\.?d|"
                        r"th[eè]se|sales|account executive|recruiter)\b", re.I)
# senior and experienced-hire titles, dropped only with --drop-senior
# "Lead Scoring" and "Head Pose Estimation" name the work, "Product Manager" a job, not a grade (review 2026-09-29)
SENIOR_TITLE = re.compile(r"\b(s[ée]nior|sr\.?|(?<!technical )staff|principal|lead(?:er)?(?!\s+(?:scoring|generation|gen)\b)|head of"
                          r"|director|directeur|directrice|responsable|vp|vice[- ]president|chief|expert"
                          r"|(?<!product )(?<!project )(?<!program )(?<!account )(?<!ads )(?<!content )(?<!community )(?<!bid )manager"
                          r"|confirm(?:[ée]e?|ed)|exp[ée]riment[ée]e?|experienced)\b", re.I)
# "Data Scientist Junior/Confirmé", "(Experienced or Junior)": open to a junior, so not out
JUNIOR_TOO = re.compile(r"\b(junior|jr\.?|d[ée]butant\w*|graduate|entry[- ]level|jeune dipl[ôo]m[ée]\w*)", re.I)


def is_senior(title: str) -> bool:
    return bool(SENIOR_TITLE.search(title)) and not JUNIOR_TOO.search(title)


# ---------------------------------------------------------------- http

def read_url(req: urllib.request.Request) -> str:
    """One retry, 3 s later, for a timeout, a dropped connection or a 5xx (Algolia timed out once on 2026-09-29);
    a 4xx such as a wrong slug fails at once."""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        # urllib raises a dropped connection raw (RemoteDisconnected, ConnectionResetError, IncompleteRead) and an SSL
        # error during the read as ssl.SSLError; OSError holds those, URLError and timeouts. 429 means "slow down".
        except (OSError, http.client.HTTPException) as e:
            if attempt == 2 or (isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429):
                raise
            # a 429 is retried only when it asks for a short wait: Workable asked for 83,988 s on 2026-09-29, and
            # asking again 3 s later only extends a ban like that
            wait = (e.headers.get("Retry-After") or "").strip() if isinstance(e, urllib.error.HTTPError) and e.headers else ""
            if isinstance(e, urllib.error.HTTPError) and e.code == 429 and wait and not (wait.isdigit() and int(wait) <= 10):
                raise
            time.sleep(int(wait) if wait.isdigit() else RETRY_WAIT)


def get_json(url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(UA, **({"Content-Type": "application/json"} if body is not None else {}))
    return json.loads(read_url(urllib.request.Request(url, data=data, headers=headers)))


def get_text(url: str) -> str:
    return read_url(urllib.request.Request(url, headers=UA))


# ---------------------------------------------------------------- boards that refused this machine

def refused_until(retry_after: str | None, now: dt.datetime) -> dt.datetime | None:
    """When a 429's Retry-After ends: seconds or an HTTP date (RFC 9110); None when it is neither."""
    wait = (retry_after or "").strip()
    if wait.isdigit():
        return now + dt.timedelta(seconds=int(wait))
    try:
        return email.utils.parsedate_to_datetime(wait)
    except (TypeError, ValueError):
        return None


def refusals(path: str | None, now: dt.datetime) -> dict[str, str]:
    """{ats: until} for the boards the blocks file says still refuse this machine at `now`. A file that does not parse
    stops the run: it is the only record of a ban, and guessing past it would ask the board again."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        held = json.load(f)
    return {ats: until for ats, until in held.items() if dt.datetime.fromisoformat(until) > now}


def keep_refusals(path: str, refused: dict[str, str | None], now: dt.datetime) -> dict[str, str]:
    """Write each refusal's end into the blocks file, keeping a later end already there. A 429 without a usable
    Retry-After is not written (its row already failed loudly). Returns what was written."""
    held = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            held = json.load(f)
    wrote = {}
    for ats, wait in refused.items():
        until = refused_until(wait, now)
        if until is None:
            continue
        if ats in held and dt.datetime.fromisoformat(held[ats]) > until:
            until = dt.datetime.fromisoformat(held[ats])
        held[ats] = wrote[ats] = until.isoformat(timespec="seconds")
    if wrote:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(held, f, indent=1)
    return wrote


def plain(s: str | None) -> str:
    """Text without tags. A list item, paragraph or line break stays a line of its own: posting_check reads a
    posting line by line, and "5+ years" in one <li> read as nothing when every item ran into one line (review 5)."""
    s = re.sub(r"(?i)<\s*(?:br\s*/?|/p|/li|/h[1-6]|/div|/tr)\s*>|<\s*li\b[^>]*>", "\n", html.unescape(s or ""))
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[^\S\n]+", " ", s)
    return re.sub(r" *\n[\s]*", "\n", s).strip()


def lead(company, ats, id_, title, location, remote, posted, url, text) -> dict:
    return {"company": company, "ats": ats, "id": str(id_ or url), "title": (title or "").strip(),
            "location": (location or "").strip(), "remote": remote, "posted": posted or "", "url": url or "",
            "text": plain(text)}


# ---------------------------------------------------------------- one parser per ATS (pure: payload in, leads out)

def parse_ashby(c, p):
    out = []
    for j in p["jobs"]:
        places = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)]
        out.append(lead(c, "ashby", j.get("id") or j.get("jobUrl"), j.get("title"), "; ".join(x for x in places if x),
                        j.get("isRemote") or j.get("workplaceType"), j.get("publishedAt"), j.get("jobUrl"), j.get("descriptionPlain")))
    return out


def parse_greenhouse(c, p):
    return [lead(c, "greenhouse", j.get("id"), j.get("title"), (j.get("location") or {}).get("name"), None,
                 j.get("updated_at"), j.get("absolute_url"), j.get("content")) for j in p["jobs"]]


def parse_lever(c, p):
    return [lead(c, "lever", j.get("id"), j.get("text"), (j.get("categories") or {}).get("location"),
                 j.get("workplaceType"), j.get("createdAt"), j.get("hostedUrl"), j.get("descriptionPlain")) for j in p]


def parse_workable(c, p):
    return [lead(c, "workable", j.get("shortcode") or j.get("application_url"), j.get("title"),
                 ", ".join(x for x in (j.get("city"), j.get("country")) if x), j.get("telecommuting"),
                 j.get("published_on"), j.get("application_url") or j.get("url"), j.get("description")) for j in p["jobs"]]


def parse_teamtailor(c, p):
    out = []
    for j in p["items"]:
        jp = j.get("_jobposting") or {}
        locs = jp.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        place = "; ".join(", ".join(x for x in ((l.get("address") or {}).get("addressLocality"), (l.get("address") or {}).get("addressCountry")) if x)
                          for l in locs if isinstance(l, dict))
        out.append(lead(c, "teamtailor", j.get("id"), j.get("title"), place, jp.get("jobLocationType"),
                        j.get("date_published"), j.get("url"), j.get("content_html")))
    return out


def parse_recruitee(c, p):
    return [lead(c, "recruitee", j.get("id"), j.get("title"), j.get("location"), j.get("remote"), j.get("published_at"),
                 j.get("careers_url"), (j.get("description") or "") + " " + (j.get("requirements") or ""))
            for j in p["offers"] if j.get("status", "published") == "published"]


def parse_homerun(c, xml_text):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    if root.tag != "{http://www.w3.org/2005/Atom}feed":   # a changed feed must fail the target, not read as 0 jobs
        raise ValueError(f"not an Atom feed: root element {root.tag}")
    out = []
    for e in root.findall("a:entry", ns):
        link = e.find("a:link", ns)
        body = e.find("a:content", ns) if e.find("a:content", ns) is not None else e.find("a:summary", ns)
        out.append(lead(c, "homerun", (e.findtext("a:id", "", ns)), e.findtext("a:title", "", ns), "", None,
                        e.findtext("a:updated", "", ns), link.get("href") if link is not None else "", body.text if body is not None else ""))
    return out


def workday_detail(c, base, info):
    loc = "; ".join([info.get("location") or ""] + list(info.get("additionalLocations") or []))
    country = (info.get("country") or {}).get("descriptor", "")
    return lead(c, "workday", info.get("jobReqId") or info.get("externalUrl"), info.get("title"),
                ", ".join(x for x in (loc, country) if x), info.get("remoteType"), info.get("postedOn"),
                info.get("externalUrl"), info.get("jobDescription"))


# ---------------------------------------------------------------- fetch

def fetch(t: dict, keep_all: bool = False, senior_ok: bool = True) -> list[dict]:
    c, ats, slug, site = t["company"], t["ats"].lower(), t["slug"], (t.get("site") or "").strip()
    if ats == "ashby":
        return parse_ashby(c, get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"))
    if ats == "greenhouse":
        return parse_greenhouse(c, get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"))
    if ats == "lever":
        host = "api.eu.lever.co" if site == "eu" else "api.lever.co"
        return parse_lever(c, get_json(f"https://{host}/v0/postings/{slug}?mode=json"))
    if ats == "workable":
        return parse_workable(c, get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"))
    if ats == "teamtailor":
        return parse_teamtailor(c, get_json(f"https://{slug}.teamtailor.com/jobs.json"))
    if ats == "recruitee":
        return parse_recruitee(c, get_json(f"https://{slug}.recruitee.com/api/offers"))
    if ats == "homerun":
        return parse_homerun(c, get_text(f"https://feed.homerun.co/{slug}"))
    if ats == "workday":
        wd, career_site = site.split("/", 1)
        host = f"https://{slug}.{wd}.myworkdayjobs.com"
        # Workday's job API is the careers page's own back end, not a published feed, so the page's robots.txt decides.
        # Criteo and Thales disallow theirs (2026-09-29); SmartRecruiters was left out for the same reason.
        base = f"{host}/wday/cxs/{slug}/{career_site}"
        robots = robots_txt(host)
        for path in (f"{host}/{career_site}/", f"{base}/jobs"):   # the page a person opens, and the API behind it
            if not site_allowed(robots, path):
                raise Skipped(f"robots.txt disallows {urllib.parse.urlsplit(path).path}: check the careers page by hand")
        rows = {}
        # Big boards list thousands (Thales and NVIDIA show 2,000), so search the role words instead of paging them all,
        # inside the board's own France filter when it has one (Accenture "machine learning": 2,000 rows, 25 in France).
        # ponytail: a posting whose only place is "Remote" or "EMEA" falls outside that filter; widen here if one is missed.
        facets = {} if keep_all else france_facets(get_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0,
                                                                              "searchText": ""}))
        for q in WORKDAY_QUERIES:
            for r in workday_pages(lambda off, q=q: get_json(f"{base}/jobs", {"appliedFacets": facets, "limit": WORKDAY_PAGE,
                                                                                  "offset": off, "searchText": q}), f"{c} {q!r}"):
                rows.setdefault(r.get("externalPath"), r)
        # the list has no text; open only the titles the filter would keep (all of them with --all)
        keep = [r for r in rows.values() if keep_all or keep_title(r.get("title", ""), senior_ok)]
        with ThreadPoolExecutor(4) as ex:  # NVIDIA keeps a few hundred titles; one by one took minutes
            infos = list(ex.map(lambda r: get_json(base + r["externalPath"])["jobPostingInfo"], keep))
        return [workday_detail(c, base, i) for i in infos], len(rows)
    raise ValueError(f"unknown ats {ats!r}")


class Skipped(Exception):
    """A target the site asks bots not to read: named in the summary, not counted as a failure."""


def robots_txt(host: str) -> str:
    try:
        return get_text(f"{host}/robots.txt")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""          # no robots.txt: nothing is disallowed
        raise


def site_allowed(robots: str, url: str) -> bool:
    """RFC 9309 for the `*` group (no robots.txt names this script): the longest matching rule decides, a tie goes to
    Allow, `*` and a final `$` are patterns. The stdlib parser takes the first match instead, so "Allow: /" before
    "Disallow: /Careers/" would read as allowed."""
    path = urllib.parse.urlsplit(url).path or "/"
    rules, agents, in_rules = [], [], False
    for raw in robots.lstrip("\ufeff").splitlines():  # a leading BOM would hide the first User-agent line: fail open
        key, _, val = raw.split("#", 1)[0].partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(val)
        elif key in ("allow", "disallow"):
            in_rules = True
            if "*" in agents and val:
                rules.append((key == "allow", val))
    best = None  # (length, allow)
    for allow, pat in rules:
        rx = re.escape(pat).replace(r"\*", ".*")
        if pat.endswith("$"):
            rx = rx[:-2] + "$"           # re.escape turned the end anchor into "\$"
        if re.match(rx, path) and (best is None or (len(pat), allow) > best):
            best = (len(pat), allow)
    return best is None or best[1]


def france_facets(page: dict) -> dict:
    """The appliedFacets that keep a Workday search to France: every facet value named "France", nested groups
    included (Accenture keeps locationCountry inside locationMainGroup). {} when the board has none: search it all."""
    out: dict = {}

    def walk(facets):
        for f in facets or []:
            for v in f.get("values") or []:
                if "values" in v:
                    walk([v])
                elif re.fullmatch(r"\s*france\s*", v.get("descriptor") or "", re.I) and v.get("id") and f.get("facetParameter"):
                    out.setdefault(f["facetParameter"], []).append(v["id"])
    walk(page.get("facets"))
    # Workday ANDs different facets: a site named "France" next to the country would shrink the search to that site
    pick = next((k for k in out if "country" in k.lower()), next(iter(out), None))
    return {pick: out[pick]} if pick else {}


def workday_pages(get_page, label: str = "") -> list[dict]:
    """Every row of one Workday search. Workday sends `total` on the first page only (later pages say 0,
    checked on criteo 2026-09-29), so paging never stops on `total`: it stops on a short page, or on a page
    that brings no row not already read (Renault's board kept returning full pages past its 40 results,
    2026-09-29). A search that fills every allowed page says so on stderr with the first page's total."""
    rows, paths, off, total = [], set(), 0, None
    for _ in range(WORKDAY_MAX_PAGES):
        got = get_page(off)
        total = got.get("total") if total is None else total
        page = got["jobPostings"]
        new = []
        for r in page:
            if r.get("externalPath") not in paths:
                paths.add(r.get("externalPath"))
                new.append(r)
        rows += new
        if len(page) < WORKDAY_PAGE or not new:
            return rows
        off += WORKDAY_PAGE
    print(f"warning: workday search {label} stopped at {len(rows)} rows of {total}; narrow the search words", file=sys.stderr)
    return rows


def keep_title(title: str, senior_ok: bool = True) -> bool:
    return bool(TITLE_KEEP.search(title)) and not TITLE_DROP.search(title) and (senior_ok or not is_senior(title))


def keep_place(location: str) -> bool:
    """A place in France, a remote scope that holds France (Europe, EMEA, worldwide) or a bare "Remote" (posting_check
    asks about it later). The ATS remote flag is not read: Mistral marks Palo Alto roles remote, Criteo marks Barcelona
    hybrid, and neither is open to someone in France (2026-09-29 run, 146 of 271 leads were kept on such a flag or word)."""
    parts = [p.strip() for p in re.split(r"[;|]", location) if p.strip()]
    if not parts:
        return True
    for p in parts:
        label, hits = eligibility(p, True)
        # "Paris, FR": Teamtailor writes the country as its ISO code, and a suburb name alone is in no list
        if re.search(FRANCE_PLACE, p, re.I) or re.search(r"(?:^|,\s*)FR\s*$", p) or label == "allow" or (label == "unclear" and hits):
            return True
    return False


def run(targets: list[dict], keep_all: bool, senior_ok: bool = True, skip_ats: tuple = (), held: dict | None = None,
        refused: dict | None = None):
    """Report rows: company, ats, seen, kept, placed abroad (right title, place not open from France), status.
    A board named in skip_ats is not asked, nor one `held` names ({ats: until}, from the blocks file). A board that
    answers 429 with a long wait fails its row and is not asked again this run; its Retry-After goes into `refused`."""
    held = held or {}
    refused = {} if refused is None else refused
    leads, report = [], []
    for t in targets:
        ats = str(t.get("ats") or "").lower()
        if ats in skip_ats:
            report.append((t["company"], t["ats"], None, None, None, "SKIPPED --skip-ats"))
            continue
        if ats in held or ats in refused:
            why = f"refuses this machine until {held[ats]}" if ats in held else "refused this run"
            report.append((t["company"], t["ats"], None, None, None, f"SKIPPED {ats} {why}"))
            continue
        try:
            got = fetch(t, keep_all, senior_ok)
        except Skipped as e:
            report.append((t["company"], t["ats"], None, None, None, f"SKIPPED {e}"))
            continue
        except Exception as e:  # reported by name below and in the exit code
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                refused[ats] = e.headers.get("Retry-After") if e.headers else None
            report.append((t["company"], t["ats"], None, None, None, f"FAILED {type(e).__name__}: {e}"))
            continue
        got, seen = got if isinstance(got, tuple) else (got, len(got))
        titled = got if keep_all else [x for x in got if keep_title(x["title"], senior_ok)]
        kept = titled if keep_all else [x for x in titled if keep_place(x["location"])]
        leads += kept
        report.append((t["company"], t["ats"], seen, len(kept), len(titled) - len(kept), ""))
    return leads, report


# ---------------------------------------------------------------- selftest

def selftest() -> int:
    ok = True

    def expect(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print(f"FAIL {name}: got {got!r} want {want!r}")

    wd_info = {"title": "Senior Machine Learning Engineer - Causality", "jobDescription": "<p>Build <b>models</b></p>",
               "location": "Paris", "additionalLocations": ["Grenoble"], "country": {"descriptor": "France"},
               "externalUrl": "https://x.wd3.myworkdayjobs.com/S/job/Paris/r1", "postedOn": "Posted 28 Days Ago",
               "remoteType": "Hybrid", "jobReqId": "r20135"}
    w = workday_detail("Criteo", "", wd_info)
    expect("workday id is the req id", w["id"], "r20135")
    expect("workday place", w["location"], "Paris; Grenoble, France")
    expect("workday text is plain", w["text"], "Build models")
    tt = parse_teamtailor("P", {"items": [{"id": "u1", "title": "ML Engineer - CDI - Paris", "url": "https://p/1",
                                            "date_published": "2026-07-08", "content_html": "<p>x &amp; y</p>",
                                            "_jobposting": {"jobLocation": [{"address": {"addressLocality": "Paris", "addressCountry": "FR"}}]}}]})
    expect("teamtailor place from JSON-LD", tt[0]["location"], "Paris, FR")
    expect("teamtailor entities decoded", tt[0]["text"], "x & y")
    rc = parse_recruitee("K", {"offers": [{"id": 7, "title": "Spontaneous Application", "location": "Paris, France",
                                           "status": "published", "careers_url": "u", "description": "d", "requirements": "r"},
                                          {"id": 8, "title": "Old", "status": "closed"}]})
    expect("recruitee keeps published only", [x["id"] for x in rc], ["7"])
    gh = parse_greenhouse("D", {"jobs": [{"id": 5, "title": "Data Scientist", "location": {"name": "Paris, France"},
                                          "content": "&lt;p&gt;Hello&lt;/p&gt;", "absolute_url": "u"}]})
    expect("greenhouse escaped html becomes text", gh[0]["text"], "Hello")
    expect("list items stay lines", plain("<ul><li>5+ years of <b>Python</b></li>\n<li>Nice to have</li></ul><p>Paris</p>"),
           "5+ years of Python\nNice to have\nParis")
    # senior and experienced-hire titles are out with --drop-senior; "Technical Staff" is an AI-lab engineer title
    for title, want in [("Senior Machine Learning Engineer", False), ("Lead Data Scientist", False), ("Staff ML Engineer", False),
                        ("Data Scientist Confirmé", False), ("ML Engineering Manager", False), ("Head of AI", False),
                        ("AI Engineer Expert", False), ("Sr. Data Scientist", False),
                        ("Data Scientist Sénior", False), ("Confirmed Machine Learning Engineer (Productivity)", False),
                        ("Experienced Data Scientist", False), ("Directeur Data & IA", False), ("Head of Data Science", False),
                        ("Tech Lead ML", False), ("Data Scientist Junior/Confirmé (H/F)", True),
                        ("Data Scientist (Experienced or Junior)", True), ("Machine Learning Engineer - Lead Scoring", True),
                        ("Computer Vision Engineer - Head Pose Estimation", True), ("ML Engineer - Ads Manager", True),
                        # "Leader" is the lead's grade too (Ippon on Indeed, 2026-09-29); "Leadership" names a field
                        ("AI/ML TECHNICAL LEADER (H/F)", False), ("Data Scientist, Leadership Analytics", True)]:
        expect(f"title {title!r}", keep_title(title, senior_ok=False), want)
    expect("senior titles are kept by default", keep_title("Senior Machine Learning Engineer"), True)
    for title, want in [("AI Engineer", True), ("Data Scientist", True),
                        ("Ingénieur IA", True), ("Machine Learning Engineer Intern", False), ("Stage - Data Scientist", False),
                        ("GenAI Sales Specialist", False), ("Chief Operations Officer (COO)", False), ("Research Engineer", True),
                        ("Member of Technical Staff, Training Infrastructure", True),
                        ("AMI Scientist/Interns - Geometry and 3D Vision", False), ("Data Scientist, Early-stage Ventures", True),
                        ("A.I. Engineer", True)]:
        expect(f"title {title!r}", keep_title(title, senior_ok=False), want)
    pages = {0: {"total": 45, "jobPostings": [{"externalPath": f"/a{i}"} for i in range(20)]},
             20: {"total": 0, "jobPostings": [{"externalPath": f"/b{i}"} for i in range(20)]},
             40: {"total": 0, "jobPostings": [{"externalPath": f"/c{i}"} for i in range(5)]}}
    expect("workday pages past the first-page-only total", len(workday_pages(lambda off: pages[off])), 45)
    # Accenture's facets as they came on 2026-09-29, values cut: France sits one level down
    acc = {"facets": [{"facetParameter": "timeType", "values": [{"descriptor": "Full time", "id": "t1"}]},
                      {"facetParameter": "locationMainGroup", "values": [
                          {"facetParameter": "locationCountry", "descriptor": "Country", "values": [
                              {"descriptor": "Spain", "id": "es"}, {"descriptor": "France", "id": "54c5"}]},
                          {"facetParameter": "locations", "descriptor": "City", "values": [
                              {"descriptor": "Paris", "id": "p1"}, {"descriptor": "France Travail", "id": "x"}]}]}]}
    expect("workday France facet, nested", france_facets(acc), {"locationCountry": ["54c5"]})
    expect("workday board without a France facet searches everything", france_facets({"facets": []}), {})
    skipped = run([{"company": "HF", "ats": "Workable", "slug": "huggingface", "site": ""}], False, skip_ats=("workable",))
    expect("--skip-ats leaves the board alone", skipped, ([], [("HF", "Workable", None, None, None, "SKIPPED --skip-ats")]))
    short_row = run([{"company": "OnlyAName", "ats": None, "slug": None, "site": None}], False)
    # a board that refused this machine: remembered until its wait ends, asked once per run at most (2026-09-29)
    now = dt.datetime(2026, 9, 29, 7, 13, 24, tzinfo=dt.timezone.utc)
    expect("Retry-After in seconds", refused_until("83988", now).isoformat(), "2026-09-30T06:33:12+00:00")
    expect("Retry-After as a date", refused_until("Wed, 30 Sep 2026 12:00:00 GMT", now).isoformat(), "2026-09-30T12:00:00+00:00")
    expect("Retry-After that is neither", refused_until("soon", now), None)
    real, asked = globals()["fetch"], []

    def refuse(t, keep_all, senior_ok=True):
        asked.append(t["slug"])
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "83988"}, None)
    globals()["fetch"] = refuse
    try:
        refused = {}
        _, rep = run([{"company": c, "ats": "Workable", "slug": c, "site": ""} for c in ("a", "b")], False, refused=refused)
        expect("a refusing board is asked once per run", asked, ["a"])
        expect("its Retry-After is handed back", refused, {"workable": "83988"})
        expect("the next row on it is skipped, not failed", [r[-1].split(" ")[0] for r in rep], ["FAILED", "SKIPPED"])
        with tempfile.TemporaryDirectory() as tmp:
            blocks, csv_in, out = (os.path.join(tmp, n) for n in ("blocks.json", "t.csv", "o.jsonl"))
            expect("no blocks file, no refusals", refusals(blocks, now), {})
            keep_refusals(blocks, {"workable": "83988", "lever": None}, now)
            keep_refusals(blocks, {"workable": "60"}, now)            # a shorter wait does not shorten a longer one
            expect("a refusal is kept until it ends", refusals(blocks, now), {"workable": "2026-09-30T06:33:12+00:00"})
            expect("and let go after", refusals(blocks, now + dt.timedelta(days=1)), {})
            with open(csv_in, "w", encoding="utf-8") as f:
                f.write("company,ats,slug,site\nHF,workable,huggingface,\n")
            os.remove(blocks)
            asked.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                first = main([csv_in, "--out", out, "--blocks", blocks])       # the 429 fails the row and is written
                second = main([csv_in, "--out", out, "--blocks", blocks])      # the next run does not ask
            expect("main writes the refusal, then skips the board", (first, second, asked, list(refusals(
                blocks, dt.datetime.now(dt.timezone.utc)))), (1, 0, ["huggingface"], ["workable"]))
    finally:
        globals()["fetch"] = real
    expect("a row with no ats fails alone", short_row[1][0][-1].startswith("FAILED"), True)
    two = {"facets": [{"facetParameter": "locations", "values": [{"descriptor": "France", "id": "site"}]},
                      {"facetParameter": "locationCountry", "values": [{"descriptor": "France", "id": "fr"}]}]}
    expect("workday: one France facet, the country, not an AND with a site", france_facets(two), {"locationCountry": ["fr"]})
    criteo = "User-agent: *\nAllow: /EPITASchoolFair2026/\nDisallow: /Criteo_Career_Site/\nDisallow: /refreshFacet/"  # 2026-09-29
    expect("robots.txt disallow is honoured", site_allowed(criteo, "https://c.wd3.myworkdayjobs.com/Criteo_Career_Site/"), False)
    expect("robots.txt allow passes", site_allowed(criteo, "https://c.wd3.myworkdayjobs.com/EPITASchoolFair2026/"), True)
    expect("no robots.txt allows", site_allowed("", "https://n.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/"), True)
    expect("the longest rule wins, not the first", site_allowed("User-agent: *\nAllow: /\nDisallow: /Careers/", "https://t/Careers/x"), False)
    expect("a longer Allow beats a shorter Disallow", site_allowed("User-agent: *\nDisallow: /\nAllow: /jobs/", "https://t/jobs/1"), True)
    sr = "User-agent: LinkedInBot\nAllow: /v1/companies/\nUser-agent: *\nDisallow: /"  # api.smartrecruiters.com, 2026-09-29
    expect("another agent's Allow does not open the * group", site_allowed(sr, "https://api/v1/companies/x"), False)
    expect("a leading BOM does not open the file", site_allowed("\ufeffUser-agent: *\nDisallow: /Careers/", "https://t/Careers/"), False)
    try:
        parse_homerun("K", "<rss><channel/></rss>")
        expect("a feed that is not Atom fails", "no error", "ValueError")
    except ValueError:
        pass
    # read_url: one retry for a timeout or a 5xx, none for a 4xx
    real_open, real_wait = urllib.request.urlopen, globals()["RETRY_WAIT"]
    tries = []

    def flaky(errors):
        def urlopen(req, timeout=None):
            tries.append(1)
            if errors:
                raise errors.pop(0)
            return io.BytesIO(b'{"ok": 1}')
        return urlopen

    def http_error(code, retry_after=None):
        return urllib.error.HTTPError("u", code, "x", {"Retry-After": retry_after} if retry_after else None, None)

    globals()["RETRY_WAIT"] = 0
    try:
        for name, errors, want in (("a timeout is tried again", [TimeoutError("t")], (2, "ok")),
                                   ("a 503 is tried again", [http_error(503)], (2, "ok")),
                                   ("a dropped connection is tried again", [http.client.RemoteDisconnected("x")], (2, "ok")),
                                   ("a reset connection is tried again", [ConnectionResetError("x")], (2, "ok")),
                                   ("an SSL error in the read is tried again", [ssl.SSLError("x")], (2, "ok")),
                                   ("a 429 is tried again", [http_error(429)], (2, "ok")),
                                   ("a 429 asking 2 s is tried again", [http_error(429, "2")], (2, "ok")),
                                   ("a 429 asking a day is not", [http_error(429, "83988")], (1, "HTTPError")),
                                   ("a 429 with a date is not", [http_error(429, "Wed, 30 Sep 2026 12:00:00 GMT")], (1, "HTTPError")),
                                   ("a 404 fails at once", [http_error(404)], (1, "HTTPError")),
                                   ("two timeouts fail", [TimeoutError("t"), TimeoutError("t")], (2, "TimeoutError"))):
            tries.clear()
            urllib.request.urlopen = flaky(list(errors))
            try:
                got = "ok" if get_json("https://x")["ok"] == 1 else "?"
            except Exception as e:
                got = type(e).__name__
            expect(name, (len(tries), got), want)
    finally:
        urllib.request.urlopen, globals()["RETRY_WAIT"] = real_open, real_wait
    real_robots = globals()["robots_txt"]
    globals()["robots_txt"] = lambda host: "User-agent: *\nDisallow: /wday/"
    try:
        fetch({"company": "W", "ats": "workday", "slug": "w", "site": "wd3/S"})
        expect("robots.txt on the API path skips the target", "read", "Skipped")
    except Skipped as e:
        expect("the skip names the API path", "/wday/cxs/w/S/jobs" in str(e), True)
    finally:
        globals()["robots_txt"] = real_robots
    # the France filter goes on every search, except under --all ("keep every title and place")
    sent, real_get = [], globals()["get_json"]
    fr_page = {"facets": [{"facetParameter": "locationCountry", "values": [{"descriptor": "France", "id": "fr"}]}],
               "total": 0, "jobPostings": []}
    globals()["robots_txt"] = lambda host: ""
    globals()["get_json"] = lambda url, body=None: sent.append((body or {}).get("appliedFacets")) or fr_page
    try:
        for keep_all, want in ((False, {"locationCountry": ["fr"]}), (True, {})):
            sent.clear()
            fetch({"company": "W", "ats": "workday", "slug": "w", "site": "wd3/S"}, keep_all=keep_all)
            expect(f"workday facets with keep_all={keep_all}", sent[-1], want)
    finally:
        globals()["robots_txt"], globals()["get_json"] = real_robots, real_get
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        n = len(workday_pages(lambda off: {"total": 5000, "jobPostings": [{"externalPath": f"/{off}_{i}"} for i in range(WORKDAY_PAGE)]},
                              "NVIDIA 'deep learning'"))
    expect("a search that fills every page stops at the cap", n, WORKDAY_PAGE * WORKDAY_MAX_PAGES)
    expect("and says so with the total", "of 5000" in err.getvalue(), True)
    # Renault, 2026-09-29: 40 results, then the same full page again at every later offset
    first_two = {0: [f"/r{i}" for i in range(20)], 20: [f"/r{i}" for i in range(20, 40)]}
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        n = len(workday_pages(lambda off: {"total": 40 if off == 0 else 0,
                                           "jobPostings": [{"externalPath": p} for p in first_two.get(off, first_two[0])]}, "Renault"))
    expect("a board that repeats its pages stops when nothing new comes", (n, err.getvalue()), (40, ""))
    try:
        parse_ashby("M", {"postings": []})
        expect("a renamed top-level key fails", "no error", "KeyError")
    except KeyError:
        pass
    real = globals()["fetch"]

    flags = []

    def fake(t, keep_all=False, senior_ok=True):
        flags.append(keep_all)
        if t["company"] == "skip":
            raise Skipped("robots.txt disallows /Careers/")
        if t["company"] == "broken":
            raise ValueError("unknown ats 'x'")
        return [lead("ok", "ashby", "1", "ML Engineer", "Paris, France", None, "", "u", ""),
                lead("ok", "ashby", "2", "ML Engineer", "London", None, "", "u2", ""),
                lead("ok", "ashby", "3", "Office Manager", "Paris", None, "", "u3", "")]

    globals()["fetch"] = fake
    try:
        got, rep = run([{"company": c, "ats": "a"} for c in ("skip", "broken", "ok")], False)
    finally:
        globals()["fetch"] = real
    expect("run reports skipped, failed and read targets", [r[-1].split(" ")[0] for r in rep], ["SKIPPED", "FAILED", ""])
    expect("run keeps the readable target's lead", len(got), 1)
    expect("run counts the right title placed abroad", rep[2][2:5], (3, 1, 1))
    globals()["fetch"] = fake
    try:
        flags.clear()
        run([{"company": "ok", "ats": "a"}], True)
    finally:
        globals()["fetch"] = real
    expect("--all reaches fetch (Workday filters titles inside fetch)", flags, [True])
    # the last five were kept by the old flag-and-word filter on the 2026-09-29 run
    for place, want in [("Paris, France", True), ("Vélizy-Villacoublay, France", True), ("", True), ("Remote", True),
                        ("Remote (Europe)", True), ("London / Remote EU", True), ("New York, US", False),
                        ("Barcelona, Spain", False), ("Palo Alto; New York; San Francisco", False), ("Hybrid London", False),
                        ("US, CA, Santa Clara; US, CA, Remote, United States of America", False), ("Germany, Remote", False),
                        ("Issy-les-Moulineaux, FR", True), ("London, GB; FR", True), ("Frankfurt", False)]:
        expect(f"place {place!r}", keep_place(place), want)
    print("selftest ok" if ok else "selftest FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets", nargs="?")
    ap.add_argument("--out", default="leads.jsonl")
    ap.add_argument("--all", action="store_true", help="keep every title and place")
    ap.add_argument("--drop-senior", action="store_true", help="drop senior, lead and experienced-hire titles")
    ap.add_argument("--skip-ats", action="append", default=[], help="do not ask this board (repeatable), e.g. workable")
    ap.add_argument("--blocks", help="JSON file of boards that refused this machine and until when: read, then updated")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.targets:
        ap.error("targets.csv is required")
    now = dt.datetime.now(dt.timezone.utc)
    held, refused = refusals(a.blocks, now), {}
    with open(a.targets, encoding="utf-8") as f:
        targets = [r for r in csv.DictReader(f) if r.get("company") and not r["company"].startswith("#")]
    leads, report = run(targets, a.all, not a.drop_senior, tuple(x.lower() for x in a.skip_ats), held, refused)
    with open(a.out, "w", encoding="utf-8") as f:
        for x in leads:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    failed = [r for r in report if r[-1].startswith("FAILED")]
    skipped = [r for r in report if r[-1].startswith("SKIPPED")]
    for company, ats, got, kept, abroad, err in report:
        print(f"{company:28} {ats:11} " + (err if err else f"{got:4} seen, {kept:3} kept, {abroad:3} placed abroad"))
    print(f"{len(leads)} leads from {len(targets) - len(failed) - len(skipped)}/{len(targets)} targets "
          f"({len(skipped)} skipped, {len(failed)} failed) -> {a.out}")
    wrote = keep_refusals(a.blocks, refused, now) if a.blocks else {}
    for ats, wait in refused.items():
        print(f"{ats} refused this machine (Retry-After {wait!r}): " + (f"skipped until {wrote[ats]}, kept in {a.blocks}"
              if ats in wrote else "not remembered, pass --blocks with a Retry-After to skip it next time"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
