#include <stddef.h>
#include <stdint.h>

#include "../src/record.h"

/* A harness with state leaking between inputs, for the gate's leak check:
 * only inputs after the first in a process take this path. */
static int calls;

static int stale_path(void) { return calls * 2; }

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  char name[256];
  if (calls++ > 0) stale_path();
  parse_record(data, size, name, sizeof name);
  return 0;
}
