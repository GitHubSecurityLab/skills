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
