# Java sink catalog

"Detector" names the Jazzer bug detector (sanitizer) that reports the sink
on its own.

Patterns are for ripgrep (`rg -n -e '<pattern>' --type java <dirs>`) and also
work with `grep -rnE`. Search main sources, not tests. Drop calls as
`SKILL.md`, "Find sinks", says: `text.matches("[a-z]+")` stays when `text`
can come from input.

## Deserialization
Detector: `Deserialization`, for `ObjectInputStream` only; other deserializers only when they load a class named in the input (`ReflectiveCall`).
```
ObjectInputStream|readObject\s*\(|readUnshared\s*\(|XMLDecoder|XStream|SerializationUtils\.deserialize|Kryo|new\s+Yaml\s*\(|activateDefaultTyping|enableDefaultTyping|@JsonTypeInfo
```
Check: input reaching a deserializer that can instantiate arbitrary types.

## XML parsing (external entities, SSRF)
Detector: `XmlParserSsrfGuidance` steers parsers toward external fetches, which `ServerSideRequestForgery` reports.
```
DocumentBuilderFactory|SAXParserFactory|XMLInputFactory|TransformerFactory|SchemaFactory|SAXReader|SAXBuilder|XMLReader
```
Check: whether external entities and DTDs are disabled for parsers that read input.

## OS commands
Detector: `OsCommandInjection`, partly: only the executable (`argv[0]`), not the arguments of `sh -c`.
```
Runtime\.getRuntime\(\)\.exec|ProcessBuilder|\.exec\s*\(
```
Check: input in the executable or the arguments.

## File paths
Detector: `FilePathTraversal`, partly: one target path.
```
new\s+File\s*\(|Paths\.get\s*\(|Path\.of\s*\(|File(Input|Output)Stream\s*\(|Files\.(newInputStream|newOutputStream|write|writeString|delete|copy|move|createDirectories|readAllBytes)\s*\(|getNext\w*Entry|\w*(Zip|Jar|Archive)Entry\b|[eE]ntry\w*\.getName\s*\(\)
```
Check: paths built from input without canonicalization and a base-directory
check; archive entry names written to disk ("zip slip").

## JNDI and LDAP
Detector: `NamingContextLookup` (remote JNDI lookups), `LdapInjection`.
```
\.lookup\s*\(|lookupLink\s*\(|InitialContext|DirContext|\.search\s*\(
```
Check: input in lookup names, distinguished names or search filters.

## SQL
Detector: `SqlInjection`, when a real driver parses the query (not a mock).
```
createStatement|executeQuery|executeUpdate|\.execute\s*\([^)]*\+|prepareStatement\s*\([^)]*\+|createQuery\s*\([^)]*\+|createNativeQuery
```
Check: queries built by string concatenation with input.

## Expression languages and scripting
Detector: `ExpressionLanguageInjection` (EL, Spring EL, OGNL, JEXL 2, MVEL), `ScriptEngineInjection`, `TemplateInjection` (FreeMarker); partly: not Velocity, Groovy or other template engines.
```
ExpressionFactory|ELProcessor|SpelExpressionParser|parseExpression|Ognl\.|OgnlUtil|MVEL\.|ScriptEngine|\.eval\s*\(|GroovyShell|freemarker\.template|Velocity|TemplateEngine
```
Check: input used as an expression, script or template source.

## Reflection and class loading
Detector: `ReflectiveCall`, partly: class and native library loading, not `getMethod`, `invoke` or `newInstance` on existing classes.
```
Class\.forName\s*\(|loadClass\s*\(|getMethod\s*\(|getDeclaredMethod\s*\(|\.invoke\s*\(|newInstance\s*\(
```
Check: class or method names taken from input.

## Regular expressions
Detector: `RegexInjection`, for a pattern from input (invalid or
explosive). A fixed pattern on input text (ReDoS) only shows up as a
timeout, and rarely when it's quadratic.
```
Pattern\.compile\s*\(|\.matcher\s*\(|\.matches\s*\(|\.replaceAll\s*\(|\.replaceFirst\s*\(|\.split\s*\(
```
Check: input used as a pattern; a regex that backtracks, applied to input.

## XPath
Detector: `XPathInjection`.
```
\bXPath\w*|[xX][pP]ath\w*\.(compile|evaluate)\s*\(
```
Check: XPath expressions built from input.

## Outbound requests
Detector: `ServerSideRequestForgery`.
```
new\s+URL\s*\(|URI\.create|openConnection|HttpClient|HttpRequest|RestTemplate|WebClient|new\s+Socket\s*\(
```
Check: input-controlled host, port or URL.

## Resource sizes
Detector: `BigDecimal` steers toward huge exponents; the rest shows up as timeouts or out-of-memory.
```
new\s+BigDecimal\s*\(|new\s+(byte|char|int|long)\s*\[\s*\w+|ByteBuffer\.allocate|new\s+ArrayList\s*<[^>]*>\s*\(\s*\w+
```
Check: sizes or exponents taken from input (denial of service from small inputs).

## Notes

- `sun.misc.Unsafe` array access is checked by the `UnsafeSanitizer` detector.
- Native code reached through JNI is outside these detectors; fuzz it as a
  separate C/C++ target, or with Jazzer's `--asan`/`--ubsan` options.
- A sink that only receives values the threat model marks as trusted isn't
  reachable by the attacker; record it as such.
