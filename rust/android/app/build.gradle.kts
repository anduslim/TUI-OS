// The Rust core (crates/tuios-android) is built first with cargo-ndk into
// src/main/jniLibs/arm64-v8a/libtuios_android.so; see rust/README.md.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val keystore: String? = System.getenv("TUIOS_KEYSTORE")

android {
    namespace = "org.olares.tuios2"
    compileSdk = 35

    defaultConfig {
        // Installs beside TUI OS v1 (org.olares.olares_workspace_tui).
        applicationId = "org.olares.tuios2"
        minSdk = 30 // Boox Tab Ultra C runs Android 11
        targetSdk = 34
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "0.1.0-dev"
        ndk { abiFilters += "arm64-v8a" }
    }

    signingConfigs {
        create("release") {
            if (keystore != null) {
                storeFile = file(keystore)
                storePassword = System.getenv("TUIOS_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("TUIOS_KEY_ALIAS")
                keyPassword = System.getenv("TUIOS_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystore != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
