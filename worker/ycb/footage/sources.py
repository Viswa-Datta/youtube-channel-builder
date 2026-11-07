"""Footage and research retrieval. Everything returned here comes from a real API response;
nothing is ever synthesised. If a search returns nothing, the caller records a GAP.

Order per scene: (a) real case footage -> (b) news/documents on camera -> (c) stock B-roll.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

import re

import requests

from ..config import require, settings
from .rights import suggest_license

UA = {"User-Agent": "YouTubeChannelBuilder/1.0 (footage research)"}


@dataclass
class Candidate:
    tier: Literal["real", "news_document", "stock_broll"]
    title: str
    source_name: str
    source_url: str
    media_url: str | None
    agency: str | None = None
    release_date: str | None = None
    license: str = "Do Not Use"
    rights_note: str = ""
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    meta: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- (a) real / archive footage
def internet_archive(query: str, rows: int = 10) -> list[Candidate]:
    r = requests.get("https://archive.org/advancedsearch.php", headers=UA, timeout=30, params={
        "q": f"({query}) AND mediatype:(movies)", "fl[]": ["identifier", "title", "date", "licenseurl", "rights",
                                                            "creator", "collection"],
        "rows": rows, "output": "json"})
    r.raise_for_status()
    out = []
    for d in r.json().get("response", {}).get("docs", []):
        ident = d["identifier"]
        coll = d.get("collection")
        coll = ",".join(coll) if isinstance(coll, list) else (coll or "")
        s = suggest_license(source_url=f"https://archive.org/details/{ident}", agency=d.get("creator") if isinstance(d.get("creator"), str) else None,
                            licenseurl=d.get("licenseurl"), rights_text=str(d.get("rights") or ""), collection=coll)
        out.append(Candidate("real", d.get("title") or ident, "Internet Archive", f"https://archive.org/details/{ident}",
                             None, agency=d.get("creator") if isinstance(d.get("creator"), str) else None,
                             release_date=(d.get("date") or "")[:10] or None, license=s.license, rights_note=s.note,
                             meta={"identifier": ident, "collection": coll}))
    return out


def internet_archive_media_url(identifier: str) -> str | None:
    r = requests.get(f"https://archive.org/metadata/{identifier}", headers=UA, timeout=30)
    r.raise_for_status()
    files = r.json().get("files", [])
    for pref in ("h.264", "MPEG4", "h.264 IA", "512Kb MPEG4"):
        for f in files:
            if f.get("format") == pref:
                return f"https://archive.org/download/{identifier}/{f['name']}"
    return None


def library_of_congress(query: str, rows: int = 10) -> list[Candidate]:
    r = requests.get("https://www.loc.gov/search/", headers=UA, timeout=30,
                     params={"q": query, "fa": "online-format:video", "fo": "json", "c": rows})
    r.raise_for_status()
    out = []
    for d in r.json().get("results", []):
        rights = " ".join(d.get("rights", []) if isinstance(d.get("rights"), list) else [str(d.get("rights") or "")])
        s = suggest_license(source_url=d.get("url") or d.get("id"), agency="Library of Congress", rights_text=rights)
        out.append(Candidate("real", d.get("title", "")[:200], "Library of Congress", d.get("url") or d.get("id"), None,
                             agency=(d.get("contributor") or ["Library of Congress"])[0] if isinstance(d.get("contributor"), list) else "Library of Congress",
                             release_date=(d.get("date") or "")[:10] or None, license=s.license, rights_note=s.note))
    return out


def nara(query: str, rows: int = 10) -> list[Candidate]:
    if not settings.nara_api_key:
        return []
    r = requests.get("https://catalog.archives.gov/api/v2/records/search", timeout=30,
                     headers={**UA, "x-api-key": settings.nara_api_key},
                     params={"q": query, "typeOfMaterials": "Moving Images", "limit": rows})
    r.raise_for_status()
    out = []
    for hit in r.json().get("body", {}).get("hits", {}).get("hits", []):
        rec = hit.get("_source", {}).get("record", {})
        naid = rec.get("naId")
        url = f"https://catalog.archives.gov/id/{naid}"
        s = suggest_license(source_url=url, agency="US National Archives", rights_text=str(rec.get("useRestriction", "")))
        out.append(Candidate("real", rec.get("title", "")[:200], "US National Archives", url, None,
                             agency=(rec.get("creators") or [{}])[0].get("heading", "US National Archives"),
                             license=s.license, rights_note=s.note, meta={"naId": naid}))
    return out


# ---------------------------------------------------------------- (b) news / documents
def gdelt_news(query: str, rows: int = 25, gov_only: bool = False) -> list[dict]:
    """Contemporaneous news (or .gov releases) as research sources. Free, no key."""
    # Exact phrase, English sources only: a loose keyword search returns unrelated articles in any language.
    phrase = '"' + query.replace('"', "").strip() + '"'
    q = f"{phrase} sourcelang:english" + (" domain:.gov" if gov_only else "")
    r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", headers=UA, timeout=30,
                     params={"query": q, "mode": "ArtList", "format": "json", "maxrecords": rows, "sort": "HybridRel"})
    if r.status_code != 200 or not r.text.strip().startswith("{"):
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
    out = []
    for a in r.json().get("articles", []):
        t = (a.get("title") or "").lower()
        if words and sum(w in t for w in words) < max(1, len(words) // 2):
            continue  # the headline must be about the subject
        out.append({"title": a.get("title"), "url": a.get("url"), "publisher": a.get("domain"),
                    "published_at": (a.get("seendate") or "")[:8], "excerpt": ""})
    return out


def courtlistener(query: str, rows: int = 10) -> list[dict]:
    """Court records first. Needs a free CourtListener token."""
    if not settings.courtlistener_token:
        return []
    r = requests.get("https://www.courtlistener.com/api/rest/v4/search/", timeout=30,
                     headers={**UA, "Authorization": f"Token {settings.courtlistener_token}"},
                     params={"q": query, "type": "r", "order_by": "score desc"})
    r.raise_for_status()
    out = []
    for d in r.json().get("results", [])[:rows]:
        out.append({"title": d.get("caseName") or d.get("case_name"), "publisher": d.get("court"),
                    "url": "https://www.courtlistener.com" + (d.get("docket_absolute_url") or d.get("absolute_url") or ""),
                    "published_at": d.get("dateFiled"), "excerpt": (d.get("snippet") or "")[:600]})
    return out


# ---------------------------------------------------------------- (c) stock B-roll (generic only)
def pexels(query: str, rows: int = 8) -> list[Candidate]:
    require("pexels")
    r = requests.get("https://api.pexels.com/videos/search", timeout=30, headers={"Authorization": settings.pexels_api_key},
                     params={"query": query, "orientation": "landscape", "per_page": rows, "size": "medium"})
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        files = sorted([f for f in v.get("video_files", []) if f.get("width") and f["width"] <= 1920],
                       key=lambda f: -f["width"])
        if not files:
            continue
        out.append(Candidate("stock_broll", f"Pexels video {v['id']}", "Pexels", v["url"], files[0]["link"],
                             agency=(v.get("user") or {}).get("name"), license="Licensed",
                             rights_note="Pexels License: free to use, no attribution required; B-roll only.",
                             duration=v.get("duration"), width=files[0]["width"], height=files[0]["height"]))
    return out


def pixabay(query: str, rows: int = 8) -> list[Candidate]:
    require("pixabay")
    r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                     params={"key": settings.pixabay_api_key, "q": query, "per_page": max(3, rows), "safesearch": "true"})
    r.raise_for_status()
    out = []
    for v in r.json().get("hits", []):
        vid = v["videos"].get("large") or v["videos"].get("medium")
        if not vid or not vid.get("url"):
            continue
        out.append(Candidate("stock_broll", f"Pixabay video {v['id']}", "Pixabay", v["pageURL"], vid["url"],
                             agency=v.get("user"), license="Licensed",
                             rights_note="Pixabay Content License; B-roll only.", duration=v.get("duration"),
                             width=vid.get("width"), height=vid.get("height")))
    return out


def search_scene(need: dict) -> list[Candidate]:
    """Search in priority order for one footage need. Never returns made-up items."""
    q = need["search_query"]
    res: list[Candidate] = []
    from .discovery import is_graphic
    try:
        if need["tier"] == "real":
            res += internet_archive(q) + library_of_congress(q) + nara(q)
        elif need["tier"] == "news_document":
            res += library_of_congress(q)
        else:
            if settings.pexels_api_key:
                res += pexels(q)
            if settings.pixabay_api_key:
                res += pixabay(q)
    except requests.RequestException as e:  # network/API trouble: report, do not invent
        res = []
        need.setdefault("errors", []).append(str(e))
    return [c for c in res if not is_graphic(c.title)]
