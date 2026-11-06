"""The sink catalogs' and the harness recon's patterns find what they look for and skip known misfires (Python's
re reads them as ripgrep does)."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS = os.path.join(ROOT, "skills", "shared", "security-threat-model", "references")


def patterns(catalog: str, section: str) -> list:
    with open(os.path.join(REFS, catalog), encoding="utf-8") as handle:
        text = handle.read()
    body = re.search(r"^## %s(?: \([^\n]*\))?\n(.*?)(?=^## |\Z)" % re.escape(section), text, re.S | re.M).group(1)
    return [re.compile(p) for p in re.search(r"```\n(.*?)\n```", body, re.S).group(1).splitlines()]


# (catalog, section, lines it must find, lines it must skip)
CASES = [
    ("java.md", "File paths",
     ["new File(dir, entry.getName())", "ZipEntry e = zin.getNextEntry();",
      "for (ZipArchiveEntry entry : zip.getEntries()) {", "Path p = base.resolve(entry.getName());"],
     ["String n = obj.getClass().getName();", "log.info(node.getName());"]),
    ("java.md", "SQL",
     ['stmt.execute("DROP TABLE " + name);', "Statement s = conn.createStatement();", "rs = stmt.executeQuery(sql);"],
     ["executor.execute(task);", "pool.execute(() -> run());"]),
    ("java.md", "XPath",
     ["XPathExpression e = xpath.compile(expr);", "xPath.evaluate(query, doc);",
      "XPath xp = XPathFactory.newInstance().newXPath();"],
     ["Pattern p = Pattern.compile(re);", "engine.evaluate(script);"]),
    ("js.md", "Command execution",
     ["exec(cmd, done);", 'cp.spawn("sh", ["-c", input]);', 'const { execSync } = require("child_process");',
      "shell.exec(command);"],
     ["rules.block.exec(src);", "const m = re.exec(line);"]),
    ("js.md", "Prototype pollution",
     ["target[key] = source[key];", "obj[k] ="],
     ["if (a[i] === b[i]) {", "return x[k] == y;"]),
    ("c-cpp.md", "Format strings",
     ["printf(msg);", "fprintf(stderr, fmt, x);", "snprintf(buf, sizeof(buf), user_fmt, a);", "syslog(LOG_ERR, msg);"],
     ['fprintf(stderr, "error: %s\\n", msg);', 'snprintf(buf, sizeof buf, "%d", n);', 'printf("%s", s);',
      'sprintf(out, "%02x", b);']),
    ("c-cpp.md", "Files and archive entries",
     ['FILE *f = fopen(path, "rb");', "int fd = open(name, O_RDONLY);", "std::ifstream in(path);", "out.open(dest);",
      "unlink(tmp);", "const char *n = archive_entry_pathname(entry);"],
     ['FILE *f = fdopen(fd, "r");', "reopen_log();"]),
    ("c-cpp.md", "Processes",
     ["system(cmd);", 'FILE *p = popen(command, "r");', "execvp(argv[0], argv);", "posix_spawnp(&pid, file, NULL, NULL, args, env);"],
     ["execute(query);", "obj->system_id = 1;"]),
    ("c-cpp.md", "Network",
     ["getaddrinfo(host, port, &hints, &res);", "curl_easy_setopt(h, CURLOPT_URL, url);", "connect(fd, addr, len);"],
     ["connect_signal(button, cb);"]),
    ("c-cpp.md", "Output that others parse",
     ["html_escape(out, in, n);", "xmlEncodeEntitiesReentrant(doc, input);", "sanitize_filename(name);", "shell_quote(arg);"],
     ["escape_count++;"]),
    ("c-cpp.md", "Allocations with arithmetic",
     ["p = MALLOC(n * size);", "q = xmlMalloc(len + 1);", "r = g_realloc(r, n + 4);",
      "size_t body_len = total - hdr_len;"],
     ["p = malloc(len);", "size_t name_len = p->len;"]),
    ("c-cpp.md", "Input field reads",
     ["uint32_t n = ntohl(v);", "long count = strtol(p, &end, 10);", "width = atoi(field);",
      'sscanf(line, "%d %d", &w, &h);', "unsigned long long v = strtoull(s, NULL, 16);"],
     ["size_t n = strlen(s);"]),
    ("c-cpp.md", "Index and pointer arithmetic",
     ["p += hdr_len;", "remaining -= len;"],
     ["count -= 1;"]),
]


class CatalogPatternTest(unittest.TestCase):
    def test_cases(self):
        for catalog, section, found, skipped in CASES:
            compiled = patterns(catalog, section)
            for line in found:
                with self.subTest(catalog=catalog, section=section, line=line):
                    self.assertTrue(any(p.search(line) for p in compiled), "not found")
            for line in skipped:
                with self.subTest(catalog=catalog, section=section, line=line):
                    self.assertFalse(any(p.search(line) for p in compiled), "misfires")


class ReconPatternTest(unittest.TestCase):
    def test_existing_harnesses_found(self):
        with open(os.path.join(ROOT, "skills", "fuzzing", "fuzz-campaign", "references", "targets.md"), encoding="utf-8") as handle:
            pattern = re.compile(re.search(r"`rg -n -e '([^']+)'`", handle.read()).group(1))
        for line in ("int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {", "FUZZ_TEST(ParserTest, Parses);",
                     "DEFINE_PROTO_FUZZER(const fuzz::Input &input) {", "DEFINE_TEXT_PROTO_FUZZER(const Msg &m) {",
                     "while (__AFL_LOOP(1000)) {"):
            with self.subTest(line=line):
                self.assertRegex(line, pattern)

    def test_property_based_tests_found(self):
        with open(os.path.join(ROOT, "skills", "shared", "security-threat-model", "SKILL.md"), encoding="utf-8") as handle:
            pattern = re.compile(re.search(r"`rg -n -e '([^']+)'`", handle.read()).group(1))
        for line in ("fc.assert(fc.property(fc.string(), (s) => {", "await fc.assert(fc.asyncProperty(arb, async (v) => {",
                     'test.prop([fc.string()])("round-trips", (s) => {', "@Property void roundTrip(@ForAll String s) {",
                     "@Property(tries = 100)", 'rc::check("parse after print", [](const Doc &d) {',
                     "RC_GTEST_PROP(Parser, RoundTrips, (const std::string &s)) {"):
            with self.subTest(line=line):
                self.assertRegex(line, pattern)
        for line in ('@PropertySource("classpath:app.properties")', '@JsonProperty("name")',
                     'Object.defineProperty(obj, "x", desc);', 'String v = props.getProperty("key");',
                     'const fc = require("fast-check");'):
            with self.subTest(line=line):
                self.assertNotRegex(line, pattern)


if __name__ == "__main__":
    unittest.main()
