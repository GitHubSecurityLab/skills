# Regular expressions applied to input (Java/JS)

A reached regex isn't cleared just because fuzzing hit no timeout: fuzzing
finds only the exponential kind of backtracking, as a timeout. Most ReDoS
is quadratic and takes seconds only past about 50,000 characters, far
beyond the fuzzer's inputs. A regex backtracks when one part can match the
same text in more than one way:
- nested or adjacent quantifiers over overlapping characters: `(a+)+`, or
  `\s*,\s*$` on a run of spaces;
- a repeated class before something that fails: `[\r\n]+$` on newlines
  that end in another character.

1. **List** the regexes in the regular-expression sink groups whose state
   is reached, with tries left, and keep only those whose subject (the
   text matched) comes from input. Write them one per line in
   `.seclab/fuzz/sinks/<id>.regex`, with three tab-separated fields:
   - the site, as `file:line`;
   - the flags, in JavaScript's letters, which `recheck` takes. For Java,
     `CASE_INSENSITIVE`, `MULTILINE` and `DOTALL` are `i`, `m` and `s`,
     whether set in `Pattern.compile` or inline at the pattern's start,
     such as `(?i)`. Take inline flags out of the pattern, since `recheck`
     can't parse them, and leave out Java's other flags;
   - the pattern as the engine sees it, with a string literal's escapes
     resolved. Write a tab, line break or other control character in it
     as a regex escape, such as `\t`, `\n` or `\x0b`: a raw one breaks
     the file's tab-separated lines.
2. **Screen and time** them with `recheck`. It tells which ones backtrack
   and, for each of those, writes an attack that should time out. It's
   optional and needs Node.js. Without `recheck`, read each regex for the
   shapes above and write the attack yourself: a prefix, a repeated part,
   and a suffix that makes the match fail. Your attack takes the place of
   `repro` below. In interactive mode, ask before installing `recheck`, as
   for the install plan. `recheck` installs under `.seclab/fuzz/tools/`,
   outside the project:
   <!-- ci: redos-check -->
   ```sh
   npm install --prefix "$F/tools/recheck" --ignore-scripts --no-audit --no-fund --loglevel=error recheck@4.5.0
   NODE_PATH="$F/tools/recheck/node_modules" node - "$F/sinks/<id>.regex" "$F/sinks/<id>.redos" <input_timeout_s> <<'EOF'
   const fs = require("fs"), path = require("path"), { checkSync } = require("recheck");
   const [list, out, limit] = process.argv.slice(2);
   for (const line of fs.readFileSync(list, "utf8").split("\n").filter(Boolean)) {
     const [site, flags, source] = line.split("\t");
     const r = checkSync(source, flags);
     if (r.status !== "vulnerable") { console.log(`${site}\t${r.status}`); continue; }
     const { pumps, suffix, base } = r.attack, exp = r.complexity.type === "exponential";
     const attack = (n) => pumps.map((p) => p.prefix + p.pump.repeat(n + p.bias)).join("") + suffix;
     const re = new RegExp(source, flags.replace(/[gy]/g, ""));
     re.test(attack(1)); re.test(attack(1));
     const ns = exp ? [base - 8, base - 6, base - 4] : [base >> 2, base >> 1, base];
     const ms = ns.map((n) => { const t = process.hrtime.bigint(); re.test(attack(n)); return Number(process.hrtime.bigint() - t) / 1e6; });
     let n = ns[2], t = ms[2];
     while (t < 2000 * limit) { n = exp ? n + 2 : 2 * n; t *= Math.max(ms[2] / ms[1], 2); }
     const file = path.join(out, site.replace(/[^\w.-]+/g, "_"));
     fs.mkdirSync(out, { recursive: true });
     fs.writeFileSync(file, attack(n));
     console.log(`${site}\t${r.complexity.type} ${r.complexity.degree || ""}\t` +
       ns.map((m, i) => `${attack(m).length} chars ${Math.round(ms[i])} ms`).join(", ") +
       `\trepro: ${file} (${attack(n).length} chars, about ${Math.round(t / 1000)} s)`);
   }
   EOF
   ```
   Each regex prints one of:
   - `safe`;
   - `unknown`: syntax `recheck` can't parse; read the regex yourself;
   - its complexity, its match times at three attack sizes, and `repro`:
     the attack at a size estimated to take twice the input timeout. The
     complexity is `exponential` or `polynomial` with its degree
     (`polynomial 2` is quadratic).

   If the project is Java, `recheck` and the times follow JavaScript's
   engine, so for Java they're a lead.
3. **Confirm through the harness:** a harness can cut or reject long
   input, e.g. through Java `String` parameters and fields,
   `consumeString(maxLength)` or a size check. Such a bound stops the
   attack before it reaches the regex. If the harness has one, raise it
   first (`fuzz-harness`), to four times `repro`'s size in bytes for the
   doubling below. Then put `repro` into the harness's input format (as
   is, if the harness passes its bytes on as text). Replay it as a
   reproduction (`fuzz-triage`, "Reproduce"), with the command in the
   language skill's triage.md, "Reproduce".
   - If this replay, or a doubled one below, times out: triage it like an
     external crash (`fuzz-triage`, "Collect"), with the verdict
     `timeout`. The attack is confirmed if triage puts the hang in the
     regex: its bucket's location or root cause is the regex's site. A
     hang elsewhere is a finding of its own that hides the regex: stop
     checking this regex, and name the hang's bucket in the group's Note,
     to check the regex again once that bug is fixed.
   - If the replay finishes, double the repeated part until a replay times
     out or the time stops growing: Java's engine, for one, may be faster
     than the one `recheck` timed.
     - If the time stops growing, a length limit or a filter stops the
       attack before the regex. For Java, the cause can also be that
       Java's engine doesn't backtrack on this pattern. If you find that
       limit or filter in the project's code, record the group as
       sanitized, with the limit's or filter's `file:line`.

A group still in state reached when this check ends has used its tries:
record "4 of 4 tries" in its Note, and why no attack was confirmed, e.g.
its regexes are `safe`, or their subject isn't from input.
