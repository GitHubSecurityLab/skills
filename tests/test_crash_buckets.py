"""Tests for skills/fuzzing/fuzz-campaign/scripts/crash_buckets.py.

Fixtures in tests/fixtures/crashes/native are real libFuzzer + ASan/UBSan
logs captured on macOS with Homebrew LLVM, and java/ holds real Jazzer 0.30.0
JUnit-mode logs from Maven, plus java/standalone-chain, a standalone Jazzer
0.30.0 run (JDK 21, macOS, 2026-10-30) of a target that wraps an
ArrayIndexOutOfBoundsException in an IllegalStateException. The linux/
fixtures not named as captured below (abort-buildid, msan, oom, segv, uaf)
are reconstructed from OSS-Fuzz reports on x86_64, including the
"(BuildId: ...)" suffix and module-only frames that glibc without debug
info gives there, which the aarch64 CI image doesn't reproduce; real logs
of these kinds parsed the same in the release review; js/
follows the format printed by Jazzer.js; fuzztest/ is
reconstructed from FuzzTest's report format strings (runtime.cc), except
fuzztest/abort.log (a failed assert), timeout.log and rss-limit.log (its
watchdog), real output of FuzzTest 2026-06-29 with ASan and clang-20 on
Ubuntu 24.04, from the c-fuzztest scenario's toy. stack-overflow/ is real
libFuzzer + ASan output (Homebrew LLVM, macOS arm64) of a recursive
descent parser (parse_value -> parse_array or parse_object -> parse_value)
on deeply nested inputs; array-1..3 run out of stack in different frames.
Real output captured on 2026-10-27 (macOS arm64): js/prototype-pollution,
js/throw-string, js/throw-object and js/cjs-harness from Jazzer.js 4.0.0
CLI targets;
js/esm-file-url from a Jazzer.js 4.0.0 ES module target on Node 26, in a
directory with a space (captured 2026-10-28; the directory's prefix is
replaced with /work);
js/segfault from a Jazzer.js 4.0.0 CLI target on Node 26 that sends itself
SIGSEGV, as a crash in native code does (captured 2026-10-28; Jazzer.js
prints one line, without a stack, and exits 139); js/space-path-1 and -2
(two functions throwing the same TypeError) and js/overflow-array and
-object (a recursive descent parser on deeply nested input) from a
Jazzer.js 4.0.0 CommonJS CLI target on Node 26 in a directory with a space
(captured 2026-10-28; the directory's prefix is replaced with /work/my pkg);
java/timeout-jdk21 from Jazzer 0.30.0 on JDK 21.0.12 (a thread dump with
"app//" and "java.base@..." frames); cxx/ from a libFuzzer + ASan harness
built with Homebrew LLVM (demangled template names with return types).
Real output captured on 2026-10-29 from a toy library (lib.c, a libFuzzer
harness and a main() harness) in a directory with a space: linux/*.log
named below with clang 20 in the CI image (Ubuntu 24.04, aarch64; the
directory is "/work/my lab"), native/ ones with Homebrew LLVM 23 and
llvm-symbolizer on macOS (the directory's prefix is replaced with
"/Users/me/src/my lab"): linux/ and native/spaced-path, linux/main-harness
(the main() harness), linux/memcmp (an intercepted memcmp; Ubuntu's ASan
runtime has no debug info). native/uncaught-exception: an uncaught C++
exception, Homebrew LLVM 23 on macOS (2026-10-29). linux/unsymbolized-*:
the same toy with symbolize=0. linux/null-call: a call through a null
function pointer, built without UBSan (its function check reads before the
pointer); linux/wild-read; native/null-write; cxx/ubsan-function and
-vptr (Linux, the UBSan checks of fuzz-c-cpp's build.md). js/heap-oom: a
Jazzer.js 4.0.0 CLI target on Node 26 with --max-old-space-size=64
(macOS, 2026-10-29; the directory's prefix is replaced with /work/toy);
js/esm-dependency the same way, an ES module target in fuzz/ whose
dependency throws; js/timeout and js/timeout-jest, a hang (the ReDoS toy
fuzz/trim.js) as a CLI target and as a Jest fuzz test in fuzzing mode;
linux/timeout-1 and -2, two loops under one caller.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "scripts", "crash_buckets.py")
FIX = os.path.join(ROOT, "tests", "fixtures", "crashes")


def load_module():
    spec = importlib.util.spec_from_file_location("crash_buckets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cb = load_module()


def analyze_fixture(*parts):
    with open(os.path.join(FIX, *parts), encoding="utf-8") as handle:
        return cb.analyze(handle.read())


def names(result):
    return [frame[0] for frame in result["top"]]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cb.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class NativeTests(unittest.TestCase):
    def test_stack_overflow_skips_interceptor(self):
        result = analyze_fixture("native", "stack.log")
        self.assertEqual(result["kind"], "stack-buffer-overflow WRITE")
        self.assertEqual(names(result), ["copy_field", "parse_record"])
        self.assertEqual(result["location"], "lib.c:7")

    def test_heap_overflow(self):
        result = analyze_fixture("native", "heap.log")
        self.assertEqual(result["kind"], "heap-buffer-overflow READ")
        self.assertEqual(names(result)[0], "parse_record")

    def test_ubsan(self):
        result = analyze_fixture("native", "ubsan.log")
        self.assertEqual(result["kind"], "ubsan: signed integer overflow")
        self.assertEqual(names(result)[0], "parse_record")

    def test_timeout_skips_alarm_frames(self):
        result = analyze_fixture("native", "timeout.log")
        self.assertEqual(result["kind"], "timeout")
        self.assertEqual(names(result)[0], "parse_record")

    def test_leak_from_fuzzer_runtime(self):
        result = analyze_fixture("native", "leak.log")
        self.assertEqual(result["kind"], "leak in fuzzer runtime (not a target bug)")
        self.assertEqual(result["top"], [])

    def test_real_leak(self):
        text = (
            "==1==ERROR: LeakSanitizer: detected memory leaks\n\n"
            "Direct leak of 32 byte(s) in 1 object(s) allocated from:\n"
            "    #0 0x1 in malloc /src/llvm-project/compiler-rt/lib/asan/asan_malloc_linux.cpp:69:3\n"
            "    #1 0x2 in make_node /src/proj/tree.c:10:12\n"
            "    #2 0x3 in LLVMFuzzerTestOneInput /src/proj/fuzz.c:5:3\n\n"
            "SUMMARY: AddressSanitizer: 32 byte(s) leaked in 1 allocation(s).\n"
        )
        result = cb.analyze(text)
        self.assertEqual(result["kind"], "leak")
        self.assertEqual(names(result), ["make_node"])

    def test_no_crash(self):
        result = analyze_fixture("native", "none.log")
        self.assertEqual(result["kind"], "no crash detected")


class LinuxFormatTests(unittest.TestCase):
    def test_line_and_column_locations(self):
        result = analyze_fixture("linux", "uaf-1.log")
        self.assertEqual(result["kind"], "heap-use-after-free READ")
        self.assertEqual(names(result), ["png_read_chunk", "png_read_info"])
        self.assertEqual(result["location"], "/src/libpng/pngread.c:120:7")

    def test_unsymbolized_module_frame(self):
        result = analyze_fixture("linux", "segv.log")
        self.assertEqual(result["kind"], "SEGV READ near null")
        self.assertEqual(names(result)[0], "tiff_dir_entry")
        # The location comes from the first frame that has one.
        self.assertEqual(result["location"], "/src/libtiff/libtiff/tif_dirread.c:4100:12")

    def test_a_call_through_a_null_pointer(self):
        # pc 0: "(<unknown module>)"; the unwinder lost the callers.
        result = analyze_fixture("linux", "null-call.log")
        self.assertEqual(result["kind"], "SEGV READ near null")
        self.assertEqual(names(result), ["<invalid pc>"])
        self.assertEqual(result["location"], "")

    def test_segv_access_type_and_zero_page(self):
        self.assertEqual(analyze_fixture("linux", "wild-read.log")["kind"], "SEGV READ")
        # LLVM 23 on macOS writes "HINT:".
        self.assertEqual(analyze_fixture("native", "null-write.log")["kind"], "SEGV WRITE near null")

    def test_oom_skips_engine_and_malloc(self):
        result = analyze_fixture("linux", "oom.log")
        self.assertEqual(result["kind"], "out-of-memory (malloc)")
        self.assertEqual(names(result)[0], "xmlBufCreateSize")


class LinuxBuildIdTests(unittest.TestCase):
    def test_build_id_and_module_only_frames_are_skipped(self):
        result = analyze_fixture("linux", "abort-buildid-1.log")
        self.assertEqual(result["kind"], "ABRT")
        self.assertEqual(names(result), ["parse_record", "parse"])
        self.assertEqual(result["location"], "/src/proj/parse.c:12:3")

    def test_different_assertions_get_different_buckets(self):
        first = cb.signature(analyze_fixture("linux", "abort-buildid-1.log"), 3)
        second = cb.signature(analyze_fixture("linux", "abort-buildid-2.log"), 3)
        self.assertNotEqual(first, second)

    def test_memory_sanitizer_warning(self):
        result = analyze_fixture("linux", "msan.log")
        self.assertEqual(result["kind"], "msan: use-of-uninitialized-value")
        self.assertEqual(names(result), ["parse_header"])


class SpacedPathTests(unittest.TestCase):
    def test_native_frames(self):
        for platform, root in (("linux", "/work/my lab"), ("native", "/Users/me/src/my lab")):
            result = analyze_fixture(platform, "spaced-path.log")
            self.assertEqual(names(result), ["read_first", "dispatch"])
            self.assertEqual(cb.normalize_location(result["location"], [root]), "src/lib.c:18")

    def test_ubsan_and_fuzztest_lines(self):
        result = cb.analyze("/p/my lab/src/a.c:10:7: runtime error: signed integer overflow: 1 + 2147483647\n")
        self.assertEqual(result["kind"], "ubsan: signed integer overflow")
        self.assertEqual(result["location"], "/p/my lab/src/a.c:10:7")
        text = "=== BUG FOUND!\n\n/p/my lab/parser_fuzz_test.cc:31: Counterexample found for ParserTest.RoundTrips.\n"
        self.assertEqual(names(cb.analyze(text)), ["ParserTest.RoundTrips@parser_fuzz_test.cc"])


class FrameFilterTests(unittest.TestCase):
    def test_a_main_harness_buckets_like_libfuzzer(self):
        # The same bug through main() (an AFL++ native harness) and through LLVMFuzzerTestOneInput.
        main = cb.signature(analyze_fixture("linux", "main-harness.log"), 3)
        self.assertEqual(main, ("heap-buffer-overflow READ", "read_first", "dispatch"))
        self.assertEqual(main, cb.signature(analyze_fixture("linux", "spaced-path.log"), 3))

    def test_interceptors_without_debug_info(self):
        result = analyze_fixture("linux", "memcmp.log")
        self.assertEqual(names(result), ["compare_tag", "dispatch"])

    def test_system_libraries(self):
        # macOS: an uncaught C++ exception goes through libobjc's terminate handler.
        result = analyze_fixture("native", "uncaught-exception.log")
        self.assertEqual(cb.signature(result, 3), ("ABRT", "config::parse_value"))
        self.assertEqual(result["location"], "config.cc:7")
        self.assertTrue(cb.native_skipped("f", "", "/usr/lib/x86_64-linux-gnu/libz.so.1+0x1234", None))
        self.assertTrue(cb.native_skipped("f", "", "/System/Library/Frameworks/CoreFoundation+0x10", None))


class UnsymbolizedTests(unittest.TestCase):
    def test_frames_are_module_and_offset(self):
        first = analyze_fixture("linux", "unsymbolized-heap-1.log")
        second = analyze_fixture("linux", "unsymbolized-heap-2.log")
        self.assertEqual(names(first)[:2], ["fuzz_lib+0x156c48", "fuzz_lib+0x156d78"])
        self.assertNotEqual(cb.signature(first, 3), cb.signature(second, 3))
        self.assertTrue(cb.native_skipped("linux-vdso.so.1+0x7fc", "", "linux-vdso.so.1+0x7fc", None))

    def test_a_leak_is_the_runtimes_only_with_engine_frames(self):
        result = analyze_fixture("linux", "unsymbolized-leak.log")
        self.assertEqual(result["kind"], "leak")
        self.assertEqual(names(result)[0], "fuzz_lib+0x1135b4")
        text = (
            "==1==ERROR: LeakSanitizer: detected memory leaks\n\n"
            "Direct leak of 8 byte(s) in 1 object(s) allocated from:\n"
            "    #0 0x1 in malloc (/lib/x86_64-linux-gnu/libc.so.6+0x1)\n"
            "    #1 0x2 in strdup (/lib/x86_64-linux-gnu/libc.so.6+0x2)\n\n"
        )
        self.assertEqual(cb.analyze(text)["kind"], "leak (origin unknown)")

    def test_the_output_says_to_symbolize(self):
        _, out, _ = run(os.path.join(FIX, "linux", "unsymbolized-leak.log"))
        self.assertIn("llvm-symbolizer", out)
        _, out, _ = run(os.path.join(FIX, "linux", "uaf-1.log"))
        self.assertNotIn("llvm-symbolizer", out)


class SignatureTests(unittest.TestCase):
    def test_harness_frames_are_kept_when_nothing_else_is_left(self):
        text = (
            "==1==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x1\n"
            "WRITE of size 1 at 0x1 thread T0\n"
            "    #0 0x1 in LLVMFuzzerTestOneInput /p/fuzz/x_fuzzer.c:9:3\n"
        )
        self.assertEqual(names(cb.analyze(text)), ["LLVMFuzzerTestOneInput"])

    def test_fuzzed_js_dependency_keeps_its_frames(self):
        text = (
            "==7== Uncaught Exception: RangeError: bad\n"
            "    at decode (/p/node_modules/fast-thing/index.js:10:5)\n"
            "    at Object.fuzz (/p/fuzz/thing.fuzz.js:4:3)\n"
        )
        self.assertEqual(names(cb.analyze(text)), ["decode"])
        caller = (
            "==7== Uncaught Exception: RangeError: bad\n"
            "    at decode (/p/node_modules/fast-thing/index.js:10:5)\n"
            "    at readConfig (/p/lib/config.js:22:9)\n"
            "    at Object.fuzz (/p/fuzz/thing.fuzz.js:4:3)\n"
        )
        self.assertEqual(names(cb.analyze(caller)), ["readConfig"])

    def test_timeout_signature_uses_the_frame_next_to_the_harness(self):
        def timeout(top):
            return (
                "==1== ERROR: libFuzzer: timeout after 25 seconds\n"
                "    #0 0x1 in %s /p/src/scan.c:40:5\n"
                "    #1 0x2 in scan_body /p/src/scan.c:80:3\n"
                "    #2 0x3 in parse /p/src/parse.c:12:3\n"
                "    #3 0x4 in LLVMFuzzerTestOneInput /p/fuzz/p_fuzzer.c:5:3\n" % top
            )
        first = cb.signature(cb.analyze(timeout("next_token")), 3)
        second = cb.signature(cb.analyze(timeout("skip_space")), 3)
        self.assertEqual(first, ("timeout", "parse"))
        self.assertEqual(first, second)

    def test_stackless_ubsan_uses_its_location(self):
        def ubsan(line):
            return "/p/src/a.c:%d:7: runtime error: signed integer overflow: 1 + 2147483647\n" % line
        first = cb.signature(cb.analyze(ubsan(10)), 3, ["/p"])
        second = cb.signature(cb.analyze(ubsan(90)), 3, ["/p"])
        self.assertEqual(first, ("ubsan: signed integer overflow", "src/a.c:10"))
        self.assertNotEqual(first, second)

    def test_text_after_pid_marker_without_stack_is_not_a_js_finding(self):
        text = "==4242== some engine message\nINFO: Done 10 runs in 1 second(s)\n"
        self.assertEqual(cb.analyze(text)["kind"], "no crash detected")

    def test_strip_params(self):
        self.assertEqual(cb._strip_params("(anonymous namespace)::f(int, char*)"), "(anonymous namespace)::f")
        self.assertEqual(cb._strip_params("ns::Foo::operator()(int)"), "ns::Foo::operator()")
        self.assertEqual(cb._strip_params("parse"), "parse")


class TimeoutTests(unittest.TestCase):
    def test_hangs_under_one_caller_list_their_innermost_functions(self):
        # Two loops under dispatch(): one bucket, both functions shown, and the note.
        logs = [os.path.join(FIX, "linux", name) for name in ("timeout-1.log", "timeout-2.log")]
        _, out, _ = run("--root", "/work/my lab", *logs)
        self.assertIn("| timeout | dispatch | spin_spaces in src/lib.c (1); spin_tokens in src/lib.c (1) | 2 |", out)
        self.assertIn("A timeout bucket", out)
        _, out, _ = run(os.path.join(FIX, "native", "timeout.log"))
        self.assertIn("| timeout | parse_record | parse_record in lib.c | 1 |", out)

    def test_js_timeouts_have_no_stack(self):
        # Jazzer.js (CLI and Jest) ends a timeout's report at the libFuzzer line.
        logs = [os.path.join(FIX, "js", name) for name in ("timeout.log", "timeout-jest.log")]
        _, out, _ = run(*logs)
        self.assertIn("| timeout | - | - | 2 |", out)
        self.assertIn("groups by kind alone", out)
        _, out, _ = run(logs[0])
        self.assertNotIn("groups by kind alone", out)


class StackOverflowTests(unittest.TestCase):
    def test_grouped_by_the_recursion_not_where_it_ran_out(self):
        sigs = {name: cb.signature(analyze_fixture("stack-overflow", name), 3)
                for name in ("array-1.log", "array-2.log", "array-3.log", "object.log")}
        self.assertEqual(sigs["array-1.log"], ("stack-overflow", "parse_array", "parse_value"))
        self.assertEqual(sigs["array-1.log"], sigs["array-2.log"])
        self.assertEqual(sigs["array-1.log"], sigs["array-3.log"])
        self.assertEqual(sigs["object.log"], ("stack-overflow", "parse_object", "parse_value"))

    def test_without_recursion_the_top_frames_count(self):
        text = (
            "==1==ERROR: AddressSanitizer: stack-overflow on address 0x1\n"
            "    #0 0x1 in big_frame /src/a.c:3\n"
            "    #1 0x2 in parse /src/a.c:9\n"
            "    #2 0x3 in LLVMFuzzerTestOneInput /src/fuzz/f.c:5\n"
        )
        self.assertEqual(cb.signature(cb.analyze(text), 3), ("stack-overflow", "big_frame", "parse"))


class CxxTests(unittest.TestCase):
    def test_template_with_a_std_return_type_is_a_project_frame(self):
        result = analyze_fixture("cxx", "template-return-type.log")
        self.assertEqual(result["kind"], "heap-buffer-overflow WRITE")
        sig = cb.signature(result, 3)
        self.assertEqual(sig, ("heap-buffer-overflow WRITE", "parse_items<int>"))

    def test_std_algorithm_frames_returning_void_are_skipped(self):
        sig = cb.signature(analyze_fixture("cxx", "std-algorithm.log"), 3)
        self.assertEqual(
            sig, ("heap-buffer-overflow READ", "Table::at", "sort_rows(Table&, unsigned long)::$_0::operator()", "sort_rows")
        )

    def test_ubsan_kinds_with_qualified_names(self):
        result = analyze_fixture("cxx", "ubsan-function.log")
        self.assertEqual(result["kind"], "ubsan: call to function shapes::scale(int*) through pointer to "
                                         "incorrect function type _")
        self.assertEqual(cb.signature(result, 3)[1:], ("shapes::convert",))
        result = analyze_fixture("cxx", "ubsan-vptr.log")
        self.assertEqual(result["kind"], "ubsan: downcast of address _ which does not point to an object of type _")
        self.assertEqual(cb.signature(result, 3)[1:], ("shapes::square_area", "shapes::measure"))

    def test_names(self):
        self.assertEqual(cb._strip_params("std::__1::optional<int> parse_items<int>(char const*, unsigned long)"),
                         "parse_items<int>")
        self.assertEqual(cb._strip_params("Table::at(unsigned long) const"), "Table::at")
        self.assertEqual(cb._strip_params("unsigned long count(char const*) noexcept"), "count")
        self.assertEqual(cb._strip_params("operator new(unsigned long)"), "operator new")
        self.assertEqual(cb._strip_params("void std::__1::__sort<int*>(int*, int*)"), "std::__1::__sort<int*>")

    def test_driver_frames_below_the_harness_are_dropped(self):
        text = (
            "==1==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000\n"
            "    #0 0x1 in LLVMFuzzerTestOneInput /src/fuzz/f.c:5\n"
            "    #1 0x2 in ExecuteFilesOnyByOne (/out/fuzz_record+0x1a2b)\n"
            "    #2 0x3 in LLVMFuzzerRunDriver (/out/fuzz_record+0x1c3d)\n"
            "    #3 0x4 in main (/out/fuzz_record+0x1e4f)\n"
        )
        self.assertEqual(names(cb.analyze(text)), ["LLVMFuzzerTestOneInput"])


class JsTests(unittest.TestCase):
    def test_prototype_pollution_without_a_stack(self):
        result = analyze_fixture("js", "prototype-pollution.log")
        self.assertEqual(result["kind"], "Prototype Pollution: Prototype of Object changed")
        self.assertEqual(result["engine"], "jazzer.js")

    def test_harness_entry_points_outside_harness_named_files(self):
        # An ES module's bare "fuzz" in fuzz/thing.mjs; the fuzzed code is a dependency.
        result = analyze_fixture("js", "esm-dependency.log")
        self.assertEqual(names(result), ["decode"])
        self.assertEqual(result["location"], "/work/toy/node_modules/fast-thing/index.js:2")
        # module.exports = { fuzz(data) {...} } in any file.
        text = (
            "==7== Uncaught Exception: RangeError: bad\n"
            "    at decode (/p/node_modules/fast-thing/index.js:10:5)\n"
            "    at Object.fuzz (/p/test/thing.js:4:3)\n"
        )
        self.assertEqual(names(cb.analyze(text)), ["decode"])
        self.assertEqual(names(cb.analyze(text.replace("Object.fuzz", "fuzz"))), ["fuzz"])

    def test_helpers_in_a_fuzz_directory_are_harness_frames(self):
        # A hooks file in fuzz/: the hook, not the project code that ran the command.
        text = (
            "==7== Uncaught Exception: Error: Command injection\n"
            "    at Object.<anonymous> (/work/toy/fuzz/hooks.js:20:9)\n"
            "    at run (/work/toy/src/tools.js:8:3)\n"
            "    at module.exports.fuzz (/work/toy/fuzz/tools.js:4:3)\n"
        )
        result = cb.analyze(text)
        self.assertEqual(names(result), ["run"])
        self.assertEqual(result["location"], "/work/toy/src/tools.js:8")
        # A file below such a directory isn't the harness's: a checkout in ~/fuzzing/, a TypeScript
        # project's build in .seclab/fuzz/build/ts/.
        for path in ("/home/me/fuzzing/toy/src/tools.js", "/work/toy/.seclab/fuzz/build/ts/src/tools.js"):
            self.assertEqual(names(cb.analyze(text.replace("/work/toy/fuzz/hooks.js", path))),
                             ["Object.<anonymous>", "run"])

    def test_a_commonjs_cli_target_is_a_harness_frame(self):
        result = analyze_fixture("js", "cjs-harness.log")
        self.assertEqual(names(result), ["parseRecord"])

    def test_thrown_values_without_a_stack(self):
        self.assertEqual(analyze_fixture("js", "throw-string.log")["kind"], "Uncaught Exception: boom N")
        self.assertEqual(analyze_fixture("js", "throw-object.log")["kind"], "Uncaught Exception: unknown")

    def test_a_v8_heap_out_of_memory(self):
        # V8 aborts the process: no libFuzzer report and no JS stack.
        result = analyze_fixture("js", "heap-oom.log")
        self.assertEqual(result["kind"], "out-of-memory (JS heap)")
        self.assertEqual(result["engine"], "jazzer.js")

    def test_a_segmentation_fault_without_a_stack(self):
        result = analyze_fixture("js", "segfault.log")
        self.assertEqual(result["kind"], "Segmentation Fault")
        self.assertEqual(result["engine"], "jazzer.js")

    def test_commonjs_paths_with_a_space(self):
        # CommonJS frames name the file by its path, not encoded.
        self.assertEqual(names(analyze_fixture("js", "space-path-1.log")), ["parseName"])
        self.assertEqual(names(analyze_fixture("js", "space-path-2.log")), ["parseSize"])
        logs = [os.path.join(FIX, "js", name) for name in ("space-path-1.log", "space-path-2.log")]
        _, out, _ = run("--json", "--root", "/work/my pkg", *logs)
        locations = sorted(list(b["locations"]) for b in json.loads(out)["buckets"])
        self.assertEqual(locations, [["src/record.js:2"], ["src/record.js:6"]])

    def test_a_stack_overflow_is_grouped_by_its_recursion(self):
        array = analyze_fixture("js", "overflow-array.log")
        self.assertEqual(array["kind"], "stack-overflow")
        self.assertEqual(cb.signature(array, 3), ("stack-overflow", "parseArray", "parseValue"))
        self.assertEqual(cb.signature(analyze_fixture("js", "overflow-object.log"), 3),
                         ("stack-overflow", "parseArray", "parseObject", "parseValue"))

    def test_uncaught_exception(self):
        result = analyze_fixture("js", "uncaught.log")
        self.assertEqual(result["kind"], "TypeError")
        self.assertEqual(names(result), ["parseHeader", "parse"])
        self.assertEqual(result["engine"], "jazzer.js")

    def test_bug_detector_finding_skips_jazzer_frames(self):
        result = analyze_fixture("js", "command-injection.log")
        self.assertEqual(result["kind"], "Command Injection")
        self.assertEqual(names(result), ["runTool"])

    def test_es_module_file_urls_become_paths(self):
        # Node names ES modules' files by URL, percent-encoded.
        result = analyze_fixture("js", "esm-file-url.log")
        self.assertEqual(names(result), ["parse"])  # without the harness's fuzz
        _, out, _ = run("--json", "--root", "/work/my pkg", os.path.join(FIX, "js", "esm-file-url.log"))
        bucket = json.loads(out)["buckets"][0]
        self.assertEqual(bucket["locations"], {"src/parse.js:12": 1})


class JavaTests(unittest.TestCase):
    def test_jdk21_timeout_uses_the_fuzzing_thread(self):
        result = analyze_fixture("java", "timeout-jdk21.log")
        self.assertEqual(result["kind"], "timeout")
        self.assertEqual(names(result), ["toy.SlowParser.spin", "toy.SlowParser.parse"])
        self.assertEqual(cb.signature(result, 3), ("timeout", "toy.SlowParser.parse"))
        self.assertEqual(cb.analyze("  at app//a.B.c(B.java:1)\n"), cb.analyze("  at app//a.B.c(B.java:1)\n"))
        self.assertTrue(cb.JAVA_FRAME.match("\tat java.base@21.0.12.1/java.lang.Thread.run(Thread.java:1583)"))

    def test_security_issue(self):
        text = (
            "== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueCritical: OS Command Injection\n"
            "Executing OS commands with attacker-controlled data can lead to remote code execution.\n"
            "\tat com.code_intelligence.jazzer.sanitizers.OsCommandInjection.processImplStartHook(OsCommandInjection.kt:51)\n"
            "\tat java.base/java.lang.ProcessImpl.start(ProcessImpl.java)\n"
            "\tat java.base/java.lang.ProcessBuilder.start(ProcessBuilder.java:1110)\n"
            "\tat com.example.Tools.run(Tools.java:12)\n"
            "\tat com.example.ToolsFuzzTest.fuzz(ToolsFuzzTest.java:8)\n"
        )
        result = cb.analyze(text)
        self.assertEqual(result["kind"], "OS Command Injection")
        self.assertEqual(names(result), ["com.example.Tools.run"])
        self.assertEqual(result["location"], "Tools.java:12")

    def test_plain_exception(self):
        text = (
            "== Java Exception: java.lang.ArrayIndexOutOfBoundsException: Index 5 out of bounds for length 4\n"
            "\tat com.example.Parser.read(Parser.java:40)\n"
            "\tat com.example.ParserFuzzTest.fuzz(ParserFuzzTest.java:9)\n"
        )
        result = cb.analyze(text)
        self.assertEqual(result["kind"], "java.lang.ArrayIndexOutOfBoundsException")
        self.assertEqual(names(result)[0], "com.example.Parser.read")


def _chain(cause: str, frame: str, junit: bool = False) -> str:
    """A library wrapping a low-level failure in its own exception at one site."""
    body = (
        "com.example.CodecException: decode failed\n"
        "\tat com.example.Codec.decode(Codec.java:30)\n"
        "\tat com.example.CodecFuzzTest.fuzz(CodecFuzzTest.java:9)\n"
        "\tSuppressed: java.io.IOException: close failed\n"
        "\t\tat com.example.Stream.close(Stream.java:5)\n"
        "\t\t... 2 more\n"
        "Caused by: %s\n"
        "\tat %s\n"
        "\tat com.example.Codec.decode(Codec.java:28)\n"
        "\t... 1 more\n" % (cause, frame)
    )
    if junit:
        return ("com.code_intelligence.jazzer.junit.FuzzTestFindingException: com.example.CodecException: decode failed\n"
                "\tat com.code_intelligence.jazzer.junit.FuzzTestExecutor.execute(FuzzTestExecutor.java:1)\n"
                "Caused by: " + body)
    return "== Java Exception: " + body


class JavaChainTests(unittest.TestCase):
    def test_a_real_standalone_chain(self):
        result = analyze_fixture("java", "standalone-chain.log")
        self.assertEqual(result["kind"], "java.lang.ArrayIndexOutOfBoundsException")
        self.assertEqual(names(result)[:2], ["toy.Codec.field", "toy.Codec.decode"])
        self.assertEqual(result["location"], "Codec.java:6")

    def test_the_deepest_cause_decides(self):
        a = cb.analyze(_chain("java.lang.ArrayIndexOutOfBoundsException: Index 5 out of bounds for length 3",
                              "com.example.Fields.get(Fields.java:12)"))
        b = cb.analyze(_chain("java.lang.NegativeArraySizeException: -1", "com.example.Table.alloc(Table.java:44)"))
        self.assertEqual(a["kind"], "java.lang.ArrayIndexOutOfBoundsException")
        self.assertEqual(names(a)[0], "com.example.Fields.get")
        self.assertEqual(a["location"], "Fields.java:12")
        self.assertNotEqual(cb.signature(a, 3), cb.signature(b, 3))

    def test_standalone_and_junit_agree(self):
        cause, frame = "java.lang.ArrayIndexOutOfBoundsException: Index 5", "com.example.Fields.get(Fields.java:12)"
        self.assertEqual(cb.signature(cb.analyze(_chain(cause, frame)), 3),
                         cb.signature(cb.analyze(_chain(cause, frame, junit=True)), 3))

    def test_suppressed_exceptions_are_not_the_cause(self):
        result = cb.analyze(_chain("java.lang.IllegalStateException: bad", "com.example.State.check(State.java:3)"))
        self.assertNotIn("com.example.Stream.close", names(result))

    def test_jazzer_stack_overflow_and_out_of_memory(self):
        overflow = (
            "== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueLow: Stack overflow (use '-Xss921k' to reproduce)\n"
            "\tat com.example.Json.value(Json.java:20)\n"
            "\tat com.example.Json.parse(Json.java:8)\n"
            "\tat com.example.JsonFuzzTest.fuzz(JsonFuzzTest.java:9)\n"
            "Caused by: java.lang.StackOverflowError\n"
            "\tat com.example.Json.array(Json.java:31)\n"
            "\tat com.example.Json.value(Json.java:22)\n"
        )
        result = cb.analyze(overflow)
        self.assertEqual(result["kind"], "stack-overflow")
        self.assertEqual(names(result)[:2], ["com.example.Json.value", "com.example.Json.parse"])
        oom = (
            "== Java Exception: com.code_intelligence.jazzer.api.FuzzerSecurityIssueLow: Out of memory (use '-Xmx1g' to reproduce)\n"
            "Caused by: java.lang.OutOfMemoryError: Java heap space\n"
            "\tat com.example.Table.alloc(Table.java:44)\n"
        )
        result = cb.analyze(oom)
        self.assertEqual(result["kind"], "out-of-memory")
        self.assertEqual(names(result), ["com.example.Table.alloc"])


class JUnitModeTests(unittest.TestCase):
    def test_wrapped_exception_uses_caused_by_stack(self):
        result = analyze_fixture("java", "junit-index.log")
        self.assertEqual(result["kind"], "java.lang.ArrayIndexOutOfBoundsException")
        self.assertEqual(names(result), ["com.example.Parser.parse"])
        self.assertEqual(result["location"], "Parser.java:7")
        self.assertEqual(result["engine"], "jazzer")

    def test_detector_finding_message_on_next_line(self):
        result = analyze_fixture("java", "junit-command-injection.log")
        self.assertEqual(result["kind"], "OS Command Injection")
        self.assertEqual(names(result), ["com.example.Tools.run"])


class FuzzTestTests(unittest.TestCase):
    def test_property_failure_uses_failing_check(self):
        result = analyze_fixture("fuzztest", "property.log")
        self.assertEqual(result["kind"], "fuzztest failure")
        self.assertEqual(result["location"], "parser_fuzz_test.cc:31")
        self.assertEqual(names(result), ["ParserTest.ParseSerializeRoundTrips@parser_fuzz_test.cc"])

    def test_two_failing_checks_in_one_test_share_a_bucket(self):
        # The location names the check; the frame doesn't, as frames elsewhere name functions.
        with open(os.path.join(FIX, "fuzztest", "property.log")) as handle:
            text = handle.read()
        other = cb.analyze(text.replace("parser_fuzz_test.cc:31: Failure", "parser_fuzz_test.cc:33: Failure"))
        self.assertEqual(cb.signature(other, 3), cb.signature(cb.analyze(text), 3))
        self.assertEqual(other["location"], "parser_fuzz_test.cc:33")

    def test_the_test_file_is_named_without_its_directory(self):
        text = (
            "=== BUG FOUND!\n\n"
            "/work/a/repo/parser_fuzz_test.cc:31: Counterexample found for ParserTest.RoundTrips.\n"
        )
        other = text.replace("/work/a/repo", "/home/b/checkout")
        self.assertEqual(cb.signature(cb.analyze(text), 3), cb.signature(cb.analyze(other), 3))
        self.assertEqual(names(cb.analyze(text)), ["ParserTest.RoundTrips@parser_fuzz_test.cc"])

    def test_an_abort_uses_the_report_after_the_banner(self):
        result = analyze_fixture("fuzztest", "abort.log")
        self.assertEqual(result["kind"], "ABRT")
        self.assertEqual(cb.signature(result, 3)[1:], ("ParseRecord",))
        self.assertTrue(result["location"].endswith("src/record.cc:10:3"))

    def test_only_the_bug_banner_starts_a_report(self):
        # FuzzTest prints none of these; a target may.
        for line in ("[*] Fuzz test failed", "=== Fuzz test failure", "==== Fuzzing stopped"):
            self.assertEqual(cb.analyze(line + "\nINFO: done\n")["kind"], "no crash detected")

    def test_a_watchdog_abort_is_a_timeout_or_an_oom(self):
        result = analyze_fixture("fuzztest", "timeout.log")
        self.assertEqual(result["kind"], "timeout")
        self.assertEqual(names(result), ["RecordTest.SpinsOnS@record_fuzz_test.cc"])
        result = analyze_fixture("fuzztest", "rss-limit.log")
        self.assertEqual(result["kind"], "out-of-memory (rss)")
        self.assertEqual(names(result), ["RecordTest.HoardsOnH@record_fuzz_test.cc"])


class BucketTests(unittest.TestCase):
    def test_same_bug_different_lines_share_bucket(self):
        code, out, _ = run("--json", os.path.join(FIX, "linux"))
        self.assertEqual(code, 0)
        data = json.loads(out)
        uaf = [b for b in data["buckets"] if b["kind"] == "heap-use-after-free READ"]
        self.assertEqual(len(uaf), 1)
        self.assertEqual(len(uaf[0]["logs"]), 2)

    def test_logs_with_nul_bytes_are_read(self):
        with open(os.path.join(FIX, "native", "heap.log"), "rb") as handle:
            text = handle.read()
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "copy-0"))
            with open(os.path.join(tmp, "copy-0", "log"), "wb") as handle:
                handle.write(b"target output\x00\x01\x00\n" + text)
            with open(os.path.join(tmp, "crash-1"), "wb") as handle:
                handle.write(b"\x00\x01binary input")
            code, out, _ = run("--json", tmp)
        data = json.loads(out)
        self.assertEqual([b["kind"] for b in data["buckets"]], ["heap-buffer-overflow READ"])
        self.assertEqual(len(data["skipped"]), 1)

    def test_nul_bytes_after_the_first_4k_are_dropped(self):
        with open(os.path.join(FIX, "js", "throw-string.log"), "rb") as handle:
            text = handle.read()
        text = b"ordinary output\n" * 400 + text.replace(b"==32705== Uncaught", b"\x00==32705== Uncaught")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "late-nul.log")
            with open(path, "wb") as handle:
                handle.write(text)
            self.assertEqual(cb.analyze(cb.read_text(path))["kind"], "Uncaught Exception: boom N")

    def test_markdown_and_binary_skipping(self):
        code, out, _ = run(os.path.join(FIX, "native"))
        self.assertEqual(code, 0)
        self.assertIn("## Crash buckets: 10 logs in 9 buckets", out)
        self.assertIn("| stack-buffer-overflow WRITE | copy_field > parse_record | lib.c:7 |", out)
        self.assertIn("Skipped 1 binary files", out)
        last_rows = [line for line in out.splitlines() if line.startswith("| ")][-2:]
        self.assertTrue(any("no crash detected" in row for row in last_rows))

    def test_ignore_pattern_and_depth(self):
        _, out, _ = run("--json", "--frames", "1", "--ignore", "^copy_field$", os.path.join(FIX, "native", "stack.log"))
        data = json.loads(out)
        self.assertEqual(data["buckets"][0]["frames"], ["parse_record"])

    def test_missing_path(self):
        code, _, err = run("does-not-exist")
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_several_locations_are_listed_and_flagged(self):
        # The same function crashing at two lines: one bucket, two locations.
        code, out, _ = run(os.path.join(FIX, "linux", "uaf-1.log"), os.path.join(FIX, "linux", "uaf-2.log"))
        self.assertEqual(code, 0)
        self.assertIn("/src/libpng/pngread.c:120 (1); /src/libpng/pngread.c:123 (1)", out)
        self.assertIn("may hold distinct bugs", out)

    def test_roots_are_stripped_from_locations(self):
        text = (
            "==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1\n"
            "READ of size 1 at 0x1 thread T0\n"
            "    #0 0x1 in parse %s/src/p.c:10:3\n"
            "    #1 0x2 in LLVMFuzzerTestOneInput %s/fuzz/p_fuzzer.c:5:3\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            for name, root in (("a.log", "/ckout"), ("b.log", "/ckout.seclab-worktrees/abc")):
                with open(os.path.join(tmp, name), "w") as handle:
                    handle.write(text % (root, root))
            _, out, _ = run("--json", "--root", "/ckout", "--root", "/ckout.seclab-worktrees/abc", tmp)
        bucket = json.loads(out)["buckets"][0]
        self.assertEqual(bucket["locations"], {"src/p.c:10": 2})
        self.assertEqual(bucket["frames"], ["parse"])

    def test_a_root_through_a_symlink_matches_real_paths(self):
        # Runtimes and symbolizers print real paths (macOS: /private/tmp for /tmp).
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(os.path.realpath(tmp), "repo")
            os.makedirs(real)
            link = os.path.join(tmp, "link")
            os.symlink(real, link)
            log = os.path.join(tmp, "a.log")
            with open(log, "w") as handle:
                handle.write("==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1\n"
                             "    #0 0x1 in parse %s/src/p.c:10:3\n" % real)
            _, out, _ = run("--json", "--root", link, log)
        self.assertEqual(json.loads(out)["buckets"][0]["locations"], {"src/p.c:10": 1})

    def test_launcher_logs_are_named_by_run(self):
        self.assertEqual(cb.example_name("/x/runs/repro-h-3/copy-0/log"), "repro-h-3")
        self.assertEqual(cb.example_name("/x/runs/h-long1/copy-2/log"), "h-long1/copy-2")
        self.assertEqual(cb.example_name("/x/logs/crash-1.log"), "crash-1.log")

    def test_bad_regex(self):
        code, _, err = run("--ignore", "(", os.path.join(FIX, "native"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
