#!/usr/bin/env python3
"""
Job Board Scraper — SDR / BDR / ADR Role Aggregator  (v2)
==========================================================
Scrapes 15+ job boards / VC-PE portfolio sites for Sales, Business, or
Account Development Representative roles in configurable target locations,
then writes a clean, structured Markdown report.

──────────────────────────────────────────────────────────────────────────────
CRON SETUP — runs every morning at 7 AM
──────────────────────────────────────────────────────────────────────────────
Linux / macOS:
  $ crontab -e

  # Basic (system Python):
  0 7 * * * /usr/bin/python3 /path/to/job_scraper.py >> /path/to/scraper.log 2>&1

  # With virtualenv:
  0 7 * * * /path/to/.venv/bin/python /path/to/job_scraper.py >> /path/to/scraper.log 2>&1

  # With flags:
  0 7 * * * /path/to/.venv/bin/python /path/to/job_scraper.py --timeout 15 -o ~/reports/ >> ~/scraper.log 2>&1

macOS LaunchAgent alternative:
  Create ~/Library/LaunchAgents/com.job_scraper.plist — see Apple docs.

──────────────────────────────────────────────────────────────────────────────
SETUP
──────────────────────────────────────────────────────────────────────────────
  pip install playwright requests beautifulsoup4 lxml
  playwright install chromium
  python3 job_scraper.py            # run with defaults
  python3 job_scraper.py --help     # see all options
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse, urlencode

import requests
from bs4 import BeautifulSoup, Tag

# ── Optional Playwright ───────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

TODAY = datetime.date.today().isoformat()
DAYS_WINDOW = 2  # default; overridden by --days flag or web UI

DEFAULT_ROLES = [
    "SDR", "BDR", "ADR",
    "Sales Development Representative",
    "Business Development Representative",
    "Account Development Representative",
]

DEFAULT_LOCATIONS = [
    "New York", "NYC", "Manhattan", "Brooklyn",
    "Boston", "Cambridge, MA",
    "Austin", "Austin, TX",
    "Atlanta", "Atlanta, GA",
    "Remote", "Hybrid", "Anywhere",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

LOCATION_EMOJI = {
    "new york": "🗽", "nyc": "🗽", "manhattan": "🗽", "brooklyn": "🗽",
    "boston": "🦞", "cambridge": "🦞",
    "austin": "🤠",
    "atlanta": "🦞",
    "remote": "🌐", "hybrid": "🏠", "anywhere": "🌐",
}

# ── Investor label lookup ─────────────────────────────────────────────────────
INVESTOR_MAP = {
    "Insight Partners": "Insight Partners (PE/VC)",
    "Sequoia Capital":  "Sequoia Capital (VC)",
    "Greylock":         "Greylock (VC)",
    "General Catalyst": "General Catalyst (VC)",
    "K1 Investment":    "K1 Investment Management (PE)",
    "Index Ventures":   "Index Ventures (VC)",
    "a16z":             "Andreessen Horowitz (VC)",
    "Bessemer":         "Bessemer Venture Partners (VC)",
    "Accel":            "Accel (VC)",
    "Lightspeed":       "Lightspeed Venture Partners (VC)",
    "Battery Ventures": "Battery Ventures (VC)",
    "Thoma Bravo":      "Thoma Bravo (PE)",
    "Vista Equity":     "Vista Equity Partners (PE)",
    "Wellfound":        "—",
    "LinkedIn":         "—",
    "Indeed":           "—",
    "Glassdoor":        "—",
}

# ══════════════════════════════════════════════════════════════════════════════
#  CLI ARGUMENT PARSER
# ══════════════════════════════════════════════════════════════════════════════

# fmt: off
ALL_SOURCES = [
    "builtin", "trueup", "indexventures", "insightpartners",
    "sequoia", "k1", "generalcatalyst", "greylock", "repvue",
    # new in v2
    "a16z", "bessemer", "accel", "lightspeed", "battery",
    "thomabravo", "vistaequity",
    "wellfound", "linkedin", "indeed", "glassdoor",
    "greenhouse", "lever",
]
# fmt: on


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scrape job boards for SDR / BDR / ADR roles and output a Markdown report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 job_scraper.py                         # defaults (all sources)
  python3 job_scraper.py --sources builtin repvue linkedin
  python3 job_scraper.py --exclude trueup indeed
  python3 job_scraper.py --roles "SDR" "Account Executive" --locations "Denver" "Remote"
  python3 job_scraper.py --timeout 20 --no-headless -o ~/Desktop/
  python3 job_scraper.py --days 1                   # only jobs posted in the last 24h
  python3 job_scraper.py --list-sources
""",
    )
    p.add_argument("--roles", "-r", nargs="+", default=None,
                    help="Override default role keywords (default: SDR BDR ADR + long forms)")
    p.add_argument("--add-roles", nargs="+", default=None,
                    help="Add extra role keywords to the defaults")
    p.add_argument("--locations", "-l", nargs="+", default=None,
                    help="Override default target locations")
    p.add_argument("--add-locations", nargs="+", default=None,
                    help="Add extra locations to the defaults")
    p.add_argument("--output", "-o", default=".",
                    help="Output directory for the .md report (default: cwd)")
    p.add_argument("--filename", "-f", default=None,
                    help="Custom output filename (default: job_listings_YYYY-MM-DD.md)")
    p.add_argument("--sources", "-s", nargs="+", default=None, choices=ALL_SOURCES,
                    help="Only scrape these sources")
    p.add_argument("--exclude", "-x", nargs="+", default=[], choices=ALL_SOURCES,
                    help="Skip these sources")
    p.add_argument("--timeout", "-t", type=int, default=12,
                    help="Per-page timeout in seconds (default: 12)")
    p.add_argument("--days", "-d", type=int, default=2,
                    help="Only show jobs posted within this many days (default: 2)")
    p.add_argument("--no-headless", action="store_true",
                    help="Show the browser window (useful for debugging)")
    p.add_argument("--verbose", "-v", action="store_true",
                    help="Enable debug logging")
    p.add_argument("--list-sources", action="store_true",
                    help="Print available sources and exit")
    p.add_argument("--json", action="store_true",
                    help="Also write a .json file alongside the .md")
    return p


# ══════════════════════════════════════════════════════════════════════════════
#  DATA MODEL
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str
    investor: Optional[str] = None
    company_size: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    # These are set dynamically based on CLI args
    _role_re: "re.Pattern | None" = field(default=None, repr=False, compare=False)
    _loc_re:  "re.Pattern | None" = field(default=None, repr=False, compare=False)

    def matches_role(self) -> bool:
        if self._role_re:
            return bool(self._role_re.search(self.title))
        return bool(ROLE_RE.search(self.title))

    def matches_location(self) -> bool:
        if self._loc_re:
            return bool(self._loc_re.search(self.location))
        return bool(LOC_RE.search(self.location))

    def is_relevant(self) -> bool:
        return self.matches_role() and self.matches_location()

    def to_dict(self) -> dict:
        return {
            "title": self.title, "company": self.company,
            "location": self.location, "url": self.url,
            "source": self.source, "investor": self.investor,
            "company_size": self.company_size, "tags": self.tags,
        }


# Global compiled regexes — built at startup from CLI args
ROLE_RE: re.Pattern = re.compile("")
LOC_RE:  re.Pattern = re.compile("")


def build_regexes(roles: list[str], locations: list[str]) -> None:
    global ROLE_RE, LOC_RE
    role_alts = "|".join(re.escape(r).replace(r"\ ", r"\s+") for r in roles)
    ROLE_RE = re.compile(rf"\b({role_alts})\b", re.IGNORECASE)
    loc_alts  = "|".join(re.escape(l).replace(r"\ ", r"\s*") for l in locations)
    LOC_RE  = re.compile(rf"\b({loc_alts})\b", re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════════════
#  PLAYWRIGHT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

PAGE_TIMEOUT = 12_000  # overridden by --timeout


def pw_get(page, url: str, scroll: int = 2) -> str:
    """Navigate to url, brief wait, scroll, return HTML."""
    try:
        page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
    except Exception as exc:
        log.debug(f"Nav error {url}: {exc}")
        try:
            return page.content()
        except Exception:
            return ""
    page.wait_for_timeout(min(2000, PAGE_TIMEOUT // 3))
    for _ in range(scroll):
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            break
        page.wait_for_timeout(800)
    return page.content()


def get_soup(url: str) -> Optional[BeautifulSoup]:
    """Plain HTTP GET → soup."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except Exception as exc:
        log.debug(f"Static fetch failed {url}: {exc}")
        return None


def get_json(url: str) -> Optional[dict | list]:
    """Plain HTTP GET → JSON."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.debug(f"JSON fetch failed {url}: {exc}")
        return None


# ── helper: extract company / location from a parent element ──────────────────
def _extract_context(container: Optional[Tag], title: str) -> tuple[str, str, str]:
    """Return (company, location, company_size) from parent element text."""
    if not container:
        return "Unknown", "Unknown", ""
    raw = container.get_text(" | ", strip=True)

    company = "Unknown"
    # look for class-based company labels
    co_el = container.find(class_=re.compile(r"company|employer|org|brand", re.I))
    if co_el:
        company = co_el.get_text(strip=True)
    if company == "Unknown":
        # first chunk before the title
        parts = [p.strip() for p in raw.split("|") if p.strip() and p.strip() != title]
        if parts:
            company = parts[0][:60]

    loc_match = LOC_RE.search(raw)
    location = loc_match.group(0) if loc_match else "Unknown"

    size_match = re.search(r"(\d[\d,]+)\s*(?:employees|people|staff|\+)", raw, re.I)
    size = size_match.group(0) if size_match else ""
    if not size:
        size_match2 = re.search(r"\b(\d{1,3}(?:,\d{3})*(?:\s*-\s*\d{1,3}(?:,\d{3})*)?)(?:\s*employees|\s*\+)", raw, re.I)
        if size_match2:
            size = size_match2.group(0)

    return company, location, size


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: BuiltIn
# ══════════════════════════════════════════════════════════════════════════════

BUILTIN_CITIES = [
    ("New York City", "New York"),
    ("Boston", "Massachusetts"),
    ("Austin", "Texas"),
    ("Atlanta", "Georgia"),
]

def scrape_builtin(page, roles: list[str]) -> list[Job]:
    log.info("BuiltIn: scraping …")
    jobs, seen = [], set()

    def _parse(html: str, fallback_loc: str) -> list[Job]:
        soup = BeautifulSoup(html, "lxml")
        found = []
        for a in soup.find_all("a", href=re.compile(r"/job/")):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://builtin.com" + href
            if href in seen:
                continue
            # BuiltIn sometimes smashes all card text into one <a>; try to
            # separate the title by looking at the first line only.
            raw_text = a.get_text(" ", strip=True)
            # Heuristic: title is the first sentence-like chunk
            title = raw_text.split("\n")[0].strip()
            # If the whole anchor text is one blob, sometimes the title is
            # just the slug from URL
            if len(title) > 120:
                slug = href.rstrip("/").split("/")[-1]
                title = slug.replace("-", " ").title()
            if not ROLE_RE.search(title):
                continue
            seen.add(href)
            parent = a.find_parent("div") or a.find_parent("li") or a.parent
            co, loc, sz = _extract_context(parent, title)
            if loc == "Unknown":
                loc = fallback_loc
            j = Job(title=title, company=co, location=loc, url=href,
                    source="BuiltIn", company_size=sz or None)
            if j.matches_role():
                found.append(j)
        return found

    # location-specific
    for city, state in BUILTIN_CITIES:
        for role in roles[:3]:  # top 3 role keywords to keep it fast
            url = (f"https://builtin.com/jobs?search={quote(role)}"
                   f"&city={quote(city)}&state={quote(state)}"
                   f"&country=USA&allLocations=false")
            html = pw_get(page, url)
            jobs.extend(_parse(html, f"{city}, {state}"))
            time.sleep(0.3)

    # remote
    for role in roles[:3]:
        url = f"https://builtin.com/jobs?search={quote(role)}&remote=true&country=USA"
        html = pw_get(page, url)
        jobs.extend(_parse(html, "Remote"))
        time.sleep(0.3)

    log.info(f"  BuiltIn → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: TrueUp
# ══════════════════════════════════════════════════════════════════════════════

def scrape_trueup(page, roles: list[str]) -> list[Job]:
    log.info("TrueUp: scraping …")
    jobs, seen = [], set()
    locs = ["New York", "Boston", "Austin", "Atlanta", "Remote"]
    search_roles = roles[:3]

    for role in search_roles:
        for loc in locs:
            url = f"https://www.trueup.io/jobs?q={quote(role)}&l={quote(loc)}"
            html = pw_get(page, url, scroll=2)
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=re.compile(r"/jobs?/")):
                href = a.get("href", "")
                if not href.startswith("http"):
                    href = "https://www.trueup.io" + href
                if href in seen:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) > 200 or not ROLE_RE.search(title):
                    continue
                seen.add(href)
                parent = a.find_parent("div") or a.parent
                co, lo, sz = _extract_context(parent, title)
                jobs.append(Job(title=title, company=co, location=lo or loc,
                                url=href, source="TrueUp", company_size=sz or None))
            time.sleep(0.3)

    log.info(f"  TrueUp → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Index Ventures (custom page with embedded data)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_index_ventures(page) -> list[Job]:
    log.info("Index Ventures: scraping …")
    jobs = []

    html = pw_get(page, "https://www.indexventures.com/startup-jobs", scroll=3)
    soup = BeautifulSoup(html, "lxml")

    for a in soup.find_all("a", href=re.compile(r"/startup-jobs/.+/")):
        href = a.get("href", "")
        if not href.startswith("http"):
            href = "https://www.indexventures.com" + href
        raw = a.get_text(" | ", strip=True)
        # Index page puts title, company, location, category all in the link text
        title_match = ROLE_RE.search(raw)
        if not title_match:
            continue
        # Extract structured parts — Index uses a table-like layout:
        # "Title | Company | Location | Category | Stage | Size | Date"
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        title = parts[0] if parts else raw[:80]
        company = parts[1] if len(parts) > 1 else "Unknown"
        location = parts[2] if len(parts) > 2 else "Unknown"
        size = ""
        for p in parts:
            if re.search(r"\d+-\d+|\d{2,}", p) and not re.search(r"20\d\d", p):
                size = p
                break

        j = Job(title=title, company=company, location=location,
                url=href, source="Index Ventures", investor="Index Ventures",
                company_size=size or None)
        if j.is_relevant():
            jobs.append(j)

    log.info(f"  Index Ventures → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Consider.com platform (generic)
#  Used by: Insight, Sequoia, Greylock, GC, K1, a16z, Bessemer, Accel,
#           Lightspeed, Battery
# ══════════════════════════════════════════════════════════════════════════════

def scrape_consider_board(page, board_url: str, name: str, investor: str,
                          roles: list[str]) -> list[Job]:
    log.info(f"{name}: scraping (Consider platform) …")
    jobs, seen = [], set()
    base = urlparse(board_url)

    def _parse(html: str) -> list[Job]:
        soup = BeautifulSoup(html, "lxml")
        found = []

        # Consider renders jobs as <a> tags; also check for Greenhouse redirect links
        for a in soup.find_all("a", href=re.compile(r"/jobs/\d+|greenhouse\.io|lever\.co")):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = f"{base.scheme}://{base.netloc}{href}"
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) > 200:
                title = a.get("aria-label", "")
            if not title or not ROLE_RE.search(title):
                continue
            seen.add(href)
            container = a.find_parent("li") or a.find_parent("div") or a.parent
            co, loc, sz = _extract_context(container, title)
            j = Job(title=title, company=co, location=loc, url=href,
                    source=name, investor=investor, company_size=sz or None)
            if j.is_relevant():
                found.append(j)
        return found

    # Try targeted search queries first
    for role in roles[:3]:
        url = f"{board_url}?query={quote(role)}"
        html = pw_get(page, url, scroll=2)
        jobs.extend(_parse(html))
        time.sleep(0.3)

    # Also base page
    html = pw_get(page, board_url, scroll=2)
    jobs.extend(_parse(html))

    log.info(f"  {name} → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: RepVue (SDR/BDR section)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_repvue(page) -> list[Job]:
    log.info("RepVue: scraping …")
    jobs, seen = [], set()
    base = "https://www.repvue.com/sales-jobs/sdr-bdr"

    for loc in ["new-york", "boston", "austin", "atlanta", "remote"]:
        html = pw_get(page, f"{base}?location={loc}", scroll=2)
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=re.compile(r"/company/|/jobs?")):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.repvue.com" + href
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or not ROLE_RE.search(title):
                continue
            seen.add(href)
            parent = a.find_parent("div") or a.parent
            co, lo, sz = _extract_context(parent, title)
            jobs.append(Job(title=title, company=co, location=lo or loc.replace("-", " ").title(),
                            url=href, source="RepVue", company_size=sz or None))
        time.sleep(0.3)

    log.info(f"  RepVue → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Wellfound (formerly AngelList Talent)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_wellfound(page, roles: list[str]) -> list[Job]:
    log.info("Wellfound: scraping …")
    jobs, seen = [], set()
    role_slugs = {
        "SDR": "sales-development-representative",
        "BDR": "business-development-representative",
        "Sales Development Representative": "sales-development-representative",
        "Business Development Representative": "business-development-representative",
        "Account Development Representative": "account-development-representative",
    }
    locs = {
        "new-york": "New York",
        "boston": "Boston",
        "austin": "Austin",
        "atlanta": "Atlanta",
    }

    for role_key in list(role_slugs.values())[:3]:
        for loc_slug, loc_name in locs.items():
            url = f"https://wellfound.com/role/r/{role_key}/l/{loc_slug}"
            html = pw_get(page, url, scroll=2)
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=re.compile(r"/jobs/|/company/")):
                href = a.get("href", "")
                if not href.startswith("http"):
                    href = "https://wellfound.com" + href
                if href in seen:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) > 200 or not ROLE_RE.search(title):
                    continue
                seen.add(href)
                parent = a.find_parent("div") or a.parent
                co, lo, sz = _extract_context(parent, title)
                jobs.append(Job(title=title, company=co, location=lo or loc_name,
                                url=href, source="Wellfound", company_size=sz or None))
            time.sleep(0.3)

    # Also try remote
    for role_key in list(role_slugs.values())[:2]:
        url = f"https://wellfound.com/role/r/{role_key}?remote=true"
        html = pw_get(page, url, scroll=2)
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=re.compile(r"/jobs/|/company/")):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://wellfound.com" + href
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or not ROLE_RE.search(title):
                continue
            seen.add(href)
            parent = a.find_parent("div") or a.parent
            co, lo, sz = _extract_context(parent, title)
            jobs.append(Job(title=title, company=co, location=lo or "Remote",
                            url=href, source="Wellfound", company_size=sz or None))
        time.sleep(0.3)

    log.info(f"  Wellfound → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: LinkedIn (public search — no login required)
# ══════════════════════════════════════════════════════════════════════════════

LINKEDIN_GEOIDS = {
    "New York": "102571732",
    "Boston":   "102380872",
    "Austin":   "104472866",
    "Atlanta":  "103500991",
}

def scrape_linkedin(page, roles: list[str]) -> list[Job]:
    log.info("LinkedIn: scraping (public, no login) …")
    jobs, seen = [], set()

    search_roles = roles[:2]  # LinkedIn is slow, keep it tight
    for role in search_roles:
        for city, geo_id in LINKEDIN_GEOIDS.items():
            params = urlencode({
                "keywords": role,
                "location": city,
                "geoId": geo_id,
                "f_TPR": f"r{DAYS_WINDOW * 86400}",  # seconds window
                "position": "1",
                "pageNum": "0",
            })
            url = f"https://www.linkedin.com/jobs/search/?{params}"
            html = pw_get(page, url, scroll=2)
            soup = BeautifulSoup(html, "lxml")

            for a in soup.find_all("a", href=re.compile(r"linkedin\.com/jobs/view/")):
                href = a.get("href", "").split("?")[0]  # strip tracking params
                if href in seen:
                    continue
                title = a.get_text(strip=True)
                if not title or not ROLE_RE.search(title):
                    continue
                seen.add(href)
                parent = a.find_parent("div") or a.parent
                co, loc, sz = _extract_context(parent, title)
                jobs.append(Job(title=title, company=co, location=loc or city,
                                url=href, source="LinkedIn", company_size=sz or None))
            time.sleep(0.5)

    # Remote
    for role in search_roles:
        params = urlencode({
            "keywords": role,
            "f_WT": "2",  # remote
            "f_TPR": f"r{DAYS_WINDOW * 86400}",
            "location": "United States",
        })
        url = f"https://www.linkedin.com/jobs/search/?{params}"
        html = pw_get(page, url, scroll=2)
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=re.compile(r"linkedin\.com/jobs/view/")):
            href = a.get("href", "").split("?")[0]
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or not ROLE_RE.search(title):
                continue
            seen.add(href)
            parent = a.find_parent("div") or a.parent
            co, loc, sz = _extract_context(parent, title)
            jobs.append(Job(title=title, company=co, location=loc or "Remote",
                            url=href, source="LinkedIn", company_size=sz or None))
        time.sleep(0.5)

    log.info(f"  LinkedIn → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Indeed (public search)
# ══════════════════════════════════════════════════════════════════════════════

INDEED_LOCATIONS = {
    "New+York,+NY": "New York",
    "Boston,+MA":   "Boston",
    "Austin,+TX":   "Austin",
    "Atlanta,+GA":  "Atlanta",
}

def scrape_indeed(page, roles: list[str]) -> list[Job]:
    log.info("Indeed: scraping …")
    jobs, seen = [], set()

    for role in roles[:2]:
        for loc_param, loc_name in INDEED_LOCATIONS.items():
            url = f"https://www.indeed.com/jobs?q={quote(role)}&l={loc_param}&fromage={DAYS_WINDOW}"
            html = pw_get(page, url, scroll=2)
            soup = BeautifulSoup(html, "lxml")

            for a in soup.find_all("a", href=re.compile(r"/rc/clk|/viewjob|/pagead")):
                href = a.get("href", "")
                if not href.startswith("http"):
                    href = "https://www.indeed.com" + href
                if href in seen:
                    continue
                title = a.get_text(strip=True)
                if not title or not ROLE_RE.search(title):
                    continue
                seen.add(href)
                parent = a.find_parent("div") or a.parent
                co, loc, sz = _extract_context(parent, title)
                jobs.append(Job(title=title, company=co, location=loc or loc_name,
                                url=href, source="Indeed", company_size=sz or None))
            time.sleep(0.5)

        # Remote
        url = f"https://www.indeed.com/jobs?q={quote(role)}&l=Remote&fromage={DAYS_WINDOW}"
        html = pw_get(page, url, scroll=2)
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=re.compile(r"/rc/clk|/viewjob|/pagead")):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.indeed.com" + href
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or not ROLE_RE.search(title):
                continue
            seen.add(href)
            parent = a.find_parent("div") or a.parent
            co, loc, sz = _extract_context(parent, title)
            jobs.append(Job(title=title, company=co, location=loc or "Remote",
                            url=href, source="Indeed", company_size=sz or None))
        time.sleep(0.5)

    log.info(f"  Indeed → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Glassdoor (public search)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_glassdoor(page, roles: list[str]) -> list[Job]:
    log.info("Glassdoor: scraping …")
    jobs, seen = [], set()

    for role in roles[:2]:
        url = (f"https://www.glassdoor.com/Job/jobs.htm?"
               f"sc.keyword={quote(role)}&locT=N&locId=1&fromAge={DAYS_WINDOW}")
        html = pw_get(page, url, scroll=2)
        soup = BeautifulSoup(html, "lxml")

        for a in soup.find_all("a", href=re.compile(r"/job-listing/|/partner/jobListing")):
            href = a.get("href", "")
            if not href.startswith("http"):
                href = "https://www.glassdoor.com" + href
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or not ROLE_RE.search(title):
                continue
            seen.add(href)
            parent = a.find_parent("div") or a.parent
            co, loc, sz = _extract_context(parent, title)
            jobs.append(Job(title=title, company=co, location=loc,
                            url=href, source="Glassdoor", company_size=sz or None))
        time.sleep(0.5)

    log.info(f"  Glassdoor → {len(jobs)} raw hits")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Greenhouse Boards API  (public JSON API)
#  Many VC-backed companies use Greenhouse. We can hit their public boards API.
# ══════════════════════════════════════════════════════════════════════════════

# Popular companies known to use Greenhouse — their board token is the slug
GREENHOUSE_BOARDS = [
    # (board_token, company_name, investor_if_known)
    ("airtable", "Airtable", "Thoma Bravo"),
    ("figma", "Figma", None),
    ("notion", "Notion", "Sequoia Capital"),
    ("databricks", "Databricks", "a16z"),
    ("gusto", "Gusto", "General Catalyst"),
    ("brex", "Brex", "Greylock"),
    ("plaid", "Plaid", "Index Ventures"),
    ("ramp", "Ramp", "Greylock"),
    ("rippling", "Rippling", None),
    ("cockroachlabs", "Cockroach Labs", "Accel"),
    ("moveworks", "Moveworks", None),
    ("snyk", "Snyk", "Accel"),
    ("mux", "Mux", "Accel"),
    ("navan", "Navan", "Lightspeed"),
    ("gong", "Gong", "Sequoia Capital"),
    ("lattice", "Lattice", None),
    ("anthropic", "Anthropic", None),
    ("scale", "Scale AI", "Accel"),
    ("retool", "Retool", "Sequoia Capital"),
    ("vanta", "Vanta", "Sequoia Capital"),
    ("vercel", "Vercel", "Accel"),
    ("sardine", "Sardine", "a16z"),
    ("chainalysis", "Chainalysis", "Accel"),
    ("perfectserve", "PerfectServe", "K1 Investment"),
]

def scrape_greenhouse(roles: list[str]) -> list[Job]:
    """Hit the Greenhouse public boards API (no browser needed)."""
    log.info(f"Greenhouse API: checking {len(GREENHOUSE_BOARDS)} boards …")
    jobs = []

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS_WINDOW)

    for token, company, investor in GREENHOUSE_BOARDS:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        data = get_json(url)
        if not data or "jobs" not in data:
            continue
        for item in data["jobs"]:
            title = item.get("title", "")
            if not ROLE_RE.search(title):
                continue
            # Filter by updated_at date
            updated = item.get("updated_at", "")
            if updated:
                try:
                    ts = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
            loc = item.get("location", {}).get("name", "Unknown")
            job_url = item.get("absolute_url", "")
            j = Job(title=title, company=company, location=loc,
                    url=job_url, source="Greenhouse", investor=investor)
            if j.is_relevant():
                jobs.append(j)
        time.sleep(0.1)

    log.info(f"  Greenhouse API → {len(jobs)} raw hits (window: {DAYS_WINDOW}d)")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPER: Lever Boards API  (public JSON API)
# ══════════════════════════════════════════════════════════════════════════════

LEVER_BOARDS = [
    # (company_slug, company_name, investor_if_known)
    ("confluent", "Confluent", "Index Ventures"),
    ("twilio", "Twilio", None),
    ("netlify", "Netlify", "a16z"),
    ("samsara", "Samsara", "a16z"),
    ("census", "Census", "Sequoia Capital"),
    ("drata", "Drata", "Insight Partners"),
    ("harness", "Harness", "Battery Ventures"),
    ("miro", "Miro", "Accel"),
    ("cloverly", "Cloverly", None),
    ("postman", "Postman", "Insight Partners"),
    ("automox", "Automox", "Insight Partners"),
    ("lucidworks", "Lucidworks", None),
]

def scrape_lever(roles: list[str]) -> list[Job]:
    """Hit the Lever public postings API (no browser needed)."""
    log.info(f"Lever API: checking {len(LEVER_BOARDS)} boards …")
    jobs = []

    cutoff_ms = int((datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(days=DAYS_WINDOW)).timestamp() * 1000)

    for slug, company, investor in LEVER_BOARDS:
        url = f"https://api.lever.co/v0/postings/{slug}"
        data = get_json(url)
        if not data or not isinstance(data, list):
            continue
        for item in data:
            title = item.get("text", "")
            if not ROLE_RE.search(title):
                continue
            # Filter by createdAt (epoch ms)
            created = item.get("createdAt", 0)
            if created and created < cutoff_ms:
                continue
            cats = item.get("categories", {})
            loc = cats.get("location", "Unknown")
            job_url = item.get("hostedUrl", "")
            j = Job(title=title, company=company, location=loc,
                    url=job_url, source="Lever", investor=investor)
            if j.is_relevant():
                jobs.append(j)
        time.sleep(0.1)

    log.info(f"  Lever API → {len(jobs)} raw hits (window: {DAYS_WINDOW}d)")
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  MARKDOWN + JSON WRITER
# ══════════════════════════════════════════════════════════════════════════════

def loc_emoji(loc: str) -> str:
    low = loc.lower()
    for k, e in LOCATION_EMOJI.items():
        if k in low:
            return e
    return "📍"


def dedupe(jobs: list[Job]) -> list[Job]:
    seen, out = set(), []
    for j in jobs:
        if j.url not in seen:
            seen.add(j.url)
            out.append(j)
    return out


def write_markdown(jobs: list[Job], path: Path, roles: list[str], locations: list[str]) -> None:
    jobs = dedupe(jobs)
    groups: dict[str, list[Job]] = {}
    for j in jobs:
        groups.setdefault(j.source, []).append(j)

    L = []
    L += [
        f"# 🚀 SDR / BDR / ADR Job Listings — {TODAY}",
        "",
        f"> **{len(jobs)} unique role(s)** across **{len(groups)} source(s)**",
        f"> **Roles:** {' · '.join(roles[:6])}",
        f"> **Locations:** {' · '.join(dict.fromkeys(locations[:10]))}",
        "",
        "---", "",
        "## 📑 Summary", "",
    ]
    for i, (src, sj) in enumerate(sorted(groups.items()), 1):
        anchor = re.sub(r"[^a-z0-9]+", "-", src.lower()).strip("-")
        L.append(f"{i}. [{src}](#{anchor}) — **{len(sj)}** role(s)")
    L += ["", "---", ""]

    for src, sj in sorted(groups.items()):
        inv_label = ""
        inv_val = sj[0].investor if sj else None
        if inv_val:
            inv_label = f" · _{INVESTOR_MAP.get(inv_val, inv_val)}_"

        L += [f"## {src}{inv_label}", "", f"**{len(sj)} role(s)**", ""]
        L.append("| # | Role | Company | Location | Size | PE/VC |")
        L.append("|---|------|---------|----------|------|-------|")
        for n, j in enumerate(sorted(sj, key=lambda x: x.location.lower()), 1):
            role_link = f"[{j.title[:80]}]({j.url})"
            co = j.company or "—"
            em = loc_emoji(j.location)
            lo = f"{em} {j.location}" if j.location else "—"
            sz = j.company_size or "—"
            iv = INVESTOR_MAP.get(j.investor, j.investor) if j.investor else "—"
            L.append(f"| {n} | {role_link} | {co} | {lo} | {sz} | {iv} |")
        L += ["", "---", ""]

    L.append(f"_Generated by `job_scraper.py` on {TODAY}_")
    path.write_text("\n".join(L), encoding="utf-8")
    log.info(f"✅ Markdown report → {path.resolve()}")


def write_json(jobs: list[Job], path: Path) -> None:
    jobs = dedupe(jobs)
    data = [j.to_dict() for j in jobs]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"✅ JSON report → {path.resolve()}")


# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE REGISTRY — maps flag keys to scraper functions
# ══════════════════════════════════════════════════════════════════════════════

# Each value is (func, needs_playwright: bool)
# func signature: (page_or_None, roles) -> list[Job]

def _make_consider(url, name, investor):
    """Factory for Consider-platform scrapers."""
    def _scrape(page, roles):
        return scrape_consider_board(page, url, name, investor, roles)
    return _scrape

SOURCE_REGISTRY: dict[str, tuple] = {
    # key: (scraper_func, needs_playwright)
    "builtin":          (lambda p, r: scrape_builtin(p, r),          True),
    "trueup":           (lambda p, r: scrape_trueup(p, r),           True),
    "indexventures":    (lambda p, r: scrape_index_ventures(p),      True),
    "insightpartners":  (_make_consider("https://jobs.insightpartners.com/jobs", "Insight Partners", "Insight Partners"), True),
    "sequoia":          (_make_consider("https://jobs.sequoiacap.com/jobs", "Sequoia Capital", "Sequoia Capital"), True),
    "k1":              (_make_consider("https://portfoliocareers.k1.com/jobs", "K1 Investment", "K1 Investment"), True),
    "generalcatalyst":  (_make_consider("https://jobs.generalcatalyst.com/jobs", "General Catalyst", "General Catalyst"), True),
    "greylock":         (_make_consider("https://jobs.greylock.com/jobs", "Greylock", "Greylock"), True),
    "repvue":           (lambda p, r: scrape_repvue(p),              True),
    # ── v2 new sources ────
    "a16z":             (_make_consider("https://jobs.a16z.com/jobs", "a16z", "a16z"), True),
    "bessemer":         (_make_consider("https://jobs.bvp.com/jobs", "Bessemer", "Bessemer"), True),
    "accel":            (_make_consider("https://jobs.accel.com/jobs", "Accel", "Accel"), True),
    "lightspeed":       (_make_consider("https://jobs.lsvp.com/jobs", "Lightspeed", "Lightspeed"), True),
    "battery":          (_make_consider("https://jobs.battery.com/jobs", "Battery Ventures", "Battery Ventures"), True),
    "thomabravo":       (_make_consider("https://jobs.thomabravo.com/jobs", "Thoma Bravo", "Thoma Bravo"), True),
    "vistaequity":      (_make_consider("https://jobs.vistaequitypartners.com/jobs", "Vista Equity", "Vista Equity"), True),
    "wellfound":        (lambda p, r: scrape_wellfound(p, r),        True),
    "linkedin":         (lambda p, r: scrape_linkedin(p, r),         True),
    "indeed":           (lambda p, r: scrape_indeed(p, r),           True),
    "glassdoor":        (lambda p, r: scrape_glassdoor(p, r),        True),
    # API-only (no browser needed)
    "greenhouse":       (lambda p, r: scrape_greenhouse(r),          False),
    "lever":            (lambda p, r: scrape_lever(r),               False),
}


# ══════════════════════════════════════════════════════════════════════════════
#  PROGRAMMATIC RUNNER (used by web UI)
# ══════════════════════════════════════════════════════════════════════════════

def run_scraper(
    active_sources: set[str] | None = None,
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    timeout_s: int = 12,
    days: int = 2,
    headless: bool = True,
):
    """
    Generator that runs the scraper and yields progress dicts:
      {"event": "start",    "total_sources": N}
      {"event": "progress", "source": "...", "index": i, "total": N, "status": "scraping"}
      {"event": "hits",     "source": "...", "count": N, "running_total": N}
      {"event": "error",    "source": "...", "message": "..."}
      {"event": "done",     "jobs": [...], "md_path": "...", "total": N}
    """
    global PAGE_TIMEOUT, DAYS_WINDOW
    PAGE_TIMEOUT = timeout_s * 1000
    DAYS_WINDOW = days

    _roles = roles or list(DEFAULT_ROLES)
    _locations = locations or list(DEFAULT_LOCATIONS)
    build_regexes(_roles, _locations)

    _active = active_sources if active_sources else set(ALL_SOURCES)

    api_sources = sorted(s for s in _active if s in SOURCE_REGISTRY and not SOURCE_REGISTRY[s][1])
    pw_sources  = sorted(s for s in _active if s in SOURCE_REGISTRY and SOURCE_REGISTRY[s][1])
    all_ordered = api_sources + pw_sources
    total_sources = len(all_ordered)

    yield {"event": "start", "total_sources": total_sources}

    all_jobs: list[Job] = []
    idx = 0

    # ── API scrapers ──────────────────────────────────────────────────────
    for src in api_sources:
        idx += 1
        yield {"event": "progress", "source": src, "index": idx,
               "total": total_sources, "status": "scraping"}
        fn, _ = SOURCE_REGISTRY[src]
        try:
            result = fn(None, _roles)
            all_jobs.extend(result)
            yield {"event": "hits", "source": src, "count": len(result),
                   "running_total": len(all_jobs)}
        except Exception as exc:
            yield {"event": "error", "source": src, "message": str(exc)}

    # ── Browser scrapers ──────────────────────────────────────────────────
    if pw_sources and PLAYWRIGHT_OK:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                java_script_enabled=True,
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.set_default_timeout(PAGE_TIMEOUT)
            page.route("**/*", lambda route: route.abort()
                       if route.request.resource_type in ("image", "media", "font", "stylesheet")
                       else route.continue_())

            for src in pw_sources:
                idx += 1
                yield {"event": "progress", "source": src, "index": idx,
                       "total": total_sources, "status": "scraping"}
                fn, _ = SOURCE_REGISTRY[src]
                try:
                    result = fn(page, _roles)
                    all_jobs.extend(result)
                    yield {"event": "hits", "source": src, "count": len(result),
                           "running_total": len(all_jobs)}
                except Exception as exc:
                    yield {"event": "error", "source": src, "message": str(exc)}

            browser.close()

    # ── Write output ──────────────────────────────────────────────────────
    out_dir = Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"job_listings_{TODAY}.md"
    write_markdown(all_jobs, md_path, _roles, _locations)
    write_json(all_jobs, md_path.with_suffix(".json"))

    final = dedupe(all_jobs)
    yield {
        "event": "done",
        "jobs": [j.to_dict() for j in final],
        "md_path": str(md_path.resolve()),
        "total": len(final),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN (CLI)
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ── handle --list-sources ──────────────────────────────────────────────
    if args.list_sources:
        print("Available sources:")
        for s in ALL_SOURCES:
            label = SOURCE_REGISTRY.get(s)
            pw_flag = "🌐 browser" if (label and label[1]) else "⚡ API"
            print(f"  {s:<20s}  ({pw_flag})")
        sys.exit(0)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Build role / location lists ────────────────────────────────────────
    roles = args.roles if args.roles else list(DEFAULT_ROLES)
    if args.add_roles:
        roles.extend(args.add_roles)
    locations = args.locations if args.locations else list(DEFAULT_LOCATIONS)
    if args.add_locations:
        locations.extend(args.add_locations)
    build_regexes(roles, locations)

    # ── Determine active sources ──────────────────────────────────────────
    active = set(args.sources) if args.sources else set(ALL_SOURCES)
    active -= set(args.exclude)

    # ── Set timeout & days window ─────────────────────────────────────────
    global PAGE_TIMEOUT, DAYS_WINDOW
    PAGE_TIMEOUT = args.timeout * 1000
    DAYS_WINDOW = args.days

    # ── Output path ───────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = args.filename or f"job_listings_{TODAY}.md"
    md_path = out_dir / fname

    log.info(f"Roles: {roles}")
    log.info(f"Locations (short): {locations[:8]}…")
    log.info(f"Active sources ({len(active)}): {sorted(active)}")
    log.info(f"Per-page timeout: {args.timeout}s | Date window: {args.days}d")

    # ── Split sources into API-only vs browser-needed ─────────────────────
    api_sources   = {s for s in active if s in SOURCE_REGISTRY and not SOURCE_REGISTRY[s][1]}
    pw_sources    = {s for s in active if s in SOURCE_REGISTRY and SOURCE_REGISTRY[s][1]}

    all_jobs: list[Job] = []

    # ── API-only scrapers (no browser needed) ──────────────────────────────
    for src in sorted(api_sources):
        fn, _ = SOURCE_REGISTRY[src]
        try:
            all_jobs.extend(fn(None, roles))
        except Exception as exc:
            log.warning(f"{src}: scraper error: {exc}")

    # ── Browser scrapers ──────────────────────────────────────────────────
    if pw_sources:
        if not PLAYWRIGHT_OK:
            log.error("Playwright required for browser sources. Install: "
                      "pip install playwright && playwright install chromium")
            if not api_sources:
                sys.exit(1)
        else:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not args.no_headless)
                ctx = browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    java_script_enabled=True,
                    viewport={"width": 1280, "height": 900},
                )
                page = ctx.new_page()
                page.set_default_timeout(PAGE_TIMEOUT)
                # Block heavy resources
                page.route("**/*", lambda route: route.abort()
                           if route.request.resource_type in ("image", "media", "font", "stylesheet")
                           else route.continue_())

                for src in sorted(pw_sources):
                    fn, _ = SOURCE_REGISTRY[src]
                    try:
                        result = fn(page, roles)
                        all_jobs.extend(result)
                    except Exception as exc:
                        log.warning(f"{src}: scraper error: {exc}")

                browser.close()

    # ── Write output ──────────────────────────────────────────────────────
    log.info(f"Total raw jobs collected: {len(all_jobs)}")
    write_markdown(all_jobs, md_path, roles, locations)
    if args.json:
        json_path = md_path.with_suffix(".json")
        write_json(all_jobs, json_path)

    final = dedupe(all_jobs)
    print(f"\n📄 Report: {md_path.resolve()}")
    print(f"   {len(final)} unique listings from {len(set(j.source for j in final))} sources.")


if __name__ == "__main__":
    main()
