"""Tests for skills/fuzzing/fuzz-campaign/scripts/reach.py.

A small Python program stands in for a coverage build: it "replays" the
chosen inputs and writes an lcov file for src/t.c, where
- line 10 runs for an input containing b"X";
- line 20 runs for an input containing b"B" only after one containing b"A"
  ran in the same process (state leaking between inputs);
- an input containing b"C" crashes (copied to the crash directory, exit 1).
"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "scripts", "reach.py")


def load_module():
    spec = importlib.util.spec_from_file_location("reach", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reach = load_module()
lc = reach.launcher()
lc.POLL_SECONDS = 0.02
lc.STOP_GRACE_SECONDS = 2.0

FAKE = textwrap.dedent(
    """\
    import os, re, shutil, sys
    mode, out = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    crash_dir = None
    if "--crash-dir" in rest:
        i = rest.index("--crash-dir"); crash_dir = rest[i + 1]; rest = rest[:i] + rest[i + 2:]
    if mode == "dir":
        files = [os.path.join(rest[0], f) for f in sorted(os.listdir(rest[0]))]
    elif mode == "envdir":  # FuzzTest: FUZZTEST_REPLAY=<dir>
        d = os.environ["FAKE_REPLAY"]
        files = [os.path.join(d, f) for f in sorted(os.listdir(d))]
    elif mode == "files":
        files = rest
    else:  # names: a Jest-style pattern, then the corpus directories
        pattern = re.compile("^(" + rest[0] + ")$")
        files = sorted({os.path.join(d, f) for d in rest[1:] for f in os.listdir(d) if pattern.match(f)},
                       key=os.path.basename)
    with open(os.environ.get("FAKE_SEEN") or os.path.join(out, "seen.txt"), "a") as log:
        log.write("\\n".join(files) + "\\n")
    if os.environ.get("FAKE_ENV_OUT"):
        open(os.environ["FAKE_ENV_OUT"], "w").close()
        if os.environ.get("FAKE_SEEN"):
            with open(os.environ["FAKE_SEEN"], "a") as log:
                log.write("env_out: " + os.environ["FAKE_ENV_OUT"] + "\\n")
    seen_a = hits10 = hits20 = 0
    for path in files:
        data = open(path, "rb").read()
        if b"C" in data:
            if crash_dir:
                shutil.copy(path, os.path.join(crash_dir, "crash-%d" % len(os.listdir(crash_dir))))
            if not os.environ.get("FAKE_KEEP_GOING"):
                sys.exit(1)
            continue
        if b"X" in data:
            hits10 += 1
        if b"B" in data and seen_a:
            hits20 += 1
        if b"A" in data:
            seen_a = 1
    lazy = os.environ.get("FAKE_LAZY") and not hits10
    with open(os.path.join(out, "cov.lcov"), "a") as lcov:  # appends: stale data would show
        if not lazy:
            # line 30: a switch case never entered, whose "not this case" branch runs
            lcov.write("SF:%s/src/t.c\\nDA:5,1\\nDA:10,%d\\nDA:20,%d\\nDA:30,0\\nBRDA:30,0,0,0\\n"
                       "BRDA:30,0,1,%d\\nend_of_record\\n" % (os.environ["FAKE_ROOT"], hits10, hits20, len(files)))
        else:
            lcov.write("SF:%s/src/other.c\\nDA:1,1\\nend_of_record\\n" % os.environ["FAKE_ROOT"])
    if os.environ.get("FAKE_STOP") and crash_dir:  # the run is stopped (Ctrl-C); the replay exits 0
        import signal, time
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        open(os.path.join(os.path.dirname(os.path.dirname(crash_dir)), "stop"), "w").close()
        time.sleep(30)
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
    """
)


class ReachTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = os.path.realpath(self.tmp.name)
        self.repo = os.path.join(base, "repo")
        self.ws = os.path.join(self.repo, ".seclab", "fuzz")
        self.corpus = os.path.join(self.ws, "corpus", "h")
        os.makedirs(self.corpus)
        self.fake = os.path.join(base, "fake.py")
        with open(self.fake, "w") as handle:
            handle.write(FAKE)
        # The launcher gives each step an empty environment: settings go through --env.
        self.env = {"FAKE_ROOT": self.repo}

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data, corpus=None):
        with open(os.path.join(corpus or self.corpus, name), "wb") as handle:
            handle.write(data)

    def run_reach(self, *extra, mode="dir", line=10, corpora=None):
        corpora = corpora or [self.corpus]
        replay = {
            "dir": "%s %s dir {out} {dir}" % (sys.executable, self.fake),
            "files": "%s %s files {out} {files}" % (sys.executable, self.fake),
            "names": "%s %s names {out} {names} %s" % (sys.executable, self.fake, " ".join(corpora)),
            "envdir": "%s %s envdir {out}" % (sys.executable, self.fake),
        }[mode]
        if mode == "envdir":
            self.env["FAKE_REPLAY"] = "{dir}"
        argv = ["--workspace", self.ws, "--harness", "h", "--file", "src/t.c", "--line", str(line),
                "--replay", replay + " --crash-dir {run}/artifacts/copy-{copy}",
                "--export", "cp {out}/cov.lcov {lcov}", "--timeout", "30"]
        for corpus in corpora:
            argv += ["--corpus", corpus]
        for key, value in self.env.items():
            argv += ["--env", "%s=%s" % (key, value)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = reach.main(argv + list(extra))
        return code, out.getvalue(), err.getvalue()


class SearchTests(ReachTestCase):
    def test_smallest_reaching_inputs_and_continuing_after_each(self):
        for name, data in (("p", b"X"), ("q", b"aX"), ("r", b"bbX"), ("s", b"cccc"), ("t", b"ddddX")):
            self.put(name, data)
        code, out, err = self.run_reach()
        self.assertEqual(code, 0, err)
        reached = [line.split()[1] for line in out.splitlines() if line.startswith("reached:")]
        self.assertEqual([os.path.basename(p) for p in reached], ["p", "q", "r"])

    def test_not_reached(self):
        self.put("a", b"aa")
        code, out, _ = self.run_reach()
        self.assertEqual(code, reach.EXIT_NOT_REACHED)
        self.assertIn("not reached", out)

    def test_zero_byte_inputs_are_left_out(self):
        self.put("empty", b"")
        self.put("x", b"X")
        seen_path = os.path.join(self.tmp.name, "seen.txt")
        self.env["FAKE_SEEN"] = seen_path
        code, _, _ = self.run_reach(mode="files")
        self.assertEqual(code, 0)
        with open(seen_path) as handle:
            self.assertNotIn("empty", handle.read())

    def test_valid_steps_leave_only_their_log(self):
        for name, data in (("p", b"X"), ("q", b"aa"), ("r", b"bbb")):
            self.put(name, data)
        code, _, err = self.run_reach()
        self.assertEqual(code, 0, err)
        left = []
        for root, dirs, files in os.walk(os.path.join(self.ws, "runs")):
            left += [d for d in dirs if d in ("inputs", "out")] + [f for f in files if f == "coverage.lcov"]
        self.assertEqual(left, [])

    def test_relative_corpus_with_files(self):
        self.put("x", b"X")
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            code, out, err = self.run_reach(mode="files", corpora=[os.path.relpath(self.corpus, self.repo)])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, err)
        self.assertIn("reached:", out)

    def test_failed_confirmation_reports_state_from_earlier_inputs(self):
        self.put("1", b"A")
        self.put("2", b"B")
        code, out, _ = self.run_reach(line=20)
        self.assertEqual(code, reach.EXIT_NOT_REACHED)
        self.assertIn("reached only after other inputs", out)

    def test_state_across_both_halves_names_no_input(self):
        # Line 20 needs A, then B, in one process. With A and B in different
        # halves, neither half reaches it: no single input is to blame.
        for name, data in (("1", b"A"), ("2", b"a"), ("3", b"B"), ("4", b"d")):
            self.put(name, data)
        code, out, _ = self.run_reach(line=20)
        self.assertEqual(code, reach.EXIT_NOT_REACHED)
        self.assertIn("reached only after other inputs: no single input of these 4 reaches", out)
        self.assertNotIn(os.path.join(self.corpus, "4"), out)

    def test_batches_stop_at_the_first_that_reaches(self):
        self.put("aa", b"X")
        for i in range(30):
            self.put("z%02d" % i, b"zzz")
        old = reach.ARG_LIMIT
        reach.ARG_LIMIT = 40  # a few names per batch
        try:
            code, out, err = self.run_reach("--max", "1", mode="names")
        finally:
            reach.ARG_LIMIT = old
        self.assertEqual(code, 0, err)
        # The first batch reaches: no other batch runs in that step (4 batches
        # in the first step, 2 in the next, then 1 each: 10 steps without it).
        self.assertIn("reached: %s" % os.path.join(self.corpus, "aa"), out)
        self.assertLess(int(out.split(" step")[0].split()[-1]), 8, out)

    def test_fresh_coverage_per_step(self):
        # The fake appends to its lcov; a reused {out} would make every later
        # subset look like it reaches the line.
        for name, data in (("a", b"X"), ("b", b"bb"), ("c", b"ccc"), ("d", b"dddd")):
            self.put(name, data)
        code, out, _ = self.run_reach("--max", "3")
        self.assertEqual(code, 0)
        self.assertEqual(sum(1 for line in out.splitlines() if line.startswith("reached:")), 1)


class ValidityTests(ReachTestCase):
    def test_ok_exit(self):
        self.put("x", b"X")
        self.env["FAKE_EXIT"] = "1"
        code, _, err = self.run_reach()
        self.assertEqual(code, reach.EXIT_INVALID)
        self.assertIn("invalid", err)
        code, _, err = self.run_reach("--ok-exit", "0,1")
        self.assertEqual(code, 0, err)

    def test_env_values_get_out(self):
        self.put("x", b"X")
        self.env["FAKE_ENV_OUT"] = "{out}/marker"
        seen_path = os.path.join(self.tmp.name, "seen.txt")
        self.env["FAKE_SEEN"] = seen_path
        code, _, err = self.run_reach()
        self.assertEqual(code, 0, err)
        with open(seen_path) as handle:
            markers = [line.split(": ", 1)[1] for line in handle.read().splitlines() if line.startswith("env_out: ")]
        self.assertTrue(markers)
        runs = os.path.join(self.ws, "runs") + os.sep
        self.assertTrue(all(m.startswith(runs) and m.endswith("/out/marker") for m in markers), markers)

    def test_dir_in_an_env_value(self):
        # FuzzTest replays the directory named by FUZZTEST_REPLAY={dir}.
        self.put("a", b"a")
        self.put("x", b"X")
        code, out, err = self.run_reach(mode="envdir")
        self.assertEqual(code, 0, err)
        self.assertIn("reached: ", out)

    def test_a_crashing_input_is_named(self):
        self.put("x", b"X")
        self.put("y", b"yyC")
        code, _, err = self.run_reach("--ok-exit", "0")
        self.assertEqual(code, reach.EXIT_INVALID)
        self.assertIn("the input %s crashed" % os.path.join(self.corpus, "y"), err)

    def test_a_crash_in_a_valid_step_is_reported_once(self):
        # libFuzzer's -merge=1 records a crashing input and goes on, exiting 0.
        for name, data in (("c", b"C"), ("x", b"aaX"), ("y", b"bbbX")):
            self.put(name, data)
        self.env["FAKE_KEEP_GOING"] = "1"
        code, out, err = self.run_reach()
        self.assertEqual(code, 0, err)
        findings = [line for line in out.splitlines() if line.startswith("finding: ")]
        self.assertEqual(len(findings), 1, out)
        self.assertIn("the input %s crashed" % os.path.join(self.corpus, "c"), findings[0])
        self.assertTrue(os.path.exists(findings[0].split()[1].rstrip(":")), findings[0])
        self.assertIn("reached: %s" % os.path.join(self.corpus, "x"), out)

    def test_an_allowed_failing_exit_is_reported(self):
        # Jest exits 1 when an input throws, with a complete report.
        self.put("x", b"X")
        self.env["FAKE_EXIT"] = "1"
        code, out, err = self.run_reach("--ok-exit", "0,1")
        self.assertEqual(code, 0, err)
        notes = [line for line in out.splitlines() if "exited 1" in line]
        self.assertEqual(len(notes), 1, out)
        self.assertIn("an input failed", notes[0])

    def test_a_stopped_step_ends_the_search(self):
        # Ctrl-C stops the step's run (the launcher runs in-process and
        # handles INT); a replay that exits 0 on TERM must not go on to the next step.
        for name in ("x", "y"):
            self.put(name, b"X")
        self.env["FAKE_STOP"] = "1"
        code, out, err = self.run_reach()
        self.assertEqual(code, reach.EXIT_INVALID, out)
        self.assertIn("was stopped", err)
        self.assertNotIn("reached:", out)

    def test_an_export_that_hangs_is_an_error(self):
        self.put("x", b"X")
        argv = ["--export", "sleep 30", "--timeout", "3"]
        code, _, err = self.run_reach(*argv)
        self.assertEqual(code, reach.EXIT_INVALID)
        self.assertIn("the export took longer than 3 s", err)

    def test_wrong_file_is_an_error_not_unreached(self):
        self.put("x", b"X")
        argv_file = ("--file", "src/nope.c")
        code, _, err = self.run_reach(*argv_file)
        self.assertEqual(code, reach.EXIT_INVALID)
        self.assertIn("no step's report has a record for src/nope.c", err)

    def test_a_line_without_a_record_is_an_error_not_unreached(self):
        # A comment, a declaration or a typo: the coverage can't tell.
        self.put("x", b"X")
        code, out, err = self.run_reach(line=7)
        self.assertEqual(code, reach.EXIT_INVALID, out)
        self.assertIn("src/t.c:7 has no line record", err)
        self.assertIn("instrumented lines nearby: 5, 10, 20", err)

    def test_a_case_line_counts_by_its_line_not_its_branches(self):
        self.put("x", b"X")
        code, out, err = self.run_reach(line=30)
        self.assertEqual(code, reach.EXIT_NOT_REACHED, err)
        self.assertIn("not reached", out)

    def test_a_file_missing_from_a_subset_report_counts_as_zero(self):
        # Istanbul lists only loaded files; the module loads only when needed.
        for name, data in (("a", b"aa"), ("b", b"bbb"), ("x", b"xxxxX")):
            self.put(name, data)
        self.env["FAKE_LAZY"] = "1"
        code, out, err = self.run_reach()
        self.assertEqual(code, 0, err)
        self.assertIn("reached: %s" % os.path.join(self.corpus, "x"), out)

    def test_a_first_batch_that_never_loads_the_module(self):
        # The reaching input is the largest, so it lands in the last batch.
        for i in range(30):
            self.put("z%02d" % i, b"zz")
        self.put("big", b"zzzzzzzzX")
        self.env["FAKE_LAZY"] = "1"
        old = reach.ARG_LIMIT
        reach.ARG_LIMIT = 40  # a few names per batch
        try:
            code, out, err = self.run_reach(mode="names")
        finally:
            reach.ARG_LIMIT = old
        self.assertEqual(code, 0, err)
        self.assertIn("reached: %s" % os.path.join(self.corpus, "big"), out)

    def test_a_file_no_step_ever_lists_is_still_an_error(self):
        self.put("x", b"zz")
        self.env["FAKE_LAZY"] = "1"
        code, _, err = self.run_reach()
        self.assertEqual(code, reach.EXIT_INVALID)
        self.assertIn("no step's report has a record for src/t.c", err)


class JestNameTests(ReachTestCase):
    def setUp(self):
        super().setUp()
        self.regression = os.path.join(self.repo, "src", "t.fuzz", "d", "t")
        os.makedirs(self.regression)

    def test_same_name_same_content_is_kept_once(self):
        self.put("abc", b"X")
        self.put("abc", b"X", self.regression)
        code, out, err = self.run_reach(mode="names", corpora=[self.corpus, self.regression])
        self.assertEqual(code, 0, err)
        self.assertIn("1 candidates", out)

    def test_same_name_different_content_fails_and_suggests_a_probe(self):
        self.put("abc", b"X")
        self.put("abc", b"Y", self.regression)
        code, _, err = self.run_reach(mode="names", corpora=[self.corpus, self.regression])
        self.assertEqual(code, reach.EXIT_INVALID)
        self.assertIn("probe", err)

    def test_names_are_escaped(self):
        self.put("a.b(1)", b"X")
        self.put("a-b-1-", b"X")
        code, out, err = self.run_reach(mode="names")
        self.assertEqual(code, 0, err)
        self.assertIn("reached: %s" % os.path.join(self.corpus, "a-b-1-"), out)
        self.assertEqual(reach.js_escape("a.b(1)"), "a\\.b\\(1\\)")

    def test_batches_with_the_only_reaching_input_in_the_first_batch(self):
        self.put("aa", b"X")
        for i in range(30):
            self.put("z%02d" % i, b"zzz")
        old = reach.ARG_LIMIT
        reach.ARG_LIMIT = 40  # a few names per batch
        try:
            code, out, err = self.run_reach(mode="names")
        finally:
            reach.ARG_LIMIT = old
        self.assertEqual(code, 0, err)
        self.assertIn("reached: %s" % os.path.join(self.corpus, "aa"), out)


if __name__ == "__main__":
    unittest.main()
