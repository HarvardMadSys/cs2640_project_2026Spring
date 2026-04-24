CXX := g++
CXXFLAGS := -std=c++23 -Wall -Wextra -O3

ifeq ($(SAN),1)
  CXXFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer -g
else
  CXXFLAGS += -DNDEBUG
endif

HOST_SRCS := $(wildcard *.cpp)
HOST_HDRS := $(wildcard *.h)

STORE_SRCS := store.cpp hazard.cpp handle.cpp

EXAMPLE_SRCS := $(wildcard example/*.cpp)
EXAMPLE_LIBS := $(EXAMPLE_SRCS:.cpp=.so)

TEST_SRCS := $(wildcard tests/*.cpp)
TEST_LIBS := $(TEST_SRCS:.cpp=.so)

BENCH_PLUGIN_SRCS := $(filter-out benchmarks/compare_%, $(wildcard benchmarks/*.cpp))
BENCH_PLUGIN_LIBS := $(BENCH_PLUGIN_SRCS:.cpp=.so)
BENCH_STANDALONE_SRCS := $(wildcard benchmarks/compare_*.cpp)
BENCH_STANDALONE_BINS := $(BENCH_STANDALONE_SRCS:.cpp=)

.PHONY: all clean test bench

all: host $(EXAMPLE_LIBS) $(TEST_LIBS) $(BENCH_PLUGIN_LIBS) $(BENCH_STANDALONE_BINS)

host: $(HOST_SRCS) $(HOST_HDRS)
	$(CXX) $(CXXFLAGS) -Wl,--export-dynamic -o $@ $(filter %.cpp, $^) \
		-ldl -lpthread -lmimalloc

example/%.so: example/%.cpp client.h
	$(CXX) $(CXXFLAGS) -fPIC -shared -I. -o $@ $<

tests/%.so: tests/%.cpp client.h
	$(CXX) $(CXXFLAGS) -fPIC -shared -I. -o $@ $<

benchmarks/%.so: benchmarks/%.cpp client.h
	$(CXX) $(CXXFLAGS) -fPIC -shared -I. -Iexternal/ -o $@ $<

benchmarks/compare_%: benchmarks/compare_%.cpp $(STORE_SRCS) $(HOST_HDRS)
ifeq ($(SAN),1)
	$(warning building benchmark under sanitizers!)
endif
	$(CXX) $(CXXFLAGS) -I. -Iexternal/ -o $@ $< $(STORE_SRCS) -lpthread -lmimalloc

bench: $(BENCH_PLUGIN_LIBS) $(BENCH_STANDALONE_BINS)

test: host $(TEST_LIBS)
	@for t in $(TEST_LIBS); do \
		printf 'running %s ... ' "$$t"; \
		./host "$$t" > /dev/null && echo ok || { echo FAILED; exit 1; }; \
	done

clean:
	rm -f host $(EXAMPLE_LIBS) $(TEST_LIBS) $(BENCH_PLUGIN_LIBS) $(BENCH_STANDALONE_BINS)
