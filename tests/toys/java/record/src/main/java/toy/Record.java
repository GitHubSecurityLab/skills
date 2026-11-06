package toy;

import java.nio.charset.StandardCharsets;

public final class Record {
  private Record() {}

  /** Planted bug for the CI scenarios: the length byte isn't checked against the input. */
  public static String parse(byte[] data) {
    if (data.length < 5 || data[0] != 'R' || data[1] != 'E' || data[2] != 'C' || data[3] != '1') {
      return null;
    }
    int len = data[4] & 0xff;
    return new String(data, 5, len, StandardCharsets.ISO_8859_1);
  }
}
