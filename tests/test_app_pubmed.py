import unittest
from unittest.mock import patch

from app import app
from pubmed_mining import PubMedRequestError


class SuccessfulPubMedClient:
    def search(self, query, max_results):
        return {
            "query": query,
            "total_results": 1,
            "records": [],
            "analytics": {"returned_results": 0},
        }


class UnavailablePubMedClient:
    def search(self, query, max_results):
        raise PubMedRequestError("service unavailable")


class PubMedRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_route_returns_search_results(self):
        with patch("app.pubmed_client", SuccessfulPubMedClient()):
            response = self.client.get("/api/pubmed?query=ginseng&max_results=5")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["query"], "ginseng")

    def test_route_rejects_invalid_parameters(self):
        response = self.client.get("/api/pubmed?query=&max_results=invalid")

        self.assertEqual(response.status_code, 400)

    def test_route_maps_pubmed_failures_to_bad_gateway(self):
        with patch("app.pubmed_client", UnavailablePubMedClient()):
            response = self.client.get("/api/pubmed?query=ginseng")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {"error": "PubMed is temporarily unavailable."},
        )
