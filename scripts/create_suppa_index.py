#!/usr/bin/env python3
"""
create_suppa_index.py

Builds browsable JSON indexes for SuppA/ETC supplementary data.

Today, the raw SuppA data is a plain GitHub repo of static JSON-LD files
(imranasif87/ETC-API). In the future a real server will replace it. To
make that swap a one-line change, "where do I get the list/content of
records" is isolated behind SuppAProvider. 

Separately, some SuppA fields (mainly for Works and Persons — title,
incipit, scoring, name, life dates) are intentionally NOT duplicated in
SuppA; they live in RISM and are only referenced via `derivedFrom`.
RismEnrichment fills those in:

  - Works:   read from index/index.json, which the existing
             jekyll-rism-online-renderer `jekyll load-data` step has
             *already* built earlier in the same CI run. Pure local
             lookup — no extra HTTP calls, no extra failure mode.
  - Persons: no equivalent pre-built RISM person index exists, so this
             does one live, cached, rate-limited fetch per distinct
             derivedFrom URI. 

IMPORTANT ORDERING REQUIREMENT: this script must run AFTER
`bundle exec jekyll load-data` in the same CI job, so index/index.json
exists. See .github/workflows/jekyll.yml.

Output (index/, alongside but never touching the existing index/index.json):

    index/suppa-work.json
    index/suppa-person.json
    index/suppa-performance.json
    index/suppa-correspondence.json
    index/suppa-place.json
    index/suppa-postcard.json
    index/suppa-recording.json
    index/suppa-index.json   <- combined, all types, tagged with entityType
"""

from __future__ import annotations

import abc
import json
import os
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

GITHUB_OWNER  = os.environ.get("SUPPA_GH_OWNER", "imranasif87")
GITHUB_REPO   = os.environ.get("SUPPA_GH_REPO", "ETC-API")
GITHUB_BRANCH = os.environ.get("SUPPA_GH_BRANCH", "main")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")

# Where the real SuppA server will eventually live.
SUPPA_API_BASE = os.environ.get("SUPPA_API_BASE", "https://etc.web.ox.ac.uk")

# "github" today, "api" once the real server exists and ApiProvider is written.
PROVIDER_MODE = os.environ.get("SUPPA_PROVIDER_MODE", "github")

ENTITY_TYPES = [
    "work", "person", "performance", "correspondence",
    "place", "postcard", "recording",
]

INDEX_DIR        = Path("index")
RISM_INDEX_PATH  = INDEX_DIR / "index.json"
HEADERS_JSON     = {"Accept": "application/ld+json, application/json"}
DELAY            = 0.2
MAX_RETRIES      = 3


# --------------------------------------------------------------------------
# HTTP helper
# --------------------------------------------------------------------------

def http_get_json(url: str, extra_headers: dict | None = None):
    headers = dict(HEADERS_JSON)
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} for {url} (attempt {attempt + 1})")
            if e.code == 404:
                return None
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"  Error fetching {url}: {e} (attempt {attempt + 1})")
            time.sleep(2 ** attempt)
    return None


def _get(d: dict, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


# --------------------------------------------------------------------------
# A pointer to "somewhere I can fetch this record from"
# --------------------------------------------------------------------------

@dataclass
class RecordRef:
    entity_type: str
    identifier: str       # e.g. "ETC-44" / "45128"
    fetch_url: str          # where to GET the full JSON-LD document right now
    #canonical_uri: str       # best guess at the real SuppA URI once the real API exists


# --------------------------------------------------------------------------
# SuppAProvider — where the raw record files come from.
# (Not called "Source" — see module docstring.)
# --------------------------------------------------------------------------

class SuppAProvider(abc.ABC):
    @abc.abstractmethod
    def list_records(self, entity_type: str) -> list[RecordRef]:
        ...

    def fetch_record(self, ref: RecordRef) -> dict | None:
        return http_get_json(ref.fetch_url)


class GitHubProvider(SuppAProvider):
    """
    Treats the ETC-API GitHub repo of static JSON-LD files as the current
    SuppA backend. Lists the whole repo tree once (recursive Git Trees API
    call) rather than one Contents-API call per folder, to stay well
    inside rate limits as record counts grow.
    """

    def __init__(self, owner: str, repo: str, branch: str, token: str | None = None):
        self.owner, self.repo, self.branch, self.token = owner, repo, branch, token
        self._tree: list[dict] | None = None

    def _auth_headers(self) -> dict:
        return {"Authorization": f"token {self.token}"} if self.token else {}

    def _get_tree(self) -> list[dict]:
        if self._tree is not None:
            return self._tree
        url = (f"https://api.github.com/repos/{self.owner}/{self.repo}"
               f"/git/trees/{self.branch}?recursive=1")
        data = http_get_json(url, extra_headers=self._auth_headers())
        if not data:
            print(f"  WARNING: could not list tree for {self.owner}/{self.repo}@{self.branch}")
            self._tree = []
            return self._tree
        if data.get("truncated"):
            print("  WARNING: GitHub tree response truncated — repo too large for a single "
                  "recursive listing. Switch to per-folder Contents API calls, or move to "
                  "the real SuppA API.")
        self._tree = data.get("tree", [])
        return self._tree

    def list_records(self, entity_type: str) -> list[RecordRef]:
        prefix = f"api/{entity_type}/"
        refs = []

        for node in self._get_tree():
            path = node.get("path", "")

            if (
                node.get("type") == "blob"
                and path.startswith(prefix)
                and path.endswith(".json")
            ):
                identifier = Path(path).stem

                # Current fetch location: GitHub Raw.
                # In the future, ApiProvider will return a real ETC/SuppA API URL here.
                raw_url = (
                    f"https://raw.githubusercontent.com/"
                    f"{self.owner}/{self.repo}/{self.branch}/{path}"
                )

                refs.append(
                    RecordRef(
                        entity_type=entity_type,
                        identifier=identifier,
                        fetch_url=raw_url,
                    )
                )

        return refs


class ApiProvider(SuppAProvider):
    """
    Placeholder for the real SuppA server. NOT IMPLEMENTED — the real
    list/search endpoint shape is still undecided (deferred earlier).
    This is the only class that needs writing when that's designed;
    everything else is already provider-agnostic.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def list_records(self, entity_type: str) -> list[RecordRef]:
        raise NotImplementedError(
            "ApiProvider.list_records() is not implemented yet. "
            "Keep SUPPA_PROVIDER_MODE=github until the real API's list endpoint exists."
        )


def get_provider() -> SuppAProvider:
    if PROVIDER_MODE == "github":
        return GitHubProvider(GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH, GITHUB_TOKEN)
    elif PROVIDER_MODE == "api":
        return ApiProvider(SUPPA_API_BASE)
    raise ValueError(f"Unknown SUPPA_PROVIDER_MODE: {PROVIDER_MODE!r}")


# --------------------------------------------------------------------------
# RismEnrichment — pulls RISM-side display fields via `derivedFrom`.
# --------------------------------------------------------------------------

def _rism_person_info(data: dict) -> dict:
    name = None
    label = data.get("label")
    if isinstance(label, dict):
        vals = label.get("none") or label.get("en")
        if isinstance(vals, list) and vals:
            name = vals[0]

    dates = None
    summary_items = _get(data, "biographicalDetails", "summary", default=[])
    if isinstance(summary_items, list):
        for item in summary_items:
            label_en = _get(item, "label", "en", default=[])
            if label_en and label_en[0] == "Life dates":
                value_none = _get(item, "value", "none", default=[])
                if value_none:
                    dates = value_none[0]
                break

    return {"name": name, "dates": dates}


class RismEnrichment:
    def __init__(self, work_index_path: Path = RISM_INDEX_PATH):
        self._work_index: dict[str, dict] = {}
        self._person_cache: dict[str, dict] = {}

        if work_index_path.exists():
            try:
                with work_index_path.open(encoding="utf-8") as f:
                    for entry in json.load(f):
                        if entry.get("id"):
                            self._work_index[entry["id"]] = entry
                print(f"Loaded {len(self._work_index)} RISM work summaries from {work_index_path}")
            except Exception as e:
                print(f"WARNING: could not read {work_index_path}: {e}")
        else:
            print(f"WARNING: {work_index_path} not found — SuppA work titles will fall back "
                  f"to their SuppA identifier. Run this script AFTER `bundle exec jekyll load-data`.")

    def work_summary(self, derived_from: str | None) -> dict | None:
        if not derived_from:
            return None
        return self._work_index.get(derived_from)

    def person_info(self, derived_from: str | None) -> dict:
        if not derived_from:
            return {}
        if derived_from in self._person_cache:
            return self._person_cache[derived_from]
        print(f"    fetching RISM person info for {derived_from} ...", end=" ")
        data = http_get_json(derived_from)
        info = _rism_person_info(data) if data else {}
        print("ok" if info.get("name") else "not found")
        self._person_cache[derived_from] = info
        time.sleep(DELAY)
        return info


# --------------------------------------------------------------------------
# Relationship helpers
# (relationships.items[] fields are inconsistent: sometimes "role",
#  sometimes "qualifier"; "relatedTo" may be absent when only a free-text
#  "note" is known — e.g. a recipient with no RISM/SuppA URI.)
# --------------------------------------------------------------------------

def find_relationship(data: dict, *role_values: str) -> dict | None:
    items = _get(data, "relationships", "items", default=[])
    if not isinstance(items, list):
        return None
    wanted = set(role_values)
    for item in items:
        role = item.get("role") or item.get("qualifier")
        if role in wanted:
            return item
    return None


def relationship_text(item: dict | None) -> str | None:
    if not item:
        return None
    return item.get("note") or item.get("relatedTo")


# --------------------------------------------------------------------------
# Per-type summary extraction
# --------------------------------------------------------------------------

def extract_common(ref: RecordRef, data: dict) -> dict:
    """
    `id` is the record's canonical JSON-LD identifier.

    `recordUrl` is the URL that must actually be fetched by the browser.
    Today that is a raw GitHub URL; later it will be the real SuppA API URL.
    """
    return {
        "id": data.get("id") or ref.fetch_url,
        "recordUrl": ref.fetch_url,
        "identifier": ref.identifier,
        "entityType": ref.entity_type,
        "etcIdentifier": data.get("etcIdentifier"),
        "derivedFrom": data.get("derivedFrom"),
        "title": data.get("label") or data.get("title") or ref.identifier,
    }


def extract_work(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    rism_summary = enrichment.work_summary(out["derivedFrom"])
    if rism_summary:
        for key in ("title", "textIncipit", "scoringSummary", "catalogNumber", "keyMode", "subject", "incipit"):
            if key in rism_summary:
                out[key] = rism_summary[key]
    elif out["derivedFrom"]:
        print(f"    WARNING: derivedFrom {out['derivedFrom']} not found in {RISM_INDEX_PATH}")

    dedicatees = _get(data, "dedicatees", "items")
    if isinstance(dedicatees, list) and dedicatees:
        first = dedicatees[0]
        out["dedicatee"] = first if isinstance(first, str) else first.get("label")
    return out


def extract_person(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    rism_info = enrichment.person_info(out["derivedFrom"])
    if rism_info.get("name"):
        out["title"] = rism_info["name"]
    elif out["title"] == ref.identifier:
        bio = data.get("biography") or ""
        if bio:
            out["title"] = bio[:80].rsplit(" ", 1)[0] + "…"
    out["dates"] = rism_info.get("dates")
    out["biography"] = data.get("biography")
    return out


def extract_performance(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    work_rel = find_relationship(data, "etcrel:work")
    venue_rel = find_relationship(data, "etcrel:venue")
    out["title"] = data.get("descriptionLabel") or out["title"]
    out["date"] = data.get("date")
    out["venue"] = relationship_text(venue_rel)
    out["relatedWork"] = work_rel.get("relatedTo") if work_rel else None
    return out


def extract_correspondence(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    author = relationship_text(find_relationship(data, "etcrel:author"))
    recipient = relationship_text(find_relationship(data, "etcrel:recipient"))
    work_rel = find_relationship(data, "etcrel:work")
    out["date"] = data.get("date")
    out["referenceType"] = data.get("referenceType")
    out["author"] = author
    out["recipient"] = recipient
    out["relatedWork"] = work_rel.get("relatedTo") if work_rel else None
    if author and recipient:
        out["title"] = f"{author} to {recipient}" + (f", {out['date']}" if out["date"] else "")
    elif out["date"]:
        out["title"] = f"Correspondence, {out['date']}"
    return out


def extract_place(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    out["title"] = data.get("placeName") or out["title"]
    out["cityTown"] = data.get("cityTown")
    out["country"] = data.get("country")
    out["placeType"] = data.get("placeType")
    return out


def extract_postcard(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    sender = relationship_text(find_relationship(data, "etcrel:sender"))
    recipient = relationship_text(find_relationship(data, "etcrel:recipient"))
    out["date"] = data.get("postmarkDate")
    out["sender"] = sender
    out["recipient"] = recipient
    if sender and recipient:
        out["title"] = f"Postcard, {sender} to {recipient}" + (f", {out['date']}" if out["date"] else "")
    elif out["date"]:
        out["title"] = f"Postcard, {out['date']}"
    return out


def extract_recording(ref: RecordRef, data: dict, enrichment: RismEnrichment) -> dict:
    out = extract_common(ref, data)
    work_rel = find_relationship(data, "etcrel:recordingOf")
    out["date"] = data.get("recordingDate")
    out["recordingLabel"] = data.get("recordingLabel")
    out["catalogueNumber"] = data.get("catalogueNumber")
    out["relatedWork"] = work_rel.get("relatedTo") if work_rel else None
    bits = [b for b in (data.get("recordingLabel"), data.get("catalogueNumber"), out["date"]) if b]
    if bits:
        out["title"] = "Recording — " + ", ".join(bits)
    return out


EXTRACTORS = {
    "work": extract_work,
    "person": extract_person,
    "performance": extract_performance,
    "correspondence": extract_correspondence,
    "place": extract_place,
    "postcard": extract_postcard,
    "recording": extract_recording,
}


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------

def build_index_for_type(provider: SuppAProvider, entity_type: str, enrichment: RismEnrichment) -> list[dict]:
    print(f"\n== {entity_type} ==")
    refs = provider.list_records(entity_type)
    print(f"  found {len(refs)} record(s)")
    extractor = EXTRACTORS[entity_type]

    summaries = []
    for i, ref in enumerate(refs, 1):
        print(f"  [{i}/{len(refs)}] {ref.identifier}", end=" ... ")
        data = provider.fetch_record(ref)
        if data is None:
            print("FAILED")
            continue
        try:
            summaries.append(extractor(ref, data, enrichment))
            print("ok")
        except Exception as e:
            print(f"ERROR extracting summary: {e}")
        time.sleep(DELAY)
    return summaries


def main():
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    provider = get_provider()
    enrichment = RismEnrichment()

    combined = []
    for entity_type in ENTITY_TYPES:
        summaries = build_index_for_type(provider, entity_type, enrichment)
        out_path = INDEX_DIR / f"suppa-{entity_type}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(summaries, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  wrote {out_path} ({len(summaries)} records)")
        combined.extend(summaries)

    combined_path = INDEX_DIR / "suppa-index.json"
    with combined_path.open("w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nWrote {combined_path} ({len(combined)} records total)")


if __name__ == "__main__":
    main()