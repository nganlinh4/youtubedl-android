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
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int (*main_fn)(int, char**);

static void* load_private_library(const char* directory, const char* name, int flags) {
    if (!directory || !directory[0]) return dlopen(name, flags);
    size_t size = strlen(directory) + strlen(name) + 2;
    char* path = malloc(size);
    if (!path) return NULL;
    snprintf(path, size, "%s/%s", directory, name);
    void* library = dlopen(path, flags);
    free(path);
    return library;
}

/* FFmpeg shared libraries to preload (order matters for dependency resolution) */
static int is_shared_library(const char* name) {
    return strncmp(name, "lib", 3) == 0 && strstr(name, ".so") != NULL &&
        strcmp(name, "libffmpeg_real.so") != 0 &&
        strcmp(name, "libffprobe_real.so") != 0;
}

static void preload_private_libraries(const char* directory) {
    if (!directory || !directory[0]) return;
    for (int pass = 0; pass < 16; pass++) {
        DIR* stream = opendir(directory);
        if (!stream) return;
        struct dirent* entry;
        while ((entry = readdir(stream)) != NULL) {
            if (is_shared_library(entry->d_name))
                load_private_library(directory, entry->d_name, RTLD_NOW | RTLD_GLOBAL);
        }
        closedir(stream);
    }
}

int main(int argc, char* argv[]) {
    const char* library_dir = getenv("SGT_FFMPEG_LIBRARY_DIR");
    if (library_dir && library_dir[0]) setenv("LD_LIBRARY_PATH", library_dir, 1);

    preload_private_libraries(library_dir);

    /* Load the real FFmpeg binary from the extracted packages directory.
     * It's placed there as "libffmpeg_real.so" by the app's download manager.
     * LD_LIBRARY_PATH includes packages/ffmpeg/usr/lib so dlopen finds it. */
    void* bin = load_private_library(library_dir, "libffmpeg_real.so", RTLD_NOW);
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
