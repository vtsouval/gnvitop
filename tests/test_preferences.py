import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gnvitop import preferences, server


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dashboard.json"
        self.env = patch.dict(os.environ, {"GNVITOP_PREFERENCES": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.directory.cleanup)
        self.data = {"groups": [{"id": "lab", "name": "Lab"}, {"id": "future", "name": "Future"}], "hosts": {"ssh-alias": {"name": "My server", "group": "lab"}}}
        self.client = server.app.test_client()

    def test_names_and_empty_groups_survive_reload(self):
        expected = preferences.save(self.data)
        self.assertEqual(preferences.load(), expected)
        self.assertEqual(expected["hosts"]["ssh-alias"]["name"], "My server")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_invalid_changes_preserve_previous_file(self):
        preferences.save(self.data)
        original = self.path.read_bytes()
        self.data["hosts"]["ssh-alias"]["group"] = "missing"
        with self.assertRaises(ValueError):
            preferences.save(self.data)
        self.assertEqual(self.path.read_bytes(), original)

    def test_duplicate_groups_and_invalid_names_rejected(self):
        for data in [{"groups": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}, {"groups": [{"id": "a", "name": " "}]}, {"hosts": {"alias": {"name": "bad\nname"}}}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                preferences.save(data)

    def test_mutations_require_token_and_same_origin(self):
        self.assertEqual(self.client.post("/api/preferences", json=self.data).status_code, 403)
        headers = {"X-Gnvitop-Token": server.PREFERENCES_TOKEN, "Origin": "https://other.example"}
        self.assertEqual(self.client.post("/api/preferences", json=self.data, headers=headers).status_code, 403)
        headers["Origin"] = "http://localhost"
        self.assertEqual(self.client.post("/api/preferences", json=self.data, headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/preferences").json, preferences.load())
        self.assertEqual(self.client.post("/api/preferences", json=self.data, headers=headers, environ_base={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)

    def test_display_names_cannot_break_script_block(self):
        self.data["groups"][0]["name"] = "</script><script>alert(1)</script>"
        preferences.save(self.data)
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("\\u003c/script>", html)


if __name__ == "__main__":
    unittest.main()
