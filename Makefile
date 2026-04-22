CXX      := g++
CXXFLAGS := -std=c++23 -Wall -Wextra -O2

ifeq ($(SAN),1)
  CXXFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer -g
endif

EXAMPLE_SRCS := $(wildcard example/*.cpp)
EXAMPLE_LIBS := $(EXAMPLE_SRCS:.cpp=.so)

.PHONY: all clean

all: host $(EXAMPLE_LIBS)

host: host.cpp store.cpp store.h client.h
	$(CXX) $(CXXFLAGS) -Wl,--export-dynamic -o $@ host.cpp store.cpp -ldl -lpthread -lmimalloc

example/%.so: example/%.cpp client.h
	$(CXX) $(CXXFLAGS) -fPIC -shared -I. -o $@ $<

clean:
	rm -f host $(EXAMPLE_LIBS)
