from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class VerifyWebSmokeTests(unittest.TestCase):
    def test_verify_web_assets_are_present_and_wired(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="./styles.css">', html)
        self.assertIn('<script type="module" src="./app.js"></script>', html)
        self.assertIn('id="nothing-id"', html)
        self.assertIn('id="record"', html)

        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("/v1/identity/", js)
        self.assertIn("NTH-[0-9]{6}", js)
        self.assertIn("textContent", js)
        self.assertNotIn("innerHTML", js)

        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".lookup", css)
        self.assertIn(".card", css)
        self.assertIn("@media", css)

    def test_verify_web_javascript_has_valid_syntax_when_node_is_available(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available in this test environment")
        result = subprocess.run(
            [node, "--check", str(WEB / "app.js")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=result.stderr or result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
