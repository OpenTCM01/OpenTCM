import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalDeploymentTests(unittest.TestCase):
    def test_local_entrypoint_uses_loopback(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == "app"]
        self.assertEqual(len(calls), 1)
        keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
        self.assertEqual(ast.literal_eval(keywords["host"]), "127.0.0.1")
        self.assertFalse(ast.literal_eval(keywords["debug"]))

    def test_setup_docs_are_local_only(self):
        forbidden = re.compile(
            r"cloudflare|cloudflared|tunnel|credentials-file|your-domain|"
            r"waitress|gunicorn|nginx|reverse.proxy|public HTTPS domain", re.I,
        )
        for path in (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))):
            with self.subTest(document=path.name):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))
        guide = (ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8000/", guide)
        self.assertIn("python app.py", guide)


class PublicAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.saved_env = dict(os.environ)
        os.environ.update({
            "FLASK_SECRET_KEY": "test-session-secret-not-for-deployment",
            "OPENTCM_PASSWORD": "test-login-not-for-deployment",
            "OPENTCM_CONVERSATION_DB": str(Path(cls.directory.name) / "conversations.sqlite"),
            "OPENTCM_KG_DB": str(Path(cls.directory.name) / "missing.sqlite"),
            "OPENTCM_MODERN_DB": str(Path(cls.directory.name) / "missing-modern.sqlite"),
            "DEEPSEEK_API_KEY": "",
        })
        sys.path.insert(0, str(ROOT))
        import app

        cls.module = app
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls.saved_env)
        cls.directory.cleanup()

    def setUp(self):
        self.client.get("/logout")

    def login(self):
        return self.client.post("/login", data={"password": "test-login-not-for-deployment"})

    def test_password_gate_and_language_pages(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/conversations").status_code, 302)
        self.assertEqual(self.client.post("/login", data={"password": "wrong"}).status_code, 200)
        self.assertEqual(self.login().status_code, 302)
        for language in ("zh-Hans", "zh-Hant", "en"):
            self.assertEqual(self.client.get(f"/?lang={language}").status_code, 200)
            self.assertEqual(self.client.get(f"/chat_page?lang={language}").status_code, 200)
            with self.client.get(f"/guide/user?lang={language}") as response:
                self.assertEqual(response.status_code, 200)

    def test_conversations_create_restore_and_delete(self):
        self.login()
        created = self.client.post("/api/conversations", json={"lang": "en"}).get_json()
        identifier = created["conversation"]["id"]
        self.assertTrue(identifier)
        self.assertEqual(self.client.get(f"/api/conversations/{identifier}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/conversations/{identifier}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/conversations/{identifier}").status_code, 404)

    def test_unconfigured_password_fails_closed(self):
        environment = dict(os.environ)
        environment.pop("OPENTCM_PASSWORD", None)
        result = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT,
                                env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set your own OPENTCM_PASSWORD", result.stderr)


if __name__ == "__main__":
    unittest.main()
