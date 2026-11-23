# Java sink catalog

The "Detector" line says whether a Jazzer bug detector (sanitizer)
reports the sink on its own (yes, partly or no) and names the detector.
`fuzz-java`'s harness.md, "Bug detectors", says what each one checks.

Patterns are for ripgrep: `rg -n -e '<pattern>' --type java <dirs>`.
They also work with `grep -rnE`. Search main sources, not tests. Drop
calls as `SKILL.md`, "Find sinks", says. For example,
`text.matches("[a-z]+")` stays when `text` can come from input.

## Deserialization
Detector: partly (`Deserialization`): `ObjectInputStream` only. Other
deserializers are reported only when they construct or call the
honeypot class `jaz.Zer` named in the input (`ReflectiveCall`).
```
ObjectInputStream|readObject\s*\(|readUnshared\s*\(|XMLDecoder|XStream|SerializationUtils\.deserialize|Kryo|new\s+Yaml\s*\(|activateDefaultTyping|enableDefaultTyping|@JsonTypeInfo
```
Check: input reaching a deserializer that can instantiate arbitrary types.

## XML parsing (external entities, SSRF)
Detector: partly (`ServerSideRequestForgery`): an external entity or DTD
fetched over the network, not one read from a local file. The guidance
hook `XmlParserSsrfGuidance` steers parsers toward such fetches.
```
DocumentBuilderFactory|SAXParserFactory|XMLInputFactory|TransformerFactory|SchemaFactory|SAXReader|SAXBuilder|XMLReader
```
Check: whether external entities and DTDs are disabled for parsers that
read input.

## OS commands
Detector: partly (`OsCommandInjection`): only the executable (`argv[0]`),
not the arguments of `sh -c`.
```
Runtime\.getRuntime\(\)\.exec|ProcessBuilder|\.exec\s*\(
```
Check: input in the executable or the arguments.

## File paths
Detector: partly (`FilePathTraversal`): only a path that normalizes to
the one path it looks for, `../jazzer-traversal` by default.
```
new\s+File\s*\(|Paths\.get\s*\(|Path\.of\s*\(|File(Input|Output)Stream\s*\(|Files\.(newInputStream|newOutputStream|write|writeString|delete|copy|move|createDirectories|readAllBytes)\s*\(|getNext\w*Entry|\w*(Zip|Jar|Archive)Entry\b|[eE]ntry\w*\.getName\s*\(\)
```
Check: paths built from input without canonicalization and a
base-directory check; archive entry names written to disk ("zip slip").

## JNDI and LDAP
Detector: yes for remote JNDI lookups (`NamingContextLookup`); partly for
LDAP (`LdapInjection`): only names and filters that become invalid syntax.
```
\.lookup\s*\(|lookupLink\s*\(|InitialContext|DirContext|\.search\s*\(
```
Check: input in lookup names, distinguished names or search filters.

## SQL
Detector: partly (`SqlInjection`): a query that becomes invalid syntax,
and only when a real driver parses it (not a mock).
```
createStatement|executeQuery|executeUpdate|\.execute\s*\([^)]*\+|prepareStatement\s*\([^)]*\+|createQuery\s*\([^)]*\+|createNativeQuery
```
Check: queries built by string concatenation with input.

## Expression languages and scripting
Detector: partly: `ExpressionLanguageInjection` for EL, Spring EL, OGNL,
JEXL 2 and MVEL; `ScriptEngineInjection`; `TemplateInjection` for
FreeMarker. Not Velocity, Groovy or other template engines.
```
ExpressionFactory|ELProcessor|SpelExpressionParser|parseExpression|Ognl\.|OgnlUtil|MVEL\.|ScriptEngine|\.eval\s*\(|GroovyShell|freemarker\.template|Velocity|TemplateEngine
```
Check: input used as an expression, script or template source.

## Reflection and class loading
Detector: partly (`ReflectiveCall`): class and native library loading,
not `getMethod`, `invoke` or `newInstance` on existing classes.
```
Class\.forName\s*\(|loadClass\s*\(|getMethod\s*\(|getDeclaredMethod\s*\(|\.invoke\s*\(|newInstance\s*\(
```
Check: class or method names taken from input.

## Regular expressions
Detector: partly (`RegexInjection`): a pattern from input that is
invalid or explosive. A fixed pattern applied to input text (ReDoS) shows
up only as a timeout, and rarely when matching takes quadratic time.
```
Pattern\.compile\s*\(|\.matcher\s*\(|\.matches\s*\(|\.replaceAll\s*\(|\.replaceFirst\s*\(|\.split\s*\(
```
Check: input used as a pattern; a regex that backtracks, applied to input.

## XPath
Detector: partly (`XPathInjection`): an expression from input is reported
only when it becomes invalid syntax.
```
\bXPath\w*|[xX][pP]ath\w*\.(compile|evaluate)\s*\(
```
Check: XPath expressions built from input.

## Outbound requests
Detector: yes (`ServerSideRequestForgery`).
```
new\s+URL\s*\(|URI\.create|openConnection|HttpClient|HttpRequest|RestTemplate|WebClient|new\s+Socket\s*\(
```
Check: input-controlled host, port or URL.

## Resource sizes
Detector: no. Bugs at these sinks show up as timeouts or out-of-memory;
the `BigDecimal` hook only steers the fuzzer toward huge exponents.
```
new\s+BigDecimal\s*\(|new\s+(byte|char|int|long)\s*\[\s*\w+|ByteBuffer\.allocate|new\s+ArrayList\s*<[^>]*>\s*\(\s*\w+
```
Check: sizes or exponents taken from input (denial of service from small
inputs).

## Notes

- The `UnsafeSanitizer` detector checks `sun.misc.Unsafe` array access.
- These detectors don't see native code reached through JNI. Fuzz it as a
  separate C/C++ target, or with Jazzer's `--asan`/`--ubsan` options.
- A sink that only receives values the threat model marks as trusted isn't
  reachable by the attacker. Record it as unreachable.
