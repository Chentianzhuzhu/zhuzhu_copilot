plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

android {
    namespace = "com.zhuzhu.copilot.service.accessibility"
    compileSdk = 36
    defaultConfig { minSdk = 26 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
}

dependencies {
    implementation(project(":core:agent"))
    implementation(project(":core:ui"))
    implementation(project(":core:data"))
    implementation(libs.hilt.android)
    implementation(libs.haze)
    ksp(libs.hilt.compiler)
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
}
