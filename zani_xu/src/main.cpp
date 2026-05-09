#define FUSE_USE_VERSION 31

#include <fuse3/fuse.h>
#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <cstring>
#include <errno.h>
#include <curl/curl.h>
#include <algorithm>

// Global list to store tickers from .config
std::vector<std::string> tickers;

// Helper to check if a path corresponds to a ticker in our list
// Example: path "/AAPL" returns "AAPL", path "/invalid" returns ""
std::string get_ticker_from_path(const char* path) {
    std::string p = path;
    if (p.length() <= 1) return "";
    std::string name = p.substr(1); 
    for (const auto& t : tickers) {
        if (name == t) return t;
    }
    return "";
}

static size_t WriteCallback(void* contents, size_t size, size_t nmemb, void* userp) {
    ((std::string*)userp)->append((char*)contents, size * nmemb);
    return size * nmemb;
}

// Helper to fetch data for a specific ticker
std::string fetch_data(std::string ticker) {
    CURL* curl;
    CURLcode res;
    std::string readBuffer;
    curl = curl_easy_init();
    if(curl) {
        // Updated to the URL format you specified
        std::string url = "http://zanixu.com:5000/?ticker=" + ticker;
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &readBuffer);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L); 
        res = curl_easy_perform(curl);
        curl_easy_cleanup(curl);
        if(res == CURLE_OK) {
            if (readBuffer.empty() || readBuffer.back() != '\n') readBuffer += '\n';
            return readBuffer;
        }
    }
    return "Error fetching data\n";
}

static int do_getattr(const char *path, struct stat *st, struct fuse_file_info *fi) {
    memset(st, 0, sizeof(struct stat));
    std::string p = path;

    if (p == "/") {
        st->st_mode = S_IFDIR | 0755;
        st->st_nlink = 2;
    } else {
        std::string ticker = get_ticker_from_path(path);
        if (!ticker.empty()) {
            st->st_mode = S_IFREG | 0444;
            st->st_nlink = 1;
            // We fetch once to get the size
            st->st_size = fetch_data(ticker).length(); 
        } else {
            return -ENOENT;
        }
    }
    return 0;
}

static int do_unlink(const char *path) {
    if (!get_ticker_from_path(path).empty()) return 0;
    return -ENOENT;
}

static int do_mkdir(const char *path, mode_t mode) {
    return 0; 
}

static int do_read(const char *path, char *buffer, size_t size, off_t offset, struct fuse_file_info *fi) {
    std::string ticker = get_ticker_from_path(path);
    if (ticker.empty()) return -ENOENT;

    std::string data = fetch_data(ticker);
    size_t len = data.length();
    if ((size_t)offset >= len) return 0;
    if (offset + size > len) size = len - offset;

    memset(buffer, 0, size);
    memcpy(buffer, data.c_str() + offset, size);
    return size;
}

static int do_readdir(const char *path, void *buffer, fuse_fill_dir_t filler,
                      off_t offset, struct fuse_file_info *fi, enum fuse_readdir_flags flags) {
    filler(buffer, ".", NULL, 0, FUSE_FILL_DIR_PLUS);
    filler(buffer, "..", NULL, 0, FUSE_FILL_DIR_PLUS);
    
    if (std::string(path) == "/") {
        for (const auto& ticker : tickers) {
            filler(buffer, ticker.c_str(), NULL, 0, FUSE_FILL_DIR_PLUS);
        }
    }
    return 0;
}

static const struct fuse_operations operations = {
    .getattr = do_getattr,
    .mkdir   = do_mkdir,
    .unlink  = do_unlink,
    .read    = do_read,
    .readdir = do_readdir,
};

void load_config() {
    std::ifstream file(".config");
    std::string line;
    while (std::getline(file, line)) {
        // Remove trailing \r if file was saved with Windows line endings
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) tickers.push_back(line);
    }
}

int main(int argc, char *argv[]) {
    load_config();
    curl_global_init(CURL_GLOBAL_DEFAULT);
    int ret = fuse_main(argc, argv, &operations, NULL);
    curl_global_cleanup();
    return ret;
}
