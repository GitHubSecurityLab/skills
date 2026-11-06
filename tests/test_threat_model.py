"""Commands in security-threat-model's SKILL.md, run as written."""

import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "shared", "security-threat-model", "SKILL.md")


class HistoryGrepTest(unittest.TestCase):
    def test_security_fixes_found(self):
        with open(SKILL, encoding="utf-8") as handle:
            pattern = re.search(r"git log --oneline -i -E --grep='([^']+)'", handle.read()).group(1)
        for subject in ("Fix double free in yaml_parser_delete", "fix memory leak on error path",
                        "Fix heap-use-after-free found by OSS-Fuzz", "Fix CVE-2024-1234", "Fix out of bounds read"):
            with self.subTest(subject=subject):
                self.assertRegex(subject, re.compile(pattern, re.I))


class ExcludeTest(unittest.TestCase):
    def test_exclude_added_once_before_seclab_exists(self):
        with open(SKILL, encoding="utf-8") as handle:
            spans = re.findall(r"`([^`]+)`", handle.read().replace("\n  ", " "))
        check = next(s for s in spans if "check-ignore" in s)
        add = next(s for s in spans if "info/exclude" in s)
        command = check if check == add else "%s || %s" % (check, add)
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            for _ in range(2):
                subprocess.run(["sh", "-c", command], cwd=repo, check=True)
            exclude = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=repo, check=True,
                                     capture_output=True, text=True).stdout.strip()
            with open(os.path.join(repo, exclude), encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line == ".seclab/"]
        self.assertEqual(len(lines), 1, command)


if __name__ == "__main__":
    unittest.main()
