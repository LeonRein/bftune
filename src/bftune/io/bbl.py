"""Pure-Python Betaflight blackbox (.BBL/.BFL) decoder.

Implements the "Data version 2" blackbox format as written by
betaflight/src/main/blackbox/blackbox.c (encodings and predictors from
blackbox_fielddefs.h). The frame-validation logic follows the reference
parser shipped with betaflight-configurator (src/blackbox-viewer/flightlog_parser.js).

A single file may contain several logs (one per arm/disarm or blackbox start);
each starts with the "H Product:" header marker.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

LOG_START_MARKER = b"H Product:Blackbox flight data recorder by Nicholas Sherlock"

# Encodings (blackbox_fielddefs.h)
ENC_SIGNED_VB = 0
ENC_UNSIGNED_VB = 1
ENC_NEG_14BIT = 3
ENC_TAG8_8SVB = 6
ENC_TAG2_3S32 = 7
ENC_TAG8_4S16 = 8
ENC_NULL = 9
ENC_TAG2_3SVARIABLE = 10

# Predictors
PRED_0 = 0
PRED_PREVIOUS = 1
PRED_STRAIGHT_LINE = 2
PRED_AVERAGE_2 = 3
PRED_MINTHROTTLE = 4
PRED_MOTOR_0 = 5
PRED_INC = 6
PRED_HOME_COORD = 7
PRED_1500 = 8
PRED_VBATREF = 9
PRED_LAST_MAIN_FRAME_TIME = 10
PRED_MINMOTOR = 11

# Events (blackbox.h)
EV_SYNC_BEEP = 0
EV_INFLIGHT_ADJUSTMENT = 13
EV_LOGGING_RESUME = 14
EV_DISARM = 15
EV_FLIGHTMODE = 30
EV_LOG_END = 255

MAX_FRAME_LENGTH = 256
MAX_TIME_JUMP_US = 10_000_000
MAX_ITERATION_JUMP = 5000


def _sx(v: int, bits: int) -> int:
    """Sign-extend an unsigned `bits`-wide integer."""
    m = 1 << (bits - 1)
    return (v ^ m) - m


@dataclass
class FrameDef:
    names: list[str] = field(default_factory=list)
    signed: list[int] = field(default_factory=list)
    predictor: list[int] = field(default_factory=list)
    encoding: list[int] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.names)


@dataclass
class Log:
    """One decoded log session."""

    index: int
    headers: dict[str, str]
    main_names: list[str]
    main: np.ndarray  # (N, F) int64, valid main frames only
    slow_names: list[str]
    slow: np.ndarray  # (M, S) int64
    slow_at: np.ndarray  # (M,) index into `main` of the next main frame when the S frame arrived
    events: list[dict]
    stats: dict

    def col(self, name: str) -> np.ndarray:
        return self.main[:, self.main_names.index(name)]

    def has(self, name: str) -> bool:
        return name in self.main_names

    def header_int(self, key: str, default: int | None = None) -> int | None:
        v = self.headers.get(key)
        if v is None:
            return default
        try:
            return int(v.split(",")[0])
        except ValueError:
            return default

    def header_ints(self, key: str) -> list[int]:
        v = self.headers.get(key)
        if not v:
            return []
        return [int(x) for x in v.split(",") if x.strip().lstrip("-").isdigit()]


class _Stream:
    __slots__ = ("data", "end", "eof", "pos")

    def __init__(self, data: bytes, start: int, end: int):
        self.data = data
        self.pos = start
        self.end = end
        self.eof = False

    def byte(self) -> int:
        if self.pos < self.end:
            b = self.data[self.pos]
            self.pos += 1
            return b
        self.eof = True
        return 0

    def uvb(self) -> int:
        result = 0
        shift = 0
        data = self.data
        for _ in range(5):
            if self.pos >= self.end:
                self.eof = True
                return 0
            b = data[self.pos]
            self.pos += 1
            result |= (b & 0x7F) << shift
            if b < 128:
                return result & 0xFFFFFFFF
            shift += 7
        return 0

    def svb(self) -> int:
        u = self.uvb()
        return (u >> 1) ^ -(u & 1)

    def tag2_3s32(self) -> tuple[int, int, int]:
        lead = self.byte()
        sel = lead >> 6
        if sel == 0:
            return _sx((lead >> 4) & 3, 2), _sx((lead >> 2) & 3, 2), _sx(lead & 3, 2)
        if sel == 1:
            v0 = _sx(lead & 0x0F, 4)
            b = self.byte()
            return v0, _sx(b >> 4, 4), _sx(b & 0x0F, 4)
        if sel == 2:
            v0 = _sx(lead & 0x3F, 6)
            v1 = _sx(self.byte() & 0x3F, 6)
            v2 = _sx(self.byte() & 0x3F, 6)
            return v0, v1, v2
        return self._tag2_case3(lead)

    def _tag2_case3(self, lead: int) -> tuple[int, int, int]:
        out = [0, 0, 0]
        for i in range(3):
            s = lead & 3
            if s == 0:
                out[i] = _sx(self.byte(), 8)
            elif s == 1:
                b1 = self.byte()
                b2 = self.byte()
                out[i] = _sx(b1 | (b2 << 8), 16)
            elif s == 2:
                b1 = self.byte()
                b2 = self.byte()
                b3 = self.byte()
                out[i] = _sx(b1 | (b2 << 8) | (b3 << 16), 24)
            else:
                b1 = self.byte()
                b2 = self.byte()
                b3 = self.byte()
                b4 = self.byte()
                out[i] = _sx(b1 | (b2 << 8) | (b3 << 16) | (b4 << 24), 32)
            lead >>= 2
        return out[0], out[1], out[2]

    def tag2_3svariable(self) -> tuple[int, int, int]:
        lead = self.byte()
        sel = lead >> 6
        if sel == 0:
            return _sx((lead >> 4) & 3, 2), _sx((lead >> 2) & 3, 2), _sx(lead & 3, 2)
        if sel == 1:
            v0 = _sx((lead & 0x3E) >> 1, 5)
            b2 = self.byte()
            v1 = _sx(((lead & 1) << 4) | ((b2 & 0xF0) >> 4), 5)
            return v0, v1, _sx(b2 & 0x0F, 4)
        if sel == 2:
            b2 = self.byte()
            v0 = _sx(((lead & 0x3F) << 2) | ((b2 & 0xC0) >> 6), 8)
            b3 = self.byte()
            v1 = _sx(((b2 & 0x3F) << 1) | ((b3 & 0x80) >> 7), 7)
            return v0, v1, _sx(b3 & 0x7F, 7)
        return self._tag2_case3(lead)

    def tag8_4s16_v2(self) -> list[int]:
        selector = self.byte()
        values = [0, 0, 0, 0]
        nibble = 0
        buf = 0
        for i in range(4):
            s = selector & 3
            if s == 1:
                if nibble == 0:
                    buf = self.byte()
                    values[i] = _sx(buf >> 4, 4)
                    nibble = 1
                else:
                    values[i] = _sx(buf & 0x0F, 4)
                    nibble = 0
            elif s == 2:
                if nibble == 0:
                    values[i] = _sx(self.byte(), 8)
                else:
                    c1 = (buf & 0x0F) << 4
                    buf = self.byte()
                    values[i] = _sx(c1 | (buf >> 4), 8)
            elif s == 3:
                if nibble == 0:
                    c1 = self.byte()
                    c2 = self.byte()
                    values[i] = _sx((c1 << 8) | c2, 16)
                else:
                    c1 = self.byte()
                    c2 = self.byte()
                    values[i] = _sx(((buf & 0x0F) << 12) | (c1 << 4) | (c2 >> 4), 16)
                    buf = c2
            selector >>= 2
        return values

    def tag8_8svb(self, count: int) -> list[int]:
        if count == 1:
            return [self.svb()]
        header = self.byte()
        out = []
        for _ in range(count):
            out.append(self.svb() if header & 1 else 0)
            header >>= 1
        return out


def _to_s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def split_logs(data: bytes) -> list[tuple[int, int]]:
    """Return (start, end) byte ranges of each log in the file."""
    starts = []
    pos = data.find(LOG_START_MARKER)
    while pos >= 0:
        starts.append(pos)
        pos = data.find(LOG_START_MARKER, pos + 1)
    return [(s, starts[i + 1] if i + 1 < len(starts) else len(data)) for i, s in enumerate(starts)]


def _parse_headers(data: bytes, start: int, end: int) -> tuple[dict[str, str], int]:
    headers: dict[str, str] = {}
    pos = start
    while pos < end and data[pos : pos + 2] == b"H ":
        nl = data.find(b"\n", pos, end)
        if nl < 0:
            nl = end
        line = data[pos + 2 : nl].decode("latin-1").rstrip("\r")
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k] = v
        pos = nl + 1
    return headers, pos


def _frame_defs(headers: dict[str, str]) -> dict[str, FrameDef]:
    defs: dict[str, FrameDef] = {}
    for key, val in headers.items():
        if not key.startswith("Field "):
            continue
        parts = key.split(" ")
        if len(parts) != 3:
            continue
        ftype, prop = parts[1], parts[2]
        fd = defs.setdefault(ftype, FrameDef())
        if prop == "name":
            fd.names = val.split(",")
        else:
            nums = [int(x) for x in val.split(",") if x != ""]
            if prop == "signed":
                fd.signed = nums
            elif prop == "predictor":
                fd.predictor = nums
            elif prop == "encoding":
                fd.encoding = nums
    # P frames share names/signedness with I frames
    if "P" in defs and "I" in defs:
        defs["P"].names = defs["I"].names
        defs["P"].signed = defs["I"].signed
    return defs


def _decode_one(data: bytes, index: int, start: int, end: int) -> Log:
    headers, pos = _parse_headers(data, start, end)
    if headers.get("Data version", "2").strip() != "2":
        raise ValueError(f"Unsupported blackbox data version {headers.get('Data version')}")
    defs = _frame_defs(headers)
    if "I" not in defs:
        raise ValueError("Log has no main-frame field definitions")
    idef, pdef = defs["I"], defs.get("P")
    sdef = defs.get("S")
    nf = idef.count

    i_interval = max(1, int(headers.get("I interval", "32")))
    p_interval_raw = headers.get("P interval", "1")
    if "/" in p_interval_raw:
        p_num, p_den = (int(x) for x in p_interval_raw.split("/"))
    else:
        p_num, p_den = 1, int(p_interval_raw)
    motor_output = [int(x) for x in headers.get("motorOutput", "0").split(",")]
    min_motor = motor_output[0]
    minthrottle = int(headers.get("minthrottle", "1000").split(",")[0])
    vbatref = int(headers.get("vbatref", "0") or 0)

    def should_have_frame(idx: int) -> bool:
        return ((idx % i_interval) + p_num - 1) % p_den < p_num

    motor0_idx = idef.names.index("motor[0]") if "motor[0]" in idef.names else -1
    ITER, TIME = 0, 1

    s = _Stream(data, pos, end)
    rows: list[list[int]] = []
    slow_rows: list[list[int]] = []
    slow_at: list[int] = []
    events: list[dict] = []
    stats = {"corrupt": 0, "desync": 0, "I": 0, "P": 0, "S": 0, "E": 0}

    prev: list[int] | None = None
    prev2: list[int] | None = None
    valid = False
    last_iter = -1
    last_time = -1
    last_slow = [0] * (sdef.count if sdef else 0)

    def parse_frame(fd: FrameDef, prev_, prev2_, skipped: int, cur: list[int]) -> None:
        pred = fd.predictor
        enc = fd.encoding
        n = fd.count
        i = 0

        def apply(idx: int, value: int) -> int:
            p = pred[idx]
            if p == PRED_0:
                return value
            if p == PRED_PREVIOUS:
                return value + prev_[idx] if prev_ is not None else value
            if p == PRED_STRAIGHT_LINE:
                return value + 2 * prev_[idx] - prev2_[idx] if prev_ is not None else value
            if p == PRED_AVERAGE_2:
                if prev_ is None:
                    return value
                sm = prev_[idx] + prev2_[idx]
                return value + int(sm / 2)  # trunc toward zero like C
            if p == PRED_MINMOTOR:
                return _to_s32(value) + min_motor
            if p == PRED_MINTHROTTLE:
                return _to_s32(value) + minthrottle
            if p == PRED_MOTOR_0:
                return value + cur[motor0_idx]
            if p == PRED_1500:
                return value + 1500
            if p == PRED_VBATREF:
                return value + vbatref
            if p == PRED_LAST_MAIN_FRAME_TIME:
                return value + (prev_[TIME] if prev_ is not None else 0)
            raise ValueError(f"Unsupported predictor {p}")

        while i < n:
            if pred[i] == PRED_INC:
                cur[i] = skipped + 1 + (prev_[i] if prev_ is not None else 0)
                i += 1
                continue
            e = enc[i]
            if e == ENC_SIGNED_VB:
                cur[i] = apply(i, s.svb())
                i += 1
            elif e == ENC_UNSIGNED_VB:
                cur[i] = apply(i, s.uvb())
                i += 1
            elif e == ENC_NEG_14BIT:
                cur[i] = apply(i, -_sx(s.uvb() & 0x3FFF, 14))
                i += 1
            elif e == ENC_TAG8_4S16:
                vals = s.tag8_4s16_v2()
                for j in range(4):
                    if i < n:
                        cur[i] = apply(i, vals[j])
                    i += 1
            elif e == ENC_TAG2_3S32:
                vals = s.tag2_3s32()
                for j in range(3):
                    if i < n:
                        cur[i] = apply(i, vals[j])
                    i += 1
            elif e == ENC_TAG2_3SVARIABLE:
                vals = s.tag2_3svariable()
                for j in range(3):
                    if i < n:
                        cur[i] = apply(i, vals[j])
                    i += 1
            elif e == ENC_TAG8_8SVB:
                j = i + 1
                while j < i + 8 and j < n and enc[j] == ENC_TAG8_8SVB:
                    j += 1
                cnt = j - i
                vals = s.tag8_8svb(cnt)
                for k in range(cnt):
                    cur[i] = apply(i, vals[k])
                    i += 1
            elif e == ENC_NULL:
                cur[i] = apply(i, 0)
                i += 1
            else:
                raise ValueError(f"Unsupported encoding {e}")

    def count_skipped() -> int:
        if last_iter < 0:
            return 0
        c = 0
        idx = last_iter + 1
        while not should_have_frame(idx):
            c += 1
            idx += 1
        return c

    def parse_event() -> dict | None:
        et = s.byte()
        ev: dict = {"type": et, "at": len(rows)}
        if et == EV_SYNC_BEEP:
            ev["time"] = s.uvb()
        elif et == EV_FLIGHTMODE:
            ev["flags"] = s.uvb()
            ev["last_flags"] = s.uvb()
        elif et == EV_DISARM:
            ev["reason"] = s.uvb()
        elif et == EV_INFLIGHT_ADJUSTMENT:
            f = s.byte()
            ev["func"] = f & 127
            if f >= 128:
                raw = bytes(s.byte() for _ in range(4))
                ev["value"] = struct.unpack("<f", raw)[0]
            else:
                ev["value"] = s.svb()
        elif et == EV_LOGGING_RESUME:
            ev["iteration"] = s.uvb()
            ev["time"] = s.uvb()
        elif et == EV_LOG_END:
            msg = bytes(s.byte() for _ in range(11))
            if msg != b"End of log\x00":
                return None
            s.end = s.pos
        else:
            return None
        return ev

    frame_start = 0
    last_type: str | None = None
    cur: list[int] = [0] * nf
    pending_event: dict | None = None
    premature_eof = False
    valid_types = {"I", "P", "E"} | ({"S"} if sdef else set()) | ({"G"} if "G" in defs else set()) | (
        {"H"} if "H" in defs else set()
    )

    while True:
        if s.pos < s.end:
            cmd = chr(data[s.pos])
            s.pos += 1
            at_eof = False
        else:
            cmd = ""
            at_eof = True
        if last_type is not None:
            size = s.pos - frame_start
            looks_complete = (cmd in valid_types) or (at_eof and not premature_eof)
            if size <= MAX_FRAME_LENGTH and looks_complete:
                # complete previous frame
                if last_type == "I":
                    accept = True
                    if last_iter != -1:
                        accept = (
                            last_iter <= cur[ITER] < last_iter + MAX_ITERATION_JUMP
                            and last_time <= cur[TIME] < last_time + MAX_TIME_JUMP_US
                        )
                    if accept:
                        last_iter, last_time = cur[ITER], cur[TIME]
                        valid = True
                        rows.append(cur)
                        stats["I"] += 1
                        prev = cur
                        prev2 = cur
                    else:
                        valid = False
                        prev = prev2 = None
                        stats["desync"] += 1
                    cur = [0] * nf
                elif last_type == "P":
                    if valid and (cur[TIME] > last_time + MAX_TIME_JUMP_US or cur[ITER] > last_iter + MAX_ITERATION_JUMP):
                        valid = False
                    if valid:
                        last_iter, last_time = cur[ITER], cur[TIME]
                        rows.append(cur)
                        stats["P"] += 1
                        prev2 = prev
                        prev = cur
                        cur = [0] * nf
                    else:
                        stats["desync"] += 1
                elif last_type == "S":
                    slow_rows.append(list(last_slow))
                    slow_at.append(len(rows))
                    stats["S"] += 1
                elif last_type == "E":
                    if pending_event is not None:
                        if pending_event["type"] == EV_LOGGING_RESUME:
                            last_iter = pending_event["iteration"]
                            last_time = pending_event["time"]
                        events.append(pending_event)
                        stats["E"] += 1
            else:
                valid = False
                prev = prev2 = None
                stats["corrupt"] += 1
                s.pos = frame_start + 1
                last_type = None
                premature_eof = False
                s.eof = False
                continue
        if at_eof:
            break
        frame_start = s.pos - 1
        if cmd not in valid_types:
            valid = False
            last_type = None
            continue
        last_type = cmd
        s.eof = False
        if cmd == "I":
            parse_frame(idef, prev, None, 0, cur)
        elif cmd == "P":
            if pdef is None:
                last_type = None
                continue
            if not valid or prev is None:
                # can't decode without history; parse to skip bytes
                tmp = [0] * nf
                parse_frame(pdef, [0] * nf, [0] * nf, 0, tmp)
            else:
                parse_frame(pdef, prev, prev2, count_skipped(), cur)
        elif cmd == "S":
            parse_frame(sdef, None, None, 0, last_slow)
        elif cmd in ("G", "H"):
            tmp = [0] * defs[cmd].count
            parse_frame(defs[cmd], None, None, 0, tmp)
        elif cmd == "E":
            pending_event = parse_event()
        if s.eof:
            premature_eof = True

    main = np.array(rows, dtype=np.int64) if rows else np.zeros((0, nf), dtype=np.int64)
    slow = (
        np.array(slow_rows, dtype=np.int64)
        if slow_rows
        else np.zeros((0, sdef.count if sdef else 0), dtype=np.int64)
    )
    return Log(
        index=index,
        headers=headers,
        main_names=list(idef.names),
        main=main,
        slow_names=list(sdef.names) if sdef else [],
        slow=slow,
        slow_at=np.array(slow_at, dtype=np.int64),
        events=events,
        stats=stats,
    )


def decode(path: str) -> list[Log]:
    """Decode every log session contained in a blackbox file."""
    with open(path, "rb") as fh:
        data = fh.read()
    ranges = split_logs(data)
    if not ranges:
        raise ValueError(f"{path}: no blackbox log header found")
    return [_decode_one(data, i, a, b) for i, (a, b) in enumerate(ranges)]
