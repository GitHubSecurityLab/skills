package toy;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Both slip past Jazzer's detectors: the command is "sh", and the path is resolved against a base directory. */
public final class Tools {
  private Tools() {}

  public static int greet(String name) throws IOException, InterruptedException {
    return new ProcessBuilder("sh", "-c", "echo hello " + name).start().waitFor();
  }

  public static byte[] readData(Path base, String name) throws IOException {
    return Files.readAllBytes(base.resolve(name));
  }
}
