#include <stddef.h>
#include <stdint.h>

#include "../src/record.h"

/* A harness whose state makes later inputs in a process repeat work, for the
 * gate's leak check: the same code is covered either way, only the hit counts
 * differ. */
static size_t seen;

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  char name[256];
  seen++;
  for (size_t i = 0; i < seen; i++) parse_record(data, size, name, sizeof name);
  return 0;
}
