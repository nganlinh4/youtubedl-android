package com.yausername.youtubedl_android

import org.gradle.api.Project
import org.gradle.api.publish.PublishingExtension
import org.gradle.kotlin.dsl.get
import org.gradle.kotlin.dsl.getByType
import org.gradle.plugins.signing.SigningExtension

internal fun Project.configureSigning() {
    // Only sign when GPG key is available (CI/release), skip for local builds
    val hasGpgKey = try {
        val result = Runtime.getRuntime().exec(arrayOf("gpg", "--list-secret-keys"))
        val output = result.inputStream.bufferedReader().readText()
        result.waitFor()
        result.exitValue() == 0 && output.contains("sec")
    } catch (_: Exception) { false }

    if (!hasGpgKey) return

    val publishing = extensions.getByType<PublishingExtension>()
    extensions.getByType<SigningExtension>().run {
        useGpgCmd()
        sign(publishing.publications["release"])
    }
}