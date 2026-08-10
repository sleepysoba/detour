"""Wikimedia attraction discovery for arbitrary destinations."""

from __future__ import annotations

import logging
import math
import re
from time import perf_counter
from typing import Any
from urllib.parse import quote

import requests

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
DEFAULT_RADIUS_METERS = 10_000
DEFAULT_CANDIDATE_LIMIT = 25

logger = logging.getLogger(__name__)

_ATTRACTION_TERMS = {
    "arboretum",
    "architecture",
    "art",
    "beach",
    "building",
    "canyon",
    "center",
    "centre",
    "church",
    "creek",
    "district",
    "gallery",
    "garden",
    "historic",
    "landmark",
    "library",
    "mall",
    "market",
    "memorial",
    "museum",
    "music",
    "neighborhood",
    "park",
    "path",
    "site",
    "stadium",
    "street",
    "theater",
    "theatre",
    "trail",
    "university",
    "venue",
}
_BLOCKED_TITLE_PARTS = (
    "list of ",
    "timeline of ",
    " elections",
    " election",
    "school district",
    "radio station",
    "television station",
    "high school",
    "jila",
    "school of business",
    "laboratory for",
    "season",
    "state highway",
    "interstate ",
    "u.s. route",
)
_BIOGRAPHY_PATTERN = re.compile(
    r"\b(?:is|was) an? (?:american|canadian|british|australian|german|french|spanish) "
    r"(?:politician|athlete|player|actor|actress|writer|businessman|businesswoman|scientist)\b",
    re.IGNORECASE,
)

_DESTINATION_BLOCKED_PARTS = (
    "athletics",
    "airport",
    "business school",
    "elections",
    "events center",
    "fieldhouse",
    "high school",
    "marathon",
    "orchestra",
    "prediction center",
    "school district",
    "science institute",
    "geological society",
    " station",
    "sports season",
    "stadium",
    "university of",
    " weekly",
)
_DESTINATION_EVENT_PARTS = ("festival", "fest", "race", "championship")
_STRONG_PLACE_TERMS = {
    "arboretum",
    "auditorium",
    "beach",
    "building",
    "canyon",
    "district",
    "gallery",
    "garden",
    "historic",
    "landmark",
    "library",
    "mall",
    "market",
    "memorial",
    "mountain",
    "museum",
    "neighborhood",
    "observatory",
    "park",
    "site",
    "street",
    "theater",
    "theatre",
    "trail",
}
_CLEAR_VISITOR_SIGNALS = (
    "arboretum",
    "auditorium",
    "garden",
    "historic district",
    "historic site",
    "landmark",
    "market",
    "museum",
    "national register of historic places",
    "observatory",
    "park",
    "performing arts",
    "scenic",
    "theater",
    "theatre",
    "tourist attraction",
    "visitor attraction",
    "visitor center",
)
_STRONG_CLOSED_INDICATORS = (
    "closed forever",
    "now closed",
    "permanently closed",
    "closed permanently",
    "was later demolished",
    "was demolished",
    "has been demolished",
    "demolished in",
    "no longer exists",
)
_ADMINISTRATIVE_FACILITY_INDICATORS = (
    "district court",
    "government office",
    "administrative headquarters",
    "municipal office",
    "county offices",
)
_ORDINARY_OFFICE_INDICATORS = (
    "office skyscraper",
    "office tower",
    "commercial office building",
    "contain office space",
    "contains office space",
)
_OPERATIONAL_RESEARCH_INDICATORS = (
    "federally funded research and development center",
    "operational facility",
    "research institute",
    "research laboratories",
)


def attraction_quality_score(candidate: dict) -> int:
    """Score obvious place quality without attempting a universal classifier."""
    title = str(candidate.get("name") or candidate.get("title") or "").casefold()
    description = str(candidate.get("description") or "").casefold()
    text = f"{title} {description}"
    strong_hits = sum(term in text for term in _STRONG_PLACE_TERMS)
    title_hits = sum(term in title for term in _STRONG_PLACE_TERMS)
    opening_identity = description.split(".", 1)[0]
    visitor_scope = f"{title} {opening_identity}"
    has_visitor_signal = any(
        re.search(rf"\b{re.escape(signal)}\b", visitor_scope)
        for signal in _CLEAR_VISITOR_SIGNALS
    )

    # Strong current-state statements override historical/notability signals.
    # Words such as "former" or "historic" alone are intentionally insufficient.
    if any(indicator in description for indicator in _STRONG_CLOSED_INDICATORS):
        return -12
    if not has_visitor_signal and any(
        indicator in text for indicator in _ADMINISTRATIVE_FACILITY_INDICATORS
    ):
        return -11
    if not has_visitor_signal and any(
        indicator in description[:700] for indicator in _ORDINARY_OFFICE_INDICATORS
    ):
        return -10
    if not has_visitor_signal and any(
        indicator in description[:700] for indicator in _OPERATIONAL_RESEARCH_INDICATORS
    ):
        return -10

    if any(part in title for part in _DESTINATION_BLOCKED_PARTS):
        # A university museum or university observatory remains a usable place.
        if not any(
            term in title
            for term in ("museum", "gallery", "observatory", "garden", "arboretum", "neighborhood", "district")
        ):
            return -10
    if any(part in title for part in _DESTINATION_EVENT_PARTS) or (
        any(
            phrase in description[:220]
            for phrase in ("annual race", "annual road race", "annual festival", "is an annual 10-")
        )
    ):
        return -8
    if any(term in description[:220] for term in (" football stadium", "multi-purpose arena")):
        return -10
    if any(part in title for part in ("road", "highway", "route ")):
        return -10
    if "born" in description[:240] or _BIOGRAPHY_PATTERN.search(description[:400]):
        return -10
    if any(
        phrase in description[:300]
        for phrase in (
            "is a private university",
            "is a public university",
            "is an organization",
            "is a city in",
            "is a home rule city",
            "is a census-designated place",
            "is a newspaper",
        )
    ):
        return -8

    score = (title_hits * 5) + min(strong_hits, 5)
    if candidate.get("distance_m") is not None and float(candidate["distance_m"]) <= 12_000:
        score += 2
    if any(term in text for term in ("visitor", "tourist", "scenic", "cultural", "public")):
        score += 2
    return score


def filter_attraction_candidates(candidates: list[dict], *, limit: int = 25) -> list[dict]:
    """Prefer actual visitor places and remove clear people/events/administrative noise."""
    ranked = [candidate for candidate in candidates if attraction_quality_score(candidate) >= 1]
    ranked.sort(
        key=lambda item: (
            -attraction_quality_score(item),
            float(item.get("distance_m")) if item.get("distance_m") is not None else float("inf"),
            str(item.get("name") or ""),
        )
    )
    return ranked[:limit]


class WikimediaError(RuntimeError):
    """Normalized Wikimedia failure that does not expose response bodies."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class WikimediaService:
    """Discover and normalize nearby English Wikipedia place candidates."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 15,
        user_agent: str = "DetourCapstone/0.1 (educational project)",
        session: requests.Session | None = None,
    ):
        if not user_agent.strip():
            raise WikimediaError("INVALID_ARGUMENT", "A Wikimedia User-Agent is required.")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent.strip()})

    def _get_json(self, params: dict[str, Any], integration: str) -> dict:
        started = perf_counter()
        try:
            response = self.session.get(
                WIKIPEDIA_API_URL,
                params={"format": "json", "formatversion": 2, **params},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            raise WikimediaError("UPSTREAM_TIMEOUT", f"{integration} timed out.") from exc
        except requests.RequestException as exc:
            raise WikimediaError("UPSTREAM_ERROR", f"{integration} request failed.") from exc
        except ValueError as exc:
            raise WikimediaError("MALFORMED_RESPONSE", f"{integration} returned invalid JSON.") from exc

        if not isinstance(payload, dict) or payload.get("error"):
            raise WikimediaError("MALFORMED_RESPONSE", f"{integration} returned an invalid response.")
        logger.info("integration=%s duration_ms=%d", integration, round((perf_counter() - started) * 1000))
        return payload

    @staticmethod
    def _is_usable(title: str, extract: str, pageprops: Any) -> bool:
        lowered_title = title.casefold()
        if any(blocked in lowered_title for blocked in _BLOCKED_TITLE_PARTS):
            return False
        if isinstance(pageprops, dict) and "disambiguation" in pageprops:
            return False
        if len(extract.strip()) < 80 or _BIOGRAPHY_PATTERN.search(extract):
            return False
        return True

    @staticmethod
    def _rank(candidate: dict) -> tuple[float, float]:
        title = candidate["title"].casefold()
        extract = candidate["description"].casefold()
        title_hits = sum(term in title for term in _ATTRACTION_TERMS)
        extract_hits = sum(term in extract for term in _ATTRACTION_TERMS)
        relevance = (title_hits * 4) + min(extract_hits, 6)
        distance = candidate.get("distance_m")
        return (-float(relevance), float(distance) if distance is not None else float("inf"))

    @staticmethod
    def _distance_meters(latitude: float, longitude: float, other_lat: float, other_lon: float) -> float:
        radius = 6_371_000
        lat1 = math.radians(latitude)
        lat2 = math.radians(other_lat)
        delta_lat = lat2 - lat1
        delta_lon = math.radians(other_lon - longitude)
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    def discover_attractions(
        self,
        *,
        city: str,
        latitude: float,
        longitude: float,
        limit: int = DEFAULT_CANDIDATE_LIMIT,
        radius_meters: int = DEFAULT_RADIUS_METERS,
    ) -> list[dict]:
        """Return 15–30 nearby, attributable place candidates where available."""
        city_name = (city or "").strip()
        if not city_name:
            raise WikimediaError("INVALID_ARGUMENT", "City is required.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
            raise WikimediaError("INVALID_ARGUMENT", "limit must be an integer from 1 to 30.")
        if not 10 <= radius_meters <= 10_000:
            raise WikimediaError("INVALID_ARGUMENT", "radius_meters must be from 10 to 10000.")

        # Nearby results are cheap identifiers; use a wider pool before fetching
        # extracts so arbitrary cities are not dominated by the closest offices,
        # schools, and sports facilities.
        search_limit = min(150, max(limit * 5, 75))
        search_payload = self._get_json(
            {
                "action": "query",
                "list": "geosearch",
                "gscoord": f"{float(latitude):.6f}|{float(longitude):.6f}",
                "gsradius": radius_meters,
                "gslimit": search_limit,
                "gsnamespace": 0,
            },
            "Wikimedia nearby search",
        )
        query = search_payload.get("query")
        nearby = query.get("geosearch") if isinstance(query, dict) else None
        if not isinstance(nearby, list):
            raise WikimediaError("MALFORMED_RESPONSE", "Wikimedia nearby search returned invalid results.")
        if not nearby:
            return []

        discovery_by_id = {
            str(item["pageid"]): item
            for item in nearby
            if isinstance(item, dict) and item.get("pageid") is not None
        }
        if not discovery_by_id:
            raise WikimediaError("MALFORMED_RESPONSE", "Wikimedia nearby results omitted page identifiers.")

        city_token = city_name.split(",", 1)[0].strip().casefold()
        city_parts = [part.strip() for part in city_name.split(",") if part.strip()]
        category_locations = list(dict.fromkeys([", ".join(city_parts[:2]), city_parts[0]]))
        category_supplement_failed = False
        for category_location in category_locations:
            location_member_count = 0
            for category_prefix in ("Tourist attractions", "Museums"):
                try:
                    supplemental_payload = self._get_json(
                        {
                            "action": "query",
                            "list": "categorymembers",
                            "cmtitle": f"Category:{category_prefix} in {category_location}",
                            "cmlimit": 50,
                            "cmnamespace": 0,
                        },
                        "Wikimedia city attraction categories",
                    )
                except WikimediaError as exc:
                    logger.warning(
                        "wikimedia_category_supplement_unavailable city=%s code=%s",
                        city_name,
                        exc.code,
                    )
                    category_supplement_failed = True
                    break
                supplemental_query = supplemental_payload.get("query")
                supplemental = (
                    supplemental_query.get("categorymembers")
                    if isinstance(supplemental_query, dict)
                    else None
                )
                if not isinstance(supplemental, list):
                    raise WikimediaError(
                        "MALFORMED_RESPONSE", "Wikimedia city categories returned invalid results."
                    )
                for item in supplemental:
                    if isinstance(item, dict) and item.get("pageid") is not None:
                        discovery_by_id.setdefault(str(item["pageid"]), item)
                        location_member_count += 1
            if category_supplement_failed or location_member_count:
                break

        location_terms = " ".join(city_parts[:2])
        for search_query in (f'"{location_terms}" visitor attraction',):
            supplemental_payload = self._get_json(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": search_query,
                    "srlimit": 20,
                    "srnamespace": 0,
                },
                "Wikimedia city attraction search",
            )
            supplemental_query = supplemental_payload.get("query")
            supplemental = (
                supplemental_query.get("search") if isinstance(supplemental_query, dict) else None
            )
            if not isinstance(supplemental, list):
                raise WikimediaError("MALFORMED_RESPONSE", "Wikimedia city search returned invalid results.")
            for item in supplemental:
                if isinstance(item, dict) and item.get("pageid") is not None:
                    discovery_by_id.setdefault(str(item["pageid"]), item)

        pages: list[dict] = []
        discovery_by_title = {
            str(item.get("title") or "").casefold(): item for item in discovery_by_id.values()
        }
        redirect_discovery_by_title: dict[str, dict] = {}
        page_ids = list(discovery_by_id)
        for offset in range(0, len(page_ids), 50):
            detail_payload = self._get_json(
                {
                    "action": "query",
                    "pageids": "|".join(page_ids[offset : offset + 50]),
                    "prop": "extracts|info|pageprops|coordinates",
                    "inprop": "url",
                    "coprimary": "all",
                    "exintro": 1,
                    "explaintext": 1,
                    "exsentences": 6,
                    "redirects": 1,
                },
                "Wikimedia page extracts",
            )
            detail_query = detail_payload.get("query")
            detail_pages = detail_query.get("pages") if isinstance(detail_query, dict) else None
            if not isinstance(detail_pages, list):
                raise WikimediaError("MALFORMED_RESPONSE", "Wikimedia page extracts returned invalid results.")
            redirects = detail_query.get("redirects") if isinstance(detail_query, dict) else None
            if isinstance(redirects, list):
                for redirect in redirects:
                    if not isinstance(redirect, dict):
                        continue
                    source = discovery_by_title.get(str(redirect.get("from") or "").casefold())
                    target = str(redirect.get("to") or "").casefold()
                    if source and target:
                        redirect_discovery_by_title[target] = source
            pages.extend(detail_pages)

        candidates: list[dict] = []
        nearby_ids = {
            str(item.get("pageid")) for item in nearby if isinstance(item, dict) and item.get("pageid")
        }
        for page in pages:
            if not isinstance(page, dict) or page.get("missing") is True:
                continue
            page_id = str(page.get("pageid") or "")
            discovery = (
                discovery_by_id.get(page_id)
                or discovery_by_title.get(str(page.get("title") or "").casefold())
                or redirect_discovery_by_title.get(str(page.get("title") or "").casefold())
            )
            title = page.get("title")
            extract = page.get("extract")
            if not discovery or not isinstance(title, str) or not isinstance(extract, str):
                continue
            if not self._is_usable(title, extract, page.get("pageprops")):
                continue
            if title.casefold() in {city_token, city_name.casefold()}:
                continue

            source_url = page.get("fullurl")
            if not isinstance(source_url, str) or not source_url.startswith("https://"):
                source_url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            coordinates = page.get("coordinates")
            coordinate = coordinates[0] if isinstance(coordinates, list) and coordinates else {}
            raw_latitude = discovery.get("lat", coordinate.get("lat"))
            raw_longitude = discovery.get("lon", coordinate.get("lon"))
            candidate_latitude = float(raw_latitude) if raw_latitude is not None else None
            candidate_longitude = float(raw_longitude) if raw_longitude is not None else None
            if candidate_latitude is not None and candidate_longitude is not None:
                distance_m = self._distance_meters(
                    float(latitude), float(longitude), candidate_latitude, candidate_longitude
                )
                if distance_m > 30_000:
                    continue
            else:
                distance_m = discovery.get("dist")

            searchable_text = f"{title} {extract}".casefold()
            if page_id not in nearby_ids:
                if city_token not in searchable_text or not any(
                    term in searchable_text for term in _ATTRACTION_TERMS
                ):
                    continue

            try:
                candidate = {
                    "page_id": page_id,
                    "title": title.strip(),
                    "name": title.strip(),
                    "description": extract.strip(),
                    "source_url": source_url,
                    "latitude": candidate_latitude,
                    "longitude": candidate_longitude,
                    "distance_m": round(float(distance_m), 1) if distance_m is not None else None,
                    "source": "Wikimedia/Wikipedia",
                }
            except (KeyError, TypeError, ValueError):
                continue
            candidates.append(candidate)

        candidates.sort(
            key=lambda candidate: (
                -attraction_quality_score(candidate),
                *self._rank(candidate),
            )
        )
        selected = candidates[:limit]
        logger.info(
            "wikimedia_discovery city=%s discovered=%d usable=%d selected=%d",
            city_name,
            len(pages),
            len(candidates),
            len(selected),
        )
        return selected
