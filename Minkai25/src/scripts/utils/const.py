KiB = 1024
MiB = 1024 * KiB
GiB = 1024 * MiB
TiB = 1024 * GiB
PiB = 1024 * TiB
EiB = 1024 * PiB
ZiB = 1024 * EiB
YiB = 1024 * ZiB

KB = 1000
MB = 1000 * KB
GB = 1000 * MB
TB = 1000 * GB
PB = 1000 * TB
EB = 1000 * PB


OP_NAMES = {
    0: "NOP",
    1: "GET",
    2: "GETS",
    3: "SET",
    4: "ADD",
    5: "CAS",
    6: "REPLACE",
    7: "APPEND",
    8: "PREPEND",
    9: "DELETE",
    10: "INCR",
    11: "DECR",
    12: "READ",
    13: "WRITE",
    14: "UPDATE",
    255: "INVALID",
}
