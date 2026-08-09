import unittest
from unittest.mock import Mock

import requests

from pubmed_mining import PubMedClient, PubMedQueryError, PubMedRequestError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class PubMedClientTests(unittest.TestCase):
    def test_search_returns_records_and_bibliometric_summary(self):
        session = Mock()
        session.get.side_effect = [
            FakeResponse({"esearchresult": {"count": "42", "idlist": ["1", "2"]}}),
            FakeResponse(
                {
                    "result": {
                        "uids": ["1", "2"],
                        "1": {
                            "uid": "1",
                            "title": "Herbal medicine trial",
                            "fulljournalname": "Journal of TCM",
                            "pubdate": "2024 Jan 10",
                            "authors": [{"name": "Li Wei"}],
                            "pubtype": ["Clinical Trial"],
                            "articleids": [{"idtype": "doi", "value": "10.1/example"}],
                        },
                        "2": {
                            "uid": "2",
                            "title": "Acupuncture review",
                            "fulljournalname": "Journal of TCM",
                            "pubdate": "2023 Dec",
                            "authors": [{"name": "Zhang Min"}],
                            "pubtype": ["Review"],
                            "articleids": [],
                        },
                    }
                }
            ),
        ]
        client = PubMedClient(
            session=session,
            email="maintainer@example.org",
            api_key="test-api-key",
        )

        result = client.search("acupuncture", max_results=2)

        self.assertEqual(result["query"], "acupuncture")
        self.assertEqual(result["total_results"], 42)
        self.assertEqual(result["records"][0]["doi"], "10.1/example")
        self.assertEqual(result["analytics"]["publication_years"], {"2024": 1, "2023": 1})
        self.assertEqual(result["analytics"]["top_journals"], [{"name": "Journal of TCM", "count": 2}])
        self.assertEqual(session.get.call_count, 2)
        search_params = session.get.call_args_list[0].kwargs["params"]
        self.assertEqual(search_params["tool"], "OpenTCM")
        self.assertEqual(search_params["email"], "maintainer@example.org")
        self.assertEqual(search_params["api_key"], "test-api-key")

    def test_search_rejects_an_empty_query(self):
        with self.assertRaises(PubMedQueryError):
            PubMedClient(session=Mock()).search("   ")

    def test_search_wraps_network_errors(self):
        session = Mock()
        session.get.side_effect = requests.Timeout()

        with self.assertRaises(PubMedRequestError):
            PubMedClient(session=session).search("ginseng")

    def test_search_rejects_ncbi_error_payloads(self):
        session = Mock()
        session.get.return_value = FakeResponse({"error": "API rate limit exceeded"})

        with self.assertRaises(PubMedRequestError):
            PubMedClient(session=session).search("ginseng")

    def test_search_rejects_excessively_long_queries(self):
        with self.assertRaises(PubMedQueryError):
            PubMedClient(session=Mock()).search("x" * 501)


if __name__ == "__main__":
    unittest.main()
