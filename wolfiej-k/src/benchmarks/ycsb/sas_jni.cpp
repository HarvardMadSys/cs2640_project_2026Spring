#include <cstdint>
#include <cstring>
#include <jni.h>
#include <mutex>
#include <new>
#include <string_view>

#include "host_drivers.h"

namespace {

constexpr size_t HEADER_SIZE = sizeof(uint32_t);

void* alloc_payload(const uint8_t* src, uint32_t n) {
    auto* buf = static_cast<uint8_t*>(::operator new(HEADER_SIZE + n));
    memcpy(buf, &n, HEADER_SIZE);
    memcpy(buf + HEADER_SIZE, src, n);
    return buf;
}

void free_payload(void* p) noexcept { ::operator delete(p); }

uint32_t payload_size(const void* p) noexcept {
    uint32_t n;
    memcpy(&n, p, HEADER_SIZE);
    return n;
}

const uint8_t* payload_data(const void* p) noexcept {
    return static_cast<const uint8_t*>(p) + HEADER_SIZE;
}

std::once_flag g_init_flag;

} // namespace

extern "C" {

JNIEXPORT void JNICALL Java_site_ycsb_db_SasClient_init0(JNIEnv*, jclass,
                                                         jlong capacity_hint) {
    call_once(g_init_flag, [capacity_hint] {
        sas::host_driver::setup(static_cast<size_t>(capacity_hint));
    });
}

JNIEXPORT void JNICALL Java_site_ycsb_db_SasClient_cleanup0(JNIEnv*, jclass) {}

JNIEXPORT jobject JNICALL Java_site_ycsb_db_SasClient_readOpen0(
    JNIEnv* env, jclass, jbyteArray jkey, jlongArray jhandleOut) {
    jsize klen = env->GetArrayLength(jkey);
    auto* kbuf =
        static_cast<jbyte*>(env->GetPrimitiveArrayCritical(jkey, nullptr));
    auto* h = sas::host_driver::store()->get(std::string_view{
        reinterpret_cast<const char*>(kbuf), static_cast<size_t>(klen)});
    env->ReleasePrimitiveArrayCritical(jkey, kbuf, JNI_ABORT);
    if (!h) {
        return nullptr;
    }
    uint32_t n = payload_size(h->value);
    jobject buffer = env->NewDirectByteBuffer(
        const_cast<uint8_t*>(payload_data(h->value)), static_cast<jlong>(n));
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
    void* obj = alloc_payload(reinterpret_cast<const uint8_t*>(vbuf),
                              static_cast<uint32_t>(vlen));
    env->ReleasePrimitiveArrayCritical(jval, vbuf, JNI_ABORT);
    auto* kbuf =
        static_cast<jbyte*>(env->GetPrimitiveArrayCritical(jkey, nullptr));
    sas::host_driver::store()->put(
        std::string_view{reinterpret_cast<const char*>(kbuf),
                         static_cast<size_t>(klen)},
        obj, &free_payload);
    env->ReleasePrimitiveArrayCritical(jkey, kbuf, JNI_ABORT);
}
}
