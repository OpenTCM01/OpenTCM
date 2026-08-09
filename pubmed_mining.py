"""PubMed search and lightweight bibliometric analysis for OpenTCM."""

from collections import Counter
import os
import re
from typing import Any, Dict, List, Optional

import requests


EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_MAX_RESULTS = 20
MAX_RESULTS = 100
MAX_QUERY_LENGTH = 500
TOOL_NAME = "OpenTCM"


class PubMedError(Exception):
    """Base exception for PubMed mining failures."""


class PubMedQueryError(PubMedError):
    """Raised when a PubMed query is invalid."""


class PubMedRequestError(PubMedError):
    """Raised when PubMed cannot be reached or returns invalid data."""


class PubMedClient:
    """Fetch PubMed records and derive summary statistics from their metadata."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        email: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.email = email or os.getenv("PUBMED_EMAIL")
        self.api_key = api_key or os.getenv("NCBI_API_KEY")

    def search(self, query: str, max_results: int = DEFAULT_MAX_RESULTS) -> Dict[str, Any]:
        """Return PubMed records and publication-level aggregation for a query."""
        normalized_query = self._validate_query(query)
        validated_max_results = self._validate_max_results(max_results)
        search_payload = self._get_json(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": normalized_query,
                "retmax": validated_max_results,
                "retmode": "json",
                "sort": "relevance",
            },
        )
        search_result = search_payload.get("esearchresult", {})
        identifiers = search_result.get("idlist", [])
        total_results = self._as_int(search_result.get("count"))

        if not identifiers:
            return {
                "query": normalized_query,
                "total_results": total_results,
                "records": [],
                "analytics": self._build_analytics([]),
            }

        summary_payload = self._get_json(
            "esummary.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(identifiers),
                "retmode": "json",
            },
        )
        summary_result = summary_payload.get("result", {})
        records = [
            self._to_record(summary_result[identifier])
            for identifier in identifiers
            if identifier in summary_result
        ]

        return {
            "query": normalized_query,
            "total_results": total_results,
            "records": records,
            "analytics": self._build_analytics(records),
        }

    def _get_json(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        request_params = dict(params)
        request_params["tool"] = TOOL_NAME
        if self.email:
            request_params["email"] = self.email
        if self.api_key:
            request_params["api_key"] = self.api_key

        try:
            response = self.session.get(
                f"{EUTILS_BASE_URL}/{endpoint}",
                params=request_params,
                timeout=self.timeout,
                headers={"User-Agent": "OpenTCM-PubMed-Mining/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise PubMedRequestError("Unable to retrieve PubMed data.") from error

        if not isinstance(payload, dict):
            raise PubMedRequestError("PubMed returned an invalid response.")
        if payload.get("error"):
            raise PubMedRequestError(f"PubMed returned an error: {payload['error']}")
        return payload

    @staticmethod
    def _validate_query(query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise PubMedQueryError("A non-empty query is required.")
        normalized_query = query.strip()
        if len(normalized_query) > MAX_QUERY_LENGTH:
            raise PubMedQueryError(
                f"query must not exceed {MAX_QUERY_LENGTH} characters."
            )
        return normalized_query

    @staticmethod
    def _validate_max_results(max_results: int) -> int:
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise PubMedQueryError("max_results must be an integer.")
        if not 1 <= max_results <= MAX_RESULTS:
            raise PubMedQueryError(f"max_results must be between 1 and {MAX_RESULTS}.")
        return max_results

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_record(summary: Dict[str, Any]) -> Dict[str, Any]:
        authors = [
            author.get("name", "")
            for author in summary.get("authors", [])
            if author.get("name")
        ]
        article_ids = summary.get("articleids", [])
        doi = next(
            (
                article_id.get("value")
                for article_id in article_ids
                if article_id.get("idtype") == "doi"
            ),
            None,
        )
        published_at = summary.get("pubdate") or summary.get("epubdate") or ""
        pmid = str(summary.get("uid", ""))
        return {
            "pmid": pmid,
            "title": summary.get("title", ""),
            "journal": summary.get("fulljournalname") or summary.get("source", ""),
            "published_at": published_at,
            "authors": authors,
            "publication_types": summary.get("pubtype", []),
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        }

    @staticmethod
    def _build_analytics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        year_counts: Counter[str] = Counter()
        journal_counts: Counter[str] = Counter()
        publication_type_counts: Counter[str] = Counter()

        for record in records:
            year_match = re.search(r"\b(?:19|20)\d{2}\b", record["published_at"])
            if year_match:
                year_counts[year_match.group(0)] += 1
            if record["journal"]:
                journal_counts[record["journal"]] += 1
            publication_type_counts.update(record["publication_types"])

        return {
            "returned_results": len(records),
            "publication_years": dict(sorted(year_counts.items(), reverse=True)),
            "top_journals": PubMedClient._top_counts(journal_counts),
            "publication_types": PubMedClient._top_counts(publication_type_counts),
        }

    @staticmethod
    def _top_counts(counter: Counter[str]) -> List[Dict[str, Any]]:
        return [
            {"name": name, "count": count}
            for name, count in counter.most_common()
        ]
