#pragma once

#include <memory>

#if defined(SAS_BACKEND_HP)
#include "hazard.h"
#include "hp_store.h"
namespace sas {
std::unique_ptr<hazard_domain> g_domain;
} // namespace sas
namespace sas::hp {
std::unique_ptr<object_store> g_store;
} // namespace sas::hp
namespace sas::host_driver {
inline auto* store() noexcept { return sas::hp::g_store.get(); }
inline void setup(size_t initial_capacity = 1024) {
    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::hp::g_store = std::make_unique<sas::hp::object_store>(initial_capacity);
}
inline void teardown() {
    sas::hp::g_store.reset();
    sas::g_domain.reset();
}
} // namespace sas::host_driver

#elif defined(SAS_BACKEND_EBR)
#include "ebr.h"
#include "ebr_store.h"
namespace sas::ebr {
std::unique_ptr<ebr_domain> g_domain;
std::unique_ptr<object_store> g_store;
} // namespace sas::ebr
namespace sas::host_driver {
inline auto* store() noexcept { return sas::ebr::g_store.get(); }
inline void setup(size_t initial_capacity = 1024) {
    sas::ebr::g_domain = std::make_unique<sas::ebr::ebr_domain>();
    sas::ebr::g_store =
        std::make_unique<sas::ebr::object_store>(initial_capacity);
}
inline void teardown() {
    sas::ebr::g_store.reset();
    sas::ebr::g_domain.reset();
}
} // namespace sas::host_driver

#elif defined(SAS_BACKEND_SHARDED)
#include "sharded.h"
namespace sas::host_driver {
inline std::unique_ptr<sas::bench::sharded_store> g_store;
inline auto* store() noexcept { return g_store.get(); }
inline void setup(size_t initial_capacity = 1024) {
    g_store = std::make_unique<sas::bench::sharded_store>(initial_capacity);
}
inline void teardown() { g_store.reset(); }
} // namespace sas::host_driver

#elif defined(SAS_BACKEND_SPINLOCK)
#include "spinlock.h"
namespace sas::host_driver {
inline std::unique_ptr<sas::bench::spinlock_store> g_store;
inline auto* store() noexcept { return g_store.get(); }
inline void setup(size_t initial_capacity = 1024) {
    g_store = std::make_unique<sas::bench::spinlock_store>(initial_capacity);
}
inline void teardown() { g_store.reset(); }
} // namespace sas::host_driver

#elif defined(SAS_BACKEND_HYBRID)
#include "hazard.h"
#include "hybrid.h"
namespace sas {
std::unique_ptr<hazard_domain> g_domain;
std::unique_ptr<hybrid_store> g_store;
} // namespace sas
namespace sas::host_driver {
inline auto* store() noexcept { return sas::g_store.get(); }
inline void setup(size_t initial_capacity = 1024) {
    sas::g_domain = std::make_unique<sas::hazard_domain>();
    sas::g_store = std::make_unique<sas::hybrid_store>(initial_capacity);
}
inline void teardown() {
    sas::g_store.reset();
    sas::g_domain.reset();
}
} // namespace sas::host_driver

#else
#error "No SAS_BACKEND_* macro defined"
#endif
