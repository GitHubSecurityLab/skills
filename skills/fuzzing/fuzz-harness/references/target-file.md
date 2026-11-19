# The minimal target file

Standalone use has no campaign that ranked the target and wrote the
target file. Write a minimal `targets/<id>.md` with the entry point, what
the attacker controls there, and the scope files, each with a one-line
reason. To find the scope files, follow the calls from the entry point and
keep the files where attacker data is processed; stop at code the threat
model excludes. Put the bare paths into `targets/<id>.scope`, which the
gate measures. Don't read `fuzz-campaign`'s targets.md for this file.
