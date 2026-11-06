#!/usr/bin/env python3
"""Group fuzzer crash reports into buckets by crash type and top frames.

Input: log files (or directories of log files) captured while reproducing
crash inputs, one log per input. Recognized reports:
  - AddressSanitizer, UndefinedBehaviorSanitizer, MemorySanitizer (which
    reports as a WARNING), LeakSanitizer, and libFuzzer errors (timeout,
    out-of-memory, ...), including FuzzTest crashes, which use the same
    sanitizer reports; a SEGV's kind adds the access type, and "near null"
    for an address in the zero page
  - Jazzer (Java): "== Java Exception: ..." with "at pkg.Class.method(...)",
    and in JUnit mode "FuzzTestFindingException" followed by "Caused by: ..."
  - FuzzTest: "=== BUG FOUND!", then the report of a signal (an abort) if
    any; a watchdog message before it ("[!] Per-input timeout exceeded",
    "[!] RSS limit exceeded"); else a property failure's check
  - Jazzer (Java) timeouts: the thread dump's thread that ran the input
  - stack overflows (Node's "RangeError: Maximum call stack size exceeded"
    too): grouped by the project functions that recur in the stack, not by
    where it ran out
  - Jazzer.js: "==PID== Uncaught Exception: ..." or a bug detector title,
    with "at fn (file:line:col)" frames (file:// URLs for ES modules), or
    without a stack (thrown strings and other non-Error values; prototype
    pollution, whose detail follows on an indented line; "Segmentation
    Fault", a crash in native code); V8's "FATAL ERROR: ... JavaScript heap
    out of memory", also without a stack

Frames from sanitizer runtimes, the fuzzing engine, the language runtime
and system libraries are skipped, and so are harness frames (fuzz entry
points, main() in native code, files named like harnesses, and files right
in a fuzz/ or fuzzing/ directory), unless nothing else is left; native
frames below the entry point belong to the engine's driver or libc. C++
names are compared without parameters, qualifiers and return types;
unsymbolized native frames are named by module
and offset (`binary+0x1a2b`), and a jump to an invalid address is the
frame `<invalid pc>`. A leak counts as the fuzzing engine's only when its
stacks show engine frames. JS dependency frames (node_modules) are skipped
unless the stack has no other project frames, so a fuzzed dependency keeps
its own frames.

The signature is the crash type plus the top N remaining function names
(default 3); a timeout uses only the frame closest to the harness, since
where the alarm fired varies, and a report without a stack uses its
location. Each bucket also lists its crash locations (the first frame with
one, without column, relative to the --root trees; for a timeout, the
innermost function and its file), a second fingerprint: a bucket with
several locations may hold distinct bugs. A report with neither a stack nor
a location (a Jazzer.js timeout) groups by its kind alone. Logs without a
recognized report are listed as "no crash detected" (useful for State
non-reproducible).

Standard library only, Python 3.9+. Reads files; never runs target code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse

# --------------------------------------------------------------------------
# Frame filters

NATIVE_SKIP_FUNCS = re.compile(
    r"^(__asan|__ubsan|__msan|__lsan|__sanitizer|_?__interceptor_|__interception|"
    r"_sigtramp|__sigtramp|_start$|start$|__libc_start|__GI_|abort$|raise$|"
    r"pthread_kill|__pthread_kill|__assert_rtn|__assert_fail|__cxa_|"
    r"std::|__cxxabi|fuzzer::|fuzztest::|centipede::|absl::|testing::|RUN_ALL_TESTS$|"
    r"operator new|operator delete|malloc$|calloc$|realloc$|free$|"
    r"posix_memalign$|memalign$|aligned_alloc$|valloc$|reallocarray$|"
    r"_Znwm|_Znam|_ZdlPv|_ZdaPv|__memcpy|__memmove|__memset|__strlen|afl_|__afl|"
    # libc functions the sanitizers intercept, and the runtime's helpers; named
    # in a frame when the runtime has no debug info.
    r"mem(cpy|move|set|cmp|chr|rchr|mem)$|bcmp$|"
    r"str(len|nlen|cpy|ncpy|cat|ncat|cmp|ncmp|casecmp|ncasecmp|chr|rchr|str|casestr|dup|ndup|spn|cspn|pbrk|tok)$|"
    r"wcs(len|nlen|cpy|ncpy|cat|cmp)$|v?(f|s|sn)?printf$|v?[fs]?scanf$|"
    r"\w+InterceptorCommon$|printf_common$|scanf_common$)"
)
NATIVE_SKIP_FILES = re.compile(
    r"(^|/)(Fuzzer[A-Za-z]*\.cpp|FuzzerMain\.cpp|afl-[^/]*\.c|aflpp_driver\.c|"
    r"sanitizer_[^/]*|asan_[^/]*|ubsan_[^/]*|msan_[^/]*|lsan_[^/]*)(:|$)"
)
NATIVE_SKIP_MODULES = re.compile(
    r"(libclang_rt|libsystem_|libc\.so|libc\+\+|libc\+\+abi|libstdc\+\+|"
    r"libpthread|ld-linux|dyld|libdyld|libgcc_s|libobjc|libunwind|linux-vdso|linux-gate|"
    r"^/usr/lib/|^/lib/|^/System/)"
)
ENGINE_FRAME = re.compile(r"^(fuzzer::|fuzztest::|centipede::)|(^|/)Fuzzer[A-Za-z]*\.cpp(:|$)")
UNSYMBOLIZED = re.compile(r"\+0x[0-9a-fA-F]+$")
JAVA_SKIP = re.compile(
    r"^(com\.code_intelligence\.jazzer\.|java\.|javax\.|jdk\.|sun\.|kotlin\.|"
    r"org\.junit\.|junit\.|org\.opentest4j\.|org\.apache\.maven\.surefire\.|"
    r"org\.gradle\.|worker\.org\.gradle\.)"
)
JS_SKIP = re.compile(
    r"(node:internal|^internal/|@jazzer\.js|/jazzer\.js/packages/|<anonymous>$|^node:)"
)
JS_DEPENDENCY = re.compile(r"/node_modules/")
HARNESS_FUNCS = re.compile(
    r"^(LLVMFuzzerTestOneInput|LLVMFuzzerInitialize|LLVMFuzzerCustomMutator|"
    r"LLVMFuzzerCustomCrossOver|(?:[\w$.]+\.)?fuzzerTestOneInput|"
    # Jazzer.js CommonJS CLI targets; an ESM target's frame is a bare "fuzz",
    # too common a name, so only its file's name or directory marks it.
    r"module\.exports\.fuzz|exports\.fuzz|Object\.fuzz)$"
)
HARNESS_FILES = re.compile(r"(?i)(fuzzer|fuzz_?test|_fuzz|fuzz_|\.fuzz\.)")
HARNESS_DIRS = re.compile(r"(^|/)fuzz(ing)?/")
# A file right in such a directory: the harness and its helpers (a check
# in fuzz-js's fuzz/<target>.js). Not one further down: a checkout in
# ~/fuzzing/, the workspace's .seclab/fuzz/build/.
HARNESS_DIR_FILES = re.compile(r"(^|/)fuzz(ing)?/[^/]+$")

# --------------------------------------------------------------------------
# Line patterns

NATIVE_FRAME = re.compile(r"^\s*#(\d+)\s+0x[0-9a-fA-F]+\s+(?:in\s+)?(.*)$")
# Module prefixes: "java.base/", "java.base@21.0.1/", and "app//" (JDK 21 thread dumps).
JAVA_FRAME = re.compile(r"^\s*at\s+(?:[\w.\-@]*/)*([\w$.<>]+)\(([^)]*)\)\s*$")
JAVA_THREAD = re.compile(r'^(Thread\[|")')
# "at fn (path:line:col)", whose path may have spaces (CommonJS paths aren't
# encoded), or "at path:line:col", whose path has spaces only if absolute.
JS_FRAME = re.compile(r"^\s*at\s+(?:async\s+)?(.+?)\s+\((.+):(\d+):(\d+)\)\s*$")
JS_BARE_FRAME = re.compile(r"^\s*at\s+(?:async\s+)?(/[^()]*?|[^()\s]+?):(\d+):(\d+)\s*$")
JS_STACK_OVERFLOW = "RangeError: Maximum call stack size exceeded"
V8_OOM = re.compile(r"^FATAL ERROR: .*JavaScript heap out of memory")

ASAN_ERROR = re.compile(
    r"==\d+==\s*ERROR:\s*(AddressSanitizer|MemorySanitizer|ThreadSanitizer|"
    r"HWAddressSanitizer|LeakSanitizer):\s*(.*)$"
)
UBSAN_ERROR = re.compile(r"^(.+?:\d+(?::\d+)?):\s*runtime error:\s*(.*)$")
SAN_WARNING = re.compile(r"==\d+==\s*WARNING:\s*(MemorySanitizer|ThreadSanitizer):\s*(.*)$")
BUILD_ID = re.compile(r"\s*\(BuildId: [0-9a-fA-F]+\)$")
LIBFUZZER_ERROR = re.compile(r"==\d+==\s*ERROR:\s*libFuzzer:\s*(.*)$")
JAVA_EXCEPTION = re.compile(r"^==\s*Java Exception:\s*([\w$.]+)(?::\s*(.*))?$")
JAVA_ASSERT = re.compile(r"^==\s*Java Assertion Error")
JUNIT_FINDING = re.compile(r"com\.code_intelligence\.jazzer\.junit\.FuzzTestFindingException:")
CAUSED_BY = re.compile(r"^Caused by:\s*([\w$.]+)(?::\s*(.*))?$")
FUZZTEST_BUG = re.compile(r"^=== BUG FOUND!")
FUZZTEST_COUNTEREXAMPLE = re.compile(r"^(.+?:\d+): Counterexample found for (\S+?)\.?$")
GTEST_FAILURE = re.compile(r"^(.+:\d+): Failure$")
JS_FINDING = re.compile(r"^==\d+==\s+(Uncaught Exception:\s*)?(.+)$")
FUZZTEST_WATCHDOG = re.compile(r"^\[!\] (Per-input timeout|RSS limit) exceeded")
ACCESS = re.compile(r"^(READ|WRITE) of size \d+")
SIGNAL_ACCESS = re.compile(r"The signal is caused by a (READ|WRITE) memory access")
ZERO_PAGE = re.compile(r"(?i)hint: (address|pc) points to the zero page")

ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
NUMBER = re.compile(r"\b\d+\b")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def _normalize(text: str) -> str:
    text = QUOTED.sub("_", text)
    text = ADDRESS.sub("_", text)
    text = NUMBER.sub("N", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Frame parsing


def js_frame(line: str):
    """(function, `path:line`, "") of a Node stack frame, or None. ES
    modules' frames name the file by URL (file:///p/my%20pkg/x.js)."""
    match = JS_FRAME.match(line)
    if match:
        function, path, number = match.group(1), match.group(2), match.group(3)
    else:
        match = JS_BARE_FRAME.match(line)
        if not match:
            return None
        function, path, number = "<anonymous>", match.group(1), match.group(2)
    if path.startswith("file://"):
        path = urllib.parse.unquote(path[len("file://") :])
    return function, "%s:%s" % (path, number), ""


def parse_native_frame(rest: str):
    """Return (function, location, module) from the text after '#N 0x... in '."""
    rest = BUILD_ID.sub("", rest.strip())
    if rest == "(<unknown module>)":
        # A jump to an invalid address, e.g. through a null function pointer.
        return "<invalid pc>", "", ""
    module = ""
    location = ""
    match = re.search(r"(?:^|\s)\(([^()]*\+0x[0-9a-fA-F]+)\)$", rest)
    if match:
        module = match.group(1)
        rest = rest[: match.start()].strip()
    else:
        # An absolute path may have spaces; it starts at the first " /".
        match = re.search(r"\s(/.*:\d+(?::\d+)?)$", rest) or re.search(r"\s(\S+:\d+(?::\d+)?)$", rest)
        if match:
            location = match.group(1)
            rest = rest[: match.start()].strip()
        else:
            match = re.search(r"\s(\S+)$", rest)
            if match and ("/" in match.group(1) or match.group(1).endswith((".c", ".cc", ".cpp", ".h"))):
                location = match.group(1)
                rest = rest[: match.start()].strip()
    function = re.sub(r"\+0x[0-9a-fA-F]+$", "", rest).strip()
    if not function and module:
        # Unsymbolized: the module and offset identify the frame within a build.
        function = os.path.basename(module)
    return function, location, module


def native_skipped(function: str, location: str, module: str, extra) -> bool:
    if not function or function.startswith("<unknown"):
        return True
    if NATIVE_SKIP_FUNCS.search(_strip_params(function)):
        return True
    if location and NATIVE_SKIP_FILES.search(location):
        return True
    if module and NATIVE_SKIP_MODULES.search(module):
        return True
    return bool(extra and any(r.search(function) or r.search(location) for r in extra))


def collect_native_stack(lines: list, start: int) -> list:
    """Frames of the first stack at or after `start`: [(func, loc, module)]."""
    frames = []
    seen_frame = False
    for line in lines[start:]:
        match = NATIVE_FRAME.match(line)
        if match:
            if seen_frame and match.group(1) == "0":
                break
            seen_frame = True
            frames.append(parse_native_frame(match.group(2)))
        elif seen_frame and not line.strip():
            break
        elif seen_frame and line.strip().startswith(("SUMMARY", "==", "Shadow", "0x", "Indirect", "Direct")):
            break
    return frames


def native_stacks(lines: list, start: int) -> list:
    """All stacks (lists of frames) at or after `start`, in order."""
    stacks = []
    current: list = []
    for line in lines[start:]:
        match = NATIVE_FRAME.match(line)
        if match:
            if match.group(1) == "0" and current:
                stacks.append(current)
                current = []
            current.append(parse_native_frame(match.group(2)))
        elif current and not line.strip():
            stacks.append(current)
            current = []
    if current:
        stacks.append(current)
    return stacks


# --------------------------------------------------------------------------
# Report recognition


def analyze(text: str, extra_skip=None) -> dict:
    """Return {'kind', 'frames' (all), 'top' (filtered), 'location', 'engine'}."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()

        match = JAVA_EXCEPTION.match(stripped)
        if match or JAVA_ASSERT.match(stripped):
            cls, message = (match.group(1), match.group(2)) if match else ("Java assertion error", None)
            return _java_result(_java_chain(lines, index, cls, message), extra_skip)

        if JUNIT_FINDING.search(stripped):
            return _junit_result(lines, index, extra_skip)

        match = ASAN_ERROR.search(stripped) or SAN_WARNING.search(stripped)
        if match:
            tool, detail = match.group(1), match.group(2)
            if tool == "LeakSanitizer":
                return _leak_result(lines, index, extra_skip)
            kind = detail.split(" on ")[0].split(" (")[0].strip()
            for follow in lines[index + 1 : index + 4]:
                access = ACCESS.match(follow.strip())
                if access:
                    kind += " " + access.group(1)
                    break
            # Signals (SEGV, BUS): the access type; the zero page means a null dereference.
            hints = "\n".join(lines[index + 1 : index + 5])
            access = SIGNAL_ACCESS.search(hints)
            if access:
                kind += " " + access.group(1)
            if ZERO_PAGE.search(hints):
                kind += " near null"
            prefix = {"MemorySanitizer": "msan: ", "ThreadSanitizer": "tsan: "}.get(tool, "")
            frames = collect_native_stack(lines, index + 1)
            return _result(prefix + _normalize(kind), frames, "sanitizer", extra_skip)

        match = UBSAN_ERROR.match(stripped)
        if match:
            detail = match.group(2)
            # Quoted types and C++ names have "::"; the description ends at ": ".
            kind = "ubsan: " + _normalize(detail).split(": ")[0]
            frames = collect_native_stack(lines, index + 1)
            result = _result(kind, frames, "sanitizer", extra_skip)
            if not result["location"]:
                result["location"] = match.group(1)
            return result

        if V8_OOM.match(stripped):
            # V8 aborts the process itself, without a JS stack.
            return _result("out-of-memory (JS heap)", [], "jazzer.js", extra_skip)

        match = LIBFUZZER_ERROR.search(stripped)
        if match:
            detail = match.group(1)
            if detail.startswith("timeout"):
                kind = "timeout"
            elif detail.startswith("out-of-memory (malloc"):
                kind = "out-of-memory (malloc)"
            elif detail.startswith("out-of-memory"):
                kind = "out-of-memory (rss)"
            else:
                kind = _normalize(detail.split(":")[0])
            frames = collect_native_stack(lines, index + 1)
            if not frames:
                # Jazzer and Jazzer.js print managed stacks after libFuzzer errors.
                frames = _java_fuzzing_thread(lines[index + 1 :])
                engine = "jazzer" if frames else "libfuzzer"
                if not frames:
                    frames = [f for f in (js_frame(l) for l in lines[index + 1 :]) if f]
                    engine = "jazzer.js" if frames else "libfuzzer"
                return _result(kind, frames, engine, extra_skip)
            return _result(kind, frames, "libfuzzer", extra_skip)

        match = JS_FINDING.match(stripped)
        if match:
            title = match.group(2).strip()
            if title.startswith(("ERROR:", "WARNING:")):
                continue
            uncaught = bool(match.group(1))
            kind = title.split(":")[0].strip() if uncaught else title
            if uncaught and title == JS_STACK_OVERFLOW:
                kind = "stack-overflow"
            frames = []
            for follow in lines[index + 1 :]:
                frame = js_frame(follow)
                if frame:
                    frames.append(frame)
                elif frames and not follow.strip():
                    break
            if frames:
                return _result(kind if uncaught else _normalize(kind), frames, "jazzer.js", extra_skip)
            if not uncaught and title == "Segmentation Fault":
                # A crash in native code (an addon, Node itself): Jazzer.js
                # prints only this line.
                return _result(title, [], "jazzer.js", extra_skip)
            detail = lines[index + 1] if index + 1 < len(lines) else ""
            if uncaught or detail.startswith((" ", "\t")) and detail.strip():
                # Stackless findings: thrown strings and other non-Error values,
                # and detector reports such as prototype pollution, whose detail
                # follows on an indented line. Other "==pid== text" lines aren't.
                if uncaught:
                    kind = "Uncaught Exception: " + _normalize(title)
                else:
                    kind = title + ": " + _normalize(detail.strip().split(".")[0])
                return _result(kind, [], "jazzer.js", extra_skip)

        if FUZZTEST_BUG.match(stripped):
            return _fuzztest_result(lines, index, extra_skip)

    return {"kind": "no crash detected", "frames": [], "top": [], "location": "", "engine": ""}


def _java_fuzzing_thread(lines: list) -> list:
    """Frames of the thread that ran the input, from a dump of all threads
    (a Jazzer timeout); all frames if no thread runs Jazzer's driver."""
    threads, current = [], []
    for line in lines:
        if JAVA_THREAD.match(line) and current:
            threads.append(current)
            current = []
        frame = JAVA_FRAME.match(line)
        if frame:
            current.append((frame.group(1), frame.group(2), ""))
    if current:
        threads.append(current)
    for frames in threads:
        if any(f[0].startswith("com.code_intelligence.jazzer.driver.FuzzTargetRunner") for f in frames):
            return frames
    return [f for frames in threads for f in frames]


def _java_kind(cls: str, message: str) -> str:
    message = (message or "").strip().split('"')[0].strip()
    if "FuzzerSecurityIssue" in cls and message:
        first = message.splitlines()[0]
        # Jazzer's own issues for JVM errors: "Stack overflow (use '-Xss...' to reproduce)".
        if first.startswith("Stack overflow"):
            return "stack-overflow"
        if first.startswith("Out of memory"):
            return "out-of-memory"
        return first
    return cls


def _java_chain(lines: list, index: int, cls: str, message) -> list:
    """The throwable at `index` and its causes: [[class, message, frames], ...],
    outermost first. Suppressed exceptions and their causes are left out."""
    chain = [[cls, message, []]]
    suppressed = False
    for line in lines[index + 1 :]:
        if line.startswith("Caused by:"):
            cause = CAUSED_BY.match(line.strip())
            if cause:
                chain.append([cause.group(1), cause.group(2), []])
                suppressed = False
                continue
        if re.match(r"^\s+Suppressed:", line):
            suppressed = True
            continue
        if suppressed and line[:1] in (" ", "\t"):
            continue
        frame = JAVA_FRAME.match(line)
        if frame:
            chain[-1][2].append((frame.group(1), frame.group(2), ""))
            continue
        if re.match(r"^\s+\.\.\. \d+ more\s*$", line):
            continue
        if chain[-1][2] or suppressed or not line.strip() and any(c[2] for c in chain):
            break  # text after the frames: the trace ended
        # Otherwise the message continues (a detector's description, a cause's text).
    return chain


def _java_result(chain: list, extra_skip) -> dict:
    """Bucket on the deepest cause, as Jazzer's DEDUP_TOKEN does, so distinct
    bugs behind one wrapper exception stay apart; a FuzzerSecurityIssue in the
    chain (a detector finding, Jazzer's stack overflow) wins."""
    issue = next((c for c in chain if "FuzzerSecurityIssue" in c[0]), None)
    cls, message, frames = issue or chain[-1]
    if not frames:
        frames = next((c[2] for c in reversed(chain) if c[2]), [])
    return _result(_java_kind(cls, message), frames, "jazzer", extra_skip)


def _junit_result(lines: list, index: int, extra_skip) -> dict:
    """Jazzer in JUnit mode wraps the finding in a FuzzTestFindingException."""
    chain = _java_chain(lines, index, "FuzzTestFindingException", None)
    if len(chain) == 1:
        return _result("FuzzTestFindingException", [], "jazzer", extra_skip)
    return _java_result(chain[1:], extra_skip)


def _fuzztest_result(lines: list, index: int, extra_skip) -> dict:
    """FuzzTest prints its report before the stack of a signal (an abort, a
    failed assert) and after the message of its watchdog, which aborts from
    its own thread, so the stack that follows a watchdog message isn't the
    input's."""
    for line in lines[max(0, index - 40) : index]:
        watchdog = FUZZTEST_WATCHDOG.match(line.strip())
        if watchdog:
            result = _fuzztest_bug_result(lines, index)
            result["kind"] = "timeout" if watchdog.group(1) == "Per-input timeout" else "out-of-memory (rss)"
            return result
    after = analyze("\n".join(lines[index + 1 :]), extra_skip)
    if after["kind"] not in ("no crash detected", "fuzztest failure"):
        return after
    return _fuzztest_bug_result(lines, index)


def _fuzztest_bug_result(lines: list, index: int) -> dict:
    """A FuzzTest property failure: no stack, so use the failing check's location."""
    test, location = "", ""
    for line in lines[max(0, index - 40) : index]:
        failure = GTEST_FAILURE.match(line.strip())
        if failure:
            location = failure.group(1)
    for line in lines[index + 1 : index + 6]:
        found = FUZZTEST_COUNTEREXAMPLE.match(line.strip())
        if found:
            test = found.group(2)
            location = location or found.group(1)
            break
    # The file name alone: checkouts differ between runs. No line: as frames
    # elsewhere name functions, a line shift or a second failing check in the
    # test stays in the bucket; the location keeps it.
    path = re.sub(r":\d+$", "", location)
    top = [("%s@%s" % (test or "<fuzz test>", os.path.basename(path)), location, "")]
    return {"kind": "fuzztest failure", "frames": top, "top": top, "location": location, "engine": "fuzztest"}


def _keep(frame: tuple, engine: str, extra_skip, dependencies: bool = False) -> bool:
    function, location, module = frame
    if engine == "jazzer":
        if JAVA_SKIP.search(function):
            return False
        return not (extra_skip and any(r.search(function) for r in extra_skip))
    if engine == "jazzer.js":
        if not location or JS_SKIP.search(location):
            return False
        if not dependencies and JS_DEPENDENCY.search(location):
            return False
        return not (extra_skip and any(r.search(function) or r.search(location) for r in extra_skip))
    return not native_skipped(function, location, module, extra_skip)


def _entry_point(function: str, engine: str) -> bool:
    """A fuzz entry point; for native code also main(), the entry point of an
    AFL++ native harness (or of the engine's driver, whose frames are skipped)."""
    name = _strip_params(function)
    return bool(HARNESS_FUNCS.match(name)) or name == "main" and engine not in ("jazzer", "jazzer.js")


def is_harness_frame(frame: tuple, engine: str = "") -> bool:
    function, location, _module = frame
    if _entry_point(function, engine):
        return True
    path = location.rsplit(":", 2)[0] if location else ""
    if HARNESS_DIR_FILES.search(path) or function == "fuzz" and HARNESS_DIRS.search(path):
        return True
    if not path and "." in function:
        # Java frames: the class's simple name stands in for the file.
        path = function.rsplit(".", 1)[0].rsplit(".", 1)[-1]
    return bool(path) and bool(HARNESS_FILES.search(os.path.basename(path)))


def _project_frames(frames: list, engine: str, extra_skip) -> list:
    if engine not in ("jazzer", "jazzer.js"):
        # Frames below the fuzz entry point are the engine's driver (libFuzzer,
        # AFL++'s libAFLDriver, libc); stacks list the innermost frame first.
        for index, frame in enumerate(frames):
            if _entry_point(frame[0], engine):
                frames = frames[: index + 1]
                break
    kept = [f for f in frames if _keep(f, engine, extra_skip)]
    if engine == "jazzer.js" and not [f for f in kept if not is_harness_frame(f, engine)]:
        # Nothing outside the harness: the fuzzed code is a dependency.
        kept = [f for f in frames if _keep(f, engine, extra_skip, dependencies=True)]
    project = [f for f in kept if not is_harness_frame(f, engine)]
    return project or kept


def _result(kind: str, frames: list, engine: str, extra_skip) -> dict:
    kept = _project_frames(frames, engine, extra_skip)
    return {"kind": kind, "frames": frames, "top": kept, "location": _first_location(kept), "engine": engine}


def _first_location(frames: list) -> str:
    return next((frame[1] for frame in frames if frame[1]), "")


def _leak_result(lines: list, index: int, extra_skip) -> dict:
    stacks = native_stacks(lines, index + 1)
    for stack in stacks:
        kept = _project_frames(stack, "sanitizer", extra_skip)
        if kept:
            return {
                "kind": "leak",
                "frames": stack,
                "top": kept,
                "location": _first_location(kept),
                "engine": "sanitizer",
            }
    frames = stacks[0] if stacks else []
    engine = any(ENGINE_FRAME.search(f[0]) or ENGINE_FRAME.search(f[1]) for stack in stacks for f in stack)
    return {
        "kind": "leak in fuzzer runtime (not a target bug)" if engine else "leak (origin unknown)",
        "frames": frames,
        "top": [],
        "location": "",
        "engine": "sanitizer",
    }


# --------------------------------------------------------------------------
# Bucketing


CXX_QUALIFIERS = re.compile(r"(\s+(const|volatile|noexcept|&&?))+$")


def _strip_return_type(name: str) -> str:
    """`std::__1::optional<int> parse_items<int>` -> `parse_items<int>`: the
    last word outside brackets, unless the name is an operator."""
    depth, start = 0, 0
    for index, char in enumerate(name):
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        elif char == " " and depth == 0 and not name[:index].endswith("operator"):
            start = index + 1
    return name[start:]


def _strip_params(function: str) -> str:
    """Drop a trailing parameter list, qualifiers and a leading return type,
    keeping names like `(anonymous namespace)::f` and `operator()` intact."""
    name = CXX_QUALIFIERS.sub("", function.strip())
    if not name.endswith(")"):
        return _strip_return_type(name)
    depth = 0
    for index in range(len(name) - 1, -1, -1):
        if name[index] == ")":
            depth += 1
        elif name[index] == "(":
            depth -= 1
            if depth == 0:
                head = name[:index].rstrip()
                if not head or head.endswith(("operator", "::")):
                    return _strip_return_type(name)
                return _strip_return_type(head)
    return _strip_return_type(name)


def normalize_location(location: str, roots=()) -> str:
    """`path:line` without the column, relative to the first matching root."""
    if not location:
        return ""
    match = re.match(r"^(.*?):(\d+)(?::\d+)?$", location)
    path, line = (match.group(1), match.group(2)) if match else (location, "")
    path = path.replace("\\", "/")
    for root in roots:
        root = root.rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) + 1 :]
            break
    return "%s:%s" % (path, line) if line else path


def signature(result: dict, depth: int, roots=()) -> tuple:
    top = result["top"]
    if "stack-overflow" in result["kind"] or "StackOverflowError" in result["kind"]:
        # Where the stack runs out varies with the input; the recursion
        # doesn't: the project functions that recur in the stack.
        names = [_strip_params(function) or function for function, _loc, _mod in top]
        recurring = sorted({name for name in names if names.count(name) > 1})
        if recurring:
            return (result["kind"],) + tuple(recurring)
    if result["kind"] == "timeout" and top:
        # Where the alarm fired varies; the frame next to the harness doesn't.
        top = top[-1:]
    names = [_strip_params(function) or function for function, _loc, _mod in top[:depth]]
    if not names and result["location"]:
        names = [normalize_location(result["location"], roots)]
    return (result["kind"],) + tuple(names)


def fingerprint(result: dict, roots=()) -> str:
    """The second fingerprint: the crash location, or for a timeout the
    innermost function and its file (the line varies with the alarm)."""
    if result["kind"] == "timeout" and result["top"] and result["engine"] != "fuzztest":
        function, location, _module = result["top"][0]
        path = re.sub(r":\d+$", "", normalize_location(location, roots))
        name = _strip_params(function) or function
        return "%s in %s" % (name, path) if path else name
    return normalize_location(result["location"], roots)


def bucket_id(sig: tuple) -> str:
    return hashlib.sha1("|".join(sig).encode("utf-8")).hexdigest()[:8]


def iter_logs(paths: list):
    for path in paths:
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                for filename in sorted(filenames):
                    if filename.startswith("."):
                        continue
                    yield os.path.join(dirpath, filename)
        else:
            yield path


def read_text(path: str):
    with open(path, "rb") as handle:
        data = handle.read()
    name = os.path.basename(path)
    if name != "log" and not name.endswith((".log", ".txt")) and b"\x00" in data[:4096]:
        return None  # binary file, e.g. a crash input
    # Targets print NUL bytes anywhere in their output.
    return data.replace(b"\x00", b"").decode("utf-8", errors="replace")


def build_buckets(paths: list, depth: int, extra_skip, roots=()) -> tuple:
    buckets: dict = {}
    skipped = []
    for path in iter_logs(paths):
        text = read_text(path)
        if text is None:
            skipped.append(path)
            continue
        result = analyze(text, extra_skip)
        sig = signature(result, depth, roots)
        key = bucket_id(sig)
        location = fingerprint(result, roots)
        entry = buckets.setdefault(
            key,
            {
                "bucket": key,
                "kind": result["kind"],
                "frames": list(sig[1:]),
                "location": location,
                "locations": {},
                "engine": result["engine"],
                "logs": [],
            },
        )
        entry["logs"].append(path)
        if location:
            entry["locations"][location] = entry["locations"].get(location, 0) + 1
    ordered = sorted(
        buckets.values(),
        key=lambda b: (b["kind"] == "no crash detected", "fuzzer runtime" in b["kind"], -len(b["logs"]), b["kind"]),
    )
    return ordered, skipped


def example_name(path: str) -> str:
    """A log's file name, or its run id for launcher logs (runs/<id>/copy-<i>/log)."""
    name = os.path.basename(path)
    if name != "log":
        return name
    copy_dir = os.path.dirname(path)
    run_id = os.path.basename(os.path.dirname(copy_dir))
    copy = os.path.basename(copy_dir)
    if not run_id:
        return name
    return run_id if copy in ("copy-0", "") else "%s/%s" % (run_id, copy)


def _locations_text(entry: dict) -> str:
    locations = entry["locations"]
    if not locations:
        return "-"
    if len(locations) == 1:
        return next(iter(locations))
    ordered = sorted(locations.items(), key=lambda item: (-item[1], item[0]))
    return "; ".join("%s (%d)" % item for item in ordered)


def print_markdown(buckets: list, skipped: list, show: int) -> None:
    total = sum(len(b["logs"]) for b in buckets)
    print("## Crash buckets: %d logs in %d buckets" % (total, len(buckets)))
    print()
    print("| Bucket | Kind | Top frames | Crash locations | Logs | Examples |")
    print("|---|---|---|---|---|---|")
    for entry in buckets:
        frames = " > ".join(entry["frames"]) or "-"
        examples = ", ".join(example_name(p) for p in entry["logs"][:show])
        if len(entry["logs"]) > show:
            examples += ", ..."
        print(
            "| %s | %s | %s | %s | %d | %s |"
            % (
                entry["bucket"],
                entry["kind"].replace("|", "\\|"),
                frames.replace("|", "\\|"),
                _locations_text(entry).replace("|", "\\|"),
                len(entry["logs"]),
                examples,
            )
        )
    notes = []
    if any(len(b["locations"]) > 1 and b["kind"] != "timeout" for b in buckets):
        notes.append(
            "A bucket with several crash locations may hold distinct bugs, or one bug "
            "seen in builds whose lines shifted: compare the locations in triage."
        )
    if any(len(b["locations"]) > 1 and b["kind"] == "timeout" for b in buckets):
        notes.append(
            "A timeout bucket groups hangs by the frame next to the harness and lists the "
            "innermost function of each: several functions may be distinct hangs; check each one's loop."
        )
    if any(not b["frames"] and not b["locations"] and len(b["logs"]) > 1 and b["kind"] != "no crash detected"
           and "fuzzer runtime" not in b["kind"] for b in buckets):
        notes.append(
            "A bucket without frames or locations groups by kind alone (Jazzer.js prints no stack "
            "for a timeout, a V8 heap OOM or a crash in native code): its logs may be distinct bugs; "
            "fuzz-js's triage.md names a hang's loop."
        )
    if any(b["kind"] == "no crash detected" for b in buckets):
        notes.append(
            "'no crash detected': the log has no recognized report; the input may not "
            "reproduce (State non-reproducible), or the log was cut off."
        )
    if any("fuzzer runtime" in b["kind"] for b in buckets):
        notes.append(
            "'leak in fuzzer runtime': every leaked allocation comes from the fuzzing engine "
            "(e.g. libFuzzer's RSS thread on macOS). Not a target bug; replay with "
            "-rss_limit_mb=0 to avoid it."
        )
    if any(UNSYMBOLIZED.search(f) for b in buckets for f in b["frames"]):
        notes.append(
            "Frames like `binary+0x1a2b` are unsymbolized (module and offset; stable only within one "
            "build): replay with llvm-symbolizer on PATH and symbolize=1 for names and locations."
        )
    if skipped:
        notes.append("Skipped %d binary files (crash inputs, not logs)." % len(skipped))
    if notes:
        print()
        for note in notes:
            print("- " + note)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="crash_buckets.py",
        description="Group crash logs by crash type and top frames.",
    )
    parser.add_argument("paths", nargs="+", help="log files or directories of logs")
    parser.add_argument("--frames", type=int, default=3, help="frames in the signature (default 3)")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="REGEX",
        help="also skip frames whose function or location matches (repeatable)",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="DIR",
        help="tree root to strip from crash locations, e.g. the checkout and its worktrees (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    parser.add_argument("--examples", type=int, default=3, help="example logs listed per bucket")
    args = parser.parse_args(argv)
    try:
        extra = [re.compile(pattern) for pattern in args.ignore]
    except re.error as error:
        print("error: invalid --ignore pattern: %s" % error, file=sys.stderr)
        return 2
    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
        print("error: not found: %s" % ", ".join(missing), file=sys.stderr)
        return 1
    roots = {os.path.abspath(r) for r in args.root} | {os.path.realpath(r) for r in args.root} | set(args.root)
    roots = sorted(roots, key=len, reverse=True)
    buckets, skipped = build_buckets(args.paths, args.frames, extra, roots)
    if args.json:
        json.dump({"buckets": buckets, "skipped": skipped}, sys.stdout, indent=2)
        print()
    else:
        print_markdown(buckets, skipped, args.examples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
