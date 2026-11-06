#include <stddef.h>
#include <stdint.h>
int parse_header(const uint8_t *d, size_t n);
int parse_body(const uint8_t *d, size_t n);
int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) {
  if (parse_header(d, n) >= 0 && n > 8) parse_body(d + 8, n - 8);
  return 0;
}
