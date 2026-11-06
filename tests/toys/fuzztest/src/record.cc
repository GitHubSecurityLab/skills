#include "record.h"

#include <cassert>

// Planted bug for the CI scenarios: the length byte isn't validated against
// the input's size; only an assertion guards the copy.
int ParseRecord(const std::string& data, std::string* name) {
  if (data.size() < 5 || data.compare(0, 4, "REC1") != 0) return -1;
  size_t len = static_cast<unsigned char>(data[4]);
  assert(len <= data.size() - 5);
  name->assign(data.data() + 5, len);
  return (*name)[0] == 'x' ? 1 : 0;
}
