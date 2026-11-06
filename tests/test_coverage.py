"""Tests for skills/fuzzing/fuzz-campaign/scripts/coverage.py.

istanbul-class.lcov is real output of Jazzer.js 4.0.0 (`--mode=regression
--coverage --coverageReporters=lcov`, Node 26, macOS arm64) for a CLI target
calling proj/src/reader.js, whose class method and callbacks Istanbul names
"(anonymous_<n>)". istanbul-helper-class.lcov is the same, for a target
calling proj/src/records.js: a named function, then a class whose methods
(header() never called) and callback are "(anonymous_<n>)".
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "scripts", "coverage.py")
FIX = os.path.join(ROOT, "tests", "fixtures", "coverage")


def load_module():
    spec = importlib.util.spec_from_file_location("coverage_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


cov = load_module()


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cov.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def fx(name):
    return os.path.join(FIX, name)


class ParseTests(unittest.TestCase):
    def test_llvm_lcov_counts(self):
        files = cov.parse_lcov(fx("llvm-round1.lcov"))
        parse = files["/work/proj/src/parse.c"]
        self.assertEqual(parse.branch_counts(), (10, 16))
        self.assertEqual(parse.line_counts(), (15, 17))
        self.assertEqual(parse.function_counts(), (2, 2))
        self.assertEqual(parse.functions["parse_body"][0], 15)

    def test_istanbul_unevaluated_branches(self):
        files = cov.parse_lcov(fx("istanbul.lcov"))
        js = files["/work/js/lib/parse.js"]
        self.assertEqual(js.branch_counts(), (2, 4))
        self.assertIsNone(js.branches[(13, "1", "0")])
        self.assertEqual(js.function_counts(), (1, 2))

    def test_lcov2_function_formats(self):
        text = (
            "SF:/a/x.c\n"
            "FN:3,9,alpha\n"
            "FNDA:2,alpha\n"
            "FNL:0,12,20\n"
            "FNA:0,0,beta\n"
            "DA:3,2\n"
            "end_of_record\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".lcov", delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            files = cov.parse_lcov(path)
        finally:
            os.unlink(path)
        fns = files["/a/x.c"].functions
        self.assertEqual(fns["alpha"], [3, 9, 2])
        self.assertEqual(fns["beta"], [12, 20, 0])

    def test_fallback_totals_without_detail(self):
        text = "SF:/a/y.c\nBRF:10\nBRH:4\nLF:20\nLH:5\nend_of_record\n"
        with tempfile.NamedTemporaryFile("w", suffix=".lcov", delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            files = cov.parse_lcov(path)
        finally:
            os.unlink(path)
        self.assertEqual(files["/a/y.c"].branch_counts(), (4, 10))
        self.assertEqual(files["/a/y.c"].line_counts(), (5, 20))


class MergeTests(unittest.TestCase):
    def test_merge_sums_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "merged.lcov")
            code, _, _ = run("merge", fx("llvm-round1.lcov"), fx("llvm-round1.lcov"), "-o", out)
            self.assertEqual(code, 0)
            merged = cov.parse_lcov(out)["/work/proj/src/parse.c"]
            single = cov.parse_lcov(fx("llvm-round1.lcov"))["/work/proj/src/parse.c"]
            self.assertEqual(merged.lines[5], 2 * single.lines[5])
            self.assertEqual(merged.branch_counts(), single.branch_counts())
            self.assertEqual(merged.functions["parse_body"][2], 2)

    def test_merge_keeps_unevaluated_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "merged.lcov")
            run("merge", fx("istanbul.lcov"), "-o", out)
            with open(out) as handle:
                self.assertIn("BRDA:13,1,0,-", handle.read())


class SfBaseTests(unittest.TestCase):
    RAW = (
        "SF:../../../../../../src/a.js\nDA:1,1\nDA:2,0\nBRDA:2,0,0,1\nBRDA:2,0,1,0\nend_of_record\n"
        "SF:../../../../../../src/./a.js\nDA:2,3\nend_of_record\n"
        "SF:/abs/elsewhere/b.js\nDA:1,1\nend_of_record\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # The unresolved temp path: on macOS it's a symlink (/var -> /private/var),
        # while Istanbul's relative paths start from the real working directory.
        self.repo = os.path.join(self.tmp.name, "repo")
        self.scratch = os.path.join(self.repo, ".seclab", "fuzz", "runs", "cov-h-1", "copy-0", "work")
        os.makedirs(os.path.join(self.repo, "src"))
        self.raw = os.path.join(self.repo, ".seclab", "fuzz", "runs", "cov-h-1", "lcov.info")
        os.makedirs(os.path.dirname(self.raw))
        with open(self.raw, "w") as handle:
            handle.write(self.RAW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_normalizes_paths_after_scratch_is_deleted(self):
        self.assertFalse(os.path.exists(self.scratch))
        out = os.path.join(self.tmp.name, "r1.lcov")
        code, _, err = run("merge", self.raw, "--sf-base", self.scratch, "--root", self.repo, "-o", out)
        self.assertEqual(code, 0, err)
        files = cov.parse_lcov(out)
        self.assertEqual(sorted(files), ["/abs/elsewhere/b.js", "src/a.js"])
        self.assertEqual(files["src/a.js"].lines, {1: 1, 2: 3})
        self.assertEqual(files["src/a.js"].branch_counts(), (1, 2))

    def test_other_subcommands_read_raw_files_with_sf_base(self):
        scope = os.path.join(self.tmp.name, "scope")
        with open(scope, "w") as handle:
            handle.write("src/a.js\n")
        code, out, err = run(
            "summary", self.raw, "--sf-base", self.scratch, "--root", os.path.realpath(self.repo), "--scope", scope,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("src/a.js", out)
        self.assertNotIn("incomplete", out)

    def test_without_sf_base_relative_paths_stay(self):
        out = os.path.join(self.tmp.name, "r1.lcov")
        run("merge", self.raw, "-o", out)
        self.assertIn("../../../../../../src/a.js", cov.parse_lcov(out))


class SummaryTests(unittest.TestCase):
    def test_summary_with_prev_and_scope(self):
        code, out, _ = run(
            "summary",
            fx("llvm-round2.lcov"),
            "--prev",
            fx("llvm-round1.lcov"),
            "--root",
            "/work/proj",
            "--scope",
            fx("scope.txt"),
        )
        self.assertEqual(code, 0)
        self.assertIn("1 files in scope; incomplete", out)
        self.assertIn("**Incomplete:** 1 scope entry has no coverage data", out)
        self.assertIn("**Branches:** 14/16 (87.50%)", out)
        self.assertIn("change +25.00 points (+4 covered)", out)
        self.assertIn("src/missing.c", out)
        self.assertNotIn("harness.c", out)

    def test_scope_entries_match_from_the_root_not_by_suffix(self):
        text = (
            "SF:/w/src/parse.c\nDA:1,1\nend_of_record\n"
            "SF:/w/tests/src/parse.c\nDA:1,0\nend_of_record\n"
            "SF:/w/tests/src/util.c\nDA:1,0\nend_of_record\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            lcov = os.path.join(tmp, "a.lcov")
            with open(lcov, "w") as handle:
                handle.write(text)
            for entry in ("src/parse.c", "./src/parse.c", "src/"):
                scope = os.path.join(tmp, "scope.txt")
                with open(scope, "w") as handle:
                    handle.write(entry + "\n")
                _, out, _ = run("summary", lcov, "--root", "/w", "--scope", scope)
                self.assertIn("## Coverage summary (1 files in scope)", out, entry)
                self.assertNotIn("tests/src", out, entry)

    def test_complete_scope_has_no_incomplete_flag(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("src/parse.c\n")
            path = handle.name
        try:
            _, out, err = run("summary", fx("llvm-round1.lcov"), "--root", "/work/proj", "--scope", path)
        finally:
            os.unlink(path)
        self.assertNotIn("incomplete", out.lower())
        self.assertEqual(err, "")

    def test_summary_without_scope_lists_all_files(self):
        _, out, _ = run("summary", fx("llvm-round1.lcov"), "--root", "/work/proj")
        self.assertIn("| src/parse.c | 10/16 (62.50%)", out)
        self.assertIn("| src/harness.c | 4/4 (100.00%)", out)

    def test_directory_scope_entry(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("src/\n")
            path = handle.name
        try:
            _, out, _ = run("summary", fx("llvm-round1.lcov"), "--root", "/work/proj", "--scope", path)
        finally:
            os.unlink(path)
        self.assertIn("2 files in scope", out)


    def test_several_scope_files_combine(self):
        # The campaign's combined scope: one --scope per target.
        with tempfile.TemporaryDirectory() as tmp:
            scopes = []
            for name, entry in (("a.scope", "src/parse.c"), ("b.scope", "src/harness.c")):
                scopes += ["--scope", os.path.join(tmp, name)]
                with open(scopes[-1], "w") as handle:
                    handle.write(entry + "\n")
            _, out, _ = run("summary", fx("llvm-round1.lcov"), "--root", "/work/proj", *scopes)
        self.assertIn("## Coverage summary (2 files in scope)", out)


class GapsTests(unittest.TestCase):
    def test_gaps_ranked_by_untaken_branches(self):
        _, out, _ = run("gaps", fx("llvm-round1.lcov"), "--root", "/work/proj")
        body = out.splitlines()
        rows = [line for line in body if line.startswith("| parse_")]
        self.assertTrue(rows[0].startswith("| parse_body"))
        self.assertIn("4/8 (lines 16, 18-20)", rows[0])
        self.assertIn("| 19-20 |", rows[0])
        self.assertTrue(rows[1].startswith("| parse_header"))


class BlockedBranchTests(unittest.TestCase):
    def lcov(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".lcov", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_blocked_branches_first_with_guard_and_lines_after(self):
        _, out, _ = run("gaps", fx("llvm-round1.lcov"), "--root", "/work/proj", "--source-root", fx("proj"))
        blocked = out.split("## Remaining gaps")[0]
        rows = [line for line in blocked.splitlines() if line.startswith("| src/")]
        self.assertEqual(len(rows), 6)
        # Untaken cases first, each with its own line; the taken case 'A' owns none of them.
        self.assertTrue(rows[0].startswith("| src/parse.c:19 | parse_body | `case 'B'"), rows[0])
        self.assertTrue(rows[0].endswith("| 1/2 | 1 (19) | - |"), rows[0])
        self.assertTrue([r for r in rows if "case 'A'" in r][0].endswith("| 0 | - |"))
        self.assertIn("## Remaining gaps (2 functions", out)

    def test_istanbul_zeros_need_a_taken_sibling(self):
        path = self.lcov(
            "SF:lib/a.js\nDA:1,3\nDA:2,0\nDA:3,0\nDA:4,3\n"
            "BRDA:1,0,0,3\nBRDA:1,0,1,0\n"  # blocked: the other arm ran
            "BRDA:3,1,0,0\nBRDA:3,1,1,0\n"  # never reached: not blocked
            "end_of_record\n"
        )
        _, out, _ = run("gaps", path)
        self.assertIn("## Blocked branches (1)", out)
        self.assertIn("| lib/a.js:1 | - | - | 1/2 | 2 (2-3) |", out)

    def test_jacoco_reports_blocked_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.lcov")
            run("from-jacoco", fx("jacoco.xml"), "-o", path)
            _, out, _ = run("gaps", path)
        self.assertIn("## Blocked lines (", out)
        self.assertIn("branch counts per line only", out)


def write_lcov(tmp, source, functions, lines, branches):
    """An lcov file for `source` (written to tmp/src/p.c) in LLVM's format:
    functions [(line, name, hits)], lines {line: hits}, branches
    {line: [hits, ...]} (one branch point per line; None is llvm's "-", never evaluated)."""
    os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
    with open(os.path.join(tmp, "src", "p.c"), "w") as handle:
        handle.write(source)
    out = ["SF:%s" % os.path.join(tmp, "src", "p.c")]
    out += ["FN:%d,%s" % (line, name) for line, name, _ in functions]
    out += ["FNDA:%d,%s" % (hits, name) for _, name, hits in functions]
    for line, hits in sorted(branches.items()):
        out += ["BRDA:%d,0,%d,%s" % (line, i, "-" if h is None else h) for i, h in enumerate(hits)]
    out += ["DA:%d,%d" % item for item in sorted(lines.items())]
    out.append("end_of_record")
    path = os.path.join(tmp, "p.lcov")
    with open(path, "w") as handle:
        handle.write("\n".join(out) + "\n")
    return path


def blocked_rows(out):
    section = out.split("## Blocked branches")[1].split("## Remaining gaps")[0]
    return [line for line in section.splitlines() if line.startswith("| src/")]


class GapsShapeTests(unittest.TestCase):
    """Common C blocker shapes the uncovered-lines estimate got wrong."""

    def rows(self, source, functions, lines, branches):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, functions, lines, branches)
            _, out, _ = run("gaps", path, "--root", tmp)
        return blocked_rows(out), out

    def test_a_switch_case_counts_only_its_own_body(self):
        source = "\n".join(["int dispatch(const unsigned char *p) {", "  switch (p[0]) {", "  case 1:", "    a();",
                            "    break;", "  case 2:", "    b1(); b2(); b3();", "    break;", "  case 3:", "    c();",
                            "    break;", "  }", "  return 0;", "}"]) + "\n"
        rows, _ = self.rows(source, [(1, "dispatch", 5)],
                            {1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0, 13: 5},
                            {3: [5, 0], 6: [0, 5], 9: [0, 5]})
        self.assertIn("src/p.c:6 ", rows[0])
        self.assertIn("| 3 (6-8) |", rows[0])  # its label and body, not the cases after it
        self.assertIn("src/p.c:3 ", rows[-1])
        self.assertIn("| 0 |", rows[-1])  # the taken case doesn't own the others

    def test_a_condition_over_two_lines(self):
        source = "\n".join(["int parse(const char *p, int n) {", "  if (n < 4 ||", "      p[0] != 'R')",
                            "    return -1;", "  int v = p[1];", "  v += p[2];", "  return v;", "}"]) + "\n"
        rows, _ = self.rows(source, [(1, "parse", 5)], {1: 5, 2: 5, 3: 0, 4: 5, 5: 0, 6: 0, 7: 0},
                            {2: [5, 0], 3: [None, None]})
        self.assertIn("| 3 (5-7) |", rows[0])

    def test_a_guard_returning_null_isnt_defensive(self):
        source = "\n".join(["node *parse_node(const char *d) {", "  if (memcmp(d, \"NODE\", 4) != 0) return NULL;",
                            "  n1(); n2();", "  n3();", "  n4();", "  return make();", "}",
                            "node *grow(char *b) {", "  if (b == NULL) {", "    a1(); a2(); a3();", "    a4();",
                            "    a5(); a6();", "    a7();", "    a8();", "  }", "  return 0;", "}"]) + "\n"
        rows, _ = self.rows(source, [(1, "parse_node", 5), (8, "grow", 5)],
                            {1: 5, 2: 5, 3: 0, 4: 0, 5: 0, 6: 0, 8: 5, 9: 5, 10: 0, 11: 0, 12: 0, 13: 0, 14: 0,
                             16: 5},
                            {2: [5, 0], 9: [0, 5]})
        self.assertIn("src/p.c:2 ", rows[0])  # the blocker first, though the NULL check blocks more
        self.assertIn("src/p.c:9 ", rows[1])

    def test_never_called_callees_behind_a_guard(self):
        source = "\n".join(["int top(const char *p) {", "  if (p[0] == 'X') {", "    deep_parse(p + 1);", "  }",
                            "  return 0;", "}", "int deep_parse(const char *p) {", "  return p[0];", "}"]) + "\n"
        rows, out = self.rows(source, [(1, "top", 5), (7, "deep_parse", 0)], {1: 5, 2: 5, 3: 0, 5: 5, 7: 0, 8: 0},
                              {2: [0, 5]})
        self.assertIn("Uncovered lines after (estimate) | Never-called callees |", out)
        self.assertTrue(rows[0].endswith("| 1 (3) | `deep_parse` |"), rows[0])

    def test_never_called_template_and_same_named_static_functions(self):
        # llvm-cpp.lcov: parse_item<int> is called by explicit template argument, never run;
        # each file has a static helper, state.cc's ran.
        if not cov._demangler():
            self.skipTest("no demangler")
        _, out, _ = run("gaps", fx("llvm-cpp.lcov"), "--root", "/work/proj", "--source-root",
                        os.path.join(FIX, "proj"))
        rows = {row.split(" |")[0]: row for row in out.splitlines() if row.startswith("| ")}
        self.assertTrue(rows["| state.cc:30"].endswith("| `int parse_item<int>(char const*)` |"), rows["| state.cc:30"])
        self.assertTrue(rows["| state.cc:33"].endswith("| - |"), rows["| state.cc:33"])
        self.assertTrue(rows["| other.cc:4"].endswith("| `helper(int)` |"), rows["| other.cc:4"])

    def test_a_keyword_isnt_a_call(self):
        never = {"catch": "Pending.catch", "check": "check"}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.js")
            with open(path, "w") as handle:
                handle.write("try {\n  check(p);\n} catch (e) {\n  p.catch(log);\n}\n")
            self.assertEqual(cov._callees(path, [2, 3], never, set(), {}), ["check"])
            self.assertEqual(cov._callees(path, [4], never, set(), {}), ["Pending.catch"])


class OwnerTests(unittest.TestCase):
    """Which function a line belongs to, with lambdas and templates."""

    JAVA = "\n".join([
        "package p;",                                  # 1
        "",                                            # 2
        "class Parser {",                              # 3
        "  int parse(java.util.List<String> items) {",  # 4
        "    int n = 0;",                              # 5: JaCoCo's line for parse
        "    items.forEach(s -> {",                    # 6
        "      if (s.isEmpty()) return;",              # 7: lambda$parse$0, blocked
        "      use(s);",                               # 8
        "    });",                                     # 9
        "    if (items.size() > 3) {",                 # 10: blocked, after the lambda
        "      big(items);",                           # 11
        "    }",                                       # 12
        "    return n;",                               # 13
        "  }",                                         # 14
        "  int other() {",                             # 15
        "    return 1;",                               # 16
        "  }",                                         # 17
        "}",                                           # 18
    ]) + "\n"

    def test_a_jacoco_lambda_belongs_to_its_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src", "p"))
            with open(os.path.join(tmp, "src", "p", "Parser.java"), "w") as handle:
                handle.write(self.JAVA)
            path = os.path.join(tmp, "j.lcov")
            with open(path, "w") as handle:
                handle.write("TN:jacoco-derived\nSF:src/p/Parser.java\n"
                             "FN:5,Parser.parse\nFN:7,Parser.lambda$parse$0\nFN:16,Parser.other\n"
                             "FNDA:1,Parser.parse\nFNDA:1,Parser.lambda$parse$0\nFNDA:0,Parser.other\n"
                             "DA:5,1\nDA:6,1\nDA:7,1\nDA:8,0\nDA:10,1\nDA:11,0\nDA:13,1\nDA:16,0\n"
                             "BRDA:7,0,0,1\nBRDA:7,0,1,0\nBRDA:10,0,0,1\nBRDA:10,0,1,0\nend_of_record\n")
            _, out, _ = run("gaps", path, "--root", tmp)
            self.assertIn("| src/p/Parser.java:10 | Parser.parse |", out)
            self.assertIn("| src/p/Parser.java:7 | Parser.parse |", out)
            self.assertNotIn("lambda", out)
            code, out, err = run("show", path, "--root", tmp, "--file", "Parser.java", "--function", "parse")
            self.assertEqual(code, 0, err)
            self.assertIn("lines 5-15", out)

    def test_show_picks_the_function_not_its_lambda(self):
        if not cov._demangler():
            self.skipTest("no demangler")
        source = "\n".join(["#include <algorithm>", "int process_kr(const char *p) {", "  int v[2] = {p[0], p[1]};",
                            "  std::for_each(v, v + 2, [](int &x) {", "    if (x == 'Q') x = 0;", "  });",
                            "  return v[0];", "}"]) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, [(2, "_Z10process_krPKc", 1), (4, "p.c:_ZZ10process_krPKcENK3$_0clERi", 2)],
                              {2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 1}, {5: [0, 2]})
            code, out, err = run("show", path, "--root", tmp, "--file", "p.c", "--function", "process_kr")
        self.assertEqual(code, 0, err)
        self.assertIn("lines 2-", out)

    def test_a_called_copy_owns_a_shared_start_line(self):
        # Template instantiations, and a static inline header function in two
        # translation units: one function in the source, several in llvm's data.
        source = "\n".join(["template <typename T>", "int classify(T v) {", "  if (v > 0) return 1;",
                            "  return 0;", "}"]) + "\n"
        for names in (("_Z8classifyIiEiT_", "_Z8classifyIlEiT_"), ("b.c:classify", "a.c:classify")):
            with tempfile.TemporaryDirectory() as tmp:
                path = write_lcov(tmp, source, [(2, names[0], 2), (2, names[1], 0)],
                                  {2: 2, 3: 2, 4: 0}, {3: [2, 0]})
                _, out, _ = run("gaps", path, "--root", tmp)
                _, summary, _ = run("summary", path, "--root", tmp)
            row = [line for line in out.splitlines() if "src/p.c:2 |" in line and "Untaken" not in line][-1]
            self.assertTrue(row.endswith("| yes |"), row)
            self.assertIn("| src/p.c | 1/2 (50.00%) | 2/3 (66.67%) | 1/1 |", summary)

    def test_llvm_macro_definitions_arent_gaps(self):
        # llvm repeats a macro's lines and branches at its #define, outside every function.
        source = "\n".join(["#define CHECK(x) ((x) ? 1 : 0)", "", "int f(int v) {", "  int r = CHECK(v == 7);",
                            "  return r;", "}"]) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, [(3, "f", 2)], {1: 2, 3: 2, 4: 2, 5: 2}, {1: [0, 2], 4: [0, 2]})
            with open(path, "a") as handle:
                handle.write("SF:%s/src/m.h\nDA:2,0\nBRDA:2,0,0,0\nBRDA:2,0,1,0\nend_of_record\n" % tmp)
            _, out, _ = run("gaps", path, "--root", tmp)
        self.assertIn("## Blocked branches (1)", out)
        self.assertIn("| src/p.c:4 | f |", out)
        self.assertNotIn("outside functions", out)
        self.assertNotIn("m.h", out)

    def test_a_macro_definition_after_a_function_isnt_its_gap(self):
        # llvm's FN has no end line: state.cc's CHECK, between helper and parse_item, is in
        # helper's range. A function a macro defines starts on its #define and keeps its gaps.
        _, out, _ = run("gaps", fx("llvm-cpp.lcov"), "--root", "/work/proj", "--source-root",
                        os.path.join(FIX, "proj"))
        self.assertIn("| state.cc:29 |", out)
        self.assertNotIn("| state.cc:21 |", out)
        self.assertNotIn("| state.cc:19 |", out)
        source = "#define GEN(name) \\\n  int name(int a) { \\\n    return a > 3 ? a : 0; \\\n  }\nGEN(gen)\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, [(2, "gen", 1)], {2: 1, 3: 1, 4: 1}, {3: [0, 1]})
            _, out, _ = run("gaps", path, "--root", tmp)
        self.assertIn("| src/p.c:3 | gen |", out)

    def test_a_constructors_lambda_doesnt_end_it(self):
        # llvm-cpp.lcov is real output of clang 23 for proj/state.cc and proj/other.cc: llvm's
        # FN for a constructor is its initializer list's line, before the body's brace.
        if not cov._demangler():
            self.skipTest("no demangler")
        args = (fx("llvm-cpp.lcov"), "--root", "/work/proj", "--source-root", os.path.join(FIX, "proj"))
        _, out, _ = run("gaps", *args)
        self.assertIn("| state.cc:13 | State::State(int) | `if (count > 100) {` |", out)
        code, out, err = run("show", *args, "--file", "state.cc", "--function", "State")
        self.assertEqual(code, 0, err)
        self.assertIn("## state.cc (lines 7-18)", out)

    def test_a_function_closes_after_its_header(self):
        text = [
            "State(int c)",              # 1
            "    : count(c),",           # 2: llvm's FN for the constructor
            "      v{c, c + 1}",         # 3: a member's brace initializer
            "{",                         # 4
            "  run([](int x) {",         # 5
            "  });",                     # 6
            "}",                         # 7
            "function f(a)",             # 8: a brace on its own line
            "{",                         # 9
            "}",                         # 10
            "const g = (x) => x * 2",    # 11: no braces
            "use(() => {",               # 12
            "});",                       # 13
        ]
        self.assertEqual(cov._closing_line(text, 2, 13), 7)
        self.assertEqual(cov._closing_line(text, 8, 13), 10)
        self.assertEqual(cov._closing_line(text, 11, 13), 11)

    def test_a_body_after_a_comment_or_braces_in_the_header(self):
        text = [
            "    : count(c)",               # 1: a comment and a blank line before the body
            "    // the body follows",      # 2
            "",                             # 3
            "{",                            # 4
            "}",                            # 5
            "    : count(c)",               # 6: a block comment before the body
            "    /* the body",              # 7
            "       follows */",            # 8
            "{",                            # 9
            "}",                            # 10
            "    : v{{c, c + 1}}",          # 11: nested braces in an initializer
            "{",                            # 12
            "}",                            # 13
            "    : p({c, c + 1})",          # 14: a brace inside an initializer's parentheses
            "{",                            # 15
            "}",                            # 16
            "function f({ a, b })",         # 17: destructured parameters, the body on the next line
            "{",                            # 18
            "}",                            # 19
            "    : a{c}, b{0}",             # 20
            "{",                            # 21
            "}",                            # 22
            "State(int c) : a{c} {",        # 23
            "}",                            # 24
            "function g({ a, b }) {",       # 25
            "}",                            # 26
        ]
        for start, closing in ((1, 5), (6, 10), (11, 13), (14, 16), (17, 19), (20, 22), (23, 24), (25, 26)):
            self.assertEqual(cov._closing_line(text, start, 26), closing, "function at line %d" % start)

    def test_name_forms_keep_a_lambdas_enclosing_function_out(self):
        forms = cov._name_forms("process_kr(char const*)::$_0::operator()(int&) const")
        self.assertNotIn("process_kr", forms)
        self.assertIn("process_kr", cov._name_forms("process_kr(char const*)"))
        self.assertIn("parse", cov._name_forms("Header.parse(Ljava/lang/String;)I"))
        self.assertIn("(anonymous_3)", cov._name_forms("(anonymous_3)"))


class GapsRankingTests(unittest.TestCase):
    SOURCE = "\n".join([
        "int parse(const char *p, int n) {",       # 1
        "  if (n < 5 || p[0] != 'R') {",            # 2: blocked, the early return runs
        "    return -1;",                           # 3
        "  }",                                      # 4
        "  int v = p[4];",                          # 5
        "  return v;",                              # 6
        "}",                                        # 7
        "int mode(int m) {",                        # 8
        "  if (m == 1) {",                          # 9: blocked, the then-block runs
        "    m += 1;",                              # 10
        "    m *= 2;",                              # 11
        "  } else {",                               # 12
        "    m = 0;",                               # 13
        "    m -= 3;",                              # 14
        "  }",                                      # 15
        "  return m;",                              # 16
        "}",                                        # 17
        "int grow(char *b) {",                      # 18
        "  if (b == NULL) {",                       # 19: blocked, defensive
        "    a1(); a2(); a3();",                    # 20
        "    a4();",                                # 21
        "    a5();",                                # 22
        "  }",                                      # 23
        "  return 0;",                              # 24
        "}",                                        # 25
    ]) + "\n"

    def test_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(
                tmp, self.SOURCE,
                [(1, "parse", 5), (8, "mode", 5), (18, "grow", 5)],
                {1: 5, 2: 5, 3: 5, 5: 0, 6: 0, 8: 5, 9: 5, 10: 5, 11: 5, 13: 0, 14: 0, 16: 5,
                 18: 5, 19: 5, 20: 0, 21: 0, 22: 0, 24: 5},
                {2: [5, 0], 9: [5, 0], 19: [0, 5]},
            )
            code, out, _ = run("gaps", path, "--root", tmp)
        self.assertEqual(code, 0)
        rows = blocked_rows(out)
        # The guard text comes from --root, without --source-root.
        self.assertIn("`if (n < 5 \\|\\| p[0] != 'R') {`", rows[0])
        self.assertTrue(rows[0].endswith("| 2 (5-6) | - |"), rows[0])  # past the early return
        self.assertTrue(rows[1].endswith("| 2 (13-14) | - |"), rows[1])  # past the then-block
        self.assertIn("src/p.c:19", rows[2])  # the NULL check comes last, though it blocks more
        self.assertTrue(rows[2].endswith("| 3 (20-22) | - |"), rows[2])

    def test_a_covered_run_with_branches_isnt_skipped(self):
        source = "f\n  if (a) {\n    if (b) x();\n  }\n  y();\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, [(1, "f", 1)], {1: 1, 2: 1, 3: 1, 5: 0}, {2: [1, 0], 3: [1, 1]})
            _, out, _ = run("gaps", path, "--root", tmp)
        self.assertTrue(blocked_rows(out)[0].endswith("| 0 | - |"))

    def test_a_callback_doesnt_end_its_function(self):
        source = "\n".join(["function parsePacket(buf) {", "  const parts = buf.split(',').map(",
                             "    (p) => p.trim());", "  if (parts.length > 3) {", "    extra(parts);",
                             "    more(parts);", "  }", "  return parts;", "}"]) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, [(1, "parsePacket", 5), (3, "(anonymous_0)", 5)],
                              {1: 5, 2: 5, 3: 5, 4: 5, 5: 0, 6: 0, 8: 5}, {4: [0, 5]})
            _, out, _ = run("gaps", path, "--root", tmp)
            self.assertIn("| src/p.c:4 | parsePacket |", out)
            code, out, err = run("show", path, "--root", tmp, "--file", "p.c", "--function", "parsePacket")
        self.assertEqual(code, 0, err)
        self.assertIn("lines 1-9", out)

    def test_without_the_source_a_callback_is_its_own_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, "", [(1, "parsePacket", 5), (3, "(anonymous_0)", 5)],
                              {1: 5, 3: 5, 4: 5, 5: 0}, {4: [0, 5]})
            os.remove(os.path.join(tmp, "src", "p.c"))
            _, out, _ = run("gaps", path, "--root", tmp)
        self.assertIn("| src/p.c:4 | (anonymous_0) |", out)  # rather than an unrelated function

    def test_methods_after_a_named_function_are_their_own(self):
        args = (fx("istanbul-helper-class.lcov"), "--root", os.path.join(FIX, "proj"))
        code, out, err = run("gaps", *args)
        self.assertEqual(code, 0, err)
        self.assertIn("| src/records.js:14 | (anonymous_2) |", out)
        self.assertIn("| src/records.js:4 | limit |", out)
        self.assertIn("| (anonymous_4) | src/records.js:20 | 2/2 (lines 22) | 21-22 | no |", out)
        self.assertIn("| (anonymous_2) | src/records.js:12 | 1/2 (lines 14) | 15 | yes |", out)
        self.assertIn("| limit | src/records.js:3 | 1/2 (lines 4) | - | yes |", out)
        code, out, err = run("show", *args, "--file", "records.js", "--function", "limit")
        self.assertEqual(code, 0, err)
        self.assertIn("lines 3-7", out)

    def test_near_linear_on_a_large_file(self):
        # sqlite3.c-sized amalgamations took 71 s, and 4,000 such functions 20 s, when gaps was
        # quadratic. Counted in executed lines of coverage.py, not seconds: CI machines differ.
        def executed(n):
            functions, lines, branches = [], {}, {}
            for f in range(n):
                base = f * 25 + 1
                functions.append((base, "f%d" % f, 1))
                for k in range(25):
                    lines[base + k] = 1 if k < 10 else 0
                branches[base + 2] = [1, 0]
                branches[base + 5] = [1, 0]
            count = [0]

            def tracer(frame, event, _arg):
                if frame.f_code.co_filename != SCRIPT:
                    return None
                count[0] += event == "line"
                return tracer

            with tempfile.TemporaryDirectory() as tmp:
                path = write_lcov(tmp, "", functions, lines, branches)
                sys.settrace(tracer)
                try:
                    code, _, _ = run("gaps", path, "--root", tmp)
                finally:
                    sys.settrace(None)
            self.assertEqual(code, 0)
            return count[0]

        # Four times the functions: about four times the work, where quadratic code does sixteen.
        self.assertLess(executed(1000) / executed(250), 6)


class ShowTests(unittest.TestCase):
    def test_show_function_marks_untaken_branches(self):
        code, out, _ = run(
            "show",
            fx("llvm-round1.lcov"),
            "--root",
            "/work/proj",
            "--source-root",
            os.path.join(FIX, "proj"),
            "--file",
            "parse.c",
            "--function",
            "parse_body",
        )
        self.assertEqual(code, 0)
        self.assertIn("lines 15-22", out)
        self.assertIn("0!|     case 'B': return 2;    <-- 1/2 branches not taken", out)
        self.assertNotIn("parse_header", out)

    def test_show_static_functions(self):
        # llvm-cov names functions with internal linkage "<file>:<name>"
        # (real output of clang 20, tests/fixtures/coverage/llvm-static.lcov).
        for path, function in (("st.c", "skip_space"), ("stcc.cc", "twice"), ("stcc.cc", "count_digits")):
            code, out, err = run(
                "show", fx("llvm-static.lcov"), "--root", "/work/proj", "--source-root",
                os.path.join(FIX, "proj"), "--file", path, "--function", function,
            )
            self.assertEqual(code, 0, err)
            self.assertNotIn("not found", out + err)

    def test_demangle_drops_the_file_prefix(self):
        names = cov.demangle(["st.c:skip_space", "parse_c", "stcc.cc:_ZL5twicei"])
        self.assertEqual(names["st.c:skip_space"], "skip_space")
        self.assertEqual(names["parse_c"], "parse_c")
        if cov._demangler():
            self.assertEqual(names["stcc.cc:_ZL5twicei"], "twice(int)")
        else:
            self.assertEqual(names["stcc.cc:_ZL5twicei"], "_ZL5twicei")

    def test_show_unknown_file(self):
        code, _, err = run("show", fx("llvm-round1.lcov"), "--file", "nope.c")
        self.assertEqual(code, 1)
        self.assertIn("not in the coverage data", err)

    def test_show_lines_for_a_method_istanbul_doesnt_name(self):
        args = ("show", fx("istanbul-class.lcov"), "--source-root", os.path.join(FIX, "proj"), "--file", "reader.js")
        code, _, err = run(*args, "--function", "parse")
        self.assertEqual(code, 1)
        self.assertIn("--lines", err)
        code, out, err = run(*args, "--lines", "8-14")
        self.assertEqual(code, 0, err)
        self.assertIn("lines 8-14", out)
        self.assertIn("0!|       return null;", out)
        self.assertIn("3 |     return fields.map((f) => f.trim())", out)
        self.assertNotIn("constructor", out)

    def test_show_caps_a_long_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = "int big(void) {\n" + "  x();\n" * 600 + "}\n"
            path = write_lcov(tmp, source, [(1, "big", 1)], {n: 1 for n in range(1, 602)}, {})
            args = ("show", path, "--root", tmp, "--file", "p.c")
            code, out, _ = run(*args, "--function", "big")
            self.assertEqual(code, 0)
            self.assertIn("|   x();", out)
            self.assertFalse("   401 " in out)
            self.assertIn("(202 more lines; use --lines <first>-<last>, or --max-lines)", out)
            _, out, _ = run(*args, "--lines", "1-602")
            self.assertIn("   602 ", out)

    def test_a_closed_pipe_ends_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, "", [], {n: 1 for n in range(1, 40000)}, {})
            proc = subprocess.Popen([sys.executable, SCRIPT, "merge", path], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            proc.stdout.readline()
            proc.stdout.close()
            err = proc.stderr.read().decode()
            proc.stderr.close()
            proc.wait()
        self.assertNotIn("Error", err)

    def test_show_lines_must_be_a_range(self):
        args = ("show", fx("istanbul-class.lcov"), "--source-root", os.path.join(FIX, "proj"), "--file", "reader.js")
        for bad in ("8", "14-8", "0-3"):
            code, _, err = run(*args, "--lines", bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("<first>-<last>", err)


class NoBranchDataTests(unittest.TestCase):
    def test_gaps_says_when_the_export_has_no_branch_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.lcov")
            with open(path, "w") as handle:
                handle.write("SF:/p/a.js\nFN:1,f\nFNDA:1,f\nDA:1,1\nDA:2,0\nend_of_record\n")
            _, out, _ = run("gaps", path)
            self.assertIn("**No branch data:** the export has no branch records", out)


class StaleSourceTests(unittest.TestCase):
    def test_a_source_changed_after_the_export_is_flagged(self):
        source = "int f(int v) {\n  if (v) return 1;\n  return 0;\n}\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_lcov(tmp, source, [(1, "f", 1)], {1: 1, 2: 1, 3: 0}, {2: [1, 0]})
            show = ("show", path, "--root", tmp, "--file", "p.c")
            for argv in (("gaps", path, "--root", tmp), show):
                self.assertNotIn("changed after", run(*argv)[1])
            later = os.path.getmtime(path) + 60
            os.utime(os.path.join(tmp, "src", "p.c"), (later, later))
            for argv in (("gaps", path, "--root", tmp), show):
                self.assertIn("**Stale:** src/p.c changed after %s was written" % path, run(*argv)[1])


class OverlapTests(unittest.TestCase):
    def test_overlap_detects_subset(self):
        _, out, _ = run("overlap", fx("llvm-round1.lcov"), fx("llvm-round2.lcov"), "--root", "/work/proj")
        self.assertIn("| Only A | 0 | 0 |", out)
        self.assertIn("Result: A covers nothing that B doesn't cover.", out)
        self.assertIn("src/parse.c: branch lines 7, 9, 18-19", out)

    def test_overlap_of_empty_data_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "empty.lcov")
            open(empty, "w").close()
            code, out, _ = run("overlap", empty, empty)
            self.assertEqual(code, 1)
            self.assertIn("inconclusive: A and B have no coverage", out)
            self.assertNotIn("exactly the same", out)
            scope = os.path.join(tmp, "no-match.scope")
            with open(scope, "w") as handle:
                handle.write("nonexistent/\n")
            code, out, _ = run("overlap", fx("llvm-round1.lcov"), fx("llvm-round2.lcov"),
                               "--root", "/work/proj", "--scope", scope)
            self.assertEqual(code, 1)
            self.assertIn("**Incomplete:** no coverage data for scope entry: nonexistent/", out)
            self.assertIn("no coverage in scope", out)

    def test_overlap_identical(self):
        _, out, _ = run("overlap", fx("llvm-round1.lcov"), fx("llvm-round1.lcov"))
        self.assertIn("cover exactly the same branches and lines", out)

    def test_overlap_without_branch_records_compares_lines_only(self):
        lines_only = "SF:/p/a.c\nDA:1,1\nDA:2,1\nDA:3,0\nend_of_record\n"
        with_branches = "SF:/p/a.c\nDA:1,1\nDA:2,1\nDA:3,0\nBRDA:2,0,0,1\nBRDA:2,0,1,0\nend_of_record\n"
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.lcov"), os.path.join(tmp, "b.lcov")
            for path, text in ((a, lines_only), (b, with_branches)):
                with open(path, "w") as handle:
                    handle.write(text)
            for pair, note in (((a, a), "A and B have"), ((a, b), "A has")):
                code, out, _ = run("overlap", *pair)
                self.assertEqual(code, 0)
                self.assertNotIn("exactly the same branches", out)
                self.assertIn("**No branch data:** %s no branch records; lines only." % note, out)
                self.assertIn("| Only B | n/a | 0 |", out)
                self.assertIn("Result: A and B cover the same lines.", out)

    def test_overlap_rejects_a_union_that_isnt_a_and_b_merged(self):
        def jacoco(path, covered_6, covered_8=0):
            with open(path, "w") as handle:
                handle.write("TN:jacoco-derived\nSF:src/X.java\nDA:6,1\nBRDA:6,0,0,%d\nBRDA:6,0,1,%d\n"
                             "DA:8,%d\nBRDA:8,0,0,%d\nBRDA:8,0,1,0\nend_of_record\n"
                             % (int(covered_6 > 0), int(covered_6 > 1), int(covered_8 > 0), int(covered_8 > 0)))
            return path
        with tempfile.TemporaryDirectory() as tmp:
            a = jacoco(os.path.join(tmp, "a.lcov"), 1, 1)
            b = jacoco(os.path.join(tmp, "b.lcov"), 1)
            union = jacoco(os.path.join(tmp, "u.lcov"), 1)  # misses A's line 8: not A and B merged
            code, out, _ = run("overlap", a, b, "--union", union)
            self.assertEqual(code, 1)
            self.assertIn("inconclusive: --union isn't A and B merged (src/X.java:8: A 1, B 0, union 0 branches)", out)
            # A line only the union covers, without branches: from another run.
            with open(union, "w") as handle:
                handle.write("TN:jacoco-derived\nSF:src/X.java\nDA:6,1\nBRDA:6,0,0,1\nBRDA:6,0,1,0\n"
                             "DA:7,1\nDA:8,1\nBRDA:8,0,0,1\nBRDA:8,0,1,0\nend_of_record\n")
            code, out, _ = run("overlap", a, b, "--union", union)
            self.assertEqual(code, 1)
            self.assertIn("inconclusive: --union isn't A and B merged (src/X.java:7: A 0, B 0, union 0 branches)", out)

    def test_overlap_counts_finds_skipped_work(self):
        # One process runs the parse loop once (state from the first input);
        # fresh processes run it per input. The covered sets are equal.
        one = "SF:/p/h.c\nDA:3,1\nDA:4,1\nDA:5,2\nBRDA:4,0,0,1\nBRDA:4,0,1,1\nend_of_record\n"
        fresh = "SF:/p/h.c\nDA:3,1\nDA:4,2\nDA:5,2\nBRDA:4,0,0,2\nBRDA:4,0,1,1\nend_of_record\n"
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "one.lcov"), os.path.join(tmp, "fresh.lcov")
            for path, text in ((a, one), (b, fresh)):
                with open(path, "w") as handle:
                    handle.write(text)
            _, out, _ = run("overlap", a, b)
            self.assertIn("cover exactly the same", out)
            code, out, _ = run("overlap", a, b, "--counts")
            self.assertEqual(code, 0)
            self.assertIn("| Different hit counts | 1 | 1 |", out)
            self.assertIn("Result: hit counts differ on 1 branch and 1 line.", out)
            self.assertIn("- /p/h.c: line 4 (A 1, B 2); branch line 4 (A 1, B 2)", out)
            code, out, _ = run("overlap", a, a, "--counts")
            self.assertIn("Result: A and B have the same hit count on every branch and line.", out)

    def test_overlap_counts_separates_once_per_process_code(self):
        # 3 fresh processes: module-level line 1 runs once per process (A 1, B 3);
        # line 6 is per input (A 3, B 3); line 8 only in one process (leaked state).
        one = "SF:/p/t.js\nDA:1,1\nDA:6,3\nDA:8,2\nend_of_record\n"
        fresh = "SF:/p/t.js\nDA:1,3\nDA:6,3\nDA:8,0\nend_of_record\n"
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "one.lcov"), os.path.join(tmp, "fresh.lcov")
            for path, text in ((a, one), (b, fresh)):
                with open(path, "w") as handle:
                    handle.write(text)
            code, out, _ = run("overlap", a, b, "--counts", "--processes", "3")
            self.assertEqual(code, 0)
            self.assertIn("| Once per process (B = 3 × A) | 0 | 1 |", out)
            self.assertIn("| Other differences | 0 | 1 |", out)
            self.assertIn("Result: hit counts differ on 0 branches and 1 line beyond once-per-process code.", out)
            self.assertIn("### Once per process\n- /p/t.js: line 1 (A 1, B 3)", out)
            self.assertIn("### Different hit counts\n- /p/t.js: line 8 (A 2, B 0)", out)
            one_ok = one.replace("DA:8,2", "DA:8,0")
            with open(a, "w") as handle:
                handle.write(one_ok)
            _, out, _ = run("overlap", a, b, "--counts", "--processes", "3")
            self.assertIn("Result: hit counts differ only on code that runs once per process", out)
            self.assertNotIn("### Different hit counts", out)


class SitesTests(unittest.TestCase):
    def test_sites_states_and_groups(self):
        code, out, _ = run("sites", fx("llvm-round1.lcov"), "--root", "/work/proj", "--sites", fx("sinks.tsv"))
        self.assertEqual(code, 0)
        self.assertIn("## Sink sites: 2 of 4 reached", out)
        self.assertIn("| hdr.len | 2/2 |", out)
        self.assertIn("| s3 | body.tag | src/parse.c:19 | not reached | 0 |", out)
        self.assertIn("unknown (file not in coverage)", out)

    def test_a_line_that_ran_with_an_untaken_branch_is_to_confirm(self):
        # `if (len > n - 4) return -3;` ran twice, its return never: a call there may not have run.
        _, out, _ = run("sites", fx("llvm-round1.lcov"), "--root", "/work/proj", "--sites", fx("sinks.tsv"))
        self.assertIn("## Sink sites: 2 of 4 reached (1 to confirm)", out)
        self.assertIn("| s1 | hdr.len | src/parse.c:9 | reached (line; a branch on it is untaken: confirm) | 2 |", out)
        self.assertIn("| s2 | hdr.len | src/parse.c:11 | reached | 4 |", out)

    def test_sites_requires_header(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as handle:
            handle.write("a\tb\n")
            path = handle.name
        try:
            code, _, err = run("sites", fx("llvm-round1.lcov"), "--sites", path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1)
        self.assertIn("'file' and 'line'", err)


class JacocoTests(unittest.TestCase):
    def test_from_jacoco(self):
        files = cov.jacoco_to_lcov(fx("jacoco.xml"), ["src/main/java"])
        path = "src/main/java/com/example/parser/Header.java"
        self.assertIn(path, files)
        header = files[path]
        self.assertEqual(header.line_counts(), (6, 9))
        self.assertEqual(header.branch_counts(), (3, 6))
        self.assertIn("Header.parse([B)I", header.functions)
        self.assertIn("Header.<init>", header.functions)
        self.assertIn("Header$Field.size", header.functions)
        self.assertEqual(header.functions["Header.parse(Ljava/lang/String;)I"][2], 0)

    def test_from_jacoco_cli_output_is_lcov(self):
        code, out, _ = run("from-jacoco", fx("jacoco.xml"))
        self.assertEqual(code, 0)
        self.assertIn("TN:jacoco-derived\nSF:com/example/parser/Header.java", out)
        self.assertIn("BRDA:6,0,1,0", out)

    def _jacoco(self, tmp, name, covered, total=2):
        path = os.path.join(tmp, name)
        branches = "".join(
            "BRDA:6,0,%d,%d\n" % (index, 1 if index < covered else 0) for index in range(total)
        )
        with open(path, "w") as handle:
            handle.write("TN:jacoco-derived\nSF:src/X.java\nDA:6,%d\n%send_of_record\n"
                         % (1 if covered else 0, branches))
        return path

    def test_jacoco_tag_survives_parse_and_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "m.lcov")
            code, _, _ = run("merge", self._jacoco(tmp, "a.lcov", 1), fx("llvm-round1.lcov"), "-o", out)
            self.assertEqual(code, 0)
            files = cov.parse_lcov(out)
            self.assertEqual(files["src/X.java"].origin, "jacoco")
            self.assertEqual(files["/work/proj/src/parse.c"].origin, "")

    def test_jacoco_tag_stays_with_its_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            native = os.path.join(tmp, "n.lcov")
            with open(native, "w") as handle:
                handle.write("SF:z/native.c\nDA:1,1\nend_of_record\n")
            out = os.path.join(tmp, "m.lcov")
            run("merge", self._jacoco(tmp, "a.lcov", 1), native, "-o", out)
            files = cov.parse_lcov(out)
            self.assertEqual((files["src/X.java"].origin, files["z/native.c"].origin), ("jacoco", ""))
            # A TN: line applies to the record that follows it.
            with open(out, "w") as handle:
                handle.write("TN:jacoco-derived\nSF:a/X.java\nDA:1,1\nend_of_record\nSF:z/native.c\nDA:1,1\nend_of_record\n")
            self.assertEqual(cov.parse_lcov(out)["z/native.c"].origin, "")

    def test_refuses_to_combine_jacoco_derived_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._jacoco(tmp, "a.lcov", 1), self._jacoco(tmp, "b.lcov", 1)
            for argv in (("merge", a, b), ("summary", a, b), ("gaps", a, b)):
                code, _, err = run(*argv)
                self.assertEqual(code, 1, argv)
                self.assertIn("jacococli merge", err)

    def test_overlap_needs_union_for_jacoco(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._jacoco(tmp, "a.lcov", 1), self._jacoco(tmp, "b.lcov", 1)
            code, _, err = run("overlap", a, b)
            self.assertEqual(code, 1)
            self.assertIn("--union", err)
            code, _, err = run("overlap", a, b, "--union", fx("llvm-round1.lcov"))
            self.assertEqual(code, 1)
            self.assertIn("JaCoCo-derived", err)
            code, _, err = run("overlap", a, b, "--counts")
            self.assertEqual(code, 1)
            self.assertIn("not how often", err)

    def test_overlap_with_union_finds_complementary_branches(self):
        # A takes the true arm and B the false arm of one condition. Each
        # report says "1 of 2 branches on line 6", which looks identical.
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._jacoco(tmp, "a.lcov", 1), self._jacoco(tmp, "b.lcov", 1)
            union = self._jacoco(tmp, "u.lcov", 2)
            code, out, _ = run("overlap", a, b, "--union", union)
            self.assertEqual(code, 0)
            self.assertIn("| Covered by both | 0 | 1 |", out)
            self.assertIn("| Only A | 1 | 0 |", out)
            self.assertIn("| Only B | 1 | 0 |", out)
            self.assertIn("each covers code the other doesn't", out)
            same = self._jacoco(tmp, "same.lcov", 1)
            _, out, _ = run("overlap", a, b, "--union", same)
            self.assertIn("| Covered by both | 1 | 1 |", out)
            self.assertIn("cover exactly the same", out)

    def test_from_jacoco_invalid_xml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write("<report>")
            path = handle.name
        try:
            code, _, err = run("from-jacoco", path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 1)
        self.assertIn("invalid XML", err)


class HelperTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(cov._ranges([5, 1, 2, 3, 9, 10]), "1-3, 5, 9-10")

    def test_demangle_leaves_plain_names(self):
        self.assertEqual(cov.demangle(["parse"]), {"parse": "parse"})

    def test_functions_on_the_same_line_share_a_range(self):
        fc = cov.FileCov("/a/t.cc")
        fc.add_function("_Z3fooIiEvT_", 5, None, 1)
        fc.add_function("_Z3fooIlEvT_", 5, None, 0)
        fc.add_function("_Z3barv", 12, None, 1)
        ranges = {name: (start, end) for start, end, name, _ in cov._function_ranges(fc)}
        self.assertEqual(ranges["_Z3fooIiEvT_"], (5, 11))
        self.assertEqual(ranges["_Z3fooIlEvT_"], (5, 11))
        self.assertEqual(ranges["_Z3barv"], (12, None))

    def test_brackets_in_strings_and_comments_dont_nest_a_function(self):
        text = [
            "function f(s) {",            # 1
            "  if (s === '}') g('{');",   # 2
            "  // a stray {",             # 3
            "  return `${s}{`;",          # 4
            "}",                          # 5
            "x.on('a', () => {",          # 6: a top-level callback
            "  h();",                     # 7
            "});",                        # 8
            "function k() {",             # 9
            "  return [1].map((v) => v);",  # 10: a callback inside k
            "}",                          # 11
        ]
        fc = cov.FileCov("/a/t.js")
        for start, name in ((1, "f"), (6, "(anonymous_0)"), (9, "k"), (10, "(anonymous_1)")):
            fc.add_function(name, start, None, 1)
        ranges = {name: (start, end) for start, end, name, _ in cov._function_ranges(fc, text)}
        self.assertEqual(ranges["f"], (1, 5))
        self.assertEqual(ranges["(anonymous_0)"], (6, 8))
        self.assertEqual(ranges["k"], (9, None))

    def test_name_forms_drop_return_type_templates_and_scope(self):
        forms = cov._name_forms("int ns::foo<std::pair<int, int> >(char const*)")
        for name in ("ns::foo<std::pair<int, int> >", "ns::foo", "foo"):
            self.assertIn(name, forms)
        self.assertIn("parse", cov._name_forms("Header.parse([B)I"))


if __name__ == "__main__":
    unittest.main()
