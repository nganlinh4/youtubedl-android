#include <dlfcn.h>
#include <stdio.h>
static const char* ffmpeg_libs[] = {
    "libavutil.so", "libswresample.so", "libswscale.so", "libpostproc.so",
    "libavcodec.so", "libavformat.so", "libavfilter.so", "libavdevice.so", NULL
};
typedef int (*main_fn)(int, char**);
int main(int argc, char* argv[]) {
    for (int i = 0; ffmpeg_libs[i]; i++) dlopen(ffmpeg_libs[i], RTLD_NOW | RTLD_GLOBAL);
    void* bin = dlopen("libffprobe_real.so", RTLD_NOW);
    if (!bin) { fprintf(stderr, "ffprobe_wrapper: %s\n", dlerror()); return 127; }
    main_fn m = (main_fn)dlsym(bin, "main");
    if (!m) { fprintf(stderr, "ffprobe_wrapper: main not found\n"); return 127; }
    return m(argc, argv);
}
