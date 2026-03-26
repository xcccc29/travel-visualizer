from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import markdown
import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape


DAY_HEADING_RE = re.compile(r"^###\s+(Day\s+\d+\s+[^\n]+)$", re.MULTILINE)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
COORDS_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")
INLINE_PLACE_RE = re.compile(
    r"\*\*(?P<name>[^*]+?)\*\*"
    r"(?:\s*\((?P<address>[^\)]+)\))?"
    r"(?:\s*[—-]\s*(?P<description>[^\n]+))?"
)
NON_DAY_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)

SECTION_LABELS = {
    "transportation",
    "morning",
    "afternoon",
    "evening",
    "post-race",
    "in salzburg",
    "suggested timing",
    "transportation (both ways)",
    "in nördlingen (~3–4 hours)",
    "in nordlingen (~3-4 hours)",
    "last-minute shopping",
    "transportation to airport",
    "why nördlingen",
    "option a (if legs are good)",
    "option b",
}

REJECT_PREFIXES = (
    "walk the ",
    "climb the ",
    "town center walk",
    "total one way",
    "evening back in munich",
    "afternoon",
    "suggested timing",
    "transportation",
)

MARKDOWN_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "smarty"]
GEOCODE_ENDPOINT = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "travel-visualizer/1.0 (portable trip visualizer)"
GEOCODE_ALIASES = {
    "mirabell palace & gardens": ["Schloss Mirabell Salzburg", "Mirabell Palace Salzburg", "Mirabell Gardens Salzburg"],
    "Mozart Residence": ["Makartplatz 8"],
    "rieskrater museum": ["RiesKraterMuseum Nördlingen", "Rieskrater Museum Nördlingen"],
}


@dataclass
class Location:
    id: str
    name: str
    source_text: str
    address: str | None = None
    description: str | None = None
    city_context: str | None = None
    coords: list[float] | None = None
    query: str | None = None


@dataclass
class TravelSegment:
    id: str
    label: str
    from_name: str
    to_name: str
    from_coords: list[float] | None = None
    to_coords: list[float] | None = None


@dataclass
class DayPlan:
    id: str
    index: int
    heading: str
    subtitle: str
    city_context: str | None
    markdown_content: str
    html_content: str = ""
    locations: list[Location] = field(default_factory=list)
    travel_segments: list[TravelSegment] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an interactive travel visualizer HTML from markdown.")
    parser.add_argument("input", type=Path, help="Path to the markdown travel plan")
    parser.add_argument("-o", "--output", type=Path, help="Path to the generated HTML file")
    parser.add_argument("--cache", type=Path, default=Path("geocache.json"), help="Path to geocode cache JSON")
    parser.add_argument("--no-geocode", action="store_true", help="Skip live geocoding and use cached coordinates only")
    parser.add_argument("--api-key", help="Google Maps API key (falls back to GOOGLE_MAPS_API_KEY)")
    return parser.parse_args()


def render_markdown(text: str) -> str:
    return markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def normalize_place_name(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed.replace("’", "'")


def cleanup_location_name(name: str) -> str:
    cleaned = normalize_place_name(name)
    cleaned = re.sub(r"^(Dinner|Lunch(?: in [^:]+)?|Breakfast|Brunch):\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^Option\s+[A-Z](?:\s*\([^\)]*\))?:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^In [^:]+:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def cleanup_address(address: str | None) -> str | None:
    if not address:
        return None
    cleaned = normalize_place_name(address)
    lowered = cleaned.lower()
    if any(token in lowered for token in ("optional", " min", "opens ", ".at", ".de", ".com")):
        return None
    return cleaned


def simplify_place_names(name: str) -> list[str]:
    candidates = [name]
    compact = re.sub(r"\s*\([^\)]*\)", "", name).strip()
    candidates.append(compact)

    simplified = compact
    replacements = (
        (r"\s*&\s*Gardens$", ""),
        (r"\s+hill walk$", ""),
        (r"\s+Mountain$", ""),
    )
    for pattern, replacement in replacements:
        simplified = re.sub(pattern, replacement, simplified, flags=re.IGNORECASE).strip()
    candidates.append(simplified)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def should_keep_location(name: str) -> bool:
    cleaned = cleanup_location_name(name)
    lowered = cleaned.rstrip(":").lower()
    if not lowered or lowered in SECTION_LABELS:
        return False
    if lowered.endswith(":"):
        return False
    if lowered.startswith("day "):
        return False
    if any(lowered.startswith(prefix) for prefix in REJECT_PREFIXES):
        return False
    if not any(character.isupper() for character in cleaned):
        return False
    return any(character.isalpha() for character in lowered)


def extract_city_context(heading: str) -> str | None:
    title_part = heading.split("|", 1)[-1].strip() if "|" in heading else heading
    arrow_parts = [part.strip() for part in title_part.split("→") if part.strip()]
    if arrow_parts:
        candidate = arrow_parts[-1]
    else:
        candidate = re.split(r"\s+[—–]\s+|\s+\+\s+|:\s+", title_part, maxsplit=1)[0]
    candidate = re.sub(r"\bArrive\b", "", candidate, flags=re.IGNORECASE).strip()
    candidate = re.sub(r"\bDay Trip\b", "", candidate, flags=re.IGNORECASE).strip(" +")
    candidate = re.sub(r"\s{2,}", " ", candidate)
    return candidate or None


def extract_intro(markdown_text: str, first_day_match: re.Match[str] | None) -> tuple[str, str]:
    if first_day_match is None:
        return markdown_text.strip(), ""
    title_text = markdown_text[: first_day_match.start()].strip()
    rendered = render_markdown(title_text) if title_text else ""
    return title_text, rendered


def extract_manual_coords(text: str) -> list[float] | None:
    match = COORDS_RE.search(text)
    if not match:
        return None
    return [float(match.group(1)), float(match.group(2))]


def parse_locations(day_id: str, city_context: str | None, body: str) -> list[Location]:
    locations: list[Location] = []
    seen: set[tuple[str, str | None]] = set()
    place_counter = 1

    for line in body.splitlines():
        if "**" not in line:
            continue
        line_coords = extract_manual_coords(line)
        for match in INLINE_PLACE_RE.finditer(line):
            raw_name = normalize_place_name(match.group("name"))
            name = cleanup_location_name(raw_name)
            if not should_keep_location(name):
                continue
            address = cleanup_address(match.group("address")) if match.group("address") else None
            description = normalize_place_name(match.group("description")) if match.group("description") else None
            dedupe_key = (name.lower(), address.lower() if address else None)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            locations.append(
                Location(
                    id=f"{day_id}-place-{place_counter}",
                    name=name,
                    source_text=raw_name,
                    address=address,
                    description=description,
                    city_context=city_context,
                    coords=line_coords,
                )
            )
            place_counter += 1

    return locations


def annotate_day_markdown(day: DayPlan) -> str:
    annotated = day.markdown_content
    for location in day.locations:
        patterns: list[str] = []
        escaped_source = re.escape(location.source_text)
        if location.address:
            escaped_address = re.escape(location.address)
            patterns.append(fr"\*\*{escaped_source}\*\*\s*\({escaped_address}\)")
        patterns.append(fr"\*\*{escaped_source}\*\*")

        replaced = False
        for pattern in patterns:
            marker = (
                f'<span class="place-link" data-location-id="{location.id}" role="button" tabindex="0">'
                f'<strong>{location.name}</strong></span>'
            )
            if location.address:
                marker += f" <span class=\"place-address\">({location.address})</span>"
            updated, count = re.subn(pattern, marker, annotated, count=1)
            if count:
                annotated = updated
                replaced = True
                break
        if not replaced:
            continue
    return annotated


def parse_travel_segments(day: DayPlan) -> list[TravelSegment]:
    heading_tail = day.heading.split("|", 1)[-1].strip() if "|" in day.heading else day.heading
    if "→" not in heading_tail:
        return []
    parts = [re.sub(r"\bArrive\b", "", part, flags=re.IGNORECASE).strip() for part in heading_tail.split("→")]
    parts = [part for part in parts if part]
    segments: list[TravelSegment] = []
    for index, (from_name, to_name) in enumerate(zip(parts, parts[1:]), start=1):
        segments.append(
            TravelSegment(
                id=f"{day.id}-segment-{index}",
                label=f"{from_name} to {to_name}",
                from_name=from_name,
                to_name=to_name,
            )
        )
    return segments


def parse_plan(markdown_text: str) -> tuple[str, str, list[DayPlan]]:
    matches = list(DAY_HEADING_RE.finditer(markdown_text))
    intro_markdown, intro_html = extract_intro(markdown_text, matches[0] if matches else None)
    if not matches:
        return intro_markdown, intro_html, []

    days: list[DayPlan] = []
    for index, match in enumerate(matches, start=1):
        start = match.end()
        end = matches[index].start() if index < len(matches) else len(markdown_text)
        heading = match.group(1).strip()
        body = markdown_text[start:end].strip()
        if index == len(matches):
            extra_section = NON_DAY_SECTION_RE.search(body)
            if extra_section:
                body = body[: extra_section.start()].strip()
        day_id = f"day-{index}"
        city_context = extract_city_context(heading)
        day = DayPlan(
            id=day_id,
            index=index,
            heading=heading,
            subtitle=heading.split("|", 1)[-1].strip() if "|" in heading else heading,
            city_context=city_context,
            markdown_content=body,
        )
        day.locations = parse_locations(day_id, city_context, body)
        day.travel_segments = parse_travel_segments(day)
        day.html_content = render_markdown(annotate_day_markdown(day))
        days.append(day)

    return intro_markdown, intro_html, days


def load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    with cache_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def build_query(name: str, address: str | None, city_context: str | None) -> str:
    parts = [name]
    if address:
        parts.append(address)
    if city_context and city_context.lower() not in name.lower():
        parts.append(city_context)
    return ", ".join(part for part in parts if part)


def geocode_candidates(location: Location) -> list[str]:
    candidates: list[str] = []
    for alias in GEOCODE_ALIASES.get(location.name.lower(), []):
        candidates.extend(
            [
                build_query(alias, location.address, None),
                build_query(alias, None, None),
                build_query(alias, location.address, location.city_context),
                build_query(alias, None, location.city_context),
            ]
        )
    for variant in simplify_place_names(location.name):
        candidates.extend(
            [
                build_query(variant, location.address, None),
                build_query(variant, None, None),
                build_query(variant, location.address, location.city_context),
                build_query(variant, None, location.city_context),
            ]
        )
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip().strip(",")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def geocode_query(query: str) -> list[float] | None:
    response = requests.get(
        GEOCODE_ENDPOINT,
        params={"q": query, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload:
        return None
    result = payload[0]
    return [float(result["lat"]), float(result["lon"])]


def hydrate_locations(days: list[DayPlan], cache: dict[str, Any], no_geocode: bool) -> None:
    for day in days:
        for location in day.locations:
            if location.coords:
                location.query = build_query(location.name, location.address, location.city_context)
                cache[location.query] = {"coords": location.coords, "name": location.name}
                continue
            candidates = geocode_candidates(location)
            location.query = candidates[0] if candidates else None
            for candidate in candidates:
                cached = cache.get(candidate)
                if cached and cached.get("coords"):
                    location.coords = cached["coords"]
                    location.query = candidate
                    break
            if location.coords or no_geocode:
                continue
            for candidate in candidates:
                resolved = geocode_query(candidate)
                time.sleep(1.1)
                cache[candidate] = {"coords": resolved, "name": location.name}
                if resolved:
                    location.coords = resolved
                    location.query = candidate
                    break


def hydrate_segments(days: list[DayPlan], cache: dict[str, Any], no_geocode: bool) -> None:
    for day in days:
        for segment in day.travel_segments:
            for field_name in ("from_name", "to_name"):
                place_name = getattr(segment, field_name)
                query = place_name
                cached = cache.get(query)
                coords = cached.get("coords") if cached else None
                if coords is None and not no_geocode:
                    coords = geocode_query(query)
                    time.sleep(1.1)
                    cache[query] = {"coords": coords, "name": place_name}
                if field_name == "from_name":
                    segment.from_coords = coords
                else:
                    segment.to_coords = coords


def serialize_days(days: list[DayPlan]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for day in days:
        serialized.append(
            {
                "id": day.id,
                "index": day.index,
                "heading": day.heading,
                "subtitle": day.subtitle,
                "cityContext": day.city_context,
                "html": day.html_content,
                "locations": [asdict(location) for location in day.locations],
                "travelSegments": [asdict(segment) for segment in day.travel_segments],
            }
        )
    return serialized


def build_output_path(input_path: Path, requested_output: Path | None) -> Path:
    return requested_output if requested_output else input_path.with_suffix(".html")


def render_template(title: str, intro_html: str, days: list[DayPlan], output_path: Path, api_key: str) -> None:
    template_dir = Path(__file__).parent
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("template.html")
    payload = {
        "title": title,
        "intro_html": intro_html,
        "days": serialize_days(days),
        "api_key": api_key,
    }
    rendered = template.render(page_title=title, intro_html=intro_html, days=days, trip_data=payload, api_key=api_key)
    output_path.write_text(rendered, encoding="utf-8")


def extract_title(markdown_text: str, input_path: Path) -> str:
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.replace("**", "")
    return input_path.stem.replace("-", " ").title()


def main() -> None:
    args = parse_args()
    api_key = args.api_key or os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise SystemExit(
            "Error: Google Maps API key is required. Create one in Google Cloud Console: "
            "https://developers.google.com/maps/documentation/javascript/get-api-key, "
            "then provide it with --api-key or set GOOGLE_MAPS_API_KEY."
        )
    markdown_text = args.input.read_text(encoding="utf-8")
    title = extract_title(markdown_text, args.input)
    _, intro_html, days = parse_plan(markdown_text)
    cache = load_cache(args.cache)
    hydrate_locations(days, cache, args.no_geocode)
    hydrate_segments(days, cache, args.no_geocode)
    save_cache(args.cache, cache)
    output_path = build_output_path(args.input, args.output)
    render_template(title, intro_html, days, output_path, api_key)
    print(f"Generated {output_path}")


if __name__ == "__main__":
    main()