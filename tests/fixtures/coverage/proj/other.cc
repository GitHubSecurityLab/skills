static int helper(int v) { return v * 2; }

int other(const char *p) {
  if (p[0] == 'Z') {
    return helper(p[1]);
  }
  return 0;
}
