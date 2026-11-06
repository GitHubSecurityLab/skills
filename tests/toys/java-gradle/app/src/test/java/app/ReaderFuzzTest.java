package app;

import com.code_intelligence.jazzer.junit.FuzzTest;

class ReaderFuzzTest {
  @FuzzTest
  void read(byte[] data) {
    Reader.read(data);
  }
}
