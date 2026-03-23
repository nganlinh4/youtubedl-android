/*
 * Custom FFmpeg wrapper for Android 15+ (16KB page alignment).
 * Preloads FFmpeg shared libraries via dlopen(), then loads the real
 * FFmpeg binary (extracted alongside shared libs) and calls its main().
 *
 * Compile with:
 *   aarch64-linux-android29-clang -o libffmpeg.so ffmpeg_wrapper.c \
 *       -ldl -Wl,-z,max-page-size=16384 -fPIE -pie -s -O2
 */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

typedef int (*main_fn)(int, char**);

/* FFmpeg shared libraries to preload (order matters for dependency resolution) */
static const char* ffmpeg_libs[] = {
    "libavutil.so",
    "libswresample.so",
    "libswscale.so",
    "libpostproc.so",
    "libavcodec.so",
    "libavformat.so",
    "libavfilter.so",
    "libavdevice.so",
    NULL
};

int main(int argc, char* argv[]) {
    /* Preload FFmpeg shared libs from LD_LIBRARY_PATH (set by YoutubeDL.kt) */
    for (int i = 0; ffmpeg_libs[i]; i++) {
        dlopen(ffmpeg_libs[i], RTLD_NOW | RTLD_GLOBAL);
        /* Ignore failures — some libs may not be present in all builds */
    }

    /* Load the real FFmpeg binary from the extracted packages directory.
     * It's placed there as "libffmpeg_real.so" by the app's download manager.
     * LD_LIBRARY_PATH includes packages/ffmpeg/usr/lib so dlopen finds it. */
    void* bin = dlopen("libffmpeg_real.so", RTLD_NOW);
    if (!bin) {
        fprintf(stderr, "ffmpeg_wrapper: cannot load real ffmpeg: %s\n", dlerror());
        return 127;
    }

    main_fn real_main = (main_fn)dlsym(bin, "main");
    if (!real_main) {
        fprintf(stderr, "ffmpeg_wrapper: main() not found: %s\n", dlerror());
        return 127;
    }

    return real_main(argc, argv);
}
