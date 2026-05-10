#pragma once

#include <cstdint>
#include <cstdio>
#include <fcntl.h>
#include <unistd.h>

namespace sas::bench {

inline void reset_peak_rss() {
    int fd = open("/proc/self/clear_refs", O_WRONLY);
    if (fd < 0) {
        return;
    }
    const char buf[] = "5\n";
    ssize_t n = write(fd, buf, sizeof(buf) - 1);
    (void)n;
    close(fd);
}

inline uint64_t peak_rss_kb() {
    std::FILE* f = std::fopen("/proc/self/status", "r");
    if (!f) {
        return 0;
    }
    uint64_t kb = 0;
    char line[256];
    while (std::fgets(line, sizeof(line), f)) {
        if (std::sscanf(line, "VmHWM: %lu kB", &kb) == 1) {
            break;
        }
    }
    std::fclose(f);
    return kb;
}

inline double peak_rss_mb() {
    return static_cast<double>(peak_rss_kb()) / 1024.0;
}

} // namespace sas::bench
