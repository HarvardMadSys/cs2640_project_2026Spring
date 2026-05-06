#include <jni.h>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <new>
#include <string_view>

#include "host_drivers.h"

namespace {

constexpr std::size_t HEADER_SIZE = sizeof(uint32_t);

void* alloc_payload(const std::uint8_t* src, std::uint32_t n) {
    auto* buf = static_cast<std::uint8_t*>(::operator new(HEADER_SIZE + n));
    std::memcpy(buf, &n, HEADER_SIZE);
    std::memcpy(buf + HEADER_SIZE, src, n);
    return buf;
}

void free_payload(void* p) noexcept { ::operator delete(p); }

std::uint32_t payload_size(const void* p) noexcept {
    std::uint32_t n;
    std::memcpy(&n, p, HEADER_SIZE);
    return n;
}

const std::uint8_t* payload_data(const void* p) noexcept {
    return static_cast<const std::uint8_t*>(p) + HEADER_SIZE;
}

std::atomic_flag g_initialized = ATOMIC_FLAG_INIT;

} // namespace

extern "C" {

JNIEXPORT void JNICALL Java_site_ycsb_db_SasClient_init0(JNIEnv*, jclass,
                                                         jlong capacity_hint) {
    if (!g_initialized.test_and_set(std::memory_order_acq_rel)) {
        sas::host_driver::setup(static_cast<std::size_t>(capacity_hint));
    }
}

JNIEXPORT void JNICALL Java_site_ycsb_db_SasClient_cleanup0(JNIEnv*, jclass) {}

JNIEXPORT jobject JNICALL
Java_site_ycsb_db_SasClient_readOpen0(JNIEnv* env, jclass, jbyteArray jkey,
                                      jlongArray jhandleOut) {
    jsize klen = env->GetArrayLength(jkey);
    auto* kbuf =
        static_cast<jbyte*>(env->GetPrimitiveArrayCritical(jkey, nullptr));
    auto* h = sas::host_driver::store()->get(std::string_view{
        reinterpret_cast<const char*>(kbuf), static_cast<std::size_t>(klen)});
    env->ReleasePrimitiveArrayCritical(jkey, kbuf, JNI_ABORT);
    if (!h) {
        return nullptr;
    }
    std::uint32_t n = payload_size(h->value);
    jobject buffer = env->NewDirectByteBuffer(
        const_cast<std::uint8_t*>(payload_data(h->value)),
        static_cast<jlong>(n));
    jlong handleAddr = reinterpret_cast<jlong>(h);
    env->SetLongArrayRegion(jhandleOut, 0, 1, &handleAddr);
    return buffer;
}

JNIEXPORT void JNICALL Java_site_ycsb_db_SasClient_readClose0(JNIEnv*, jclass,
                                                              jlong handle) {
    if (handle != 0) {
        sas::host_driver::store()->close(
            reinterpret_cast<sas::object_handle*>(handle));
    }
}

JNIEXPORT void JNICALL Java_site_ycsb_db_SasClient_put0(JNIEnv* env, jclass,
                                                        jbyteArray jkey,
                                                        jbyteArray jval) {
    jsize klen = env->GetArrayLength(jkey);
    jsize vlen = env->GetArrayLength(jval);
    auto* vbuf =
        static_cast<jbyte*>(env->GetPrimitiveArrayCritical(jval, nullptr));
    void* obj = alloc_payload(reinterpret_cast<const std::uint8_t*>(vbuf),
                              static_cast<std::uint32_t>(vlen));
    env->ReleasePrimitiveArrayCritical(jval, vbuf, JNI_ABORT);
    auto* kbuf =
        static_cast<jbyte*>(env->GetPrimitiveArrayCritical(jkey, nullptr));
    sas::host_driver::store()->put(
        std::string_view{reinterpret_cast<const char*>(kbuf),
                         static_cast<std::size_t>(klen)},
        obj, &free_payload);
    env->ReleasePrimitiveArrayCritical(jkey, kbuf, JNI_ABORT);
}
}
