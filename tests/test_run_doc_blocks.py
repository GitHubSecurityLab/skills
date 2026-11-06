"""Tests for tools/run_doc_blocks.py: block extraction and placeholders.

The scenarios themselves run in CI's end-to-end jobs."""

import importlib.util
import os
import re
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "run_doc_blocks.py")


def load_module():
    spec = importlib.util.spec_from_file_location("run_doc_blocks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rdb = load_module()


class ExtractTests(unittest.TestCase):
    def doc(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        handle.write(textwrap.dedent(text))
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_marked_blocks_plain_and_indented(self):
        path = self.doc(
            """\
            Text.
            <!-- ci: one -->
            ```sh
            echo <a_b>
            ```
            1. Step:
               <!-- ci: two -->
               ```json
               {"x": "<c>"}
               ```
            <!-- ci: three -->
            ```javascript
            module.exports = { testMatch: ["<rootDir>/<d>/*.js"] };
            ```
            ```sh
            unmarked
            ```
            """
        )
        blocks = rdb.extract_blocks([path])
        self.assertEqual(sorted(blocks), ["one", "three", "two"])
        self.assertEqual(blocks["one"].text, "echo <a_b>\n")
        self.assertEqual(blocks["two"].kind, "json")
        self.assertEqual(blocks["two"].text, '{"x": "<c>"}\n')
        self.assertEqual(blocks["one"].placeholders(), {"a_b"})
        self.assertEqual(blocks["three"].kind, "javascript")
        self.assertEqual(blocks["three"].placeholders(), {"d"})

    def test_language_skill_blocks_need_a_marker(self):
        path = self.doc(
            """\
            <!-- ci: one -->
            ```sh
            a
            ```
            <!-- ci-skip: needs Bazel -->
            ```sh
            b
            ```
            ```sh
            c
            ```
            <!-- ci-skip: -->
            ```sh
            d
            ```
            ```json
            {}
            ```
            """
        )
        errors = rdb.unmarked_blocks([path])
        self.assertEqual(len(errors), 2, errors)
        self.assertIn(":9: ```sh block without", errors[0])
        self.assertIn(":12: ci-skip marker without a reason", errors[1])

    def test_every_language_skill_block_is_marked(self):
        self.assertEqual(rdb.unmarked_blocks(rdb.language_doc_paths()), [])

    def test_language_files_by_their_path_in_the_repository(self):
        # A checkout's own path may contain a language skill's name (a worktree called fuzz-js).
        with tempfile.TemporaryDirectory() as tmp:
            skills = os.path.join(tmp, "fuzz-js", "repo", "skills")
            paths = [os.path.join(skills, "fuzzing", "fuzz-campaign", "SKILL.md"),
                     os.path.join(skills, "fuzzing", "fuzz-js", "references", "run.md")]
            for path in paths:
                os.makedirs(os.path.dirname(path))
                open(path, "w").close()
            self.assertEqual(rdb.language_doc_paths(skills), paths[1:])

    def test_marker_without_block_and_duplicates_are_errors(self):
        with self.assertRaises(rdb.DocError):
            rdb.extract_blocks([self.doc("<!-- ci: x -->\ntext\n")])
        with self.assertRaises(rdb.DocError):
            rdb.extract_blocks([self.doc("<!-- ci: x -->\n```sh\na\n```\n<!-- ci: x -->\n```sh\nb\n```\n")])

    def test_fill_needs_every_placeholder(self):
        block = rdb.Block("b", "sh", 'cmd <run_s> "$F" 2>&1 < in <<"EOF"\n', "/x.md", 1)
        self.assertEqual(rdb.fill(block, {"run_s": 30}), 'cmd 30 "$F" 2>&1 < in <<"EOF"\n')
        with self.assertRaises(rdb.DocError):
            rdb.fill(block, {})


class WorkdirTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop("DOCBLOCKS_WORKDIR", None)

    def tearDown(self):
        os.environ.pop("DOCBLOCKS_WORKDIR", None)
        if self.saved is not None:
            os.environ["DOCBLOCKS_WORKDIR"] = self.saved

    def test_random_by_default(self):
        first, second = rdb.scenario_workdir("x"), rdb.scenario_workdir("x")
        try:
            self.assertNotEqual(first, second)
        finally:
            os.rmdir(first)
            os.rmdir(second)

    def test_fixed_and_emptied_with_the_variable(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ["DOCBLOCKS_WORKDIR"] = root
            work = rdb.scenario_workdir("x")
            self.assertEqual(work, os.path.join(root, "x"))
            open(os.path.join(work, "old"), "w").close()
            self.assertEqual(rdb.scenario_workdir("x"), work)
            self.assertEqual(os.listdir(work), [])


class ScenarioRunTests(unittest.TestCase):
    def register(self, name, uses, func, systems=("Linux", "Darwin")):
        rdb.SCENARIOS[name] = rdb.Scenario(name, "c", uses, func, systems)
        self.addCleanup(rdb.SCENARIOS.pop, name)

    def test_a_scenario_runs_only_on_its_systems(self):
        ran = []
        self.register("ci-elsewhere", ["c-ub"], lambda ctx: ran.append(1), systems=("Plan9",))
        with self.assertRaisesRegex(rdb.DocError, "runs on Plan9 only"):
            rdb.run_scenario("ci-elsewhere", keep=False, verbose=False)
        self.assertEqual(ran, [])

    def test_a_listed_block_must_run(self):
        self.register("ci-unused", ["c-ub", "c-env"], lambda ctx: ctx.env_block("c-ub"))
        with self.assertRaisesRegex(rdb.DocError, "lists blocks it didn't run: c-env"):
            rdb.run_scenario("ci-unused", keep=False, verbose=False)


class RepositoryTests(unittest.TestCase):
    def test_every_scenario_block_is_marked(self):
        blocks = rdb.extract_blocks(rdb.doc_paths())
        for scen in rdb.SCENARIOS.values():
            for name in scen.uses:
                self.assertIn(name, blocks, "scenario %s" % scen.name)

    def test_ci_templates_dont_publish_findings_by_default(self):
        # Workflow runs of a public repository are public: discovery runs are the maintainers' choice.
        with open(os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "references", "ci.md")) as handle:
            text = handle.read()
        templates = [t for t in text.split("```yaml\n")[1:]]
        self.assertEqual(len(templates), 3)
        for template in templates:
            body = template.split("```", 1)[0]
            self.assertNotRegex(body, r"(?m)^\s*schedule:", "a schedule is opt-in, commented out")
            uploads = body.count("actions/upload-artifact@")
            self.assertEqual(uploads, body.count("retention-days:"), "every upload expires soon")
        self.assertIn("Base64:", text)  # the logs show small crashing inputs too

    def test_js_ci_template_replays_through_jazzer(self):
        # A node:test replay has no bug detectors: the blocking replay runs Jazzer.js, after a TypeScript build.
        _env, steps = rdb.ci_template("JS/TS with Jazzer.js (CLI targets)")
        names = list(steps)
        self.assertEqual([n for n in names if n in ("Build", "Replay the corpus", "Fuzz")], ["Build", "Replay the corpus", "Fuzz"])
        self.assertRegex(steps["Replay the corpus"], r"npx jazzer .* --mode=regression")

    def test_java_ci_template_has_no_corpus_per_class(self):
        # It would mix typed methods' inputs; the driver adds the inputs directory mvn test replays itself.
        _env, steps = rdb.ci_template("Java with Jazzer (JUnit `@FuzzTest`)")
        self.assertNotIn("fuzz/corpus", steps["Fuzz"])

    def test_ci_runs_every_scenario_with_a_time_limit(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml")) as handle:
            text = handle.read()
        jobs = re.split(r"(?m)^  (?=[a-z0-9-]+:\n)", text.split("\njobs:\n", 1)[1])
        e2e = {job.split(":", 1)[0]: job for job in jobs if job.startswith("e2e-")}
        for name, job in e2e.items():
            self.assertIn("timeout-minutes:", job, name)
        for name in rdb.SCENARIOS:
            self.assertRegex(text, r"run_doc_blocks\.py run %s\n" % re.escape(name))
        for name in ("e2e-c-libfuzzer", "e2e-java", "e2e-js"):
            self.assertIn("macos-", e2e[name], name)

    def test_c_variant_flags_from_build_md(self):
        # The scenarios build with build.md's Variants table, and the CI template's flags are its libfuzzer row.
        compile_flags, link_flags = rdb.variant_flags("libfuzzer")
        self.assertIn("-fsanitize=fuzzer-no-link,address,$UB", compile_flags)
        self.assertEqual(link_flags, "-fsanitize=fuzzer,address,$UB")
        self.assertIn("-fcoverage-mapping", rdb.variant_flags("coverage")[0])
        with open(os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "references", "ci.md")) as handle:
            self.assertEqual(re.findall(r'^\s*FLAGS="([^"]*)"$', handle.read(), re.M), [compile_flags])

    def test_one_ubsan_list(self):
        # The C/C++ CI template carries build.md's list (fuzz-c-cpp), written out.
        blocks = rdb.extract_blocks(rdb.doc_paths())
        ub = re.search(r"^UB=(\S+)$", blocks["c-ub"].text, re.M).group(1)
        harden = re.search(r'^HARDEN="([^"]*)"$', blocks["c-ub"].text, re.M).group(1)
        with open(os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "references", "ci.md")) as handle:
            text = handle.read()
        self.assertEqual(re.findall(r"^\s*UB=(\S+)$", text, re.M), [ub])
        self.assertEqual(re.findall(r'^\s*HARDEN="([^"]*)"$', text, re.M), [harden])
        for path in rdb.doc_paths():
            with open(path) as handle:
                self.assertNotRegex(handle.read(), r"fsanitize=[a-z,-]*\bundefined\b", path)


if __name__ == "__main__":
    unittest.main()
