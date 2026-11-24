"""Keep privacy warnings beside clone instructions and before profile generation."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
SETUP_GUIDE = REPO / "SETUP.md"
SETUP_COMMAND = REPO / ".claude" / "commands" / "setup.md"


def section(text: str, heading: str) -> str:
    """Body of a markdown section up to the next heading of the same level."""
    level = heading.split(" ")[0]
    pattern = re.compile(
        rf"^{re.escape(heading)}\n(.*?)(?=^{level} |\Z)", re.MULTILINE | re.DOTALL
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


class TestCloneWarningsAtTheDecisionPoint(unittest.TestCase):
    def assert_warns(self, body: str, where: str):
        self.assertRegex(
            body,
            re.compile(r"public", re.IGNORECASE),
            f"{where}'s clone section must warn about public remotes",
        )
        self.assertIn(
            "personal data",
            body,
            f"{where}'s clone section must say /setup writes personal data into tracked files",
        )
        self.assertRegex(
            body,
            re.compile(r"section 8|§8|#8-keeping", re.IGNORECASE),
            f"{where}'s clone section must point at SETUP.md section 8's private-remote recipe",
        )

    def test_readme_quick_start_warns_next_to_the_clone_command(self):
        body = section(README.read_text(encoding="utf-8"), "### 1. Clone Genpact-Job-Ai")
        self.assertIn("git clone --branch master https://github.com/darksarkar28-boop/Genpact-Job-Ai.git", body)
        self.assert_warns(body, "README")

    def test_setup_guide_warns_next_to_the_clone_command(self):
        body = section(SETUP_GUIDE.read_text(encoding="utf-8"), "## 2. Clone Genpact-Job-Ai")
        self.assertIn("git clone --branch master https://github.com/darksarkar28-boop/Genpact-Job-Ai.git", body)
        self.assert_warns(body, "SETUP.md")


class TestSetupChecksOriginBeforeWriting(unittest.TestCase):
    def test_preflight_exists_and_precedes_profile_generation(self):
        text = SETUP_COMMAND.read_text(encoding="utf-8")
        self.assertIn(
            "git remote get-url origin",
            text,
            "/setup must check where the working copy would publish to",
        )
        preflight_at = text.index("git remote get-url origin")
        writes_at = text.index("## Step 3: Generate Profile Files")
        self.assertLess(
            preflight_at,
            writes_at,
            "the origin check must run before any profile file is written - the "
            "existing Step 4 note fires after everything is already on disk",
        )
        self.assertIn(
            "if the origin is public",
            text[preflight_at:writes_at].lower(),
            "the preflight must be about public visibility, not just remote presence",
        )


if __name__ == "__main__":
    unittest.main()
