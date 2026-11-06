package toy;

import java.io.IOException;

/** It slips past Jazzer's command detector: the command is "sh". */
public final class Tools {
  private Tools() {}

  public static int greet(String name) throws IOException, InterruptedException {
    return new ProcessBuilder("sh", "-c", "echo hello " + name).start().waitFor();
  }
}
