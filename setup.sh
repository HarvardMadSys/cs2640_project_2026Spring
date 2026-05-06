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

# Run `source ~/.bashrc` after setup! Necessary to build with Clang, which is
# the easiest way to get a C++23 stdlib on Ubuntu.
