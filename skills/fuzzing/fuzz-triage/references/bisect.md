# Finding the introducing commit

1. **Good tag.** Find the newest release tag where the reproducer builds
   and passes (the last release itself for new code): the good end of the
   bisect.
2. **Test script.** Write `bisect.sh` in `crashes/<bucket>/`. It builds
   the library at the commit the bisect checks out, with the reproducer's
   sanitizer flags, using the project's own build (old commits don't have
   our build changes); `exit 125` if that fails, which skips the commit.
   It then runs the reproducer and exits 1 if the bug shows, 0 otherwise.
   Never pass on the reproducer's own exit status: 128 or more (ASan's 134
   on macOS) aborts the bisect.
3. **Run the bisect.** Bisect from the good tag to the base in its own
   worktree, never in the checkout, which develop keeps changing:
   ```sh
   W="<checkout>/../<repo>.seclab-worktrees/bisect"
   git worktree add --detach "$W" <base>
   git -C "$W" bisect start <base> <good tag>
   git -C "$W" bisect run sh "$PWD/.seclab/fuzz/crashes/<bucket>/bisect.sh"
   git -C "$W" bisect reset; git worktree remove --force "$W"
   ```
