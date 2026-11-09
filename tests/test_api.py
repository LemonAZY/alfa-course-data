import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

API = "/api/v1/recommendations"


class APITest(unittest.TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"CATALOG_PATH": ""})
        env.start()
        self.addCleanup(env.stop)
        client = TestClient(app)
        self.client = client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)

    def test_cold_start(self):
        self.assertEqual(self.client.get("/health").json()["catalog"], "synthetic")
        self.assertTrue(self.client.get("/api/v1/onboarding").json()["optional"])
        result = self.client.post(API, json={}).json()
        self.assertEqual(result["mode"], "cold_start")
        self.assertEqual(result["recommendations"][0]["material"]["access"], "free")
        self.assertTrue(result["clarification"])

    def test_query_and_context(self):
        result = self.client.post(API, json={
            "task": "система продвижения", "stage": "growth", "level": "intermediate",
            "industry": "розница", "preferred_format": "course", "limit": 1,
        }).json()
        self.assertEqual(result["mode"], "personalized")
        first = result["recommendations"][0]
        self.assertEqual(first["material"]["id"], "m3")
        self.assertGreaterEqual(len(first["reasons"]), 3)
        self.assertTrue(first["next_step"])

    def test_history(self):
        result = self.client.post(API, json={
            "stage": "growth", "level": "intermediate",
            "history": [{"material_id": "t1", "event": "completed"}],
        }).json()
        ids = [row["material"]["id"] for row in result["recommendations"]]
        self.assertEqual(ids[0], "t2")
        self.assertNotIn("t1", ids)

    def test_unknown_query(self):
        result = self.client.post(API, json={"task": "квантовая телепортация"}).json()
        self.assertEqual(result["mode"], "fallback")
        self.assertTrue(result["clarification"])
        self.assertTrue(result["recommendations"])

    def test_validation(self):
        self.assertEqual(self.client.post(API, json={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.post(API, json={"email": "user@example.org"}).status_code, 422)
        self.assertEqual(self.client.post(API, json={"stage": "unsupported"}).status_code, 422)

    def test_baseline(self):
        result = self.client.post(API, json={"strategy": "baseline", "topic": "маркетинг"}).json()
        self.assertEqual(result["strategy"], "baseline")
        self.assertEqual(result["recommendations"][0]["reasons"], ["Совпадает с темой запроса."])


if __name__ == "__main__":
    unittest.main()
