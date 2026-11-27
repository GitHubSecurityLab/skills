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


class ForeignWorkspaceTest(unittest.TestCase):
    def test_tracked_files_and_links_are_reported(self):
        with open(SKILL, encoding="utf-8") as handle:
            command = re.search(r"## Reads\n.*?```sh\n(.*?)```", handle.read(), re.S).group(1)
        # What each layout creates: files, tracked files and links (relative to the repository).
        layouts = {
            "none": {}, "the user's": {"files": [".seclab/threat-model.md"]},
            "tracked model": {"tracked": [".seclab/threat-model.md"]},
            "tracked in another case": {"tracked": [".SECLAB/fuzz/env.sh"]},
            "linked directory": {"links": [(".seclab", "data")]},
            "linked model": {"links": [(".seclab/threat-model.md", "../data/model.md")]},
            "linked .gitignore": {"links": [(".seclab/.gitignore", "../data/model.md")]},
        }
        for name, layout in layouts.items():
            with self.subTest(layout=name), tempfile.TemporaryDirectory() as repo:
                def git(*args):
                    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args),
                                          cwd=repo, check=True, capture_output=True, text=True)
                git("init", "-q")
                os.makedirs(os.path.join(repo, "data"))
                for path in layout.get("files", []) + layout.get("tracked", []):
                    os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
                    with open(os.path.join(repo, path), "w", encoding="utf-8") as handle:
                        handle.write("planted\n")
                for path, target in layout.get("links", []):
                    os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
                    os.symlink(target, os.path.join(repo, path))
                for path in layout.get("tracked", []):
                    git("add", "-f", path)
                result = subprocess.run(["sh", "-c", command], cwd=repo, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout != "", name not in ("none", "the user's"), result.stdout)


class IgnoreTest(unittest.TestCase):
    def test_seclab_stays_out_of_git_whatever_the_repository_ignores(self):
        with open(SKILL, encoding="utf-8") as handle:
            prose = re.sub(r"```.*?```", "", handle.read(), flags=re.S)
        spans = re.findall(r"`([^`]+)`", prose.replace("\n  ", " "))
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
