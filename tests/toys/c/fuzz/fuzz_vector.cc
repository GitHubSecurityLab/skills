// An index past size() but within capacity: ASan sees allocated memory.
#include <cstddef>
#include <cstdint>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  std::vector<int> v;
  v.reserve(8);
  v.push_back(size > 0 ? data[0] : 1);
  return v[3] == 42;
}
