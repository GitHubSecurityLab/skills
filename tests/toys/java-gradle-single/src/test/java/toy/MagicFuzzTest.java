package toy;

import com.code_intelligence.jazzer.junit.FuzzTest;

class MagicFuzzTest {
  @FuzzTest
  void check(byte[] data) {
    Magic.check(data);
  }
}
