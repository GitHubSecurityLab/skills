package toy;

import com.code_intelligence.jazzer.junit.FuzzTest;
import com.code_intelligence.jazzer.mutation.annotation.InRange;
import com.code_intelligence.jazzer.mutation.annotation.NotNull;
import com.code_intelligence.jazzer.mutation.annotation.WithUtf8Length;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

class RecordFuzzTest {
  @FuzzTest
  void parse(byte[] data) {
    Record.parse(data);
  }

  // A harness with state leaking between inputs, for the gate's leak check:
  // only inputs after the first in a JVM take this path.
  private static int calls;

  private static int stalePath() {
    return calls * 2;
  }

  @FuzzTest
  void parseLeaky(byte[] data) {
    if (calls++ > 0) {
      stalePath();
    }
    Record.parse(data);
  }

  // Typed parameters, for triage's decoder: a length past the body's end crashes.
  @FuzzTest
  void parseTyped(@NotNull @WithUtf8Length(max = 64) String body, @InRange(min = 0, max = 255) int length) {
    byte[] text = body.getBytes(StandardCharsets.ISO_8859_1);
    ByteBuffer data = ByteBuffer.allocate(5 + text.length).put("REC1".getBytes(StandardCharsets.ISO_8859_1));
    Record.parse(data.put((byte) length).put(text).array());
  }
}
