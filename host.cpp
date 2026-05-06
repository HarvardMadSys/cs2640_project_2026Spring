#include <cstddef>
#include <string_view>

#include "handle.h"
#include "host_drivers.h"
#include "host_runtime.h"

extern "C" {
sas::object_handle* sas_get(const char* key, size_t key_len) {
    return sas::host_driver::store()->get({key, key_len});
}
void* sas_deref(sas::object_handle* handle) { return handle->value; }
void sas_close(sas::object_handle* handle) {
    sas::host_driver::store()->close(handle);
}
void sas_put(const char* key, size_t key_len, void* value, sas::dtor_fn dtor) {
    sas::host_driver::store()->put({key, key_len}, value, dtor);
}
}

int main(int argc, char* argv[]) {
    return sas::host::run(
        argc, argv, [] { sas::host_driver::setup(); },
        [] { sas::host_driver::teardown(); });
}
