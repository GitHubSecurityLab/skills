package app;

public final class Reader {
  public static void read(byte[] data) {
    core.Magic.check(data);
  }
}
