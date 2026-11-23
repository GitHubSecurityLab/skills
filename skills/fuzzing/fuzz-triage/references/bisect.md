# Finding the introducing commit

1. **Good tag.** Find the newest release tag before the base that doesn't
   have the bug: the good end of the bisect. Either the reproducer builds
   and passes there, or the code the bug is in doesn't exist there yet
   (`git log` on it). In the second case the reproducer may not build,
   which is fine: the bisect never tests its good end.
2. **Test script.** Write `bisect.sh` in `crashes/<bucket>/`:
   - It builds the library at the commit the bisect checks out, with the
     reproducer's sanitizer flags, and then the reproducer. It uses the
     project's own build, since old commits don't have our build changes.
     If either build fails:
     - if the code the bug is in doesn't exist there yet (e.g. `git grep
       -q <function>` fails), it exits 0: that commit has no bug;
     - otherwise it runs `exit 125`, which skips the commit: the bug may
       be there, untested.
   - It then runs the reproducer, and exits 1 if the bug shows and 0 if
     not. It must never forward the reproducer's exit status: a status of
     128 or more (ASan's 134 on macOS) aborts the bisect.
3. **Run the bisect.** Bisect from the good tag to the base in a worktree
   of its own, named after the bucket. Never bisect in the checkout,
   which develop keeps changing. The bisect and the cleanup run only if
   the worktree was created:
   ```sh
   W="<checkout>/../<repo>.seclab-worktrees/bisect-<bucket>"
   git worktree add --detach "$W" <base> && {
     git -C "$W" bisect start <base> <good tag> &&
       git -C "$W" bisect run sh "$PWD/.seclab/fuzz/crashes/<bucket>/bisect.sh"
     git -C "$W" bisect reset; git worktree remove --force "$W"
   }
   ```
   If the worktree already exists, an earlier bisect of this bucket was
   cut off: remove it (`fuzz-campaign`'s long-runs.md, "Cleanup") and
   run the block again.

   If skipped commits leave several candidates, git prints "There are
   only 'skip'ped commits left to test" and then lists them. The
   introducing commit is one of them: record them all.
