## ConnectFS: A Real-time Internet Data Aggregator File System

Author: Zani Xu
Abstract: ConnectFS is a real-time internet data aggregator filesystem that extends the Unix “everything is a file” abstraction to the internet. Unix traditionally represents hardware devices, process metadata, and kernel parameters as local files, but ConnectFS enables users to access internet data using standard command-line tools such as cat and grep. The system utilizes a three-tier architecture in order to bridge the latency gap between local access and remote APIs. Tier 1 utilizes a FUSE-based daemon to intercept system calls and displays the data as a local virtual filesystem. Tier 2 consists of a Flask aggregator server that makes API requests and gives data to Tier 1 while utilizing polling and caching to maintain freshness while still keeping local latency low. Tier 3 consists of the external providers, which for the sake of this project is Yahoo Finance. The current implementation of the project supports dynamic updates through a writable .config file, allowing the user to change which tickers are being cached and polled.

## Repository Layout

Here is the repository layout

```text
PotatoLatte/
├── README.md            # This file
├── report.pdf           # USENIX-format final report
├── report/              # LaTeX source file for final report, and figures
├── src/                 # Project source code
│   ├── requirements.txt # Python Dependencies
│   ├── .config          # Writable file with list of tickers
│   ├── main.cpp         # Tier 1: FUSE Daemon (C++)
│   └── app.py           # Tier 2: Flask Aggregator (Python)
└── ai-usage.md          # AI usage report (Final submission only)
```

## Build and Run

My Flask server is run on zanixu.com, but yours should be on your own domain name.

For each terminal:
ssh (username@domainname.com)

In one terminal:
```
cd src
pip install -r requirements.txt
flask --app app run --host=0.0.0.0
```

To check, visit ttp://domainname.com:5000/?ticker=AAPL

In another terminal:
```
cd src
mkdir my_mount
g++ -Wall main.cpp `pkg-config fuse3 libcurl --cflags --libs` -o my_fuse_fs
./my_fuse_fs (DOMAIN NAME HERE, mine is zanixu.com):5000 -f ~/src/my_mount
```

In third terminal:
```
cd src
cd my_mount
ls
```

You should now see the files for each ticker in .config, and can use cat or other commands on them.
