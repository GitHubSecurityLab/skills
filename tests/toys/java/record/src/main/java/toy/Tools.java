package toy;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.naming.NamingEnumeration;
import javax.naming.NamingException;
import javax.naming.directory.DirContext;
import javax.naming.directory.SearchControls;
import javax.naming.directory.SearchResult;

/** Sinks for the java scenario's detector step. */
public final class Tools {
  private Tools() {}

  /** It slips past the command detector: the command is "sh". */
  public static int greet(String name) throws IOException, InterruptedException {
    return new ProcessBuilder("sh", "-c", "echo hello " + name).start().waitFor();
  }

  /** It slips past the traversal detector: the path is resolved against a base directory. */
  public static byte[] readData(Path base, String name) throws IOException {
    return Files.readAllBytes(base.resolve(name));
  }

  /** The LDAP detector's two messages: search filters and distinguished names. */
  public static NamingEnumeration<SearchResult> findUser(DirContext ctx, String base, String name)
      throws NamingException {
    return ctx.search(base, "(cn=" + name + ")", new SearchControls());
  }
}
