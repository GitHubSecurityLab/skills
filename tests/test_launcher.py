"""Tests for skills/fuzzing/fuzz-campaign/scripts/launcher.py.

The commands are small shell and Python programs that stand in for fuzzers:
they sleep, write finding files, print status lines, or exit with a code.
"""

import contextlib
import errno
import importlib.util
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "scripts", "launcher.py")


def load_module():
    spec = importlib.util.spec_from_file_location("launcher", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lc = load_module()
lc.POLL_SECONDS = 0.05
lc.STOP_GRACE_SECONDS = 2.0
lc.DEADLINE_GRACE_SECONDS = 0.5


class LauncherTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # A space in the path checks quoting (JAVA_TOOL_OPTIONS, argv).
        self.repo = os.path.join(os.path.realpath(self.tmp.name), "my repo")
        self.ws = os.path.join(self.repo, ".seclab", "fuzz")
        os.makedirs(self.ws)

    def tearDown(self):
        for name in os.listdir(os.path.join(self.ws, "runs")) if os.path.isdir(os.path.join(self.ws, "runs")) else []:
            with contextlib.suppress(Exception):
                self.cli("stop", name)
        self.tmp.cleanup()

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lc.main(["--workspace", self.ws] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def cli(self, *argv, **kwargs):
        return subprocess.run(
            [sys.executable, SCRIPT, "--workspace", self.ws] + list(argv),
            capture_output=True,
            text=True,
            timeout=kwargs.pop("timeout", 60),
            **kwargs,
        )

    def meta(self, run_id):
        with open(os.path.join(self.ws, "runs", run_id, "meta.json")) as handle:
            return json.load(handle)

    def rdir(self, run_id):
        return os.path.join(self.ws, "runs", run_id)


class SelfEndingRunTests(LauncherTestCase):
    def test_exit_zero_is_finished_and_returns_the_exit_status(self):
        code, out, _ = self.run_main("start", "--id", "cov-h-1", "--harness", "h", "--timeout", "30", "--", "true")
        self.assertEqual(code, 0)
        self.assertIn("cov-h-1: finished, 0 findings", out)

    def test_non_zero_exit_is_reported_as_such(self):
        code, out, _ = self.run_main("start", "--id", "repro-h-1", "--harness", "h", "--timeout", "30", "--", "sh", "-c", "exit 77")
        self.assertEqual(code, 77)
        self.assertEqual(self.meta("repro-h-1")["copies"][0]["state"], "exited 77")

    def test_timeout_kills_the_command(self):
        start = time.monotonic()
        code, _, _ = self.run_main("start", "--id", "repro-h-2", "--harness", "h", "--timeout", "1", "--", "sleep", "30")
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(code, lc.EXIT_TIMED_OUT)
        self.assertEqual(self.meta("repro-h-2")["copies"][0]["state"], "timed out")

    def test_missing_program(self):
        code, _, _ = self.run_main("start", "--id", "repro-h-3", "--harness", "h", "--timeout", "5", "--", "/no/such/program")
        self.assertEqual(code, 127)
        self.assertTrue(self.meta("repro-h-3")["copies"][0]["state"].startswith("failed to start: "))

    def test_status_all_lists_a_reach_search_as_one_row(self):
        for step in range(1, 12):
            self.run_main("start", "--id", "reach-my-h-1-%d" % step, "--harness", "my-h", "--timeout", "30", "--",
                          "sh", "-c", "exit %d" % (step == 11))
        self.run_main("start", "--id", "cov-my-h-1", "--harness", "my-h", "--timeout", "30", "--", "true")
        _, out, _ = self.run_main("status", "--all")
        self.assertIn("| reach-my-h-1 (11 steps) | my-h | exited 1, 0 findings |", out)  # the last step's state
        self.assertNotIn("reach-my-h-1-", out)
        self.assertIn("| cov-my-h-1 | my-h | finished, 0 findings |", out)


class TimedRunTests(LauncherTestCase):
    def test_deadline_ends_the_run_as_finished(self):
        code, out, _ = self.run_main("start", "--id", "r1-h", "--harness", "h", "--time", "1", "--", "sleep", "30")
        self.assertEqual(code, 0)
        self.assertIn("r1-h: finished, 0 findings", out)

    def test_engine_limit_counts_as_finished(self):
        self.run_main("start", "--id", "r2-h", "--harness", "h", "--time", "1", "--", "sleep", "1")
        self.assertEqual(self.meta("r2-h")["copies"][0]["state"], "finished")

    def test_early_end_with_a_finding_is_found_crash(self):
        cmd = 'echo x > "{run}/artifacts/copy-{copy}/crash-1"; exit 1'
        _, out, _ = self.run_main("start", "--id", "r3-h", "--harness", "h", "--time", "60", "--", "sh", "-c", cmd)
        self.assertIn("r3-h: found-crash, 1 finding", out)
        finding = self.meta("r3-h")["copies"][0]["new_findings"][0]
        self.assertEqual(finding, {"path": "artifacts/copy-0/crash-1", "kind": "crash"})

    def test_early_end_without_a_finding_is_failed(self):
        _, out, _ = self.run_main("start", "--id", "r4-h", "--harness", "h", "--time", "60", "--", "sh", "-c", "exit 1")
        self.assertIn("r4-h: failed, 0 findings", out)

    def test_files_that_are_not_findings(self):
        cmd = (
            'd="{run}/artifacts/copy-{copy}"; echo x > "$d/README.txt"; echo x > "$d/slow-unit-1";'
            ' echo x > "$d/finding-timeout"; mkdir -p "{run}/out/s1/hangs"; echo x > "{run}/out/s1/hangs/id:1"'
        )
        self.run_main(
            "start", "--id", "r5-h", "--harness", "h", "--time", "60",
            "--findings", "{run}/artifacts/copy-{copy}", "--findings", "{run}/out/*/hangs",
            "--", "sh", "-c", cmd,
        )
        kinds = sorted(f["kind"] for f in self.meta("r5-h")["copies"][0]["new_findings"])
        self.assertEqual(kinds, ["hang", "timeout"])

    def test_a_command_that_cant_start_fails_the_run(self):
        code, out, _ = self.run_main("start", "--id", "r7-h", "--harness", "h", "--time", "60", "--", "/no/such/program")
        self.assertEqual(code, 127)
        state = self.meta("r7-h")["copies"][0]["state"]
        self.assertTrue(state.startswith("failed to start: "), state)
        self.assertIn("/no/such/program", state)
        self.assertIn("r7-h: failed to start: ", out)

    def test_an_empty_env_value_is_refused(self):
        # Agents' tool calls don't keep shell variables: "$RUN_PATH" may be empty.
        code, _, err = self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "5",
                                     "--env", "ASAN_OPTIONS=", "--", "true")
        self.assertEqual(code, lc.EXIT_USAGE)
        self.assertIn("--env ASAN_OPTIONS is empty", err)
        self.assertIn("env.sh", err)
        self.assertFalse(os.path.exists(self.rdir("r1-h")))

    def test_limits_and_copies_must_be_positive(self):
        for args, message in (
            (["--time", "0"], "omit --time"),
            (["--time", "-5"], "omit --time"),
            (["--time", "nan"], "--time"),
            (["--timeout", "0"], "--timeout"),
            (["--timeout", "-5"], "--timeout"),
            (["--copies", "0"], "--copies"),
        ):
            code, _, err = self.run_main("start", "--id", "r8-h", "--harness", "h", *args, "--", "true")
            self.assertEqual(code, lc.EXIT_USAGE, args)
            self.assertIn(message, err)
            self.assertFalse(os.path.exists(os.path.join(self.rdir("r8-h"), "meta.json")), args)

    def test_old_files_are_not_new_findings(self):
        old = os.path.join(self.repo, "arts")
        os.makedirs(old)
        with open(os.path.join(old, "crash-old"), "w") as handle:
            handle.write("x")
        self.run_main("start", "--id", "r6-h", "--harness", "h", "--time", "60", "--findings", old, "--", "sh", "-c", "exit 1")
        self.assertEqual(self.meta("r6-h")["copies"][0]["state"], "failed")

    def test_files_written_after_the_stop_are_not_findings(self):
        # libFuzzer's exit hook saves the input in flight when a JVM is stopped: "fuzz target exited".
        script = "trap 'echo x > \"{run}/artifacts/copy-{copy}/crash-at-exit\"; exit 1' TERM; sleep 30 & wait"
        # 2 s: under load, a TERM after 1 s can reach sh before its trap is set.
        code, out, _ = self.run_main("start", "--id", "r7-h", "--harness", "h", "--time", "2", "--", "sh", "-c", script)
        self.assertEqual(code, 0)
        copy = self.meta("r7-h")["copies"][0]
        self.assertEqual((copy["state"], copy["new_findings"]), ("finished", []))
        self.assertEqual([f["path"] for f in copy["after_stop"]], ["artifacts/copy-0/crash-at-exit"])
        self.assertIn("written after the stop, not findings", out)
        self.assertTrue(os.path.exists(os.path.join(self.rdir("r7-h"), "artifacts", "copy-0", "crash-at-exit")))

    def test_jvm_commands_get_a_longer_grace(self):
        self.assertEqual(lc.stop_grace([["/opt/jdk/bin/java", "-cp", "x", "Main"]]), lc.JVM_GRACE_SECONDS)
        self.assertEqual(lc.stop_grace([["sh", "-c", 'exec mvn -q test']]), lc.JVM_GRACE_SECONDS)
        self.assertEqual(lc.stop_grace([["/x/java/fuzzer", "-runs=1"]]), lc.STOP_GRACE_SECONDS)
        self.assertGreaterEqual(lc.JVM_GRACE_SECONDS, 15)

    def test_an_exit_near_the_limit_is_finished_only_if_it_is_0(self):
        # Within the last 5% of --time: the engine's own limit exits 0; a
        # crash there is a crash.
        for code, findings, expected in ((0, 0, "finished"), (1, 1, "found-crash"), (1, 0, "failed")):
            for ran in (99.0, None):
                copy = {"started": 1000.0, "returncode": code}
                state = lc.copy_state(copy, {"time": 100}, 1099.0, findings, "exit", ran)
                self.assertEqual(state, expected, (code, findings, ran))


class CopyEnvironmentTests(LauncherTestCase):
    def test_the_parent_of_the_copies_has_no_caller_secrets(self):
        probe = "cat /proc/$PPID/environ 2>/dev/null | tr '\\0' '\\n'; ps eww -o command= -p $PPID"
        env = dict(os.environ, SECRET_TOKEN="hunter2")
        for extra in ([], ["--detach"]):
            run_id = "env-%d" % len(extra)
            result = self.cli("start", "--id", run_id, "--harness", "h", "--timeout", "10", *extra,
                              "--", "sh", "-c", probe, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            if extra:
                self.cli("wait", run_id, "--max", "20")
            with open(os.path.join(self.rdir(run_id), "copy-0", "log")) as handle:
                log = handle.read()
            self.assertIn("launcher.py", log)  # ps saw the parent
            self.assertNotIn("hunter2", log)

    def test_placeholders_environment_and_scratch(self):
        cmd = [
            sys.executable, "-c",
            "import json, os, sys; json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': dict(os.environ)},"
            " open(os.path.join(sys.argv[1], 'probe-' + sys.argv[2] + '.json'), 'w'))",
            "{run}", "{copy}", "{dir}", "{scratch}/out",
        ]
        os.environ["SECRET_TOKEN"] = "do-not-leak"
        try:
            code, _, _ = self.run_main(
                "start", "--id", "gate-h-1", "--harness", "h", "--timeout", "30", "--copies", "2",
                "--env", "MY_DIR={tmp}/x", "--env", "JAVA_TOOL_OPTIONS=-Xmx1g", "--", *cmd,
            )
        finally:
            del os.environ["SECRET_TOKEN"]
        self.assertEqual(code, 0)
        rdir = self.rdir("gate-h-1")
        for index in (0, 1):
            with open(os.path.join(rdir, "probe-%d.json" % index)) as handle:
                probe = json.load(handle)
            scratch = os.path.join(rdir, "copy-%d" % index, "work")
            self.assertEqual(probe["argv"], [rdir, str(index), "{dir}", scratch + "/out"])
            self.assertEqual(os.path.realpath(probe["cwd"]), os.path.realpath(scratch))
            env = probe["env"]
            self.assertNotIn("SECRET_TOKEN", env)
            self.assertEqual(env["HOME"], os.path.join(scratch, "home"))
            self.assertEqual(env["TMPDIR"], os.path.join(scratch, "tmp"))
            self.assertEqual(env["MY_DIR"], os.path.join(scratch, "tmp", "x"))
            self.assertIn("PATH", env)
            self.assertEqual(
                env["JAVA_TOOL_OPTIONS"],
                '-Xmx1g -Duser.home="%s" -Djava.io.tmpdir="%s"' % (env["HOME"], env["TMPDIR"]),
            )
            self.assertTrue(os.path.isdir(os.path.join(rdir, "artifacts", "copy-%d" % index)))
        # Develop runs delete their scratch at the end and keep the logs.
        self.assertFalse(os.path.exists(os.path.join(rdir, "copy-0", "work")))
        self.assertTrue(os.path.exists(os.path.join(rdir, "copy-0", "log")))

    def test_long_runs_keep_scratch(self):
        self.run_main("start", "--id", "h-long1", "--harness", "h", "--time", "60", "--", "sh", "-c", "exit 1")
        self.assertTrue(os.path.isdir(os.path.join(self.rdir("h-long1"), "copy-0", "work")))

    def test_spec_is_stored_unresolved_and_reproducible(self):
        self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "30", "--", "sh", "-c", 'echo {copy} > "{run}/out-{copy}"')
        with open(os.path.join(self.rdir("r1-h"), "spec.json")) as handle:
            spec = json.load(handle)
        self.assertEqual(spec["copies"][0]["command"], ["sh", "-c", 'echo {copy} > "{run}/out-{copy}"'])
        self.assertEqual(spec["trees"], [self.repo])
        spec["copies"].append({"command": ["sh", "-c", 'echo second > "{run}/out-{copy}"']})
        path = os.path.join(self.repo, "spec.json")
        with open(path, "w") as handle:
            json.dump(spec, handle)
        code, _, _ = self.run_main("start", "--spec", path, "--id", "r2-h")
        self.assertEqual(code, 0)
        with open(os.path.join(self.rdir("r2-h"), "out-1")) as handle:
            self.assertEqual(handle.read().strip(), "second")

    def test_bounded_log_keeps_head_and_tail(self):
        old = (lc.LOG_MAX_BYTES, lc.LOG_HEAD_BYTES, lc.LOG_TAIL_BYTES, lc.LOG_CHECK_SECONDS)
        lc.LOG_MAX_BYTES, lc.LOG_HEAD_BYTES, lc.LOG_TAIL_BYTES, lc.LOG_CHECK_SECONDS = 20000, 1000, 2000, 0.1
        try:
            script = "import sys, time\nprint('FIRST LINE')\nfor i in range(3000): print('x' * 50)\nsys.stdout.flush(); time.sleep(1)\nprint('LAST LINE')\n"
            self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "30", "--", sys.executable, "-c", script)
        finally:
            lc.LOG_MAX_BYTES, lc.LOG_HEAD_BYTES, lc.LOG_TAIL_BYTES, lc.LOG_CHECK_SECONDS = old
        with open(os.path.join(self.rdir("r1-h"), "copy-0", "log")) as handle:
            text = handle.read()
        self.assertLess(len(text), 10000)
        self.assertTrue(text.startswith("FIRST LINE"))
        self.assertIn("cut from the middle", text)
        self.assertTrue(text.rstrip().endswith("LAST LINE"))

    def test_a_log_is_trimmed_at_the_end_too(self):
        # A run shorter than the periodic check.
        old = (lc.LOG_MAX_BYTES, lc.LOG_HEAD_BYTES, lc.LOG_TAIL_BYTES, lc.LOG_CHECK_SECONDS)
        lc.LOG_MAX_BYTES, lc.LOG_HEAD_BYTES, lc.LOG_TAIL_BYTES, lc.LOG_CHECK_SECONDS = 20000, 1000, 2000, 600
        try:
            script = "print('FIRST LINE')\nfor i in range(3000): print('x' * 50)\nprint('LAST LINE')\n"
            self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "30", "--", sys.executable, "-c", script)
        finally:
            lc.LOG_MAX_BYTES, lc.LOG_HEAD_BYTES, lc.LOG_TAIL_BYTES, lc.LOG_CHECK_SECONDS = old
        with open(os.path.join(self.rdir("r1-h"), "copy-0", "log")) as handle:
            text = handle.read()
        self.assertLess(len(text), 10000)
        self.assertTrue(text.startswith("FIRST LINE"))
        self.assertTrue(text.rstrip().endswith("LAST LINE"))

    def test_progress_samples_and_status_line(self):
        script = "print('#1 INITED cov: 10 ft: 11 corp: 1/1b exec/s: 0'); print('#9 NEW cov: 12 ft: 13 corp: 2/2b')"
        self.run_main("start", "--id", "r1-h", "--harness", "h", "--time", "60", "--", sys.executable, "-c", script)
        with open(os.path.join(self.rdir("r1-h"), "progress.tsv")) as handle:
            rows = handle.read().splitlines()
        self.assertTrue(rows[0].startswith("time\telapsed_s\tcopy\tstate"))
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[-1].endswith("#9 NEW cov: 12 ft: 13 corp: 2/2b"))

    def test_tree_commit_and_dirty_state(self):
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        with open(os.path.join(self.repo, "a.txt"), "w") as handle:
            handle.write("a")
        git = ["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["add", "a.txt"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "a"], check=True)
        with open(os.path.join(self.repo, "a.txt"), "w") as handle:
            handle.write("b")
        self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "5", "--", "true")
        tree = self.meta("r1-h")["trees"][0]
        self.assertEqual(len(tree["commit"]), 40)
        self.assertTrue(tree["dirty"])


class IdAndCpuTests(LauncherTestCase):
    def test_started_ids_are_refused(self):
        self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "5", "--", "true")
        code, _, err = self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "5", "--", "true")
        self.assertEqual(code, lc.EXIT_USAGE)
        self.assertIn("already started", err)
        self.assertIn("pick a new id", err)
        self.assertTrue(os.path.exists(os.path.join(self.rdir("r1-h"), "meta.json")))

    def test_a_prepared_spec_is_not_a_started_run(self):
        rdir = self.rdir("h-long1")
        os.makedirs(rdir)
        spec = {"id": "h-long1", "harness": "h", "time": 60, "copies": [{"command": ["sh", "-c", "exit 1"]}]}
        with open(os.path.join(rdir, "spec.json"), "w") as handle:
            json.dump(spec, handle)
        code, out, _ = self.run_main("start", "--spec", os.path.join(rdir, "spec.json"))
        self.assertEqual(code, 0)
        self.assertIn("h-long1: failed", out)

    def test_run_level_env_applies_to_every_copy(self):
        rdir = self.rdir("h-long3")
        os.makedirs(rdir)
        spec = {
            "id": "h-long3", "harness": "h", "timeout": 30,
            "env": {"A": "run", "B": "run-{copy}"},
            "copies": [
                {"command": ["sh", "-c", 'echo "$A $B" > "$0"', "{run}/out-0"]},
                {"command": ["sh", "-c", 'echo "$A $B" > "$0"', "{run}/out-1"], "env": {"A": "copy"}},
            ],
        }
        with open(os.path.join(rdir, "spec.json"), "w") as handle:
            json.dump(spec, handle)
        self.run_main("start", "--spec", os.path.join(rdir, "spec.json"))
        for index, expected in ((0, "run run-0"), (1, "copy run-1")):
            with open(os.path.join(rdir, "out-%d" % index)) as handle:
                self.assertEqual(handle.read().strip(), expected)

    def test_spec_types_and_locations_are_checked(self):
        # AFL++ specs are written by hand. A findings string used to be read
        # as a list of one-letter directories, created in the caller's
        # directory; a relative location isn't the copy's (it runs in scratch).
        rdir = self.rdir("h-long5")
        os.makedirs(rdir)
        path = os.path.join(rdir, "spec.json")
        good = {"id": "h-long5", "harness": "h", "timeout": 30, "copies": [{"command": ["true"]}]}
        cases = [
            ({"findings": "{run}/artifacts"}, "findings"),
            ({"findings": ["artifacts"]}, "absolute"),
            ({"corpus": "corpus-{copy}"}, "absolute"),
            ({"corpus": ["{run}/corpus"]}, "corpus"),
            ({"env": {"AFL_NO_UI": 1}}, "env"),
            ({"env": ["AFL_NO_UI=1"]}, "env"),
            ({"command": "true"}, "command"),
            ({"command": ["sleep", 1]}, "command"),
        ]
        cases = [(dict(good, copies=[dict(good["copies"][0], **change)]), message) for change, message in cases]
        cases += [
            (dict(good, copies={"command": ["true"]}), "copies"),
            (dict(good, copies=["true"]), "copies"),
            (dict(good, id=5), "id"),
            (dict(good, env="AFL_NO_UI=1"), "env"),
            (dict(good, trees="/abs/tree"), "trees"),
            (["not", "an", "object"], "object"),
        ]
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            for spec, message in cases:
                with open(path, "w") as handle:
                    json.dump(spec, handle)
                code, _, err = self.run_main("start", "--spec", path)
                self.assertEqual(code, lc.EXIT_USAGE, (spec, err))
                self.assertIn(message, err, spec)
                self.assertFalse(os.path.exists(os.path.join(rdir, "meta.json")), spec)
            for option, value in (("--findings", "arts"), ("--corpus", "corpus")):
                code, _, err = self.run_main("start", "--id", "r1-h", "--harness", "h", "--timeout", "5",
                                             option, value, "--", "true")
                self.assertEqual(code, lc.EXIT_USAGE, option)
                self.assertIn("%s %r isn't an absolute path" % (option, value), err)
            self.assertEqual(sorted(os.listdir(self.repo)), [".seclab"])
        finally:
            os.chdir(cwd)

    def test_prepare_writes_the_spec_and_the_command(self):
        code, out, _ = self.run_main(
            "start", "--prepare", "--id", "h-long2", "--harness", "h", "--copies", "2", "--time", "60",
            "--", "sh", "-c", "exit 1",
        )
        self.assertEqual(code, 0)
        rdir = self.rdir("h-long2")
        self.assertTrue(os.path.exists(os.path.join(rdir, "spec.json")))
        self.assertFalse(os.path.exists(os.path.join(rdir, "meta.json")))
        self.assertIn("start --spec", out)
        self.assertIn(os.path.join(rdir, "spec.json"), out.replace("'", ""))
        code, out, _ = self.run_main("start", "--spec", os.path.join(rdir, "spec.json"))
        self.assertIn("h-long2: failed", out)

    def test_rerun_modes_are_refused_in_a_path_with_a_space(self):
        # The test workspace has a space ("my repo").
        for flag in ("-merge=1", "-minimize_crash=1"):
            code, _, err = self.run_main(
                "start", "--id", "merge-h-1", "--harness", "h", "--timeout", "5", "--", "fuzzer", flag, "{run}/corpus",
            )
            self.assertEqual(code, lc.EXIT_USAGE)
            self.assertIn("without quoting", err)
            self.assertFalse(os.path.exists(os.path.join(self.rdir("merge-h-1"), "meta.json")))
        code, _, _ = self.run_main(
            "start", "--id", "merge-h-2", "--harness", "h", "--timeout", "5", "--",
            "sh", "-c", 'echo "$@" -merge=1', "sh",
        )
        self.assertEqual(code, lc.EXIT_USAGE)
        safe = os.path.join(os.path.realpath(self.tmp.name), "repo", ".seclab", "fuzz")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lc.main(["--workspace", safe, "start", "--id", "merge-h-3", "--harness", "h", "--timeout", "5", "--", "true", "-merge=1"])
        self.assertEqual(code, 0, err.getvalue())

    def run_in(self, workspace, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lc.main(["--workspace", workspace] + list(argv))
        return code, err.getvalue()

    def test_rerun_modes_check_every_path_argument(self):
        safe = os.path.join(os.path.realpath(self.tmp.name), "repo", ".seclab", "fuzz")
        crashes = os.path.join(safe, "runs", "x-long1", "out", "main", "crashes")
        os.makedirs(crashes)
        # AFL++ names a crashing seed after it: orig:<seed file name>.
        finding = os.path.join(crashes, "id:000000,sig:06,src:000000,op:dry_run,orig:seed;touch INJECTED")
        open(finding, "w").close()
        for args in (
            [finding],
            ["-exact_artifact_path=%s" % os.path.join(self.repo, "minimized"), finding.split(",orig:")[0]],
        ):
            code, err = self.run_in(safe, "start", "--id", "min-h-1", "--harness", "h", "--timeout", "5", "--",
                                    "true", "-minimize_crash=1", "-runs=10", *args)
            self.assertEqual(code, lc.EXIT_USAGE, args)
            self.assertIn("without quoting", err)
            self.assertIn(args[0].split("=")[-1], err)
        safe_finding = os.path.join(crashes, "id:000001,sig:06,src:000000,time:12,execs:34,op:havoc,rep:2")
        open(safe_finding, "w").close()
        code, err = self.run_in(safe, "start", "--id", "min-h-2", "--harness", "h", "--timeout", "5", "--",
                                "true", "-minimize_crash=1", "-exact_artifact_path={run}/min", safe_finding)
        self.assertEqual(code, 0, err)
        # Relative paths, and other arguments, split the same way; a shell's
        # -c script is the shell's to split.
        for arg in ("-dict=relative dictionary.txt", "relative input", "-dict=x;touch INJECTED"):
            code, err = self.run_in(safe, "start", "--id", "merge-h-4", "--harness", "h", "--timeout", "5", "--",
                                    "true", "-merge=1", arg)
            self.assertEqual(code, lc.EXIT_USAGE, arg)
            self.assertIn("without quoting", err)
            self.assertIn(arg.split("=")[-1], err)
        code, err = self.run_in(safe, "start", "--id", "merge-h-5", "--harness", "h", "--timeout", "5", "--",
                                "sh", "-c", '"$@" -merge=1 "{run}/corpus"', "sh", "true",
                                "--instrumentation_includes=com.example.**", "-dict=rel/x.dict")
        self.assertEqual(code, 0, err)
        code, err = self.run_in(safe, "start", "--id", "merge-h-6", "--harness", "h", "--timeout", "5", "--",
                                "sh", "-c", '/usr/bin/true -merge=1 "{run}/corpus"')  # a script starting with /
        self.assertEqual(code, 0, err)

    def test_rerun_modes_take_non_ascii_paths(self):
        workspace = os.path.join(os.path.realpath(self.tmp.name), "jos\u00e9", ".seclab", "fuzz")
        code, err = self.run_in(workspace, "start", "--id", "merge-h-1", "--harness", "h", "--timeout", "5", "--",
                                "true", "-merge=1", "{run}/corpus")
        self.assertEqual(code, 0, err)

    def test_cpu_check_refuses_and_shows_the_flag(self):
        old = lc.cpu_count
        lc.cpu_count = lambda: 2
        try:
            fake = self.rdir("busy-long1")
            os.makedirs(fake)
            meta = {
                "id": "busy-long1", "harness": "h", "started": time.time(), "ended": None,
                "supervisor": {"pid": os.getpid(), "identity": lc.process_identity(os.getpid())},
                "copies": [{"index": 0, "pid": os.getpid(), "identity": lc.process_identity(os.getpid()),
                            "findings": [], "baseline": [], "log": "/dev/null", "started": time.time()}],
            }
            with open(os.path.join(fake, "meta.json"), "w") as handle:
                json.dump(meta, handle)
            code, _, err = self.run_main("start", "--id", "r1-h", "--harness", "h", "--copies", "2", "--timeout", "5", "--", "true")
            self.assertEqual(code, lc.EXIT_CPU)
            self.assertIn("1 of 2 are in use", err)
            self.assertIn("--skip-cpu-check", err)
            code, _, _ = self.run_main("start", "--id", "r1-h", "--harness", "h", "--copies", "2", "--timeout", "5", "--skip-cpu-check", "--", "true")
            self.assertEqual(code, 0)
        finally:
            lc.cpu_count = old
            os.remove(os.path.join(self.rdir("busy-long1"), "meta.json"))


class StatusLineTests(LauncherTestCase):
    def last_status_line(self, text):
        path = os.path.join(self.repo, "log")
        with open(path, "w") as handle:
            handle.write(text)
        return lc.last_status_line(path)

    def test_stack_frames_are_not_status_lines(self):
        log = (
            "#1024\tpulse  cov: 10 ft: 11 corp: 1/1b exec/s: 512 rss: 30Mb\n"
            "#2048\tNEW    cov: 12 ft: 13 corp: 2/2b lim: 4 exec/s: 1024 rss: 30Mb\n"
            "==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1\n"
            "    #0 0x100003f00 in LLVMFuzzerTestOneInput fuzz.cc:10\n"
            "#11 0x0001827a4d78 in start+0x19f8 (dyld:arm64e+0x31d78)\n"
            "SUMMARY: AddressSanitizer: heap-buffer-overflow fuzz.cc:10\n"
        )
        self.assertEqual(self.last_status_line(log), "#2048\tNEW    cov: 12 ft: 13 corp: 2/2b lim: 4 exec/s: 1024 rss: 30Mb")
        fork = "#4096: cov: 20 ft: 30 corp: 5 exec/s 100 oom/timeout/crash: 0/0/0 time: 5s job: 2 dft_time: 0\n"
        self.assertEqual(self.last_status_line(fork + "#3 0x1 in main\n"), fork.strip())

    def test_ansi_codes_are_stripped(self):
        log = "\x1b[1;92m[+] \x1b[0mAll set and ready to roll!\n\x1b[1;94m[*] \x1b[0mEntering queue cycle 1\x1b[0m\n"
        self.assertEqual(self.last_status_line(log), "[*] Entering queue cycle 1")
        self.assertEqual(self.last_status_line("\x1b[31mplain error\x1b[0m\n"), "plain error")


class ClockTests(LauncherTestCase):
    def test_a_wall_clock_jump_doesnt_end_a_run(self):
        # After the machine sleeps, the wall clock has jumped forward while
        # the run (and the fuzzer) was paused. Deadlines and timeouts count
        # time awake; engines time their own limits by the wall clock.
        jump = os.path.join(self.repo, "jump")
        real = lc.now
        old = lc.now
        lc.now = lambda: real() + (3600 if os.path.exists(jump) else 0)
        try:
            for run_id, kind, end, expected, expected_code in (
                ("repro-h-1", "--timeout", "exit 0", "finished", 0),
                ("r1-h", "--time", "exit 1", "failed", 0),  # ended early, after the jump
                ("r2-h", "--time", "exit 0", "finished", 0),  # an engine's own (wall-clock) limit
            ):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(jump)
                cmd = 'touch "%s"; sleep 1; %s' % (jump, end)
                code, out, _ = self.run_main("start", "--id", run_id, "--harness", "h", kind, "30", "--", "sh", "-c", cmd)
                self.assertEqual(code, expected_code, out)
                self.assertEqual(self.meta(run_id)["copies"][0]["state"], expected, run_id)
        finally:
            lc.now = old


class CpuCountTests(unittest.TestCase):
    """A container's --cpus is a cgroup quota: os.cpu_count() reports the host's cores."""

    def cgroups(self, own, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = tmp.name
        for path, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
            with open(os.path.join(root, path), "w") as handle:
                handle.write(text + "\n")
        with open(os.path.join(root, "self-cgroup"), "w") as handle:
            handle.write(own + "\n")
        return lc.cgroup_cpus(os.path.join(root, "fs"), os.path.join(root, "self-cgroup"))

    def test_cgroup_quotas(self):
        # cgroup v2 in a container (its own cgroup is the root), and on a
        # host, where a parent's quota limits the child's.
        self.assertEqual(self.cgroups("0::/", {"fs/cpu.max": "200000 100000"}), 2.0)
        self.assertEqual(self.cgroups("0::/user.slice/job", {
            "fs/cpu.max": "max 100000",
            "fs/user.slice/cpu.max": "150000 100000",
            "fs/user.slice/job/cpu.max": "max 100000",
        }), 1.5)
        self.assertIsNone(self.cgroups("0::/", {"fs/cpu.max": "max 100000"}))
        # cgroup v1, the container's cgroup mounted at the controller's root.
        self.assertEqual(self.cgroups("4:cpu,cpuacct:/docker/abc\n3:memory:/docker/abc", {
            "fs/cpu/cpu.cfs_quota_us": "300000", "fs/cpu/cpu.cfs_period_us": "100000",
        }), 3.0)
        self.assertIsNone(self.cgroups("4:cpu,cpuacct:/", {
            "fs/cpu/cpu.cfs_quota_us": "-1", "fs/cpu/cpu.cfs_period_us": "100000",
        }))
        self.assertIsNone(lc.cgroup_cpus("/no/such/root", "/no/such/file"))

    def test_cpu_count_takes_the_quota(self):
        old = lc.cgroup_cpus
        try:
            lc.cgroup_cpus = lambda *args: 1.5
            self.assertEqual(lc.cpu_count(), min(2, os.cpu_count()))
            lc.cgroup_cpus = lambda *args: 0.2
            self.assertEqual(lc.cpu_count(), 1)
            lc.cgroup_cpus = lambda *args: None
            self.assertLessEqual(lc.cpu_count(), os.cpu_count())
            if hasattr(os, "sched_getaffinity"):
                self.assertEqual(lc.cpu_count(), len(os.sched_getaffinity(0)))
        finally:
            lc.cgroup_cpus = old


class ProcessIdentityTests(unittest.TestCase):
    def test_a_zombie_is_not_alive(self):
        # In a container whose PID 1 doesn't reap orphans, killed copies stay
        # zombies; they have exited.
        child = subprocess.Popen(["sh", "-c", "exit 0"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and lc.process_identity(child.pid) is not None:
            time.sleep(0.05)
        try:
            self.assertIsNone(lc.process_identity(child.pid))
            self.assertFalse(lc.is_alive(child.pid, "anything"))
        finally:
            child.wait()

    @unittest.skipIf(os.path.exists("/proc/self/stat"), "identity comes from /proc here, not ps")
    def test_identity_does_not_depend_on_the_callers_locale_or_time_zone(self):
        identities = set()
        for env in ({"LC_ALL": "C", "TZ": "UTC"}, {"LC_ALL": "de_DE.UTF-8", "TZ": "Asia/Tokyo"},
                    {"LANG": "fr_FR.UTF-8", "TZ": "America/New_York"}):
            saved = {key: os.environ.get(key) for key in ("LC_ALL", "LANG", "LC_TIME", "TZ")}
            try:
                for key in saved:
                    os.environ.pop(key, None)
                os.environ.update(env)
                identities.add(lc.process_identity(os.getpid()))
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        self.assertEqual(len(identities), 1, identities)
        self.assertIsNotNone(identities.pop())

    @unittest.skipIf(os.path.exists("/proc/self/stat"), "identity comes from /proc here")
    def test_identity_without_ps(self):
        # Codex's macOS sandbox can't run the setuid `ps`.
        saved = os.environ["PATH"]
        with tempfile.TemporaryDirectory() as empty:
            os.environ["PATH"] = empty
            try:
                mine, init = lc.process_identity(os.getpid()), lc.process_identity(1)
            finally:
                os.environ["PATH"] = saved
        self.assertNotIn(mine, (None, lc.UNKNOWN))
        self.assertNotIn(init, (None, lc.UNKNOWN, mine))
        self.assertEqual(mine, lc.process_identity(os.getpid()))


class UnreadableIdentityTests(LauncherTestCase):
    """Where process identities can't be read (a sandbox), a run is never taken for ended."""

    def unreadable(self):
        saved = lc.process_identity
        lc.process_identity = lambda pid: lc.UNKNOWN if pid else None
        self.addCleanup(setattr, lc, "process_identity", saved)

    def test_detach_is_refused(self):
        self.unreadable()
        code, _, err = self.run_main("start", "--id", "h-long1", "--harness", "h", "--time", "60", "--detach", "--", "sleep", "60")
        self.assertEqual(code, lc.EXIT_USAGE, err)
        self.assertIn("without --detach", err)
        self.assertFalse(os.path.exists(self.rdir("h-long1")))

    def test_a_live_run_is_unverified_not_died(self):
        result = self.cli("start", "--id", "h-long1", "--harness", "h", "--time", "60", "--detach", "--", "sleep", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.unreadable()
        _, out, _ = self.run_main("status")
        self.assertIn("| h-long1 | h | unverified, 0 findings |", out)
        code, out, err = self.run_main("wait", "h-long1", "--max", "5")
        self.assertEqual(code, 1)
        self.assertNotIn("died", out)
        self.assertIn("can't verify", err)
        code, out, err = self.run_main("stop", "h-long1")
        self.assertEqual(code, 1)
        self.assertIn("isn't recorded as stopped", err)
        self.assertIsNone(self.meta("h-long1")["ended"])
        self.assertTrue(os.path.exists(os.path.join(self.rdir("h-long1"), "stop")))  # the supervisor ends it

    def test_the_cpu_check_counts_unverified_copies(self):
        result = self.cli("start", "--id", "h-long1", "--harness", "h", "--copies", "2", "--time", "60", "--detach",
                          "--", "sleep", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.unreadable()
        self.assertEqual(lc.running_cores(self.ws, "")[0], 2)


class SupervisorTests(LauncherTestCase):
    def test_detach_wait_status_and_stop(self):
        result = self.cli(
            "start", "--id", "h-long1", "--harness", "h", "--copies", "2", "--time", "60", "--detach",
            "--", "sh", "-c", "sleep 300 & sleep 300",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        hint = result.stdout.split("check it with: ", 1)[1].strip()
        shown = subprocess.run(shlex.split(hint), capture_output=True, text=True, timeout=60)
        self.assertEqual(shown.returncode, 0, (hint, shown.stderr))
        self.assertIn("## h-long1 (harness h)", shown.stdout)
        waited = self.cli("wait", "h-long1", "--max", "0.5")
        self.assertIn("still running, 2 copies", waited.stdout)
        listing = self.cli("status")
        self.assertIn("| h-long1 | h | running, 0 findings |", listing.stdout)
        pids = [c["pid"] for c in self.meta("h-long1")["copies"]]
        stopped = self.cli("stop", "h-long1")
        self.assertIn("h-long1: stopped, 0 findings", stopped.stdout)
        for pid in pids:
            self.assertTrue(group_gone(pid))  # the whole group, background sleep included

    def test_a_live_run_is_never_replaced(self):
        result = self.cli("start", "--id", "h-long4", "--harness", "h", "--time", "60", "--detach", "--", "sleep", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        pid = self.meta("h-long4")["copies"][0]["pid"]
        again = self.cli("start", "--id", "h-long4", "--harness", "h", "--timeout", "5", "--", "true")
        self.assertEqual(again.returncode, lc.EXIT_USAGE, again.stderr)
        self.assertIn("still running", again.stderr)
        os.killpg(pid, 0)  # untouched
        self.assertTrue(os.path.exists(os.path.join(self.rdir("h-long4"), "meta.json")))
        self.cli("stop", "h-long4")
        ended = self.cli("start", "--id", "h-long4", "--harness", "h", "--timeout", "5", "--", "true")
        self.assertEqual(ended.returncode, lc.EXIT_USAGE, ended.stderr)
        self.assertIn("pick a new id", ended.stderr)

    def test_unsupervised_and_died(self):
        result = self.cli("start", "--id", "h-long2", "--harness", "h", "--time", "60", "--detach", "--", "sleep", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta("h-long2")
        os.kill(meta["supervisor"]["pid"], signal.SIGKILL)
        time.sleep(0.5)
        self.assertIn("running (unsupervised)", self.cli("status").stdout)
        os.killpg(meta["copies"][0]["pid"], signal.SIGKILL)
        time.sleep(0.5)
        self.assertIn("| h-long2 | h | died, 0 findings |", self.cli("status").stdout)

    def test_stale_pid_is_never_signalled(self):
        fake = self.rdir("old-long1")
        os.makedirs(fake)
        meta = {
            "id": "old-long1", "harness": "h", "started": time.time(), "ended": None,
            "supervisor": {"pid": os.getpid(), "identity": "not-this-process"},
            "copies": [{"index": 0, "pid": os.getpid(), "identity": "not-this-process",
                        "findings": [], "baseline": [], "log": "/dev/null", "started": time.time()}],
        }
        with open(os.path.join(fake, "meta.json"), "w") as handle:
            json.dump(meta, handle)
        self.assertIn("died", self.cli("status").stdout)
        result = self.cli("stop", "old-long1")
        self.assertEqual(result.returncode, 0)  # and this test process is still alive

    def test_a_died_run_keeps_its_state_and_its_elapsed_time(self):
        # Its elapsed time ends at its last sign of life; stop has nothing to stop.
        fake = self.rdir("old-long3")
        os.makedirs(os.path.join(fake, "copy-0"))
        started = time.time() - 3600
        log = os.path.join(fake, "copy-0", "log")
        open(log, "w").close()
        meta = {
            "id": "old-long3", "harness": "h", "started": started, "ended": None,
            "supervisor": {"pid": os.getpid(), "identity": "not-this-process"},
            "copies": [{"index": 0, "pid": os.getpid(), "identity": "not-this-process",
                        "findings": [], "baseline": [], "log": log, "started": started}],
        }
        with open(os.path.join(fake, "meta.json"), "w") as handle:
            json.dump(meta, handle)
        with open(os.path.join(fake, "spec.json"), "w") as handle:
            json.dump({"id": "old-long3", "harness": "h", "time": None, "copies": [{"command": ["true"]}]}, handle)
        for path in (log, os.path.join(fake, "meta.json")):
            os.utime(path, (started + 600, started + 600))
        _, out, _ = self.run_main("status", "old-long3")
        self.assertIn("elapsed 10m00s", out)
        _, out, _ = self.run_main("status")
        self.assertIn("| old-long3 | h | died, 0 findings | 10m00s |", out)
        code, out, err = self.run_main("stop", "old-long3")
        self.assertEqual(code, 0, err)
        self.assertIn("old-long3 already ended: died, 0 findings", out)
        self.assertIsNone(self.meta("old-long3")["ended"])
        self.assertFalse(os.path.exists(os.path.join(fake, "stop")))

    def test_ctrl_c_stops_a_foreground_run(self):
        proc = subprocess.Popen(
            [sys.executable, SCRIPT, "--workspace", self.ws, "start", "--id", "h-long3", "--harness", "h",
             "--time", "60", "--", "sleep", "300"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            path = os.path.join(self.rdir("h-long3"), "meta.json")
            if os.path.exists(path) and self.meta("h-long3").get("started"):
                break
            time.sleep(0.1)
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=30)
        self.assertIn("h-long3: stopped", out)
        self.assertEqual(self.meta("h-long3")["copies"][0]["state"], "stopped")

    def test_a_hangup_the_caller_ignores_stays_ignored(self):
        # `nohup <command> &` over SSH: the shell ignores HUP (and INT, for a
        # background job), so closing the session mustn't stop the run.
        def ignore():
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)

        proc = subprocess.Popen(
            [sys.executable, SCRIPT, "--workspace", self.ws, "start", "--id", "h-long5", "--harness", "h",
             "--time", "60", "--", "sleep", "300"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=ignore,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            path = os.path.join(self.rdir("h-long5"), "meta.json")
            if os.path.exists(path) and self.meta("h-long5").get("started"):
                break
            time.sleep(0.1)
        proc.send_signal(signal.SIGHUP)
        proc.send_signal(signal.SIGINT)
        time.sleep(1)
        self.assertIsNone(proc.poll())
        self.assertEqual(self.meta("h-long5")["copies"][0]["state"], "running")
        stopped = self.cli("stop", "h-long5")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        out, _ = proc.communicate(timeout=30)
        self.assertIn("h-long5: stopped", out)


def group_gone(pid, wait=5.0):
    """No live process left in the group (zombies don't count: a container's
    PID 1 may not reap them)."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not lc.group_members(pid):
            return True
        time.sleep(0.05)
    return False


def kill_group(pid):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


class WriteErrorTests(LauncherTestCase):
    def forget(self, run_id):
        """Kill what's left of an in-process run whose record names this test
        process as its supervisor, so tearDown's stop doesn't signal it."""
        path = os.path.join(self.rdir(run_id), "meta.json")
        if os.path.exists(path):
            for copy in self.meta(run_id)["copies"]:
                if lc.is_alive(copy.get("pid"), copy.get("identity")):
                    os.killpg(copy["pid"], signal.SIGKILL)
            os.remove(path)

    def test_a_full_disk_doesnt_end_supervision(self):
        full = os.path.join(self.repo, "disk-full")
        real = lc.write_json

        def write_json(path, data):
            if os.path.exists(full):
                raise OSError(errno.ENOSPC, "No space left on device", path)
            real(path, data)

        # Once the run has started, the disk "fills": meta.json can't be
        # written and progress.tsv can't be appended to.
        script = (
            "import json, os, sys, time\n"
            "run, full = sys.argv[1], sys.argv[2]\n"
            "while not json.load(open(os.path.join(run, 'meta.json'))).get('started'): time.sleep(0.02)\n"
            "open(full, 'w').close()\n"
            "try:\n"
            "    os.remove(os.path.join(run, 'progress.tsv')); os.mkdir(os.path.join(run, 'progress.tsv'))\n"
            "except OSError: pass\n"
            "time.sleep(1)\n"
        )
        old = (lc.write_json, lc.PROGRESS_SECONDS)
        lc.write_json, lc.PROGRESS_SECONDS = write_json, 0.1
        try:
            code, out, err = self.run_main(
                "start", "--id", "r1-h", "--harness", "h", "--copies", "2", "--timeout", "30",
                "--", sys.executable, "-c", script, "{run}", full,
            )
            self.assertEqual(code, 0, err)
            self.assertIn("r1-h: finished, 0 findings", out)
            self.assertEqual(err.count("can't write"), 1, err)
            for copy in self.meta("r1-h")["copies"]:
                self.assertTrue(group_gone(copy["pid"]))
        finally:
            lc.write_json, lc.PROGRESS_SECONDS = old
            self.forget("r1-h")

    def test_a_crashing_supervisor_stops_its_copies(self):
        def trim_log(_path):
            raise RuntimeError("supervisor bug")

        old = (lc.trim_log, lc.LOG_CHECK_SECONDS)
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        lc.trim_log, lc.LOG_CHECK_SECONDS = trim_log, 0.1
        try:
            with self.assertRaises(RuntimeError):
                self.run_main("start", "--id", "r1-h", "--harness", "h", "--time", "60", "--", "sleep", "300")
            self.assertTrue(group_gone(self.meta("r1-h")["copies"][0]["pid"], wait=1.0))
            self.assertEqual({sig: signal.getsignal(sig) for sig in handlers}, handlers)
        finally:
            lc.trim_log, lc.LOG_CHECK_SECONDS = old
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            self.forget("r1-h")

    @unittest.skipIf(os.geteuid() == 0, "root can write to a read-only directory")
    def test_stop_works_when_the_run_directory_is_unwritable(self):
        result = self.cli("start", "--id", "h-long1", "--harness", "h", "--time", "60", "--detach", "--", "sleep", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        pid = self.meta("h-long1")["copies"][0]["pid"]
        rdir = self.rdir("h-long1")
        os.chmod(rdir, 0o555)
        try:
            stopped = self.cli("stop", "h-long1")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertIn("h-long1: stopped", stopped.stdout)
            self.assertTrue(group_gone(pid))
        finally:
            os.chmod(rdir, 0o755)



class LifecycleTests(LauncherTestCase):
    def test_a_failed_start_stops_the_copies_it_started_and_says_why(self):
        blocker = os.path.join(self.repo, "not-a-directory")
        open(blocker, "w").close()
        for run_id, extra in (("h-long1", []), ("h-long2", ["--detach"])):
            rdir = self.rdir(run_id)
            os.makedirs(rdir)
            spec = {"id": run_id, "harness": "h", "time": 60, "copies": [
                {"command": ["sleep", "60"]},
                {"command": ["sleep", "60"], "corpus": blocker},  # its corpus can't be created
            ]}
            with open(os.path.join(rdir, "spec.json"), "w") as handle:
                json.dump(spec, handle)
            code, _, err = self.run_main("start", "--spec", os.path.join(rdir, "spec.json"), *extra)
            self.assertEqual(code, 1, err)
            self.assertIn("File exists", err)
            if extra:
                self.assertIn("failed to start", err)
            meta = self.meta(run_id)
            self.assertTrue(meta["ended"])
            self.assertTrue(meta["state"].startswith("failed to start: "), meta["state"])
            self.assertIn("File exists", meta["state"])
            self.assertEqual(len(meta["copies"]), 1)  # the copy that started was recorded ...
            self.assertEqual(meta["copies"][0]["state"], "stopped")
            self.assertTrue(group_gone(meta["copies"][0]["pid"], wait=1.0))  # ... and stopped

    def test_a_command_that_cant_start_rolls_back_the_start(self):
        for run_id, extra in (("h-long3", []), ("h-long4", ["--detach"])):
            rdir = self.rdir(run_id)
            os.makedirs(rdir)
            spec = {"id": run_id, "harness": "h", "time": 60, "copies": [
                {"command": ["sleep", "60"]},
                {"command": ["/no/such/program"]},
            ]}
            with open(os.path.join(rdir, "spec.json"), "w") as handle:
                json.dump(spec, handle)
            code, _, err = self.run_main("start", "--spec", os.path.join(rdir, "spec.json"), *extra)
            self.assertEqual(code, lc.EXIT_CANT_START, err)
            if extra:
                self.assertIn("failed to start", err)
            meta = self.meta(run_id)
            self.assertTrue(meta["ended"])
            self.assertTrue(meta["state"].startswith("failed to start: "), meta["state"])
            self.assertIn("/no/such/program", meta["state"])
            self.assertEqual(meta["copies"][0]["state"], "stopped")
            self.assertTrue(meta["copies"][1]["state"].startswith("failed to start: "), meta["copies"][1])
            self.assertTrue(group_gone(meta["copies"][0]["pid"], wait=1.0))

    def test_a_detached_command_that_cant_start_returns_127(self):
        code, _, err = self.run_main("start", "--id", "h-long5", "--harness", "h", "--timeout", "5", "--detach",
                                     "--", "/no/such/program")
        self.assertEqual(code, lc.EXIT_CANT_START, err)
        self.assertIn("h-long5 failed to start: ", err)
        self.assertIn("/no/such/program", err)
        self.assertTrue(self.meta("h-long5")["ended"])

    def test_stop_reaches_a_group_whose_leader_exited(self):
        # The leader's child lives on in its group: in scratch; elsewhere
        # (its stdout still the copy's log); elsewhere with its output
        # redirected (only its environment still says whose it is).
        sleeper = "'%s' -c 'import time; time.sleep(300)'" % sys.executable
        variants = {
            "orphan-long1": "sleep 300 &",
            "orphan-long2": '(cd "%s" && exec sleep 300) &' % self.repo,
            "orphan-long3": '(cd "%s" && exec %s >/dev/null 2>&1) &' % (self.repo, sleeper),
        }
        # One variant at a time: each orphaned group holds a core, and CI
        # runners have two.
        for run_id, child in variants.items():
            cmd = '%s echo $! > "{run}/child"; sleep 2' % child
            result = self.cli("start", "--id", run_id, "--harness", "h", "--time", "60", "--detach", "--", "sh", "-c", cmd)
            self.assertEqual(result.returncode, 0, result.stderr)
            meta = self.meta(run_id)
            os.kill(meta["supervisor"]["pid"], signal.SIGKILL)
            leader = meta["copies"][0]
            self.addCleanup(kill_group, leader["pid"])
            deadline = time.monotonic() + 30
            while lc.is_alive(leader["pid"], leader["identity"]) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(lc.is_alive(leader["pid"], leader["identity"]))
            with open(os.path.join(self.rdir(run_id), "child")) as handle:
                os.kill(int(handle.read()), 0)
            listing = self.cli("status").stdout
            self.assertIn("| %s | h | running (unsupervised), 0 findings |" % run_id, listing)
            self.assertRegex(listing, r"\| %s \|[^\n]*\| 1 \|" % run_id)  # its core is in use
            stopped = self.cli("stop", run_id)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertIn("%s: stopped" % run_id, stopped.stdout)
            self.assertTrue(group_gone(leader["pid"]), run_id)

    def test_stop_doesnt_record_a_stop_it_couldnt_make(self):
        result = self.cli("start", "--id", "h-long1", "--harness", "h", "--time", "60", "--detach", "--", "sleep", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.meta("h-long1")
        os.kill(meta["supervisor"]["pid"], signal.SIGKILL)
        self.addCleanup(kill_group, meta["copies"][0]["pid"])
        old = lc.signal_copy
        lc.signal_copy = lambda copy, rdir, sig: True  # signals that don't arrive
        try:
            code, _, err = self.run_main("stop", "h-long1")
        finally:
            lc.signal_copy = old
        self.assertEqual(code, 1, err)
        self.assertIn("still running", err)
        self.assertIsNone(self.meta("h-long1")["ended"])
        os.killpg(meta["copies"][0]["pid"], 0)
        code, out, err = self.run_main("stop", "h-long1")
        self.assertEqual(code, 0, err)
        self.assertIn("h-long1: stopped", out)

    def test_stop_says_when_the_supervisor_doesnt_end_the_run(self):
        # "ready" once the trap is set: a TERM before it would end sh, as on a busy runner.
        supervisor = subprocess.Popen(["sh", "-c", 'trap "" TERM; echo ready; sleep 300'], start_new_session=True,
                                      stdout=subprocess.PIPE)
        supervisor.stdout.readline()
        try:
            fake = self.rdir("stuck-long1")
            os.makedirs(fake)
            meta = {
                "id": "stuck-long1", "harness": "h", "started": time.time(), "ended": None, "copies": [],
                "supervisor": {"pid": supervisor.pid, "identity": lc.process_identity(supervisor.pid)},
            }
            with open(os.path.join(fake, "meta.json"), "w") as handle:
                json.dump(meta, handle)
            code, _, err = self.run_main("stop", "stuck-long1")
            self.assertEqual(code, 1, err)
            self.assertIn("still running", err)
            self.assertIsNone(self.meta("stuck-long1")["ended"])
        finally:
            os.killpg(supervisor.pid, signal.SIGKILL)
            supervisor.wait()
            supervisor.stdout.close()

    def test_stop_leaves_a_leaderless_group_that_isnt_the_copys(self):
        # A group with the recorded PGID whose processes don't work in the
        # copy's scratch: the PID was reused, so it's someone else's group.
        leader = subprocess.Popen(
            ["sh", "-c", 'sleep 300 & echo $! > child'], cwd=self.repo, start_new_session=True,
        )
        leader.wait()
        with open(os.path.join(self.repo, "child")) as handle:
            child = int(handle.read())
        try:
            fake = self.rdir("old-long2")
            os.makedirs(fake)
            meta = {
                "id": "old-long2", "harness": "h", "started": time.time(), "ended": None,
                "supervisor": {"pid": leader.pid, "identity": "gone"},
                "copies": [{"index": 0, "pid": leader.pid, "identity": "gone", "findings": [], "baseline": [],
                            "log": "/dev/null", "started": time.time()}],
            }
            with open(os.path.join(fake, "meta.json"), "w") as handle:
                json.dump(meta, handle)
            self.assertFalse(lc.orphaned_group(meta["copies"][0], fake))
            result = self.cli("stop", "old-long2")
            self.assertEqual(result.returncode, 0, result.stderr)
            os.kill(child, 0)  # untouched
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(leader.pid, signal.SIGKILL)

    def test_a_run_id_is_claimed_once(self):
        rdir = self.rdir("h-long6")
        lc.claim_run(rdir, {"id": "h-long6"})
        with self.assertRaises(lc.LauncherError):
            lc.claim_run(rdir, {"id": "h-long6"})

    def test_a_start_killed_part_way_names_the_copies_it_started(self):
        # The supervisor dies (no cleanup) while starting the second copy:
        # meta.json already names the first, so stop can reach it.
        script = (
            "import importlib.util, os, sys\n"
            "spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])\n"
            "lc = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(lc)\n"
            "spawn = lc.Supervisor.spawn\n"
            "def dying(self, index, entry):\n"
            "    if index == 1:\n"
            "        os._exit(1)\n"
            "    spawn(self, index, entry)\n"
            "lc.Supervisor.spawn = dying\n"
            "sys.exit(lc.main(sys.argv[2:]))\n"
        )
        rdir = self.rdir("h-long7")
        os.makedirs(rdir)
        spec = {"id": "h-long7", "harness": "h", "time": 60,
                "copies": [{"command": ["sleep", "60"]}, {"command": ["sleep", "60"]}]}
        with open(os.path.join(rdir, "spec.json"), "w") as handle:
            json.dump(spec, handle)
        result = subprocess.run(
            [sys.executable, "-c", script, SCRIPT, "--workspace", self.ws, "start", "--spec",
             os.path.join(rdir, "spec.json")], capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        copies = self.meta("h-long7")["copies"]
        self.assertEqual([c["index"] for c in copies], [0])
        self.addCleanup(kill_group, copies[0]["pid"])
        os.killpg(copies[0]["pid"], 0)  # still running, unsupervised
        stopped = self.cli("stop", "h-long7")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertTrue(group_gone(copies[0]["pid"]))
        # A copy started in the instant before the supervisor died isn't on
        # record: stop says where to look.
        self.assertIn("start of h-long7 didn't complete", stopped.stderr)
        self.assertIn("SECLAB_RUN=h-long7/", stopped.stderr)

    def test_wait_ends_when_a_start_was_cut_short(self):
        # The supervisor died while starting, before or after its first
        # copy, and nothing it started runs: the run never gets "started".
        gone = {"index": 0, "pid": os.getpid(), "identity": "not-this-process", "findings": [], "baseline": [],
                "log": "/dev/null", "started": time.time()}
        for run_id, copies in (("h-long8", []), ("h-long9", [gone])):
            fake = self.rdir(run_id)
            os.makedirs(fake)
            meta = {"id": run_id, "harness": "h", "started": None, "ended": None, "copies": copies,
                    "supervisor": {"pid": os.getpid(), "identity": "not-this-process"}}
            with open(os.path.join(fake, "meta.json"), "w") as handle:
                json.dump(meta, handle)
            with open(os.path.join(fake, "spec.json"), "w") as handle:
                json.dump({"id": run_id, "harness": "h", "time": 60, "copies": [{"command": ["true"]}]}, handle)
            start = time.monotonic()
            code, out, err = self.run_main("wait", run_id, "--max", "5")
            self.assertEqual(code, 0, err)
            self.assertIn("%s: died, 0 findings" % run_id, out)
            self.assertLess(time.monotonic() - start, 4)

    def test_concurrent_starts_claim_the_id_and_count_for_the_cpu_check(self):
        # A fake git holds the first start between its checks and its
        # record, the window two starts used to share.
        gate = os.path.join(self.repo, "gate")
        fake = os.path.join(self.repo, "fakebin")
        os.makedirs(gate)
        os.makedirs(fake)
        with open(os.path.join(fake, "git"), "w") as handle:
            handle.write(
                '#!/bin/sh\nif mkdir "%s/first" 2>/dev/null; then\n  touch "%s/waiting"\n'
                '  while [ ! -e "%s/go" ]; do sleep 0.05; done\nfi\nexit 1\n' % (gate, gate, gate)
            )
        os.chmod(os.path.join(fake, "git"), 0o755)
        env = dict(os.environ, PATH=fake + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))
        first = subprocess.Popen(
            [sys.executable, SCRIPT, "--workspace", self.ws, "start", "--id", "a-h", "--harness", "h",
             "--copies", str(lc.cpu_count()), "--timeout", "30", "--", "true"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 20
            while not os.path.exists(os.path.join(gate, "waiting")) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(os.path.join(gate, "waiting")))
            code, _, err = self.run_main("start", "--id", "a-h", "--harness", "h", "--timeout", "30", "--", "true")
            self.assertEqual(code, lc.EXIT_USAGE, err)
            code, _, err = self.run_main("start", "--id", "b-h", "--harness", "h", "--timeout", "30", "--", "true")
            self.assertEqual(code, lc.EXIT_CPU, err)
            self.assertFalse(os.path.exists(os.path.join(self.rdir("b-h"), "meta.json")))
        finally:
            open(os.path.join(gate, "go"), "w").close()
            out, err = first.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, err)
        self.assertIn("a-h: finished", out)


if __name__ == "__main__":
    unittest.main()
