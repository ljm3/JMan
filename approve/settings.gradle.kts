// Sandbox build note: this environment blocks raw TCP from the JVM, so
// Gradle cannot download dependencies here. Dependencies were fetched
// with curl into ~/workspace/apps/toolchain/local-maven/ by
// fetch_closure.py, and this file points at that file repository.
// On an unrestricted machine, replace these repositories with
// google(), mavenCentral(), and gradlePluginPortal().
pluginManagement {
    repositories {
        maven { url = uri("file:///home/hatch/workspace/apps/toolchain/local-maven") }
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven { url = uri("file:///home/hatch/workspace/apps/toolchain/local-maven") }
    }
}
rootProject.name = "Approve"
include(":app")
