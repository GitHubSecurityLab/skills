# Java toolchain and install plan

## Check without running project code

The check runs before the first-build notice, so it must not run the
project's code or apply its settings. The wrappers are project code:
`gradlew` runs the committed `gradle-wrapper.jar`, and `mvnw` runs the
Maven that its `distributionUrl` names. Inside the repository, even
`mvn -v` loads `.mvn/jvm.config`. So run the system tools from outside
the repository, and only test that the wrappers exist (in the build's
root directory):

<!-- ci: java-toolchain-check -->
```sh
for wrapper in mvnw gradlew; do test -x "$wrapper" && echo "$wrapper: run it only after the notice"; done
(cd / && java -version; mvn -v; gradle -v)
```

After the notice, the project's wrappers replace `mvn` and `gradle` in every
command, and `./mvnw -v` and `./gradlew -v` show the JDK the wrappers use.
Through the launcher, the wrappers run from the build's root, with the real
tool caches (`run.md`, "JUnit runs (regression, CI, upstreaming)").

`build.md`, "JDK", covers which JDK Maven and Gradle use, and how to
choose the JDK for Jazzer.

## Install plan

List only what's missing. Use `sudo` only in a disposable environment
(`fuzz-harness`'s build.md). Otherwise, print the command for the user,
or use SDKMAN (`sdk list java` shows the versions). On Ubuntu, run
`sudo apt-get update` before the first install.

| What | When | Ubuntu 24.04 | macOS (Homebrew) | Time |
|---|---|---|---|---|
| JDK 21 | no JDK that the project and Jazzer accept | `sudo apt-get install -y openjdk-21-jdk-headless`, or SDKMAN at user level | `brew install openjdk@21`; keg-only: `JAVA_HOME=$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home` | under a minute |
| Maven or Gradle | the project has no `mvnw` or `gradlew` | `sudo apt-get install -y maven` (or `gradle` through SDKMAN) | `brew install maven` or `brew install gradle` | seconds |
| Jazzer, JUnit 5 | always | test-scope dependencies, downloaded by the build (`build.md`) | same | a minute |
| JaCoCo agent and CLI | coverage | two jars fetched with `curl` (`coverage.md`) | same | seconds |

Times were measured on a clean Ubuntu 24.04 with a fast network; slower
machines take a few times longer.
