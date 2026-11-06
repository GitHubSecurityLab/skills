#include <algorithm>

struct State {
  int count;
  int v[3];
  State(int c)
      : count(c),
        v{c, c + 1, c + 2}
  {
    std::for_each(v, v + 3, [](int &x) {
      if (x == 'Q') x = 0;
    });
    if (count > 100) {
      count = 100;
    }
  }
};

static int helper(int v) { return v + 1; }

#define CHECK(x) ((x) ? 1 : 0)

template <typename T>
T parse_item(const char *p) {
  return static_cast<T>(p[0]);
}

int classify(const char *p, int n) {
  int r = CHECK(n == 7);
  if (p[0] == 'X') {
    r += parse_item<int>(p);
  }
  if (n > 50) {
    r += helper(n);
  }
  return r + helper(0);
}

int other(const char *p);

int main(int argc, char **argv) {
  State s(argc);
  int r = s.count + classify("ab", argc) + classify("cd", 8);
  return r + other("ab") > 1000;
}
