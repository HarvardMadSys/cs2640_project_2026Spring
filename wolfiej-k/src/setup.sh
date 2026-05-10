#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

# CMake
sudo apt-get update -qq
sudo apt-get install -yq cmake zlib1g-dev wget lsb-release software-properties-common gnupg

# LLVM C++23
wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh
chmod +x /tmp/llvm.sh
sudo /tmp/llvm.sh 18 > /dev/null 2>&1
sudo apt-get install -yq libc++-18-dev libc++abi-18-dev

cat << 'EOF' >> ~/.bashrc
export CXX=/usr/bin/clang++-18
export CC=/usr/bin/clang-18
export CXXFLAGS="-stdlib=libc++"
export LDFLAGS="-stdlib=libc++"
EOF

# Python
sudo apt-get install -yq python3-venv
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Java + YCSB
sudo apt-get install -yq openjdk-17-jdk
YCSB_VERSION=0.17.0
if [ ! -d external/ycsb ]; then
    mkdir -p external
    wget -qO /tmp/ycsb.tar.gz \
        https://github.com/brianfrankcooper/YCSB/releases/download/${YCSB_VERSION}/ycsb-${YCSB_VERSION}.tar.gz
    tar xf /tmp/ycsb.tar.gz -C external
    mv external/ycsb-${YCSB_VERSION} external/ycsb
    rm /tmp/ycsb.tar.gz
fi

# Lightning
sudo apt-get install -yq libboost-all-dev
if [ ! -d external/lightning ]; then
    git clone --depth=1 https://github.com/danyangz/lightning external/lightning
fi
if [ -f external/lightning/inc/config.h ]; then
    sed -i 's/^#define USE_MPK/\/\/ #define USE_MPK/' external/lightning/inc/config.h
fi
mkdir -p external/lightning/build
export JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")
(cd external/lightning/build && \
    cmake -DJAVA_CLIENT=ON .. > /dev/null && \
    make -j"$(nproc)" > /dev/null)

javac -d external/lightning/build \
    external/lightning/java/jlightning/JlightningClient.java

# Run `source ~/.bashrc` after setup! Necessary to build with Clang, which is
# the easiest way to get a C++23 stdlib on Ubuntu.
