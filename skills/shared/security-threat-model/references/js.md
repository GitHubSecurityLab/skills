# JavaScript/TypeScript sink catalog

The "Detector" line says whether Jazzer.js reports the sink on its own.

Patterns are for ripgrep:
`rg -n -e '<pattern>' --glob '!node_modules' <dirs>`. They also work with
`grep -rnE`, except for the `\x60` escape (a backtick). Search source
files only, not tests or build output. TypeScript files count the same as
JavaScript. Drop calls as `SKILL.md`, "Find sinks", says. For example,
`text.replace(/\s+$/, "")` stays when `text` can come from input.

## Code execution
Detector: yes for `eval` and `Function` (remote-code-execution); no for
`vm`.
```
\beval\s*\(|new\s+Function\s*\(|\bFunction\s*\(|vm\.(runIn\w*|Script|compileFunction)|set(Timeout|Interval)\s*\(\s*['"\x60]
```
Check: input reaching a code string.

## Command execution
Detector: partly (command-injection): only the first argument, not an
arguments array or `sh -c`.
```
child_process|shelljs|execa|\b(cp|childProcess|shell)\.\w+\s*\(|(^|[^.A-Za-z0-9_$])(exec|execSync|execFile|execFileSync|spawn|spawnSync|fork)\s*\(
```
Check: input in the command or arguments; `shell: true`.

## File paths
Detector: partly (path-traversal): not a path already resolved.
```
\.(readFile|writeFile|appendFile|createReadStream|createWriteStream|unlink|rm|rmdir|rename|mkdir|open|copyFile|symlink|readdir)(Sync)?\s*\(|path\.(join|resolve|normalize)\s*\(
```
Check: paths built from input without canonicalization and a
base-directory check.

## Archive extraction
Detector: partly, as for file paths.
```
extractAllTo|\.extract\s*\(|tar\.(x|extract)|unzip|entry\.(path|fileName)
```
Check: entry names written to disk without checks ("zip slip").

## Prototype pollution
Detector: yes. By default, the detector checks the built-in prototypes
for pollution after every input. Checking every assignment is opt-in and
slow.
```
__proto__|constructor\s*\[|Object\.assign\s*\(|\b(merge|deepMerge|extend|deepExtend|defaultsDeep|set|setPath|setValue)\s*\(|\w+\[\w+\]\s*=([^=]|$)
```
Check: recursive merges and path setters using keys from input.

## Regular expressions
Detector: no. Fuzzing finds exponential backtracking as a timeout, but
rarely finds quadratic backtracking that way.
```
\bRegExp\s*\(|\.(test|exec|match|matchAll|replace|replaceAll|search|split)\s*\(
```
The patterns find regex literals through the calls that use them. With a
string pattern, `replace`, `replaceAll` and `split` use no regex.

Check: input used as a pattern; a regex that backtracks, applied to input.

## Outbound requests
Detector: yes (ssrf).
```
\bhttps?\.(request|get)\s*\(|\bfetch\s*\(|axios|\bgot\s*\(|undici|net\.(connect|createConnection)|dgram
```
Check: input-controlled host, port or URL.

## Template rendering
Detector: no.
```
ejs\.render|pug\.(compile|render)|[Hh]andlebars\.compile|_\.template|nunjucks\.renderString|mustache\.render
```
Check: input used as template source (server-side template injection).

## Deserialization
Detector: no.
```
unserialize\s*\(|yaml\.load\s*\(|serialize-javascript|v8\.deserialize
```
Check: unsafe loaders applied to input.

## SQL and NoSQL queries
Detector: no.
```
\.(query|execute|raw)\s*\([^)]*\+|\.(query|execute|raw)\s*\(\s*\x60[^\x60]*\$\{|\$where|\$regex
```
Check: string-built queries and query-operator injection.

## Resource sizes
Detector: no. Bugs at these sinks show up as out-of-memory or timeout.
```
Buffer\.(alloc|allocUnsafe)\s*\(|new\s+Array\s*\(|\.repeat\s*\(
```
Check: sizes taken from input (denial of service from small inputs).

## Notes

- Values from `JSON.parse` stay attacker-controlled; parsed objects are
  sources for prototype pollution and dynamic property writes.
- A sink that only receives values the threat model marks as trusted
  (operator configuration) isn't reachable by the attacker. Record it as
  unreachable.
