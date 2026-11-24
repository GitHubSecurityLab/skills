#include <cstdlib>
#include <cstring>
#include <string>

#include "fuzztest/fuzztest.h"
#include "gtest/gtest.h"
#include "src/record.h"

void ParsesWithoutCrashing(const std::string& data) {
  std::string name;
  ParseRecord(data, &name);
}
FUZZ_TEST(RecordTest, ParsesWithoutCrashing)
    .WithDomains(fuzztest::Arbitrary<std::string>())
    .WithSeeds({std::string("REC1\x03" "abc", 8)});

// A fuzz test with state leaking between inputs, for the gate's leak check:
// only inputs after the first in a process take this path.
static int calls;

static int StalePath() { return calls * 2; }

void ParsesLeaky(const std::string& data) {
  if (calls++ > 0) StalePath();
  std::string name;
  ParseRecord(data, &name);
}
FUZZ_TEST(RecordTest, ParsesLeaky).WithDomains(fuzztest::Arbitrary<std::string>());

// A fuzz test that leaks on inputs starting with "LK", its seed included, for
// the leak triage: FuzzTest checks for leaks only at exit.
__attribute__((noinline)) static char* CopyPrefix(const std::string& data) {
  char* prefix = static_cast<char*>(malloc(3));
  memcpy(prefix, data.data(), 2);
  prefix[2] = 0;
  return prefix;
}

void LeaksMemory(const std::string& data) {
  if (data.size() < 2) return;
  char* prefix = CopyPrefix(data);
  if (data[0] == 'L' && data[1] == 'K') return;
  free(prefix);
}
FUZZ_TEST(RecordTest, LeaksMemory)
    .WithDomains(fuzztest::Arbitrary<std::string>())
    .WithSeeds({std::string("LK")});
