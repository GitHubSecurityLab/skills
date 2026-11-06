"""Tests for .devcontainer/post-create.sh, run with stub copilot and sudo commands on PATH."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".devcontainer", "post-create.sh")
with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as _handle:
    SKILLS = [os.path.basename(path) for path in json.load(_handle)["skills"]]

# Fails `plugin install` when STUB_INSTALL_FAILS is set; `skill list --json` prints STUB_SKILLS.
COPILOT = """#!/bin/sh
case "$*" in
  "plugin install"*) [ -z "$STUB_INSTALL_FAILS" ] || { echo "install failed" >&2; exit 1; } ;;
  "skill list --json") printf '%s\\n' "$STUB_SKILLS" ;;
esac
"""


class PostCreateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        # /workspaces/targets can't be created outside a codespace: sudo succeeds without doing anything.
        self.stub("sudo", "#!/bin/sh\nexit 0\n")
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, name, text):
        path = os.path.join(self.bin, name)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o755)

    def post_create(self, **env):
        environ = {"PATH": self.bin + ":/usr/bin:/bin", "HOME": self.tmp.name}
        environ.update(env)
        return subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=environ)

    def test_ready_only_when_copilot_lists_every_skill(self):
        self.stub("copilot", COPILOT)
        ok = self.post_create(STUB_SKILLS=json.dumps([{"name": skill} for skill in SKILLS]))
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("Fuzzing codespace ready", ok.stdout)
        failed = self.post_create(STUB_SKILLS=json.dumps([{"name": "fuzz-campaign"}]), STUB_INSTALL_FAILS="1")
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("ready", failed.stdout)
        self.assertIn("SETUP INCOMPLETE", failed.stderr)
        self.assertIn("fuzz-triage", failed.stderr)
        self.assertNotIn(" fuzz-campaign", failed.stderr.split("SETUP INCOMPLETE")[1].splitlines()[0])

    def test_without_copilot_cli(self):
        result = self.post_create()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ready", result.stdout)
        self.assertIn("SETUP INCOMPLETE", result.stderr)


if __name__ == "__main__":
    unittest.main()
