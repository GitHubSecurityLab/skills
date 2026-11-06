"""Tests for skills/fuzzing/fuzz-campaign/scripts/init.py.

Each test runs the script as the skills do (a subprocess, from inside a
fresh git repository) and checks the workspace it leaves behind.
"""

import datetime
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "scripts")
INIT = os.path.join(SCRIPTS, "init.py")
TODAY = datetime.date.today().isoformat()


class InitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        with open(os.path.join(self.repo, "a.c"), "w") as handle:
            handle.write("int a;\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git"] + list(args), cwd=cwd or self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def init(self, *args, cwd=None, env=None):
        environ = dict(os.environ)
        environ.pop("CODESPACES", None)
        environ.update(env or {})
        return subprocess.run(
            [sys.executable, INIT] + list(args), cwd=cwd or self.repo, capture_output=True, text=True, env=environ
        )

    def path(self, *parts):
        return os.path.join(self.repo, ".seclab", "fuzz", *parts)

    def ledger(self):
        with open(self.path("CAMPAIGN.md")) as handle:
            return handle.read()

    def test_scripts_compile_without_warnings(self):
        # Python 3.12 prints a SyntaxWarning for an invalid escape such as "\`" on every run.
        for name in sorted(os.listdir(SCRIPTS)):
            if name.endswith(".py"):
                with open(os.path.join(SCRIPTS, name), encoding="utf-8") as handle, warnings.catch_warnings():
                    warnings.simplefilter("error")
                    compile(handle.read(), name, "exec")

    def test_windows_and_old_python_are_refused(self):
        spec = importlib.util.spec_from_file_location("init_script", INIT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("WSL2, a VM or a codespace", module.platform_error("win32", (3, 12, 0)))
        self.assertIn("Python 3.9 or later, not 3.8", module.platform_error("linux", (3, 8, 10)))
        self.assertEqual(module.platform_error("darwin", (3, 9, 6)), "")
        self.assertEqual(module.platform_error("linux", (3, 13, 1)), "")

    def test_outside_git_repository(self):
        outside = os.path.join(self.tmp.name, "plain")
        os.makedirs(outside)
        result = self.init(cwd=outside)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not inside a git repository", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(outside, ".seclab")))

    def test_without_ledger_sets_up_and_asks_for_the_branch(self):
        result = self.init()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for name in ("launcher.py", "coverage.py", "crash_buckets.py", "reach.py"):
            self.assertTrue(os.path.exists(self.path("bin", name)), name)
        self.assertTrue(os.path.isdir(self.path("build")))
        # The agent's file tools may not create parent directories.
        for sub in ("targets", "sinks", "corpus", "dict", "coverage", "crashes", "reports"):
            self.assertTrue(os.path.isdir(self.path(sub)), sub)
        with open(os.path.join(self.repo, ".git", "info", "exclude")) as handle:
            excludes = handle.read().splitlines()
        self.assertIn(".seclab/", excludes)
        self.assertIn(".cifuzz-corpus/", excludes)
        self.assertIn("--branch new or --branch current", result.stdout)
        self.assertIn("base: %s (main)" % self.git("rev-parse", "HEAD"), result.stdout)
        self.assertIn("FIRST-BUILD NOTICE", result.stdout)
        self.assertIn("SAFETY RULES", result.stdout)
        self.assertIn("Never push", result.stdout)
        self.assertFalse(os.path.exists(self.path("CAMPAIGN.md")))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_branch_new_writes_the_ledger(self):
        result = self.init("--branch", "new")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "seclab/fuzz-" + TODAY)
        ledger = self.ledger()
        self.assertIn("- **Status:** standalone", ledger)
        self.assertIn("- **Branch:** seclab/fuzz-" + TODAY, ledger)
        self.assertIn("(main), chosen " + TODAY, ledger)
        self.assertIn("- **First-build notice:** not yet shown", ledger)
        self.assertIn("- **Disposable environment:** no", ledger)
        self.assertIn("| memory_mb | 2048 |", ledger)
        self.assertIn("| coverage_goal | none |", ledger)
        self.assertNotIn("png", ledger)  # the template's example rows
        self.assertNotIn("<", ledger)  # no placeholder left
        # A session that opens the ledger without the skills loaded learns how to resume.
        self.assertIn("Resume: invoke `fuzz-campaign`", ledger.split("## Settings")[0])
        self.assertTrue(os.path.exists(self.path("decisions.md")))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_the_plugin_is_found_by_its_manifest(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as handle:
            version = json.load(handle)["version"]
        self.assertEqual(self.init("--branch", "current").returncode, 0)
        self.assertIn("- **Plugin:** seclab-skills %s;" % version, self.ledger())
        self.assertIn("not from the plugin", self.init(cwd=ROOT).stderr)
        # A copy of the skills in the repository (gh skill install: <repo>/.agents/skills/) isn't the plugin:
        # three levels up from it is the repository, which init.py must not take for the plugin's checkout.
        os.remove(self.path("CAMPAIGN.md"))
        copy = os.path.join(self.repo, ".agents", "skills", "fuzz-campaign")
        shutil.copytree(os.path.dirname(SCRIPTS), copy, ignore=shutil.ignore_patterns("__pycache__"))
        result = subprocess.run(
            [sys.executable, os.path.join(copy, "scripts", "init.py"), "--branch", "current"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- **Plugin:** seclab-skills unknown;", self.ledger())

    def test_branch_current_and_an_existing_branch_name(self):
        self.assertEqual(self.init("--branch", "current").returncode, 0)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertIn("- **Branch:** main", self.ledger())
        os.remove(self.path("CAMPAIGN.md"))
        self.git("branch", "seclab/fuzz-" + TODAY)
        self.assertEqual(self.init("--branch", "new").returncode, 0)
        self.assertEqual(self.git("branch", "--show-current"), "seclab/fuzz-%s-2" % TODAY)

    def test_base_commit(self):
        first = self.git("rev-parse", "HEAD")
        self.git("tag", "v1.0")
        with open(os.path.join(self.repo, "b.c"), "w") as handle:
            handle.write("int b;\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "second")
        self.git("switch", "-q", "-c", "other", "v1.0")
        with open(os.path.join(self.repo, "c.c"), "w") as handle:
            handle.write("int c;\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "other")
        bad = self.init("--branch", "current", "--base", "main")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("isn't an ancestor", bad.stderr)
        self.assertFalse(os.path.exists(self.path("CAMPAIGN.md")))
        # A branch rebased onto a release keeps the release as its base.
        result = self.init("--branch", "current", "--base", "v1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "other")
        self.assertIn("- **Base commit:** %s (v1.0)" % first, self.ledger())
        os.remove(self.path("CAMPAIGN.md"))
        self.git("switch", "-q", "main")
        result = self.init("--branch", "new", "--base", "v1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), first)
        self.assertIn("- **Base commit:** %s (v1.0)" % first, self.ledger())

    def test_a_new_branch_starts_at_head(self):
        # A clone checked out on a feature branch, one commit behind the remote's default branch.
        clone = os.path.join(self.tmp.name, "clone")
        self.git("clone", "-q", self.repo, clone, cwd=self.tmp.name)
        self.git("switch", "-q", "-c", "feature", cwd=clone)
        with open(os.path.join(clone, "f.c"), "w") as handle:
            handle.write("int f;\n")
        self.git("add", "-A", cwd=clone)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "feature", cwd=clone)
        feature = self.git("rev-parse", "HEAD", cwd=clone)
        with open(os.path.join(self.repo, "b.c"), "w") as handle:
            handle.write("int b;\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "upstream")
        self.git("fetch", "-q", cwd=clone)
        result = self.init("--branch", "new", cwd=clone)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=clone), feature)
        self.assertIn("base: %s (feature), 1 commit behind origin/main" % feature, result.stdout)
        with open(os.path.join(clone, ".seclab", "fuzz", "CAMPAIGN.md")) as handle:
            self.assertIn("- **Base commit:** %s (feature)" % feature, handle.read())

    def test_a_forks_clone_counts_behind_the_project(self):
        # origin is the user's fork, which doesn't follow the project; upstream is the project.
        fork, clone = os.path.join(self.tmp.name, "fork"), os.path.join(self.tmp.name, "clone")
        self.git("clone", "-q", self.repo, fork, cwd=self.tmp.name)
        self.git("clone", "-q", fork, clone, cwd=self.tmp.name)
        with open(os.path.join(self.repo, "b.c"), "w") as handle:
            handle.write("int b;\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "upstream")
        self.git("remote", "add", "upstream", self.repo, cwd=clone)
        self.git("fetch", "-q", "upstream", cwd=clone)
        self.git("remote", "set-head", "upstream", "-a", cwd=clone)
        result = self.init(cwd=clone)
        self.assertIn("1 commit behind upstream/main", result.stdout)

    def test_a_forks_clone_without_upstreams_head(self):
        # Older git records no upstream/HEAD on fetch (only `remote set-head` does): upstream's branch that
        # origin/HEAD names.
        fork, clone = os.path.join(self.tmp.name, "fork"), os.path.join(self.tmp.name, "clone")
        self.git("clone", "-q", self.repo, fork, cwd=self.tmp.name)
        self.git("clone", "-q", fork, clone, cwd=self.tmp.name)
        with open(os.path.join(self.repo, "b.c"), "w") as handle:
            handle.write("int b;\n")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "upstream")
        self.git("remote", "add", "upstream", self.repo, cwd=clone)
        self.git("fetch", "-q", "upstream", cwd=clone)
        self.git("update-ref", "--no-deref", "-d", "refs/remotes/upstream/HEAD", cwd=clone)
        result = self.init(cwd=clone)
        self.assertIn("1 commit behind upstream/main", result.stdout)

    def test_the_ledger_branch_and_base_match_the_checkout(self):
        self.assertEqual(self.init("--branch", "current").returncode, 0)
        self.git("switch", "-q", "-c", "other")
        result = self.init()
        self.assertEqual(result.returncode, 1)
        self.assertIn("the checkout is on other", result.stderr)
        self.assertIn("ask the user", result.stderr)
        self.git("switch", "-q", "main")
        self.assertEqual(self.init().returncode, 0)
        # History rewritten under the campaign: its base is gone.
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--amend", "-m", "rewritten")
        result = self.init()
        self.assertEqual(result.returncode, 1)
        self.assertIn("isn't in the checkout's history", result.stderr)

    def test_an_existing_ledger_keeps_its_branch_and_base(self):
        self.assertEqual(self.init("--branch", "current").returncode, 0)
        before = self.ledger()
        result = self.init("--branch", "new", "--base", "main", "--notice-shown")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already records", result.stderr)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.init("--notice-shown").returncode, 0)

    def test_dirty_tree_changes_nothing(self):
        with open(os.path.join(self.repo, "a.c"), "a") as handle:
            handle.write("int b;\n")
        result = self.init("--branch", "new")
        self.assertEqual(result.returncode, 1)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertFalse(os.path.exists(self.path("CAMPAIGN.md")))

    def test_a_dirty_tree_stays_on_the_current_branch_and_untracked_files_dont_count(self):
        with open(os.path.join(self.repo, "crash.bin"), "w") as handle:
            handle.write("x")
        self.assertEqual(self.init("--branch", "new").returncode, 0)
        os.remove(self.path("CAMPAIGN.md"))
        self.git("switch", "-q", "main")
        with open(os.path.join(self.repo, "a.c"), "a") as handle:
            handle.write("int b;\n")
        result = self.init("--branch", "current")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- **Branch:** main", self.ledger())

    def test_a_standalone_ledger_moves_to_a_new_branch_before_its_first_commit(self):
        self.assertEqual(self.init("--branch", "current").returncode, 0)
        with open(os.path.join(self.repo, "fuzz.c"), "w") as handle:
            handle.write("int fuzz;\n")  # a harness written, not committed yet
        result = self.init("--branch", "new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "seclab/fuzz-" + TODAY)
        self.assertIn("- **Branch:** seclab/fuzz-" + TODAY, self.ledger())
        self.assertTrue(os.path.exists(os.path.join(self.repo, "fuzz.c")))
        self.git("add", "fuzz.c")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "harness")
        result = self.init("--branch", "new")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already records", result.stderr)

    def test_a_standalone_ledger_moves_to_a_new_branch_only_once(self):
        # A second --branch new before the first commit would leave the first new branch behind, orphaned.
        self.assertEqual(self.init("--branch", "current").returncode, 0)
        self.assertEqual(self.init("--branch", "new").returncode, 0)
        result = self.init("--branch", "new")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("only once", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "seclab/fuzz-" + TODAY)
        self.assertEqual(self.git("branch", "--list", "seclab/*"), "* seclab/fuzz-" + TODAY)
        self.assertEqual(self.init().returncode, 0)

    def test_the_missing_ledger_text_doesnt_ask_up_front(self):
        result = self.init()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Ask the user", result.stdout)
        self.assertIn("single-step request uses --branch current", result.stdout)

    def test_usage_errors_exit_1(self):
        # Exit 2 means "no ledger yet" to every skill's Start; argparse's own 2 would read as that.
        for args, message in ((["--base", "HEAD"], "--base needs --branch"), (["--branch", "bogus"], "invalid choice"),
                              (["--unknown"], "unrecognized arguments")):
            result = self.init(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(message, result.stderr)
        self.assertFalse(os.path.exists(self.path()))

    def test_scripts_are_refreshed_when_the_plugin_copy_differs(self):
        self.init("--branch", "current")
        with open(self.path("bin", "coverage.py"), "a") as handle:
            handle.write("# stale copy\n")
        result = self.init()
        self.assertIn("copied coverage.py", result.stdout)
        with open(self.path("bin", "coverage.py")) as copy, open(os.path.join(SCRIPTS, "coverage.py")) as source:
            self.assertEqual(copy.read(), source.read())
        self.assertIn("scripts: up to date", self.init().stdout)

    def test_notice_until_recorded(self):
        self.init("--branch", "current")
        self.assertIn("FIRST-BUILD NOTICE", self.init().stdout)
        result = self.init("--notice-shown")
        self.assertNotIn("FIRST-BUILD NOTICE", result.stdout)
        self.assertIn("- **First-build notice:** shown " + TODAY, self.ledger())
        self.assertNotIn("FIRST-BUILD NOTICE", self.init().stdout)

    def test_a_tracked_seclab_directory_is_refused(self):
        # A repository can commit a ledger that marks the notice shown and the environment disposable.
        os.makedirs(self.path())
        with open(self.path("CAMPAIGN.md"), "w") as handle:
            handle.write("- **First-build notice:** shown 2020-01-01\n- **Disposable environment:** yes (declared)\n")
        self.git("add", "-f", ".seclab")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "planted")
        result = self.init("--branch", "current")
        self.assertEqual(result.returncode, 1)
        self.assertIn("git tracks .seclab/fuzz/CAMPAIGN.md", result.stderr)
        self.assertFalse(os.path.exists(self.path("bin")))
        exclude = os.path.join(self.repo, ".git", "info", "exclude")
        if os.path.exists(exclude):
            with open(exclude) as handle:
                self.assertNotIn(".seclab/", handle.read())

    def test_a_tracked_seclab_directory_in_another_case_is_refused(self):
        # macOS's default file system ignores case: there, a tracked .SECLAB/fuzz/env.sh is the env.sh every
        # command sources.
        self.git("config", "core.ignorecase", "true")
        for name in (".SECLAB", ".Seclab"):
            os.makedirs(os.path.join(self.repo, name, "fuzz"))
            with open(os.path.join(self.repo, name, "fuzz", "env.sh"), "w") as handle:
                handle.write("echo planted\n")
            self.git("add", "-f", name)
            self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "planted")
            result = self.init("--branch", "current")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("git tracks %s/fuzz/env.sh" % name, result.stderr)
            self.assertFalse(os.path.exists(self.path("bin")))
            self.git("rm", "-rq", name)
            self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "removed")

    def test_a_symlinked_workspace_is_refused(self):
        # A workspace that points elsewhere, into the repository's files say, isn't the user's.
        os.makedirs(os.path.join(self.repo, "data", "fuzz"))
        with open(os.path.join(self.repo, "data", "fuzz", "env.sh"), "w") as handle:
            handle.write("echo planted\n")
        for link, target in ((".seclab", "data"), (os.path.join(".seclab", "fuzz"), os.path.join("..", "data", "fuzz")),
                             (os.path.join(".seclab", "fuzz", "env.sh"), os.path.join("..", "..", "data", "fuzz", "env.sh"))):
            shutil.rmtree(os.path.join(self.repo, ".seclab"), ignore_errors=True)
            if os.path.islink(os.path.join(self.repo, ".seclab")):
                os.remove(os.path.join(self.repo, ".seclab"))
            os.makedirs(os.path.dirname(os.path.join(self.repo, link)), exist_ok=True)
            os.symlink(target, os.path.join(self.repo, link))
            result = self.init("--branch", "current")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("%s is a symbolic link" % link, result.stderr)
            self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "fuzz", "bin")))
            with open(os.path.join(self.repo, "data", "fuzz", "env.sh")) as handle:
                self.assertEqual(handle.read(), "echo planted\n")

    def test_notice_and_disposable_state_live_in_the_git_directory(self):
        # An edited or copied ledger can't mark the notice shown or the environment disposable.
        self.init("--branch", "current")
        ledger = self.ledger().replace("not yet shown", "shown 2020-01-01")
        with open(self.path("CAMPAIGN.md"), "w") as handle:
            handle.write(ledger.replace("Disposable environment:** no", "Disposable environment:** yes (declared)"))
        result = self.init()
        self.assertIn("FIRST-BUILD NOTICE", result.stdout)
        self.assertIn("- **First-build notice:** not yet shown", self.ledger())
        self.assertIn("- **Disposable environment:** no", self.ledger())
        self.init("--notice-shown", "--disposable")
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "seclab", "notice-shown")))
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "seclab", "disposable")))
        self.assertIn("- **First-build notice:** shown " + TODAY, self.ledger())
        self.assertIn("- **Disposable environment:** yes (declared)", self.ledger())
        # A fresh clone with a copied .seclab/ starts without them.
        clone = os.path.join(self.tmp.name, "clone")
        self.git("clone", "-q", self.repo, clone, cwd=self.tmp.name)
        shutil.copytree(os.path.join(self.repo, ".seclab"), os.path.join(clone, ".seclab"))
        result = self.init(cwd=clone)
        self.assertIn("FIRST-BUILD NOTICE", result.stdout)
        with open(os.path.join(clone, ".seclab", "fuzz", "CAMPAIGN.md")) as handle:
            self.assertIn("- **Disposable environment:** no", handle.read())

    def test_the_notice_is_recorded_before_the_ledger(self):
        self.assertEqual(self.init("--notice-shown").returncode, 2)
        result = self.init("--branch", "current")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("FIRST-BUILD NOTICE", result.stdout)
        self.assertIn("- **First-build notice:** shown " + TODAY, self.ledger())

    def test_env_file_for_every_command(self):
        # Shell variables don't persist between an agent's commands: each sources this file.
        self.init()
        env = self.path("env.sh")
        with open(env) as handle:
            self.assertIn('F=%s\n' % os.path.realpath(self.path()), handle.read())
        with open(env, "a") as handle:
            handle.write("RUN_PATH=/x:/usr/bin:/bin\n")
        moved = os.path.join(self.tmp.name, "moved")
        os.rename(self.repo, moved)
        self.repo = moved
        self.init()
        out = subprocess.run(["bash", "-c", '. .seclab/fuzz/env.sh; echo "$F|$L|$RUN_PATH"'], cwd=moved,
                             capture_output=True, text=True).stdout.strip()
        f = os.path.realpath(self.path())
        self.assertEqual(out, "%s|python3 %s/bin/launcher.py|/x:/usr/bin:/bin" % (f, f))

    def run_in(self, shell, command, prelude=""):
        # As Claude Code does: the user's aliases first, then the command through eval.
        script = prelude + "eval " + shlex.quote(". .seclab/fuzz/env.sh; " + command)
        return subprocess.run([shell, "-c", script], cwd=self.repo, capture_output=True, text=True)

    def test_commands_split_words_in_bash_and_zsh(self):
        # zsh (Claude Code's and Codex's shell on macOS) doesn't split unquoted variables into words
        # by default, and applies the user's aliases (Oh My Zsh's common-aliases: alias -g L='| less').
        self.assertEqual(self.init("--branch", "current", "--notice-shown").returncode, 0)
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                if shutil.which(shell) is None:
                    self.skipTest("%s isn't installed" % shell)
                prelude = "alias -g L='| less'; " if shell == "zsh" else ""
                result = self.run_in(shell, 'JOPTS="-Xa -Xb"; printf "<%s>" $JOPTS', prelude)
                self.assertEqual(result.stdout, "<-Xa><-Xb>", result.stderr)
                run_id = "args-" + shell
                result = self.run_in(
                    shell, "$L start --id %s --harness h --timeout 30 -- /bin/echo 'a  b' c" % run_id, prelude)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                with open(self.path("runs", run_id, "copy-0", "log")) as handle:
                    self.assertIn("a  b c\n", handle.read())
                result = self.run_in(shell, "$L status " + run_id, prelude)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("finished", result.stdout)

    def test_env_file_gets_the_zsh_line_and_keeps_its_block(self):
        self.init()
        with open(self.path("env.sh"), "w") as handle:
            handle.write('F=/old\nL="python3 $F/bin/launcher.py"\nRUN_PATH=/x:/usr/bin:/bin\n')
        self.init()
        with open(self.path("env.sh")) as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines, ['[ -z "${ZSH_VERSION-}" ] || setopt sh_word_split', "F=" + os.path.realpath(self.path()),
                                 'L="python3 $F/bin/launcher.py"', "RUN_PATH=/x:/usr/bin:/bin"])
        self.init()
        with open(self.path("env.sh")) as handle:
            self.assertEqual(handle.read().splitlines(), lines)

    def test_env_file_without_f_and_l_gets_them_in_order(self):
        # L uses F, so F comes first.
        self.init()
        with open(self.path("env.sh"), "w") as handle:
            handle.write("RUN_PATH=/x:/usr/bin:/bin\n")
        self.init()
        with open(self.path("env.sh")) as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines, ['[ -z "${ZSH_VERSION-}" ] || setopt sh_word_split', "F=" + os.path.realpath(self.path()),
                                 'L="python3 $F/bin/launcher.py"', "RUN_PATH=/x:/usr/bin:/bin"])

    def test_excludes_are_added_once(self):
        self.init()
        self.init()
        with open(os.path.join(self.repo, ".git", "info", "exclude")) as handle:
            self.assertEqual(handle.read().splitlines().count(".seclab/"), 1)

    @unittest.skipIf(os.geteuid() == 0, "root can write to a read-only file")
    def test_a_read_only_git_directory_is_a_clear_error(self):
        # Codex's default sandbox makes .git read-only.
        info = os.path.join(self.repo, ".git", "info")
        os.makedirs(info, exist_ok=True)
        exclude = os.path.join(info, "exclude")
        open(exclude, "a").close()
        os.chmod(exclude, 0o444)
        os.chmod(info, 0o555)
        try:
            result = self.init("--branch", "current")
        finally:
            os.chmod(info, 0o755)
            os.chmod(exclude, 0o644)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(os.path.join(".git", "info", "exclude"), result.stderr)
        self.assertIn("approve", result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root can write to a read-only directory")
    def test_a_read_only_git_directory_is_a_clear_error_for_the_new_branch_too(self):
        # A second run: the excludes are in place, so only git switch writes to .git.
        self.init()
        heads = os.path.join(self.repo, ".git", "refs", "heads")
        os.chmod(heads, 0o555)
        try:
            result = self.init("--branch", "new")
        finally:
            os.chmod(heads, 0o755)
        self.assertEqual(result.returncode, 1)
        self.assertIn("read-only", result.stderr)
        self.assertIn("approve", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_disposable_environment(self):
        self.init("--branch", "current", env={"CODESPACES": "true"})
        self.assertIn("- **Disposable environment:** yes (detected: CODESPACES)", self.ledger())
        os.remove(self.path("CAMPAIGN.md"))
        self.init("--branch", "current", "--disposable")
        self.assertIn("- **Disposable environment:** yes (declared)", self.ledger())

    def test_runs_from_a_subdirectory_and_warns_about_unsafe_paths(self):
        unsafe = os.path.join(self.tmp.name, "my repo")
        os.rename(self.repo, unsafe)
        self.repo = unsafe
        os.makedirs(os.path.join(unsafe, "sub"))
        result = self.init("--branch", "current", cwd=os.path.join(unsafe, "sub"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: the repository path contains ' '", result.stdout)
        self.assertTrue(os.path.exists(self.path("CAMPAIGN.md")))


if __name__ == "__main__":
    unittest.main()
