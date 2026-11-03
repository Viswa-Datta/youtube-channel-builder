"""Rights rules for footage. The database enforces the same rules as CHECK constraints
(see assets table in 0001_schema.sql); this module explains them and pre-fills suggestions.

Principles:
* Publicly available does NOT mean free to reuse.
* US federal government works are generally public domain (17 U.S.C. 105); state and local
  works vary by state, so every release is checked individually.
* Never rip or re-upload another YouTuber's edit of bodycam: go to the original agency release.
* Never use sealed, juvenile or restricted hearings, leaks, or unreleased footage.
* A suggestion is never a verification: rights_checked is only set by a person in the UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LICENSES = ("Public Domain", "Public Record with release terms", "Licensed", "Commentary-only", "Do Not Use")

# Hosts that are re-uploads/edits by third parties: never a valid source for "real" footage.
THIRD_PARTY_VIDEO_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com",
                           "twitter.com", "reddit.com", "dailymotion.com", "rumble.com", "vimeo.com")
FEDERAL_HINTS = (".gov", "archives.gov", "loc.gov", "justice.gov", "fbi.gov", "uscourts.gov")
BANNED_TERMS = ("sealed", "juvenile court", "under seal", "leaked", "leak ", "unreleased", "restricted hearing")


@dataclass
class Suggestion:
    license: str
    note: str


def suggest_license(*, source_url: str | None, agency: str | None, licenseurl: str | None = None,
                    rights_text: str | None = None, collection: str | None = None) -> Suggestion:
    url = (source_url or "").lower()
    lic = (licenseurl or "").lower()
    rights = (rights_text or "").lower()
    if any(t in rights or t in url for t in BANNED_TERMS):
        return Suggestion("Do Not Use", "Source mentions sealed/juvenile/restricted/leaked material.")
    if any(h in url for h in THIRD_PARTY_VIDEO_HOSTS) and not (agency and _is_agency_channel_note(rights)):
        return Suggestion("Do Not Use", "Third-party video host. Use the original agency release, not a re-upload or edit.")
    if "publicdomain" in lic or "public domain" in rights or "creativecommons.org/publicdomain" in lic:
        return Suggestion("Public Domain", "Item metadata states public domain. Still verify the specific clip.")
    if collection and "prelinger" in collection.lower():
        return Suggestion("Public Domain", "Prelinger Archives item; most are public domain but verify this item's rights statement.")
    if any(h in url for h in FEDERAL_HINTS) and "state" not in (agency or "").lower():
        return Suggestion("Public Domain", "Looks like a US federal work (generally public domain). Confirm it is federal, not state/local.")
    if agency and re.search(r"police|sheriff|county|city|state|district attorney|court", agency.lower()):
        return Suggestion("Public Record with release terms",
                          "State/local public record. Check the agency's release terms and your state's law before use.")
    if "creativecommons.org/licenses" in lic:
        return Suggestion("Licensed", f"Creative Commons ({licenseurl}). Follow its attribution/share-alike terms.")
    return Suggestion("Do Not Use", "Rights unknown. Find the rights statement before using.")


def _is_agency_channel_note(rights: str) -> bool:
    return "official agency channel" in rights


def can_verify_real(asset: dict) -> list[str]:
    """Why an asset cannot be marked 'verified real footage'. Empty list = allowed."""
    errs = []
    if asset.get("tier") != "real":
        errs.append("Only case footage (tier 'real') can be verified real. Stock is always B-roll.")
    for f in ("source_url", "agency", "release_date"):
        if not asset.get(f):
            errs.append(f"missing {f}")
    if not asset.get("rights_checked"):
        errs.append("rights not checked by a person")
    if asset.get("license") in (None, "Do Not Use"):
        errs.append("license is Do Not Use")
    url = (asset.get("source_url") or "").lower()
    if any(h in url for h in THIRD_PARTY_VIDEO_HOSTS):
        errs.append("source is a third-party video host; link the original agency release")
    return errs


def edit_note(asset: dict) -> str:
    """The line written to edit notes / credits for this asset."""
    if asset["tier"] == "stock_broll":
        return f"B-roll (illustrative, not the real incident/person/place): {asset.get('title') or ''} — {asset.get('source_name')} ({asset.get('source_url')})"
    if asset["tier"] == "generated":
        return f"Graphic made for this video: {asset.get('title') or ''}"
    if asset["tier"] == "gap":
        return f"GAP — no verified clip: {asset.get('title') or ''}"
    return (f"{asset.get('title') or 'Clip'} — {asset.get('agency')} via {asset.get('source_name')}, released "
            f"{asset.get('release_date')}, {asset.get('license')} ({asset.get('source_url')})")
