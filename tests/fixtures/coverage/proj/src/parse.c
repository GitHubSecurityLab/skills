#include <stddef.h>
#include <stdint.h>
#include <string.h>

int parse_header(const uint8_t *d, size_t n) {
  if (n < 4) return -1;
  if (memcmp(d, "HDR", 3) != 0) return -2;
  uint8_t len = d[3];
  if (len > n - 4) return -3;
  int sum = 0;
  for (size_t i = 0; i < len; i++) sum += d[4 + i];
  return sum;
}

int parse_body(const uint8_t *d, size_t n) {
  if (n == 0) return 0;
  switch (d[0]) {
    case 'A': return 1;
    case 'B': return 2;
    default: return 3;
  }
}
