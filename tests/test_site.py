from pathlib import Path
import json
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class VerifySiteSmokeTests(unittest.TestCase):
    def test_site_assets_and_live_api_wiring(self):
        index = (SITE / "index.html").read_text(encoding="utf-8")
        verify = (SITE / "verify.html").read_text(encoding="utf-8")
        config = (SITE / "config.js").read_text(encoding="utf-8")
        app = (SITE / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (SITE / "assets" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("./verify.html", index)
        self.assertIn("./config.js", verify)
        self.assertIn("./assets/app.js", verify)
        self.assertIn("window.NOTHING_API_BASE", config)
        self.assertIn("/v1/identity/", app)
        self.assertIn("/verification-events", app)
        self.assertIn("NTH-[0-9]{6}", app)
        self.assertIn("escapeHtml", app)
        self.assertIn('cache: "no-store"', app)
        self.assertNotIn("Authorization", app)
        self.assertIn(".timeline", styles)

        bundle = json.loads((SITE / "data" / "demo-bundle.json").read_text(encoding="utf-8"))
        self.assertTrue(bundle["demo"])
        self.assertEqual(bundle["identities"][0]["nothing_id"], "NTH-000001")

    def test_site_javascript_has_valid_syntax_when_node_is_available(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this test environment")
        result = subprocess.run(
            [node, "--check", str(SITE / "assets" / "app.js")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
