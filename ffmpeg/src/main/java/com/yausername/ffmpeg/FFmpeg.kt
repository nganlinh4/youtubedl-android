package com.yausername.ffmpeg

import android.content.Context
import com.yausername.youtubedl_android.YoutubeDLException
import com.yausername.youtubedl_common.SharedPrefsHelper
import com.yausername.youtubedl_common.SharedPrefsHelper.update
import com.yausername.youtubedl_common.utils.ZipUtils.unzip
import org.apache.commons.io.FileUtils
import java.io.File

object FFmpeg {
    private var initialized = false
    private var binDir: File? = null

    @Synchronized
    fun init(appContext: Context) = init(appContext, null)

    /**
     * Initialize with an optional external directory containing native libraries.
     * When [externalZipDir] is non-null, all native files (libffmpeg.zip.so,
     * libffmpeg.so, libffprobe.so) are read from that directory instead of
     * the APK's native library directory.
     */
    @Synchronized
    fun init(appContext: Context, externalZipDir: File?) {
        if (initialized) return
        val baseDir = File(appContext.noBackupFilesDir, baseName)
        if (!baseDir.exists()) baseDir.mkdir()
        binDir = File(appContext.applicationInfo.nativeLibraryDir)
        val packagesDir = File(baseDir, packagesRoot)
        val ffmpegDir = File(packagesDir, ffmegDirName)
        initFFmpeg(appContext, ffmpegDir, externalZipDir)
        initialized = true
    }

    private fun initFFmpeg(appContext: Context, ffmpegDir: File, externalZipDir: File? = null) {
        val zipSource = externalZipDir ?: binDir!!
        val ffmpegLib = File(zipSource, ffmpegLibName)
        // using size of lib as version
        val ffmpegSize = ffmpegLib.length().toString()
        if (!ffmpegDir.exists() || shouldUpdateFFmpeg(appContext, ffmpegSize)) {
            FileUtils.deleteQuietly(ffmpegDir)
            ffmpegDir.mkdirs()
            try {
                unzip(ffmpegLib, ffmpegDir)
            } catch (e: Exception) {
                FileUtils.deleteQuietly(ffmpegDir)
                throw YoutubeDLException("failed to initialize", e)
            }
            updateFFmpeg(appContext, ffmpegSize)
        }
    }

    private fun shouldUpdateFFmpeg(appContext: Context, version: String): Boolean {
        return version != SharedPrefsHelper[appContext, ffmpegLibVersion]
    }

    private fun updateFFmpeg(appContext: Context, version: String) {
        update(appContext, ffmpegLibVersion, version)
    }

    @JvmStatic
    fun getInstance() = this
    private const val baseName = "youtubedl-android"
    private const val packagesRoot = "packages"
    private const val ffmegDirName = "ffmpeg"
    private const val ffmpegLibName = "libffmpeg.zip.so"
    private const val ffmpegLibVersion = "ffmpegLibVersion"

}