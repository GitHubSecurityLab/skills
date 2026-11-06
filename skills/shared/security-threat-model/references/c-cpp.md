# C/C++ sink catalog

Two kinds of sinks:
- **Memory safety** (the sections up to Hotspots): a statement whose
  **size, index, offset or count comes from attacker data**. Not every
  `memcpy` is a sink.
- **Files, processes, network and output** (the sections after Hotspots):
  no sanitizer sees these. A check in the harness finds what it can
  compare, such as a path or an escaped string the API returns; a call it
  doesn't see, such as `system()` or a fetch, goes undetected and really
  runs.

Patterns are for ripgrep (`rg -n -e '<pattern>' <files>`) and also work with
`grep -nE`. Run them over the files in question, then read each hit. Drop hits
whose operands are constants or `sizeof(...)`.

## Input field reads (where attacker numbers come from)
```
ntoh[sl]|be(16|32|64)toh|le(16|32|64)toh|read_u?(8|16|32|64)|get_u?(16|32|64)|<<\s*(8|16|24|32)
\b(strto(u?ll?|[iu]max)|ato(i|ll?)|sscanf)\s*\(
```
Note which variables receive lengths, counts, offsets and type tags; text
formats parse them from digits (the second pattern).

## Copies with computed length
```
\b(memcpy|memmove|memset|strncpy|strncat|bcopy)\s*\(
```
Is the length derived from an input field? Is the destination size checked
against it?

## Unbounded string operations
```
\b(strcpy|strcat|sprintf|vsprintf|gets)\s*\(
```

## Allocations with arithmetic
```
\b\w*(malloc|calloc|realloc|Malloc|Calloc|Realloc|MALLOC|CALLOC|REALLOC|alloca)\s*\([^)]*[*+]
new\s+[A-Za-z_][A-Za-z0-9_:<>]*\s*\[
\b\w*(len|size|count|num)\w*\s*=[^=;]*([*+]|-[^>])[^;]*;
```
The last pattern finds sizes computed on an earlier line: follow the
variable to the allocation. Can the multiplication or addition overflow,
or the subtraction go below zero? Is the result checked?

## Index and pointer arithmetic
```
\[[^]]*\b((len|size|off|offset|idx|index|pos|count)\w*|n)\b[^]]*\]
\b\w+\s*[-+]=\s*\w*(len|size|off)
```
Is the index bounded by the buffer size, including off-by-one?

## Loops bounded by input
```
for\s*\([^;]*;[^;]*<=?[^;]*(len|count|num|size)
while\s*\([^)]*(len|count|remaining)
```
Does every iteration stay inside the buffer?

## Narrowing or sign changes of lengths
```
\(\s*(u?int(8|16|32)_t|short|char|int|unsigned)\s*\)\s*\w*(len|size|off)
```

## Format strings
```
\b(printf|vprintf)\s*\(\s*[^" ]
\b(fprintf|vfprintf|sprintf|vsprintf|dprintf|asprintf|syslog)\s*\([^,]*,\s*[^" ]
\b(snprintf|vsnprintf)\s*\([^,]*,[^,]*,\s*[^" ]
```
A format argument that isn't a string literal. A format on the next line
isn't found.

## Frees on error paths
```
goto\s+(err|error|fail|out|cleanup|bad)
```
Look at `free(...)` calls under those labels: double free, use after free.

## Hotspots

For the threat model, report hotspots, not single statements: the files and
functions with the most size, index or offset arithmetic fed by input field
reads, plus files with past security fixes (`git log --oneline -- <file>`),
and functions that recurse once per nesting level of the input without a
depth limit (a stack overflow from deep nesting).

## Files and archive entries
```
\b(fopen|freopen|open|openat|creat|unlink|remove|rename|mkdir|symlink|chmod|realpath|opendir)\s*\(
\b[io]?fstream\s+\w+\s*\(|archive_entry_pathname|archive_write_disk
```
Check: paths built from input stay inside the base directory; archive
entry names written to disk ("zip slip").

## Processes
```
\b(system|popen|exec[lv]p?e?|posix_spawnp?)\s*\(
```
Check: input in a shell command line, or in an executable's name or
options.

## Network
```
\b(connect|getaddrinfo|gethostbyname)\s*\(|CURLOPT_URL
```
Check: input-controlled hosts, URLs, external entities or includes.

## Output that others parse
```
\w*([eE]scape|[sS]anitiz|[qQ]uote|[eE]ncode\w*Entities)\w*\s*\(
```
Check: the output of escaping and sanitizing functions (for HTML, a shell,
SQL or a file format) contains nothing they should have removed or
escaped.
