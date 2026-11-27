package toy;

// A classic target for the standalone driver, for reach.py's replays.
public final class RecordFuzzer {
  private RecordFuzzer() {}

  public static void fuzzerTestOneInput(byte[] data) {
    Record.parse(data);
  }
}
