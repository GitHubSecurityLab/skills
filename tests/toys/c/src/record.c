#include "record.h"

#include <assert.h>
#include <string.h>

/* Planted bug for the CI scenarios: the length byte isn't validated against
 * the input's size; only an assertion guards the copy, so a short record
 * aborts in every build (assertions stay on in fuzzing builds). */
int parse_record(const unsigned char *data, size_t size, char *name, size_t name_size) {
  size_t len;
  if (size < 5 || memcmp(data, "REC1", 4) != 0) return -1;
  len = data[4];
  if (len >= name_size) return -1;
  assert(len <= size - 5);
  memcpy(name, data + 5, len);
  name[len] = '\0';
  return name[0] == 'x' ? 1 : 0;
}
