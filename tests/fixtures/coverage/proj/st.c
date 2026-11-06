static int skip_space(const char *p) {
  int n = 0;
  while (p[n] == ' ') n++;
  return n;
}
int parse_c(const char *p) { return skip_space(p); }
