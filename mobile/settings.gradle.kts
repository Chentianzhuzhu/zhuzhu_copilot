pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "zhuzhu-copilot-mobile"

include(":app")
include(":core:model", ":core:common", ":core:llm", ":core:agent", ":core:data", ":core:ui")
include(":feature:chat", ":feature:tasks", ":feature:settings")
include(":service:accessibility", ":service:automation")
