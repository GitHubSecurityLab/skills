import org.junit.jupiter.api.Test;

/** Reproducer: Record.parse() trusts the length byte of a short record. */
class RecordRegressionTest {
  @Test
  void shortRecordIsRejected() {
    byte[] input = {'R', 'E', 'C', '1', 9, 'a', 'b'};
    if (toy.Record.parse(input) != null) throw new AssertionError("expected null");
  }

  public static void main(String[] args) {
    new RecordRegressionTest().shortRecordIsRejected();
  }
}
