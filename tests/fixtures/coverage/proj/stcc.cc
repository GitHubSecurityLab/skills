namespace {
int count_digits(const char *p) {
  int n = 0;
  while (p[n] >= '0' && p[n] <= '9') n++;
  return n;
}
}  // namespace
static int twice(int x) { return 2 * x; }
extern "C" int parse_cc(const char *p) { return twice(count_digits(p)); }
