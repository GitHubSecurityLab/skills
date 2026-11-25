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


class IgnoreTest(unittest.TestCase):
    def test_seclab_stays_out_of_git_whatever_the_repository_ignores(self):
        with open(SKILL, encoding="utf-8") as handle:
            spans = re.findall(r"`([^`]+)`", handle.read().replace("\n  ", " "))
        command = next(s for s in spans if s.startswith("mkdir") and ".seclab/.gitignore" in s)
        # Rules that un-ignore .seclab/: by name, all dotfiles, and an allow-list of file types.
        for rules in (None, "!.seclab/\n", ".*\n!.*\n", "*\n!*/\n!*.md\n"):
            with self.subTest(rules=rules), tempfile.TemporaryDirectory() as repo:
                def git(*args):
                    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args),
                                          cwd=repo, check=True, capture_output=True, text=True)
                git("init", "-q")
                if rules:
                    with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as handle:
                        handle.write(rules)
                    git("add", "-f", ".gitignore")
                    git("commit", "-qm", "rules")
                for _ in range(2):
                    subprocess.run(["sh", "-c", command], cwd=repo, check=True)
                with open(os.path.join(repo, ".seclab", "threat-model.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Threat model\n")
                self.assertEqual(git("status", "--porcelain", "--untracked-files=all").stdout, "")


if __name__ == "__main__":
    unittest.main()
