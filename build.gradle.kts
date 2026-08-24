import com.android.build.api.dsl.Publishing
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.bundling.Zip

// Top-level build file where you can add configuration options common to all sub-projects/modules.
buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath("com.android.tools.build:gradle:9.1.1")

        // NOTE: Do not place your application dependencies here; they belong
        // in the individual module build.gradle files
    }
}

val versionMajor = 0
val versionMinor = 17
val versionPatch = 2
val versionBuild = 0 // bump for dogfood builds, public betas, etc.
val versionCode = versionMajor * 100000 + versionMinor * 1000 + versionPatch * 100 + versionBuild
val versionName = "$versionMajor.$versionMinor.$versionPatch"

extra.apply {
    set("versionCode", versionCode)
    set("versionName", "$versionMajor.$versionMinor.$versionPatch")
    set("appCompatVer", "1.4.2")
    set("junitVer", "4.13.2")
    set("androidJunitVer", "1.1.3")
    set("espressoVer", "3.4.0")
    set("jacksonVer", "2.11.1")
    set("commonsIoVer", "2.5") // supports java 1.6
    set("commonsCompressVer", "1.12") // supports java 1.6
    set("coreKtxVer", "1.8.0")
}

allprojects {
    group = "com.github.yausername"
    version = versionName
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}

val verifyNativePageAlignment by tasks.registering(Exec::class) {
    group = "verification"
    description = "Verifies every bundled 64-bit ELF uses 16 KB LOAD alignment."
    val nativeRoots = listOf("library", "ffmpeg", "aria2c").map {
        layout.projectDirectory.dir("$it/src/main/jniLibs")
    }
    inputs.files(nativeRoots)
    val python = if (System.getProperty("os.name").startsWith("Windows")) {
        listOf("py", "-3")
    } else {
        listOf("python3")
    }
    commandLine(
        python + layout.projectDirectory.file("scripts/verify_16kb_elf.py").asFile.absolutePath
    )
}

subprojects {
    tasks.matching { it.name == "check" || it.name == "preReleaseBuild" }.configureEach {
        dependsOn(rootProject.tasks.named("verifyNativePageAlignment"))
    }
}

val librariesToPublish = listOf("common", "library", "aria2c", "ffmpeg")

tasks.register<Zip>("packagePublishedArtifacts") {
    librariesToPublish.forEach {
        dependsOn(":$it:publishReleasePublicationToMavenRepository")
    }
    from(layout.buildDirectory.dir("staging-deploy")) {
        librariesToPublish.forEach { library ->
            include("io/github/junkfood02/youtubedl-android/$library/$versionName/**")
        }
    }
    archiveFileName.set("archive-$versionName.zip")
    destinationDirectory.set(layout.buildDirectory)
}


