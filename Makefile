CXX      := g++
CXXFLAGS := -std=c++23 -Wall -Wextra -O2

ifeq ($(SAN),1)
  CXXFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer -g
endif

HOST_SRCS := $(wildcard *.cpp)
HOST_HDRS := $(wildcard *.h)

EXAMPLE_SRCS := $(wildcard example/*.cpp)
EXAMPLE_LIBS := $(EXAMPLE_SRCS:.cpp=.so)

TEST_SRCS := $(wildcard tests/*.cpp)
TEST_LIBS := $(TEST_SRCS:.cpp=.so)

.PHONY: all clean test

all: host $(EXAMPLE_LIBS) $(TEST_LIBS)

host: $(HOST_SRCS) $(HOST_HDRS)
	$(CXX) $(CXXFLAGS) -Wl,--export-dynamic -o $@ $(filter %.cpp, $^) \
		-ldl -lpthread -lmimalloc

example/%.so: example/%.cpp client.h
	$(CXX) $(CXXFLAGS) -fPIC -shared -I. -o $@ $<

tests/%.so: tests/%.cpp client.h
	$(CXX) $(CXXFLAGS) -fPIC -shared -I. -o $@ $<

test: host $(TEST_LIBS)
	@for t in $(TEST_LIBS); do \
		printf 'running %s ... ' "$$t"; \
		./host "$$t" > /dev/null && echo ok || { echo FAILED; exit 1; }; \
	done

clean:
	rm -f host $(EXAMPLE_LIBS) $(TEST_LIBS)
