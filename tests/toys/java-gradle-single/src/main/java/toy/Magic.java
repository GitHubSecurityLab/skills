package toy;

/** Planted bug for the CI scenarios, behind a 4-byte magic only coverage feedback finds. */
public final class Magic {
  public static void check(byte[] data) {
    if (data.length >= 4 && data[0] == 'F' && data[1] == 'U' && data[2] == 'Z' && data[3] == 'Z') {
      throw new IllegalStateException("planted bug behind the magic");
    }
  }
}
