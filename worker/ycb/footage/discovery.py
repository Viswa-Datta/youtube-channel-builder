"""Case discovery: checks that a case is real and finds where its public footage actually lives.

Everything here comes from a live API response; nothing is synthesised. Three outcomes per item:
* downloadable  - the source's terms allow an app to fetch the file (Wikimedia Commons, Internet Archive,
                  National Archives, Library of Congress, direct files on .gov sites);
* find_only     - the file exists but the app must not download it itself (YouTube uploads, including
                  agencies' official channels: YouTube's terms forbid automated downloading). These become
                  help requests for the user;
* reference     - text sources that confirm the case (Wikipedia, court records, news).
"""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import requests

from ..config import settings
from .rights import suggest_license

UA = {"User-Agent": "YouTubeChannelBuilder/1.0 (https://youtube-channel-builder.vercel.app; footage research)"}
AGENCY_RE = re.compile(r"\b(police|sheriff|[a-z]{0,4}pd|department|dept|county|city of|district attorney|attorney general|"
                       r"state patrol|highway patrol|marshal|court|courts|prosecutor|public safety|bureau of investigation|fbi|"
                       r"doj|department of justice)\b", re.I)
FOOTAGE_RE = re.compile(r"body[\s-]?cam|bodycam|body[\s-]?worn|dash[\s-]?cam|critical incident|surveillance|"
                        r"interrogation|interview|911|press conference|court|trial|sentencing|hearing", re.I)


GRAPHIC_RE = re.compile(r"autops|corpse|dead body|bodies|cadaver|gore|graphic|morgue|crime scene photo|injur|wound|blood", re.I)


def is_graphic(title: str) -> bool:
    """Never offer autopsy, injury or body images/videos as footage (they are also excluded from thumbnails)."""
    return bool(GRAPHIC_RE.search(title or ""))


@dataclass
class Evidence:
    kind: str                 # downloadable | find_only | reference
    source: str               # e.g. "YouTube", "Wikimedia Commons", "Wikipedia"
    title: str
    url: str
    media_url: str | None = None
    channel: str | None = None
    official: bool = False    # posted by what looks like the agency itself
    published: str | None = None
    license: str = "Do Not Use"
    rights_note: str = ""
    media_type: str = "video"  # video | image | text
    meta: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


_wm_lock = __import__("threading").Lock()
LAST_ERRORS: dict[str, str] = {}  # most recent API error per source (shown by `check-footage`, stored on ideas)


def _get(url: str, **kw) -> requests.Response | None:
    """GET that respects rate limits: on 429/503 waits Retry-After (max 30 s) and tries twice more.
    Wikimedia sites are called one at a time (their API etiquette asks for serial requests)."""
    import time
    wikimedia = "wikipedia.org" in url or "wikimedia.org" in url
    headers = {**UA, **kw.pop("headers", {})}
    timeout = kw.pop("timeout", 25)
    for attempt in range(3):
        try:
            if wikimedia:
                with _wm_lock:
                    r = requests.get(url, headers=headers, timeout=timeout, **kw)
            else:
                r = requests.get(url, headers=headers, timeout=timeout, **kw)
        except requests.RequestException:
            return None
        if r.status_code in (429, 503) and attempt < 2:
            try:
                wait = min(30.0, float(r.headers.get("Retry-After", "5")))
            except ValueError:
                wait = 5.0
            time.sleep(wait)
            continue
        return r if r.ok else None
    return None


# ---------------------------------------------------------------- reference: does the case exist?
def wikipedia(query: str, n: int = 3) -> list[Evidence]:
    r = _get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": query,
                                                           "format": "json", "srlimit": n})
    out = []
    for hit in (r.json().get("query", {}).get("search", []) if r else []):
        title = hit["title"]
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        out.append(Evidence("reference", "Wikipedia", title, "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                            media_type="text", meta={"snippet": snippet}))
    return out


def wikipedia_sections(title: str, max_chars: int = 14000, excerpt_chars: int = 1800) -> list[dict]:
    """Plain-text sections of one Wikipedia article (for research). Each section becomes a citable source."""
    r = _get("https://en.wikipedia.org/w/api.php", params={"action": "query", "prop": "extracts", "explaintext": 1,
                                                           "titles": title, "format": "json", "redirects": 1})
    pages = (r.json().get("query", {}).get("pages", {}) if r else {}).values()
    text = next((p.get("extract", "") for p in pages if p.get("extract")), "")
    if not text:
        return []
    url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    parts = re.split(r"\n(==+)\s*(.+?)\s*\1\n", "\n" + text)
    out, head, used = [], "Summary", 0
    chunks = [(head, parts[0])] + [(parts[i + 1], parts[i + 2]) for i in range(1, len(parts) - 2, 3)]
    for h, body in chunks:
        body = body.strip()
        if not body or h.lower() in ("references", "external links", "see also", "notes", "further reading", "bibliography"):
            continue
        body = body[: max(0, max_chars - used)]
        if not body:
            break
        used += len(body)
        out.append({"title": f"{title} — {h}", "url": url + ("" if h == "Summary" else "#" + h.replace(" ", "_")),
                    "publisher": "Wikipedia", "published_at": None, "excerpt": body[:excerpt_chars]})
    return out


def _agency_match(agency: str, channel: str) -> bool:
    """Channel title carries the agency's distinctive name, e.g. 'Phoenix Police Department' vs 'PhoenixPolice'."""
    generic = {"police", "department", "dept", "county", "sheriff", "office", "city", "state", "of", "the", "regional", "service"}
    key = [w for w in re.findall(r"[a-z0-9]+", agency.lower()) if w not in generic and len(w) > 2]
    flat = re.sub(r"[^a-z0-9]", "", channel.lower())
    return bool(key) and all(w in flat for w in key) and bool(re.search(r"police|sheriff|pd|county|city|state|patrol", channel, re.I))


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in {"the", "and", "case", "murder", "killing", "death", "shooting"}}


def case_articles(case_name: str, refs: list[Evidence]) -> list[Evidence]:
    """Wikipedia hits whose TITLE carries the case name (e.g. 'Killing of Elijah McClain'), best first.
    A city or general article that merely mentions the case is never treated as the case's article."""
    want = _words(case_name)
    if not want:
        return []
    need = len(want) if len(want) <= 2 else max(2, round(0.6 * len(want)))
    return [e for e in refs if len(want & _words(e.title)) >= need]


def case_confirmed(case_name: str, location: str | None, refs: list[Evidence]) -> Evidence | None:
    hits = case_articles(case_name, refs)
    return hits[0] if hits else None


# ---------------------------------------------------------------- find-only: YouTube (official agency uploads)
def youtube(query: str, n: int = 8, agency: str | None = None) -> list[Evidence]:
    if not settings.youtube_api_key:
        return []
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", headers={**UA, "x-goog-api-key": settings.youtube_api_key},
                         params={"part": "snippet", "q": query, "type": "video", "maxResults": n, "safeSearch": "none"}, timeout=25)
    except requests.RequestException as e:
        LAST_ERRORS["youtube"] = f"network: {type(e).__name__}"
        return []
    if not r.ok:
        try:
            msg = r.json().get("error", {}).get("message", "")[:200]
        except ValueError:
            msg = r.text[:200]
        LAST_ERRORS["youtube"] = f"HTTP {r.status_code}: {msg}".replace(settings.youtube_api_key, "[redacted]")
        return []
    LAST_ERRORS.pop("youtube", None)
    out = []
    for it in r.json().get("items", []):
        sn = {k: (html.unescape(v) if isinstance(v, str) else v) for k, v in it.get("snippet", {}).items()}
        vid = it.get("id", {}).get("videoId")
        if not vid:
            continue
        ch = sn.get("channelTitle", "")
        official = bool(AGENCY_RE.search(ch)) or bool(agency and _agency_match(agency, ch))
        note = ("Posted by what appears to be the agency's own channel. The app cannot download from YouTube: "
                "save the original yourself and check the agency's release terms." if official else
                "Third-party upload: do not use. Find the agency's original release instead.")
        out.append(Evidence("find_only", "YouTube", sn.get("title", ""), f"https://www.youtube.com/watch?v={vid}",
                            channel=ch, official=official, published=(sn.get("publishedAt") or "")[:10] or None,
                            license="Public Record with release terms" if official else "Do Not Use", rights_note=note,
                            meta={"video_id": vid, "channel_id": sn.get("channelId")}))
    return out


# ---------------------------------------------------------------- downloadable: Wikimedia Commons
def wikimedia_commons(query: str, n: int = 8, media: str = "video") -> list[Evidence]:
    """Freely licensed files with machine-readable license metadata. media: video | image."""
    filetype = "filetype:video" if media == "video" else "filetype:bitmap"
    r = _get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{query} {filetype}", "gsrnamespace": 6,
        "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 1920})
    out = []
    pages = (r.json().get("query", {}).get("pages", {}) if r else {}).values()
    for p in pages:
        if is_graphic(p.get("title", "")):
            continue
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata", {})
        lic_short = (md.get("LicenseShortName", {}) or {}).get("value", "")
        lic_url = (md.get("LicenseUrl", {}) or {}).get("value", "")
        artist = re.sub(r"<[^>]+>", "", (md.get("Artist", {}) or {}).get("value", "")).strip()[:120]
        date = re.sub(r"<[^>]+>", "", (md.get("DateTimeOriginal", {}) or {}).get("value", ""))[:10]
        pd = "public domain" in lic_short.lower() or lic_short.upper().startswith("PD") or "publicdomain" in lic_url
        s = suggest_license(source_url=ii.get("descriptionurl"), agency=artist or None,
                            licenseurl=lic_url or ("publicdomain" if pd else None),
                            rights_text="public domain" if pd else lic_short)
        out.append(Evidence("downloadable", "Wikimedia Commons", p.get("title", "").removeprefix("File:"),
                            ii.get("descriptionurl", ""), media_url=ii.get("url"), channel=artist or None,
                            published=date or None, license=s.license,
                            rights_note=f"{lic_short or 'license unknown'}. {s.note}",
                            media_type="video" if (ii.get("mime") or "").startswith("video") else "image",
                            meta={"mime": ii.get("mime"), "size": ii.get("size"), "license_short": lic_short,
                                  "license_url": lic_url, "thumb_url": ii.get("thumburl")}))
    return out


# ---------------------------------------------------------------- downloadable: Internet Archive (video)
def internet_archive(query: str, n: int = 6) -> list[Evidence]:
    r = _get("https://archive.org/advancedsearch.php", params={
        "q": f"({query}) AND mediatype:(movies)", "fl[]": ["identifier", "title", "date", "licenseurl", "rights", "creator", "collection"],
        "rows": n, "output": "json"})
    out = []
    for d in (r.json().get("response", {}).get("docs", []) if r else []):
        coll = d.get("collection")
        coll = ",".join(coll) if isinstance(coll, list) else (coll or "")
        creator = d.get("creator") if isinstance(d.get("creator"), str) else None
        s = suggest_license(source_url=f"https://archive.org/details/{d['identifier']}", agency=creator,
                            licenseurl=d.get("licenseurl"), rights_text=str(d.get("rights") or ""), collection=coll)
        out.append(Evidence("downloadable", "Internet Archive", d.get("title") or d["identifier"],
                            f"https://archive.org/details/{d['identifier']}", channel=creator,
                            published=(d.get("date") or "")[:10] or None, license=s.license, rights_note=s.note,
                            meta={"identifier": d["identifier"], "collection": coll}))
    return out


# ---------------------------------------------------------------- idea probing
def probe_case(case_name: str, location: str | None, agency: str | None, search_terms: list[str]) -> dict:
    """Check one proposed case: does it exist, and what public footage can actually be found?
    Returns {"verified": bool, "reference": {...}|None, "evidence": [...], "feasibility": 0-10}."""
    base = case_name
    yt_q = (search_terms[0] if search_terms else f"{case_name} bodycam")
    if agency and agency.lower() not in yt_q.lower():
        yt_q = f"{agency} {yt_q}"
    with ThreadPoolExecutor(4) as ex:
        f_wiki = ex.submit(wikipedia, base)
        f_yt = ex.submit(youtube, yt_q, 8, agency)
        f_ia = ex.submit(internet_archive, case_name, 4)
        f_wc = ex.submit(wikimedia_commons, case_name, 4, "image")
        refs, yt, ia, wc = f_wiki.result(), f_yt.result(), f_ia.result(), f_wc.result()
    ref = case_confirmed(case_name, location, refs)
    name_words = _words(case_name)

    need = min(2, len(name_words))

    def relevant(e: Evidence) -> bool:  # every distinctive word of a 1-2 word name, at least 2 of a longer one
        return len(name_words & _words(e.title + " " + (e.meta.get("snippet") or ""))) >= need

    yt = [e for e in yt if not is_graphic(e.title)]
    official_video = [e for e in yt if e.official and relevant(e) and FOOTAGE_RE.search(e.title)]
    other_yt = [e for e in yt if relevant(e) and e not in official_video][:3]
    archive = [e for e in ia if relevant(e) and e.license != "Do Not Use"]
    images = [e for e in wc if relevant(e) and e.license != "Do Not Use"]
    score = 1
    if ref:
        score += 2
    if official_video:
        score += 5
    if archive:
        score += 2
    if images:
        score += 1
    if not ref and not official_video and not archive:
        score = min(score, 2)  # nothing confirms the case exists: keep it at the bottom
    evidence = [e.dict() for e in (official_video[:4] + archive[:3] + images[:3] + other_yt)]
    return {"verified": bool(ref), "reference": ref.dict() if ref else None, "evidence": evidence,
            "feasibility": min(10, score), "youtube_checked": bool(settings.youtube_api_key) and "youtube" not in LAST_ERRORS,
            "youtube_error": LAST_ERRORS.get("youtube"), "youtube_results": len(yt)}


# ================================================================ automatic (any-niche) footage
# Licenses an app may use without a person negotiating anything, judged from the source's own metadata.
# Share-alike (BY-SA), non-commercial (NC) and no-derivatives (ND) are excluded: a narrated, cropped, zoomed video is an
# adaptation, which would put share-alike terms on the whole video, and NC/ND forbid monetised edits.
def license_ok(license_short: str | None, license_url: str | None = None) -> bool:
    s = f"{license_short or ''} {license_url or ''}".lower()
    if not s.strip():
        return False
    if any(t in s for t in ("-sa", " sa ", "by-sa", "sharealike", "share-alike", "-nc", "noncommercial", "non-commercial", "-nd", "noderiv", "fair use")):
        return False
    return any(t in s for t in ("public domain", "publicdomain", "pd-", "pd ", "cc0", "cc-zero", "no restrictions", "cc by ", "cc-by-",
                                "cc by 2", "cc by 3", "cc by 4", "licenses/by/", "attribution"))


def nasa(query: str, n: int = 8, media: str = "video") -> list[Evidence]:
    """NASA Image and Video Library: NASA-produced media is generally not copyrighted (credit NASA).
    Items credited to a non-NASA photographer/organisation are skipped."""
    r = _get("https://images-api.nasa.gov/search", params={"q": query, "media_type": media, "page_size": n})
    out = []
    for it in (r.json().get("collection", {}).get("items", [])[:n] if r else []):
        d = (it.get("data") or [{}])[0]
        credit = (d.get("photographer") or d.get("secondary_creator") or "NASA")
        if not re.search(r"\bnasa\b|jpl|goddard|johnson|kennedy|marshall|ames|langley|glenn|armstrong|stennis|\bksc\b|\bjsc\b|\bmsfc\b|\bgsfc\b",
                         f"{credit} {d.get('center', '')}", re.I):
            continue  # third-party copyright possible
        if is_graphic(d.get("title", "")):
            continue
        out.append(Evidence("downloadable", "NASA", d.get("title", "")[:300], f"https://images.nasa.gov/details/{d.get('nasa_id')}",
                            channel=f"NASA {d.get('center') or ''}".strip(), published=(d.get("date_created") or "")[:10] or None,
                            license="Public Domain", rights_note="NASA media: not copyrighted in the US; credit NASA. Do not imply NASA endorsement.",
                            media_type=media, meta={"nasa_manifest": it.get("href"), "license_short": "Public domain (NASA)",
                                                    "keywords": d.get("keywords", [])[:10], "description": (d.get("description") or "")[:400]}))
    return out


def nasa_media_url(manifest_url: str, media: str = "video") -> str | None:
    """Pick a 1080p-ish rendition; NASA "orig" files are often multi-gigabyte masters."""
    r = _get(manifest_url)
    files = r.json() if r else []
    prefs = ("~large.mp4", "~medium.mp4", "~orig.mp4", "~mobile.mp4") if media == "video" else ("~large.jpg", "~orig.jpg", "~medium.jpg")
    for p in prefs:
        for f in files:
            if f.endswith(p):
                return f.replace("http://", "https://")
    return None


def smithsonian(query: str, n: int = 8) -> list[Evidence]:
    """Smithsonian Open Access: only items marked CC0 (public domain dedication). Needs an api.data.gov key."""
    if not settings.data_gov_api_key:
        return []
    r = _get("https://api.si.edu/openaccess/api/v1.0/search", headers={"X-Api-Key": settings.data_gov_api_key},
             params={"q": f"{query} AND online_media_type:Images", "rows": n})  # key in a header, never in URLs/logs
    out = []
    for row in ((r.json().get("response") or {}).get("rows", []) if r else []):
        c = row.get("content", {})
        dnr = c.get("descriptiveNonRepeating", {})
        media = ((dnr.get("online_media") or {}).get("media") or [])
        for m in media[:1]:
            access = ((m.get("usage") or {}).get("access") or "")
            if access.upper() != "CC0" or m.get("type") != "Images" or is_graphic(row.get("title", "")):
                continue
            url = m.get("content")
            out.append(Evidence("downloadable", "Smithsonian Open Access", row.get("title", "")[:300],
                                dnr.get("record_link") or dnr.get("guid") or url, media_url=url,
                                channel=(dnr.get("data_source") or "Smithsonian"), license="Public Domain",
                                rights_note="Smithsonian Open Access, CC0 (no rights reserved).", media_type="image",
                                meta={"license_short": "CC0", "unit": row.get("unitCode")}))
    return out


def subject_article(name: str) -> dict | None:
    """The English Wikipedia article for a subject (exact title or redirect), or None. Disambiguation pages don't count."""
    r = _get("https://en.wikipedia.org/w/api.php", params={"action": "query", "titles": name, "redirects": 1, "format": "json",
                                                           "prop": "pageprops|info", "inprop": "url"})
    for p in ((r.json().get("query") or {}).get("pages") or {}).values() if r else []:
        if "missing" in p or "disambiguation" in (p.get("pageprops") or {}):
            continue
        return {"title": p["title"], "url": p.get("fullurl") or "https://en.wikipedia.org/wiki/" + p["title"].replace(" ", "_")}
    return None


def pageviews(title: str, days: int = 60) -> dict | None:
    """Real popularity: average daily Wikipedia views over `days` and the last-7-days trend vs the period before."""
    import datetime as dt
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    t = title.replace(" ", "_")
    r = _get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
             f"{requests.utils.quote(t, safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    items = (r.json().get("items") or []) if r else []
    if not items:
        return None
    v = [i.get("views", 0) for i in items]
    avg = sum(v) / len(v)
    last7 = sum(v[-7:]) / max(1, len(v[-7:]))
    before = sum(v[:-7]) / max(1, len(v[:-7]))
    return {"avg_daily": round(avg), "last7_daily": round(last7), "trend": round(last7 / before, 2) if before else None}


def demand_from_views(pv: dict | None) -> int | None:
    """Map average daily Wikipedia views to 0-10 (100/day ~ 3, 1k ~ 5, 10k ~ 7, 100k ~ 9), +1 if trending up 50%+."""
    import math
    if not pv:
        return None
    s = 1 + 2 * math.log10(max(1, pv["avg_daily"]))
    if pv.get("trend") and pv["trend"] >= 1.5:
        s += 1
    return int(max(0, min(10, round(s - 1))))


TRUSTED_IA_COLLECTIONS = ("prelinger", "fedflix", "usgovernmentdocuments", "nasa", "us_government", "national_archives",
                          "dod", "navy", "army", "airforce", "noaa", "usgs", "nps")


def probe_subject(subject: str, search_terms: list[str]) -> dict:
    """Any-niche idea check: the subject's Wikipedia article (exact title or redirect), real popularity, and how much
    free, license-checked media the agent can fetch automatically."""
    art = subject_article(subject)
    if not art:  # fall back to search, but only accept an article whose title names the subject
        hits = case_articles(subject, wikipedia(subject, 5))
        art = {"title": hits[0].title, "url": hits[0].url} if hits else None
    pv = pageviews(art["title"]) if art else None
    words = _words(subject)
    media: list[Evidence] = []
    for q in ([subject] + [t for t in search_terms if t and t.lower() != subject.lower()])[:2]:
        media += free_media(q, 6, words)
    seen, uniq = set(), []
    for e in media:
        if e.url not in seen:
            seen.add(e.url)
            uniq.append(e)
    vids = sum(1 for e in uniq if e.media_type == "video")
    imgs = sum(1 for e in uniq if e.media_type == "image")
    feas = 1 + min(5, vids) + min(4, (imgs + 1) // 2)
    if not art:
        feas = min(feas, 2)
    return {"verified": bool(art), "reference": {"title": art["title"], "url": art["url"], "source": "Wikipedia"} if art else None,
            "evidence": [e.dict() for e in uniq[:12]], "feasibility": min(10, feas), "pageviews": pv,
            "demand": demand_from_views(pv), "free_videos": vids, "free_images": imgs}


def free_media(query: str, n: int = 6, words: set[str] | None = None) -> list[Evidence]:
    """Everything the agent may download and use automatically for a query, from every free source, license-filtered."""
    def relevant(e: Evidence) -> bool:
        """Short names (<= 4 distinctive words) must match in full: 'Empire State Building' must not match
        'State Duma of the Russian Empire'. Longer names need 60% of their words."""
        if not words:
            return True
        text = e.title + " " + " ".join(e.meta.get("keywords") or []) + " " + (e.meta.get("description") or "")
        need = len(words) if len(words) <= 4 else max(3, round(0.6 * len(words)))
        return len(words & _words(text)) >= need
    out: list[Evidence] = []
    with ThreadPoolExecutor(5) as ex:
        futs = [ex.submit(wikimedia_commons, query, n, "video"), ex.submit(wikimedia_commons, query, n, "image"),
                ex.submit(nasa, query, n, "video"), ex.submit(nasa, query, n, "image"), ex.submit(internet_archive, query, n),
                ex.submit(smithsonian, query, n)]
        for f in futs:
            try:
                out += f.result()
            except Exception:  # noqa: BLE001 - one source failing never stops the search
                pass
    keep = []
    seen_titles: dict[str, int] = {}
    for e in out:
        if is_graphic(e.title) or not relevant(e):
            continue
        tkey = re.sub(r"\W+", " ", e.title.lower()).strip()
        if seen_titles.get(tkey, 0) >= 2:
            continue  # a press set with 20 near-identical shots would crowd out everything else
        seen_titles[tkey] = seen_titles.get(tkey, 0) + 1
        if e.source == "Wikimedia Commons":
            if not license_ok(e.meta.get("license_short"), e.meta.get("license_url")):
                continue
            if e.media_type == "image" and (e.meta.get("size") or 0) < 150_000:
                continue  # icons, logos, thumbnails
        elif e.source == "Internet Archive":
            # Self-labelled uploads are not trusted: only curated public-domain film and government collections.
            coll = (e.meta.get("collection") or "").lower()
            if e.license != "Public Domain" or not any(c in coll for c in TRUSTED_IA_COLLECTIONS):
                continue
            e.media_type = "video"
        keep.append(e)
    return keep
