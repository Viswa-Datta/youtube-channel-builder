"""Niche presets: how ideas, research and visuals adapt to a channel's niche.

Every preset shares the same footage rule: the agent uses only media it may legally fetch on its own (public domain,
CC0, CC BY, free stock licences) and generated graphics. The presets steer WHAT to look for and HOW to tell it.
"""
from __future__ import annotations

import re

PRESETS: dict[str, dict] = {
    "true_crime": {
        "match": r"crime|murder|case|police|court|trial|detective|serial|forensic|heist|scam|fraud",
        "subjects": "specific real, documented cases (commonly reported case name), preferring resolved cases",
        "ideas": "Prefer resolved cases with rich public records. Avoid minors as subjects, sealed records and ongoing trials.",
        "visuals": "Location photos, court buildings, documents and records, maps, timelines, archive news photos. Never "
                   "victims in distress or injury imagery; stock is only for neutral establishing shots.",
        "safety": "Victims and families are treated with dignity; allegations stay allegations unless a court confirmed them.",
    },
    "architecture": {
        "match": r"architect|building|skyscraper|bridge|tower|megaproject|engineering|construction|landmark|marvel|cathedral|dam|canal",
        "subjects": "specific famous structures, megaprojects or architects (each with a Wikipedia article)",
        "ideas": "Favour structures with dramatic stories: record-breaking engineering, construction challenges, failures, rescues.",
        "visuals": "Photos of the structure from many angles, construction-era archive photos, blueprints and drawings "
                   "(Library of Congress HABS/HAER), aerial and city B-roll, location maps, timeline of construction, "
                   "charts of height/cost/time.",
        "safety": "No unverified claims about collapses or deaths; cite engineering facts.",
    },
    "geopolitics": {
        "match": r"geopolit|politic|country|countries|war|conflict|empire|history|border|nation|military|diplomac|economy of",
        "subjects": "specific countries, conflicts, treaties, regions, leaders or historical events",
        "ideas": "Favour explanations of why something happened and what it means now; stay neutral and sourced.",
        "visuals": "Maps (regions, borders, routes), government and military photos (public domain), satellite imagery "
                   "(NASA), archive newsreels, charts (GDP, population, trade, military spending), timelines, city B-roll.",
        "safety": "Strictly neutral on contested politics: present each side's stated position; no propaganda framing.",
    },
    "motorsport": {
        "match": r"\bf1\b|formula|motorsport|racing|grand prix|nascar|rally|le mans|indycar|motogp",
        "subjects": "specific races, seasons, drivers, teams, cars or circuits",
        "ideas": "Stories of rivalries, engineering innovations, legendary races and seasons, rule changes.",
        "visuals": "Freely licensed photos of cars and drivers, circuit maps, charts (lap times, standings, championship "
                   "points), timelines, generic motorsport/stock B-roll. NEVER official race broadcast footage (owned by "
                   "the series and strictly enforced).",
        "safety": "Do not show crash injury imagery; no speculation about drivers' health.",
    },
    "science_space": {
        "match": r"space|nasa|planet|astronom|science|physics|nature|wildlife|animal|ocean|volcano|earthquake|climate|weather",
        "subjects": "specific missions, discoveries, phenomena, species or natural events",
        "ideas": "Explain one big idea per video with a clear story; prefer topics with NASA/NOAA/USGS media.",
        "visuals": "NASA video and images, NOAA/USGS media, archive science films, diagrams, charts, nature stock B-roll.",
        "safety": "Separate established science from hypotheses; no medical advice.",
    },
    "business_tech": {
        "match": r"business|company|startup|finance|money|invest|stock|crypto|tech|technology|brand|billionaire|economy",
        "subjects": "specific companies, products, founders, market events or technologies",
        "ideas": "Rise-and-fall stories, how-it-works explainers, decisions that changed an industry.",
        "visuals": "Freely licensed photos of people/places/products, charts (revenue, prices, market share), timelines, "
                   "office/city/technology stock B-roll. Never company logos as the main visual or ad footage.",
        "safety": "No financial advice; numbers must be dated and sourced.",
    },
    "generic": {
        "match": r".",
        "subjects": "specific famous people, places, events, objects or ideas (each with a Wikipedia article)",
        "ideas": "Favour subjects people actively search for and that have a real story arc.",
        "visuals": "Freely licensed photos and archive footage of the subject, maps, timelines, charts, stock B-roll.",
        "safety": "Accurate, sourced, respectful.",
    },
}


def preset_for(niche: str | None, sub_niche: str | None = None) -> dict:
    text = f"{niche or ''} {sub_niche or ''}".lower()
    for key, p in PRESETS.items():
        if key != "generic" and re.search(p["match"], text):
            return {"key": key, **p}
    return {"key": "generic", **PRESETS["generic"]}
