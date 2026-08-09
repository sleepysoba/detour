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
)
_BIOGRAPHY_PATTERN = re.compile(
    r"\b(?:is|was) an? (?:american|canadian|british|australian|german|french|spanish) "
    r"(?:politician|athlete|player|actor|actress|writer|businessman|businesswoman|scientist)\b",
    re.IGNORECASE,
)


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

        search_limit = min(50, max(limit * 2, 30))
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
        location_terms = " ".join(part.strip() for part in city_name.split(",")[:2])
        supplemental_payload = self._get_json(
            {
                "action": "query",
                "list": "search",
                "srsearch": f"{location_terms} attractions",
                "srlimit": 20,
                "srnamespace": 0,
            },
            "Wikimedia city attraction search",
        )
        supplemental_query = supplemental_payload.get("query")
        supplemental = supplemental_query.get("search") if isinstance(supplemental_query, dict) else None
        if not isinstance(supplemental, list):
            raise WikimediaError("MALFORMED_RESPONSE", "Wikimedia city search returned invalid results.")
        for item in supplemental:
            if isinstance(item, dict) and item.get("pageid") is not None:
                discovery_by_id.setdefault(str(item["pageid"]), item)

        pages: list[dict] = []
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
            pages.extend(detail_pages)

        candidates: list[dict] = []
        nearby_ids = {
            str(item.get("pageid")) for item in nearby if isinstance(item, dict) and item.get("pageid")
        }
        for page in pages:
            if not isinstance(page, dict) or page.get("missing") is True:
                continue
            page_id = str(page.get("pageid") or "")
            discovery = discovery_by_id.get(page_id)
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

        candidates.sort(key=self._rank)
        selected = candidates[:limit]
        logger.info(
            "wikimedia_discovery city=%s nearby=%d usable=%d selected=%d",
            city_name,
            len(nearby),
            len(candidates),
            len(selected),
        )
        return selected
