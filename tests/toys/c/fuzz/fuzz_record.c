#include <stddef.h>
#include <stdint.h>

#include "../src/record.h"

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  char name[256];
  parse_record(data, size, name, sizeof name);
  return 0;
}
