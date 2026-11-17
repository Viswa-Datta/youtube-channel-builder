"""JSON schemas for every agent. Output is validated here before the next agent may run."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

LicenseStatus = Literal["Public Domain", "Public Record with release terms", "Licensed", "Commentary-only", "Do Not Use"]
ClaimStatus = Literal["confirmed", "disputed", "unknown"]
SourceTier = Literal["court_record", "agency_release", "contemporaneous_news", "other_video", "reference"]
TIER_ORDER = {"court_record": 1, "agency_release": 2, "contemporaneous_news": 3, "reference": 4, "other_video": 5}


# ------------------------------------------------------------------ Channel Agent
class NameOption(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    rationale: str


class ChannelKit(BaseModel):
    names: list[NameOption] = Field(min_length=5, max_length=5)
    handles: list[str] = Field(min_length=3, max_length=8)
    description: str = Field(min_length=40, max_length=1000)
    about: str = Field(min_length=80, max_length=5000)
    pillars: list[str] = Field(min_length=3, max_length=4)
    cadence: str
    banner_prompt: str
    profile_image_prompt: str
    trailer_idea: str

    @field_validator("handles")
    @classmethod
    def handle_format(cls, v: list[str]) -> list[str]:
        out = []
        for h in v:
            h = h if h.startswith("@") else "@" + h
            if not re.fullmatch(r"@[A-Za-z0-9._-]{3,30}", h):
                raise ValueError(f"invalid YouTube handle {h!r} (3-30 chars: letters, digits, . _ -)")
            out.append(h)
        return out


CHANNEL_KIT_ITEMS = ["names", "handles", "description", "about", "pillars", "cadence", "banner_prompt",
                     "profile_image_prompt", "trailer_idea"]


# ------------------------------------------------------------------ Ideas Agent
class Idea(BaseModel):
    title: str = Field(min_length=10, max_length=100)
    hook: str = Field(min_length=10, max_length=400)
    angle: str = Field(min_length=10, max_length=400)
    pillar: str
    demand_score: int = Field(ge=0, le=10)
    competition_score: int = Field(ge=0, le=10, description="10 = very crowded")
    footage_feasibility_score: int = Field(ge=0, le=10, description="10 = verified public footage very likely")
    rationale: str
    # Case-based niches (true crime etc.): the real case this idea is about. Optional for other niches.
    case_name: str | None = Field(default=None, max_length=160, description="Common name of the real case, as reported")
    location: str | None = Field(default=None, max_length=120, description="City, State/Country")
    year: int | None = Field(default=None, description="Year of the subject; negative for BC (e.g. -2560 for the Great Pyramid)")
    agency: str | None = Field(default=None, max_length=160, description="Investigating agency, e.g. 'Phoenix Police Department'")
    case_status: str | None = Field(default=None, max_length=60, description="e.g. convicted, acquitted, resolved, unsolved")
    public_footage: list[str] = Field(default_factory=list, max_length=6, description="Kinds of footage believed public, e.g. 'bodycam released by agency'")
    search_terms: list[str] = Field(default_factory=list, max_length=4, description="Queries to find the official footage")

    @field_validator("year", mode="before")
    @classmethod
    def year_sane(cls, v):
        try:
            v = int(v)
        except (TypeError, ValueError):
            return None
        return v if -5000 <= v <= 2100 else None

    @field_validator("public_footage", "search_terms", mode="before")
    @classmethod
    def trim_lists(cls, v):
        return list(v or [])[:4] if isinstance(v, list) else []

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, v):
        return str(v)[:100] if v is not None else v

    @field_validator("hook", "angle", mode="before")
    @classmethod
    def trim_long(cls, v):
        return str(v)[:400] if v is not None else v

    @field_validator("demand_score", "competition_score", "footage_feasibility_score", mode="before")
    @classmethod
    def clamp_score(cls, v):
        try:
            return max(0, min(10, int(round(float(v)))))
        except (TypeError, ValueError):
            return 5

    @field_validator("case_name", "location", "agency", "case_status", mode="before")
    @classmethod
    def trim_text(cls, v):
        return (str(v)[:150] if v not in (None, "") else None)


class IdeaBatch(BaseModel):
    ideas: list[Idea] = Field(min_length=20, max_length=30)


class IdeaPart(BaseModel):
    """One half of an idea batch (smaller answers are faster and more reliable)."""
    ideas: list[Idea] = Field(min_length=8, max_length=16)


def rank_score(i: Idea) -> float:
    return round(0.4 * i.demand_score + 0.25 * (10 - i.competition_score) + 0.35 * i.footage_feasibility_score, 2)


# ------------------------------------------------------------------ Research Agent
class Claim(BaseModel):
    scene_idx: int | None = Field(default=None, ge=0)
    claim: str
    status: ClaimStatus
    source_refs: list[str] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def confirmed_needs_source(self) -> "Claim":
        if self.status == "confirmed" and not self.source_refs:
            raise ValueError(f"confirmed claim without a source: {self.claim[:80]!r}")
        return self


class Research(BaseModel):
    summary: str
    timeline: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(min_length=3)
    disclaimers: list[str] = Field(default_factory=list)
    sensitive_topics: list[Literal["health", "finance", "legal", "minors", "graphic"]] = Field(default_factory=list)
    fact_check_notes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="What could not be verified")


# ------------------------------------------------------------------ Script Agent
class ChartSpec(BaseModel):
    """A small bar chart for one scene. Values must come from the research claims (checked by the app)."""
    title: str = Field(max_length=70)
    unit: str = Field(default="", max_length=20, description="e.g. 'm', 'USD bn', 'points', 'million people'")
    labels: list[str] = Field(min_length=2, max_length=8)
    values: list[float] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def same_length(self) -> "ChartSpec":
        if len(self.labels) != len(self.values):
            raise ValueError("chart labels and values must have the same length")
        return self


class SceneDraft(BaseModel):
    idx: int = Field(ge=0)
    title: str
    chapter_title: str = Field(max_length=60)
    draft: str
    on_screen_text: list[str] = Field(default_factory=list, max_length=6)
    claim_refs: list[int] = Field(default_factory=list, description="indexes into research.claims")
    chart: ChartSpec | None = Field(default=None, description="only if the claims contain 2-8 comparable numbers")

    @field_validator("draft")
    @classmethod
    def draft_len(cls, v: str) -> str:
        n = len(v.split())
        if not 60 <= n <= 120:
            raise ValueError(f"scene draft must be 60-120 words, got {n}")
        return v


class ScriptDraft(BaseModel):
    scenes: list[SceneDraft] = Field(min_length=3, max_length=14)

    @model_validator(mode="after")
    def sequential(self) -> "ScriptDraft":
        if [s.idx for s in self.scenes] != list(range(len(self.scenes))):
            raise ValueError("scene idx must be 0..n-1 in order")
        return self


class SceneNarration(BaseModel):
    idx: int
    narration: str

    def words(self) -> int:
        return len(self.narration.split())


# ------------------------------------------------------------------ Motion Graphics Agent
GRAPHIC_KINDS = ("route", "place", "counter", "bars", "timeline", "points", "quote")


class Graphic(BaseModel):
    """One explainer animation shown while the narration talks about it. Checked by the app before use."""
    kind: str = Field(description="route | place | counter | bars | timeline | points | quote")
    anchor: str = Field(default="", description="5-12 words copied exactly from the narration where it should appear")
    title: str = Field(default="", description="bars/timeline/points heading, counter label, quote label")
    place: str = Field(default="", description="place: a geocodable name, e.g. 'Empire State Building, New York'")
    place_label: str = Field(default="", description="short on-screen name for the place")
    from_place: str = ""
    to_place: str = ""
    value: float | None = None
    unit: str = ""
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list, description="points: 2-5 short phrases; timeline: 'YEAR: event'")
    text: str = Field(default="", description="quote: one sentence copied from the narration")

    @field_validator("title", "place", "place_label", "from_place", "to_place", "unit", "text", "anchor", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)[:300]

    @field_validator("values", mode="before")
    @classmethod
    def _vals(cls, v):
        out = []
        for x in v or []:
            try:
                out.append(float(str(x).replace(",", "")))
            except ValueError:
                continue
        return out

    @field_validator("value", mode="before")
    @classmethod
    def _val(cls, v):
        if v in (None, ""):
            return None
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return None


class SceneGraphics(BaseModel):
    idx: int
    graphics: list[Graphic] = Field(default_factory=list)

    @field_validator("graphics", mode="before")
    @classmethod
    def _cap(cls, v):
        return (v or [])[:12]


# ------------------------------------------------------------------ Shot List Agent
class Shot(BaseModel):
    beat: int
    asset: str = Field(default="", description="id of the listed photo/clip that shows what this beat says, or ''")
    query: str = Field(default="", description="if no listed item fits: 2-5 word search for a photo/clip that would")
    label: str = Field(default="", description="optional on-screen label, max 6 words, only facts said in the beat")
    generate: str = Field(default="", description="'image' or 'video' to make an AI scale-model illustration, else ''")
    prompt: str = Field(default="", description="for generate: one sentence describing the miniature scene")

    @field_validator("generate", mode="before")
    @classmethod
    def _g(cls, v):
        v = (str(v or "")).strip().lower()
        return v if v in ("image", "video") else ""

    @field_validator("prompt", mode="before")
    @classmethod
    def _p(cls, v):
        return "" if v is None else str(v)[:400]

    @field_validator("asset", "query", "label", mode="before")
    @classmethod
    def _s(cls, v):
        return "" if v is None else str(v)[:120]


class ShotPlan(BaseModel):
    idx: int
    shots: list[Shot] = Field(default_factory=list)


# ------------------------------------------------------------------ Visual Director Agent
class FootageNeed(BaseModel):
    scene_idx: int
    priority: Literal[1, 2, 3]  # 1 real case footage, 2 news/doc on camera, 3 stock B-roll
    tier: Literal["real", "news_document", "stock_broll"]
    description: str
    search_query: str
    source_hint: str
    blur_notes: str = ""

    @model_validator(mode="after")
    def priority_matches_tier(self) -> "FootageNeed":
        expect = {"real": 1, "news_document": 2, "stock_broll": 3}[self.tier]
        if self.priority != expect:
            raise ValueError("priority must match tier (real=1, news_document=2, stock_broll=3)")
        return self


class RedactionNote(BaseModel):
    scene_idx: int
    kind: Literal["face", "plate", "manual_box", "graphic_cutaway", "audio_mute", "audio_bleep", "caption_omit", "text_pii"]
    reason: str


class FootagePlan(BaseModel):
    needs: list[FootageNeed] = Field(min_length=3)
    redactions: list[RedactionNote] = Field(default_factory=list)
    foia_suggestions: list[str] = Field(default_factory=list, description="records to request; never promised footage")


# ------------------------------------------------------------------ Editor + SEO Agent
class Chapter(BaseModel):
    t: str = Field(pattern=r"^\d{1,2}:\d{2}(:\d{2})?$")
    title: str = Field(max_length=60)


class SeoPack(BaseModel):
    titles: list[str] = Field(min_length=3, max_length=3)
    description: str = Field(min_length=200, max_length=5000)
    tags: list[str] = Field(min_length=5, max_length=30)
    thumbnail_concept: str
    thumbnail_prompt: str
    thumbnail_text: str = Field(max_length=40)
    disclosure_line: str = Field(min_length=20)
    pinned_comment: str = ""

    @field_validator("titles")
    @classmethod
    def titles_short(cls, v: list[str]) -> list[str]:
        for t in v:
            if len(t) >= 60:
                raise ValueError(f"title must be under 60 characters: {t!r} ({len(t)})")
            if re.search(r"\b(a|the) (history|story) of\b|\bexplained\b|\ban overview\b|\ba look at\b", t, re.I):
                raise ValueError(f"title reads like an encyclopedia label; give a surprising fact, conflict or question: {t!r}")
        return v

    @field_validator("tags")
    @classmethod
    def tags_budget(cls, v: list[str]) -> list[str]:
        if sum(len(t) for t in v) > 480:
            raise ValueError("tags exceed YouTube's 500-character budget")
        return v


class ProductionPack(BaseModel):
    """Everything the review UI shows for a picked idea before the whole-video render."""
    research: Research
    script: ScriptDraft
    footage: FootagePlan
    seo: SeoPack
    chapters: list[Chapter]


# ------------------------------------------------------------------ Shorts Agent
class ShortLine(BaseModel):
    text: str = Field(description="one spoken sentence")
    asset: str = Field(default="", description="id of the listed photo/clip that shows this line, or ''")
    generate: str = Field(default="", description="one sentence for an AI scale-model illustration when no item fits")

    @field_validator("text", mode="before")
    @classmethod
    def _t(cls, v):
        return re.sub(r"\s+", " ", str(v or "")).strip()[:300]

    @field_validator("asset", mode="before")
    @classmethod
    def _a(cls, v):
        return "" if v is None else str(v).strip()[:12]

    @field_validator("generate", mode="before")
    @classmethod
    def _g(cls, v):
        return "" if v is None else str(v).strip()[:400]


class ShortScript(BaseModel):
    title: str = Field(description="YouTube title, max 70 characters, no hashtags")
    hook: str = Field(description="on-screen hook for the first seconds, max 48 characters")
    lines: list[ShortLine]
    description: str = Field(default="", description="2-3 sentences")
    hashtags: list[str] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        return re.sub(r"#\w+", "", str(v or "")).strip()[:70]

    @field_validator("hook", mode="before")
    @classmethod
    def _hook(cls, v):
        return str(v or "").strip()[:60]

    @field_validator("hashtags", mode="before")
    @classmethod
    def _tags(cls, v):
        out = []
        for t in v or []:
            t = re.sub(r"[^\w]", "", str(t))
            if t and t.lower() not in [x.lower() for x in out] and t.lower() != "shorts":
                out.append(t[:30])
        return out[:4]

    @model_validator(mode="after")
    def _len(self) -> "ShortScript":
        self.lines = [x for x in self.lines if x.text][:14]
        words = sum(len(x.text.split()) for x in self.lines)
        if not 70 <= words <= 175:
            raise ValueError(f"a Short needs 100-150 spoken words (got {words})")
        return self


class ShortsBatch(BaseModel):
    shorts: list[ShortScript]
