"""Tests for tools/check_skills.py, including that it fails when it should."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "check_skills.py")


def load_module():
    spec = importlib.util.spec_from_file_location("check_skills", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cs = load_module()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def skill(name, description="Does one thing. Use when asked to do it.", extra="", body="# Title\n"):
    return "---\nname: %s\ndescription: %s\nlicense: MIT\n%s---\n%s" % (name, description, extra, body)


class CheckSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, bucket, name, text=None):
        write(os.path.join(self.root, "skills", bucket, name, "SKILL.md"), text or skill(name))

    def test_no_skills_is_an_error(self):
        report = cs.check(self.root)
        self.assertTrue(any("no skills found" in e for e in report.errors))

    def test_valid_skill_passes(self):
        self.add("fuzzing", "fuzz-run")
        report = cs.check(self.root)
        self.assertEqual(report.errors, [])

    def test_folded_description(self):
        text = "---\nname: fuzz-run\ndescription: >-\n  Runs fuzzers.\n  Use when asked.\nlicense: MIT\n---\nbody\n"
        meta, body = cs.parse_frontmatter(text)
        self.assertEqual(meta["description"], "Runs fuzzers. Use when asked.")
        self.assertEqual(body, "body")

    def test_name_must_match_directory(self):
        self.add("fuzzing", "fuzz-run", skill("fuzz-runner"))
        report = cs.check(self.root)
        self.assertTrue(any("does not match directory" in e for e in report.errors))

    def test_prefix_required(self):
        self.add("fuzzing", "run-things")
        report = cs.check(self.root)
        self.assertTrue(any("must start with 'fuzz-'" in e for e in report.errors))

    def test_description_limits(self):
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", "x" * 201))
        self.add("fuzzing", "fuzz-campaign", skill("fuzz-campaign", "y" * 340))
        report = cs.check(self.root)
        self.assertTrue(any("fuzz-run" in e and "limit is 200" in e for e in report.errors))
        self.assertFalse(any("fuzz-campaign" in e for e in report.errors))

    def test_total_budget(self):
        for index in range(25):
            name = "fuzz-s%d" % index
            self.add("fuzzing", name, skill(name, "z" * 199))
        report = cs.check(self.root)
        self.assertTrue(any("budget is 4000" in e for e in report.errors))

    def test_unknown_key_and_license(self):
        text = "---\nname: fuzz-run\ndescription: d\nlicense: Apache-2.0\nallowed-tools: Bash\n---\n"
        self.add("fuzzing", "fuzz-run", text)
        report = cs.check(self.root)
        self.assertTrue(any("allowed-tools" in e for e in report.errors))
        self.assertTrue(any("license must be MIT" in e for e in report.errors))

    def test_only_name_description_and_license_are_allowed(self):
        # Only name, description and license.
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", extra="compatibility: python3\nmetadata:\n  a: b\n"))
        errors = [e for e in cs.check(self.root).errors if "unknown frontmatter keys" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("compatibility, metadata", errors[0])

    def test_links(self):
        body = "[ok](references/a.md) [out](../fuzz-other/SKILL.md) [gone](references/missing.md) [web](https://x.y)\n"
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body=body))
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-run", "references", "a.md"), "[b](b.md)\n")
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-run", "references", "b.md"), "b\n")
        self.add("fuzzing", "fuzz-other")
        report = cs.check(self.root)
        self.assertTrue(any("leaves the skill directory" in e for e in report.errors))
        self.assertTrue(any("broken link" in e for e in report.errors))
        self.assertTrue(any("one level deep" in w for w in report.warnings))

    def test_links_in_code_are_ignored(self):
        body = (
            "Use `arr[i](x)` freely.\n"
            "```c\n"
            "if (a[n](b)) return;\n"
            "```\n"
            "[real](references/missing.md)\n"
        )
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body=body))
        report = cs.check(self.root)
        broken = [e for e in report.errors if "broken link" in e]
        self.assertEqual(len(broken), 1)
        self.assertIn("references/missing.md", broken[0])

    def test_size_warnings(self):
        long_body = "line\n" * 520
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body=long_body))
        self.add("fuzzing", "fuzz-java", skill("fuzz-java", body="line\n" * 160))
        for name, lines in (("listed", 170), ("grown", 190), ("short", 100), ("new", 160)):
            write(os.path.join(self.root, "skills", "fuzzing", "fuzz-java", name + ".md"), "line\n" * lines)
        # Long files with a reason don't warn until they grow past their cap; new overruns stand out.
        listed = {"skills/fuzzing/fuzz-java/%s.md" % name: (180, "why") for name in ("listed", "grown", "short")}
        with mock.patch.dict(cs.LONG_FILES, listed, clear=True):
            report = cs.check(self.root)
        self.assertTrue(any("fuzz-run" in w and "under 500" in w for w in report.warnings))
        self.assertTrue(any("fuzz-java/SKILL.md" in w and "about 150" in w for w in report.warnings))
        self.assertTrue(any("fuzz-java/new.md" in w and "about 150" in w for w in report.warnings))
        self.assertTrue(any("fuzz-java/grown.md" in w and "LONG_FILES allows 180" in w for w in report.warnings))
        self.assertTrue(any("fuzz-java/short.md" in w and "drop its LONG_FILES entry" in w for w in report.warnings))
        self.assertFalse(any("listed.md" in w for w in report.warnings), report.warnings)
        self.assertEqual(len(report.warnings), 5, report.warnings)
        self.assertEqual([e for e in report.errors if "a language skill needs" not in e], [])

    def test_plugin_json_must_match(self):
        self.add("fuzzing", "fuzz-run")
        self.add("shared", "security-threat-model")
        write(
            os.path.join(self.root, ".claude-plugin", "plugin.json"),
            json.dumps({"name": "x", "skills": ["./skills/fuzzing/fuzz-run", "./skills/fuzzing/fuzz-gone"]}),
        )
        report = cs.check(self.root)
        self.assertTrue(any("skill not listed: skills/shared/security-threat-model" in e for e in report.errors))
        self.assertTrue(any("no SKILL.md: skills/fuzzing/fuzz-gone" in e for e in report.errors))

    def test_marketplace_entry_matches_plugin_json(self):
        self.add("fuzzing", "fuzz-run")
        write(os.path.join(self.root, ".claude-plugin", "plugin.json"),
              json.dumps({"name": "x", "keywords": ["fuzzing"], "skills": ["./skills/fuzzing/fuzz-run"]}))
        write(os.path.join(self.root, ".claude-plugin", "marketplace.json"),
              json.dumps({"plugins": [{"name": "x", "keywords": ["fuzzing", "jazzer"]}]}))
        errors = [e for e in cs.check(self.root).errors if "marketplace.json" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("keywords", errors[0])

    def test_agent_start_commands_deny_publishing(self):
        self.add("fuzzing", "fuzz-run")
        write(
            os.path.join(self.root, "README.md"),
            "| Copilot CLI | `copilot --yolo --deny-tool 'shell(git push)' --deny-tool 'shell(gh issue)'` |\n"
            "| Claude Code | `claude --dangerously-skip-permissions --disallowedTools 'Bash(git push *)' "
            "'Bash(gh issue *)' 'Bash(gh pr *)' 'Bash(gh gist *)'` |\n"
            "Install: `copilot plugin install x`, `codex --yolo`\n",
        )
        report = cs.check(self.root)
        errors = [e for e in report.errors if "start command" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("README.md:1", errors[0])
        self.assertIn("gh pr, gh gist", errors[0])

    def test_readme_routes_a_phrase_to_the_skill_whose_description_has_it(self):
        self.add("fuzzing", "fuzz-campaign", skill("fuzz-campaign", 'Runs it. Use for "fuzz this project", "what should I fuzz?".'))
        self.add("fuzzing", "fuzz-harness", skill("fuzz-harness", 'Writes one. Use for "write a fuzz test for X".'))
        write(
            os.path.join(self.root, "README.md"),
            "| Skill | Use it for | Example requests |\n|---|---|---|\n"
            '| `fuzz-campaign` | A campaign | "What should I fuzz?", "Fuzz this project" |\n'
            '| `fuzz-harness` | One harness | "Write a fuzz test for `parse`" |\n'
            '| `fuzz-campaign` | Long runs | "Run this fuzzer for two hours" |\n'
            '| `fuzz-harness` | Writes "harnesses" | |\n'
            '| "Run this fuzzer for two hours" | `fuzz-campaign` |\n',
        )
        errors = [e for e in cs.check(self.root).errors if "routes" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("README.md:5", errors[0])
        self.assertIn("run this fuzzer for two hours", errors[0])

    def test_rules_stated_once(self):
        rule = "Hold back a use-after-free\nread or write until it's public.\n"
        self.add("fuzzing", "fuzz-campaign", skill("fuzz-campaign", body=rule))
        self.add("fuzzing", "fuzz-triage")
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-triage", "references", "fix.md"), rule)
        report = cs.check(self.root)
        errors = [e for e in report.errors if "restates" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-triage/references/fix.md", errors[0])

    def test_standalone_requests_stop(self):
        self.add("fuzzing", "fuzz-triage", skill("fuzz-triage", body="# T\n\n## Next\n\n- `fuzz-harness`.\n"))
        self.add("fuzzing", "fuzz-coverage", skill("fuzz-coverage", body="# C\n\n## Next\n\n- Standalone: stop when done.\n"))
        errors = [e for e in cs.check(self.root).errors if "standalone" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-triage", errors[0])

    def test_shared_skills_stay_out_of_the_fuzzing_workspace(self):
        self.add("shared", "security-threat-model")
        path = os.path.join(self.root, "skills", "shared", "security-threat-model", "references", "c-cpp.md")
        write(path, "Write the sites to `.seclab/fuzz/sinks/<target>.tsv`.\n")
        errors = [e for e in cs.check(self.root).errors if ".seclab/fuzz" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("security-threat-model/references/c-cpp.md", errors[0])

    def test_constant_argument_rule_keeps_its_exception(self):
        self.add("shared", "security-threat-model")
        path = os.path.join(self.root, "skills", "shared", "security-threat-model", "references", "js.md")
        write(path, "Drop calls whose arguments are\nconstants. Then read each hit.\n")
        errors = [e for e in cs.check(self.root).errors if "arguments are constants" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("references/js.md", errors[0])
        write(path, "Drop calls whose arguments are constants, unless the object they're called on can come from input.\n")
        self.assertEqual([e for e in cs.check(self.root).errors if "arguments are constants" in e], [])

    def test_hold_back_exception_stays_subordinate(self):
        # Read alone, "null dereferences go upstream" overrides the rule for a null dereference judged a vulnerability.
        self.add("fuzzing", "fuzz-campaign")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-campaign", "SKILL.md")
        write(path, skill("fuzz-campaign", body="# T\n\nNull dereferences and leaks go upstream as ordinary\nbugs.\n"))
        errors = [e for e in cs.check(self.root).errors if "ordinary bugs" in e]
        self.assertEqual(len(errors), 1, errors)
        write(path, skill("fuzz-campaign", body="# T\n\nNull dereferences go upstream as ordinary bugs, unless a "
                                               "condition above holds.\n"))
        self.assertEqual([e for e in cs.check(self.root).errors if "ordinary bugs" in e], [])

    def test_dos_growth_rule_keeps_the_small_input_case(self):
        self.add("fuzzing", "fuzz-java")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-java", "references", "triage.md")
        write(path, "Compare run times: only growth faster than the size is an algorithmic DoS signal.\n")
        errors = [e for e in cs.check(self.root).errors if "faster than the size" in e]
        self.assertEqual(len(errors), 1, errors)
        write(path, "Growth faster than the size shows an algorithmic DoS, and so does a small input that hits the timeout.\n")
        self.assertEqual([e for e in cs.check(self.root).errors if "faster than the size" in e], [])

    def test_keep_rule_keeps_the_broad_gain_case(self):
        self.add("fuzzing", "fuzz-coverage")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-coverage", "references", "diagnosis.md")
        write(path, "A harness change is kept only if its aim becomes covered (`SKILL.md`, step 9).\n")
        errors = [e for e in cs.check(self.root).errors if "aim becomes covered" in e]
        self.assertEqual(len(errors), 1, errors)
        write(path, "A harness change is kept only if its aim becomes covered or scope coverage clearly rises.\n")
        self.assertEqual([e for e in cs.check(self.root).errors if "aim becomes covered" in e], [])

    def test_launcher_commands_use_env_sh_L(self):
        # env.sh's L is a command (python3 $F/bin/launcher.py); a relative
        # L=.seclab/... breaks after a cd, and python3 $L runs python3 twice.
        self.add("fuzzing", "fuzz-harness")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-harness", "references", "runs.md")
        write(path, "```sh\nL=.seclab/fuzz/bin/launcher.py\npython3 $L status\n```\n")
        errors = [e for e in cs.check(self.root).errors if "env.sh" in e]
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("references/runs.md:2", errors[0])
        write(path, "```sh\n$L status\npython3 .seclab/fuzz/bin/launcher.py start --help\n```\n")
        self.assertEqual([e for e in cs.check(self.root).errors if "env.sh" in e], [])

    def test_engine_status_is_the_same_in_both_skills(self):
        self.add("fuzzing", "fuzz-java", skill("fuzz-java", body="# J\n\n**Status:** maintained.\n"))
        self.add("fuzzing", "fuzz-js", skill("fuzz-js", body="# JS\n\n**Status:** releases are rare.\n"))
        errors = [e for e in cs.check(self.root).errors if "Status" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-js/SKILL.md", errors[0])

    def test_workflow_skills_open_with_the_same_run_every_step_line(self):
        line = "**Run every step** this skill gives for the request; skip one only when the user asks.\n"
        self.add("fuzzing", "fuzz-campaign", skill("fuzz-campaign", body="# C\n\n" + line))
        self.add("fuzzing", "fuzz-harness", skill("fuzz-harness", body="# H\n\n" + line.replace("when", "if")))
        self.add("fuzzing", "fuzz-triage", skill("fuzz-triage", body="# T\n"))
        errors = [e for e in cs.check(self.root).errors if "Run every step" in e]
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("fuzz-harness/SKILL.md", errors[0])
        self.assertIn("differs", errors[0])
        self.assertIn("fuzz-triage/SKILL.md", errors[1])
        self.assertIn("has 0", errors[1])

    def test_standalone_skills_have_the_same_before_stopping_check(self):
        line = "- **Before stopping,** check the Done when items that this request's steps produce.\n"
        self.add("fuzzing", "fuzz-harness", skill("fuzz-harness", body="# H\n\n## Next\n\n" + line))
        self.add("fuzzing", "fuzz-coverage", skill("fuzz-coverage", body="# C\n\n## Next\n\n" + line.replace("check", "verify")))
        errors = [e for e in cs.check(self.root).errors if "Before stopping" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-harness/SKILL.md", errors[0])
        self.assertIn("differs", errors[0])

    def test_language_skill_pointers_resolve_in_every_language_skill(self):
        # "the language skill's run.md, \"Title\"" means whichever language skill the project uses.
        for lang, title in (("fuzz-c-cpp", "Merging"), ("fuzz-java", "Merging"), ("fuzz-js", "Merge")):
            self.add("fuzzing", lang, skill(lang, body="# L\n\n[references/run.md](references/run.md)\n"))
            write(os.path.join(self.root, "skills", "fuzzing", lang, "references", "run.md"), "# R\n\n## %s\n" % title)
        body = ("# C\n\nMerge with the language skill's run.md, \"Merging\". Replay with the\n"
                "language skill's coverage.md. Use the language skill's merge command. Run it\n"
                "(language skill, `regression.md`).\n")
        self.add("fuzzing", "fuzz-coverage", skill("fuzz-coverage", body=body))
        errors = sorted(e for e in cs.check(self.root).errors if "fuzz-coverage/SKILL.md" in e)
        self.assertEqual(len(errors), 6, errors)
        joined = "\n".join(errors)
        self.assertIn("fuzz-js/references/run.md has no heading or step titled 'Merging'", joined)
        for lang in ("fuzz-c-cpp", "fuzz-java", "fuzz-js"):
            self.assertIn("%s has no coverage.md" % lang, joined)
        self.assertIn("merge command", joined)
        self.assertIn("language skill, `regression.md", joined)
        self.assertNotIn("next to it", joined)

    def test_language_skills_have_the_same_references_and_tested_versions(self):
        refs = "".join("[references/%s](references/%s)\n" % (f, f) for f in cs.LANGUAGE_REFERENCES)
        self.add("fuzzing", "fuzz-java", skill("fuzz-java", body="# J\n\n" + refs + "\n## Tested versions (checked x)\n"))
        self.add("fuzzing", "fuzz-js", skill("fuzz-js", body="# JS\n\n" + refs + "\n## Supported versions\n"))
        for lang in ("fuzz-java", "fuzz-js"):
            for f in cs.LANGUAGE_REFERENCES:
                if (lang, f) != ("fuzz-js", "coverage.md"):
                    write(os.path.join(self.root, "skills", "fuzzing", lang, "references", f), "# X\n")
        errors = [e for e in cs.check(self.root).errors if "a language skill needs" in e]
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(all("fuzz-js" in e for e in errors), errors)
        self.assertTrue(any("references/coverage.md" in e for e in errors), errors)
        self.assertTrue(any("Tested versions" in e for e in errors), errors)

    def test_symptom_tables_are_the_last_common_failures_section(self):
        refs = "[references/a.md](references/a.md) [references/b.md](references/b.md) [references/c.md](references/c.md)"
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body="# R\n\n" + refs + "\n"))
        table = "| Symptom | Cause and fix |\n|---|---|\n| x | y |\n"
        refdir = os.path.join(self.root, "skills", "fuzzing", "fuzz-run", "references")
        write(os.path.join(refdir, "a.md"), "# A\n\n## Common failures\n\n" + table)
        write(os.path.join(refdir, "b.md"), "# B\n\n## Setup\n\n" + table + "\n## Run\n")
        write(os.path.join(refdir, "c.md"), "# C\n\n## Common failures\n\n" + table + "\n## Later\n")
        errors = [e for e in cs.check(self.root).errors if "Common failures" in e]
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("b.md:5", errors[0])
        self.assertIn("c.md:5", errors[1])
        self.assertIn("last section", errors[1])

    def test_every_reference_is_linked_from_its_skill(self):
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body="# R\n\nSee [references/a.md](references/a.md).\n"))
        for name in ("a.md", "b.md"):
            write(os.path.join(self.root, "skills", "fuzzing", "fuzz-run", "references", name), "# X\n")
        errors = [e for e in cs.check(self.root).errors if "not linked" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("references/b.md", errors[0])

    def test_blocking_bug_rule_stated_once(self):
        rule = "A property violation is filtered by\ndefault: a narrow precondition.\n"
        self.add("fuzzing", "fuzz-triage", skill("fuzz-triage", body=rule))
        self.add("fuzzing", "fuzz-harness")
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-harness", "references", "gate.md"), rule)
        errors = [e for e in cs.check(self.root).errors if "restates" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-harness/references/gate.md", errors[0])

    def test_next_action_rule_stated_once(self):
        rule = "The most relevant code wins; cost only\nbreaks ties.\n"
        self.add("fuzzing", "fuzz-coverage", skill("fuzz-coverage", body=rule))
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-coverage", "references", "diagnosis.md"), rule)
        errors = [e for e in cs.check(self.root).errors if "restates" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-coverage/SKILL.md", errors[0])

    def test_ledger_values_are_listed_in_the_template(self):
        # A Progress or Report value a skill sets must be one the ledger's Open crashes legend lists.
        self.add("fuzzing", "fuzz-campaign")
        ledger = os.path.join(self.root, "skills", "fuzzing", "fuzz-campaign", "references", "ledger.md")
        write(ledger, "## Open crashes\n| Bucket | Progress | Report |\n\nProgress: `triaged` (verdict),\n"
                      "`fixed-locally`. Report: `-`, or `drafted`.\n\n## Long runs\nState: `queued`.\n")
        self.add("fuzzing", "fuzz-triage", skill("fuzz-triage", body="Set its Progress to `queued`; Report\n`drafted`.\n"
                                                                   "Set its Progress to `fixed-locally`.\n"))
        errors = [e for e in cs.check(self.root).errors if "Open crashes" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("fuzz-triage/SKILL.md", errors[0])
        self.assertIn("Progress `queued`", errors[0])

    def test_pointers_into_other_skills_and_script_paths_resolve(self):
        # Other skills are named, not linked, so the link check doesn't see these.
        self.add("fuzzing", "fuzz-campaign")
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-campaign", "scripts", "init.py"),
              'SCRIPTS = ("launcher.py", "coverage.py")\n')
        self.add("fuzzing", "fuzz-harness")
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-harness", "references", "build.md"), "# B\n")
        body = (
            "# C\n\n```sh\npython3 <this skill's directory>/../fuzz-campaign/scripts/init.py\n"
            "python3 <this skill's directory>/../fuzz-campaign/scripts/setup.py\n"
            "$L status; python3 .seclab/fuzz/bin/coverage.py summary; python3 \"$F/bin/buckets.py\"\n```\n\n"
            "Build as in `fuzz-harness`'s build.md, run as in `fuzz-harness`'s\nruns.md, gate as in `fuzz-gate`'s gate.md.\n"
        )
        self.add("fuzzing", "fuzz-coverage", skill("fuzz-coverage", body=body))
        errors = [e for e in cs.check(self.root).errors if "fuzz-coverage/SKILL.md" in e and "needs exactly one" not in e]
        self.assertEqual(len(errors), 4, errors)
        for wrong in ("scripts/setup.py", "buckets.py", "`fuzz-harness`'s runs.md", "`fuzz-gate`'s gate.md"):
            self.assertTrue(any(wrong in e for e in errors), wrong)

    def test_pointers_into_other_files_name_a_title(self):
        # Step numbers shift when steps move: a pointer into another file names the step's title,
        # and that title must exist there. Numbers stay fine inside a file.
        self.add("fuzzing", "fuzz-triage", skill("fuzz-triage", body=(
            "# T\n\n## Steps\n\n1. **Collect** new files.\n2. **Reproduce** each input.\n"
            "3. **Check the precondition, then\n   freshness.** Run it.\n")))
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-harness", "SKILL.md"), skill("fuzz-harness"))
        write(os.path.join(self.root, "skills", "fuzzing", "fuzz-harness", "references", "gate.md"),
              "# Gate\n\n## 2. Reaches the target\n\n## 4. No state leaking between inputs (a smoke check)\n\n"
              "## 6. Respects the threat model\n")
        self.add("fuzzing", "fuzz-coverage", skill("fuzz-coverage", body=(
            "# C\n\n## Steps\n\n1. **Check the sinks:** run it ([references/diagnosis.md](references/diagnosis.md)).\n")))
        good = ("Reproduce (`fuzz-triage`, \"Reproduce\") as `fuzz-harness`'s gate.md, \"Reaches the\ntarget\", "
                "says; run `fuzz-harness`'s gate.md, \"No state leaking between inputs\" and \"Respects the threat "
                "model\"; see `SKILL.md`'s \"Check the sinks\" and (`fuzz-triage`, \"Check the precondition, then "
                "freshness\"); then step 3 of this file.\n\n")
        bad = ("(`fuzz-triage`, step 2), `fuzz-harness`'s gate.md, check 4, `SKILL.md`'s step 1, step 4 of\n"
               "`fuzz-triage`, checks 4 and 6 (`fuzz-harness`,\ngate.md), (`fuzz-triage`, \"Reproduction\"), "
               "`fuzz-harness`'s gate.md, \"Reaches the target\" and \"Is fast\", runs.md, \"Pick\".\n")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-coverage", "references", "diagnosis.md")
        write(path, good)
        self.assertEqual([e for e in cs.check(self.root).errors if "diagnosis.md" in e], [])
        write(path, good + bad)
        errors = [e for e in cs.check(self.root).errors if "diagnosis.md" in e]
        self.assertEqual(len(errors), 8, errors)
        for wrong in ("step 2", "check 4", "step 1", "step 4 of", "checks 4 and 6", "'Reproduction'", "'Is fast'",
                      "runs.md, \"Pick\""):
            self.assertTrue(any(wrong in e for e in errors), wrong)

    def test_sections_in_order(self):
        body = "# R\n\n## When to use\n\n## Reads\n\n## Start or resume\n\n## Setup\n\n## Next\n\n- Standalone: stop.\n"
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body=body))
        errors = [e for e in cs.check(self.root).errors if "order" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("Start or resume", errors[0])
        # Sections of their own, and no Start, are fine.
        body = "# R\n\n## When to use\n\n## Start or resume\n\n## Reads\n\n## Setup\n\n## Tested versions\n\n## Next\n"
        self.add("fuzzing", "fuzz-run", skill("fuzz-run", body=body))
        self.add("fuzzing", "fuzz-c-cpp", skill("fuzz-c-cpp", body="# C\n\n## When to use\n\n## Reads\n\n## Steps\n"))
        self.assertEqual([e for e in cs.check(self.root).errors if "order" in e], [])

    def test_terms_keep_one_meaning(self):
        # "Leak check" also reads as LeakSanitizer's; a target is an entry point, not an action's aim.
        self.add("fuzzing", "fuzz-harness")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-harness", "references", "gate.md")
        write(path, "Run the leak-check block, then the gate's leak\ncheck. Keep a change if its target becomes covered.\n")
        errors = [e for e in cs.check(self.root).errors if "references/gate.md" in e and "say" in e]
        self.assertEqual(len(errors), 3, errors)
        write(path, "<!-- ci: c-leak-check -->\nThe gate's state-leak check. Keep a change if its aim becomes covered.\n")
        self.assertEqual([e for e in cs.check(self.root).errors if "references/gate.md" in e and "say" in e], [])

    def test_commands_have_no_brace_expansion(self):
        # sh (dash) passes {1..3} on unexpanded; the agents' shells may be sh.
        self.add("fuzzing", "fuzz-triage")
        path = os.path.join(self.root, "skills", "fuzzing", "fuzz-triage", "references", "group.md")
        write(path, "```sh\nls runs/repro-<h>-{<first>..<last>}/log\n$L start -- \"{run}/corpus\"\n```\n"
                    "Prose about {a..b} isn't a command.\n")
        errors = [e for e in cs.check(self.root).errors if "brace expansion" in e]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("references/group.md:2", errors[0])

    def test_repository_passes(self):
        report = cs.check(ROOT)
        if os.path.isdir(os.path.join(ROOT, "skills")) and cs.find_skills(ROOT):
            self.assertEqual(report.errors, [])
            for path in cs.LONG_FILES:
                self.assertTrue(os.path.exists(os.path.join(ROOT, path)), "LONG_FILES lists a missing file: " + path)


if __name__ == "__main__":
    unittest.main()
