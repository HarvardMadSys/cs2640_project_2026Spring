#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -qq
sudo apt-get install -yq cmake zlib1g-dev wget lsb-release software-properties-common gnupg

wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh
chmod +x /tmp/llvm.sh
sudo /tmp/llvm.sh 18 > /dev/null 2>&1
sudo apt-get install -yq libc++-18-dev libc++abi-18-dev

sudo apt-get install -yq python3-venv
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
