#!/usr/bin/env python3
"""Generate bftune's CLI settings database from a Betaflight source tree.

Usage:
    python tools/gen_settings_db.py /path/to/betaflight 2026.6 > src/bftune/data/settings_2026.6.json

Extracts every entry of `valueTable[]` in src/main/cli/settings.c: name, type, scope
(master/profile/rateprofile), numeric range or lookup-table values, array length.
Preprocessor conditionals are ignored (all entries are kept), which keeps the
TABLE_* enum and the lookupTables[] list aligned because both use the same guards.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# enum names whose lookup array does not follow the TABLE_FOO -> lookupTableFoo convention
ALIASES = {
    "TABLE_GYRO_LPF_TYPE": "lookupTableLowpassType",
    "TABLE_DTERM_LPF_TYPE": "lookupTableLowpassType",
    "TABLE_MOTOR_PWM_PROTOCOL": "lookupTablePwmProtocol",
    "TABLE_ACC_HARDWARE": "lookupTableAccHardware",
    "TABLE_BARO_HARDWARE": "lookupTableBaroHardware",
    "TABLE_MAG_HARDWARE": "lookupTableMagHardware",
}


def strip_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def collect_defines(root: Path) -> dict[str, str]:
    defs: dict[str, str] = {}
    for p in root.rglob("*.h"):
        try:
            txt = strip_comments(p.read_text(errors="replace"))
        except OSError:
            continue
        for m in re.finditer(r"^\s*#define\s+([A-Za-z_]\w*)\s+([^\n]+)$", txt, flags=re.M):
            defs.setdefault(m.group(1), m.group(2).strip())
    return defs


def eval_expr(expr: str, defs: dict[str, str], depth: int = 0) -> int | None:
    expr = expr.strip()
    if depth > 10:
        return None
    expr = re.sub(r"\b(\d+)[uUlLfF]+\b", r"\1", expr)

    def repl(m: re.Match) -> str:
        name = m.group(0)
        if name in defs:
            v = eval_expr(defs[name], defs, depth + 1)
            if v is not None:
                return str(v)
        return name

    expr2 = re.sub(r"\b[A-Za-z_]\w*\b", repl, expr)
    expr2 = re.sub(r"\(\s*(u?int\d+_t|float|int)\s*\)", "", expr2)
    if re.search(r"[A-Za-z_]", expr2):
        return None
    try:
        return int(eval(expr2, {"__builtins__": {}}))  # noqa: S307 - digits and operators only
    except Exception:
        return None


def main() -> None:
    bf = Path(sys.argv[1])
    version = sys.argv[2] if len(sys.argv) > 2 else "unknown"
    main_dir = bf / "src" / "main"
    defs = collect_defines(main_dir)
    defs.update({
        "UINT8_MAX": "255", "INT8_MAX": "127", "INT8_MIN": "(-128)", "UINT16_MAX": "65535",
        "INT16_MAX": "32767", "INT16_MIN": "(-32768)", "UINT32_MAX": "4294967295",
        "INT32_MAX": "2147483647", "INT32_MIN": "(-2147483648)",
    })
    names = {}
    for m in re.finditer(r'#define\s+(PARAM_NAME_\w+)\s+"([^"]+)"', (main_dir / "fc/parameter_names.h").read_text()):
        names[m.group(1)] = m.group(2)

    settings_c = strip_comments((main_dir / "cli/settings.c").read_text())
    settings_h = strip_comments((main_dir / "cli/settings.h").read_text())

    # lookup table enum order
    enum_body = re.search(r"typedef enum \{(.*?)\}\s*lookupTableIndex_e", settings_h, flags=re.S).group(1)
    table_enum = [
        t.split("=")[0].strip()
        for t in enum_body.split(",")
        if t.strip() and not t.strip().startswith("#") and t.strip().startswith("TABLE_")
    ]
    table_enum = [re.sub(r"#.*", "", t).strip() for t in re.findall(r"\b(TABLE_\w+)", enum_body)]
    table_enum = [t for t in table_enum if t != "LOOKUP_TABLE_COUNT"]
    entries_body = re.search(r"lookupTables\[\]\s*=\s*\{(.*?)\};", settings_c, flags=re.S).group(1)
    table_arrays = re.findall(r"LOOKUP_TABLE_ENTRY\((\w+)\)", entries_body)
    arrays: dict[str, list[str]] = {}
    arr_re = r"const\s+char\s*\*\s*const\s+(\w+)\[[^\]]*\]\s*=\s*\{(.*?)\};"
    for m in re.finditer(arr_re, settings_c, flags=re.S):
        arrays[m.group(1)] = re.findall(r'"([^"]*)"', m.group(2))
    for p in main_dir.rglob("*.c"):
        if p.name == "settings.c":
            continue
        txt = p.read_text(errors="replace")
        for arr in table_arrays:
            if arr in arrays:
                continue
            mm = re.search(rf"const\s+char\s*\*\s*const\s+{arr}\[[^\]]*\]\s*=\s*\{{(.*?)\}};", txt, flags=re.S)
            if mm:
                arrays[arr] = re.findall(r'"([^"]*)"', strip_comments(mm.group(1)))
    # match TABLE_FOO_BAR -> lookupTableFooBar by normalized name (robust to #ifdef skew)
    norm = {re.sub(r"^lookupTable", "", a).lower(): a for a in table_arrays}
    tables = {}
    for i, enum_name in enumerate(table_enum):
        key = enum_name[len("TABLE_"):].replace("_", "").lower()
        arr = norm.get(key)
        if arr is None:
            cands = [v for k, v in norm.items() if k.startswith(key) or key.startswith(k)]
            arr = cands[0] if len(cands) == 1 else None
        if arr is None and enum_name in ALIASES and ALIASES[enum_name] in table_arrays:
            arr = ALIASES[enum_name]
        tables[enum_name] = arrays.get(arr) if arr else None

    body = re.search(r"const clivalue_t valueTable\[\]\s*=\s*\{(.*?)\n\};", settings_c, flags=re.S).group(1)
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        m = re.match(r'\{\s*("([^"]+)"|(PARAM_NAME_\w+))\s*,\s*([^,]+),\s*(.*)\}\s*,?$', line)
        if not m:
            continue
        name = m.group(2) or names.get(m.group(3))
        if not name:
            continue
        flags = m.group(4)
        rest = m.group(5)
        entry: dict = {}
        vt = re.search(r"VAR_(UINT8|INT8|UINT16|INT16|UINT32|INT32)", flags)
        entry["type"] = vt.group(1).lower() if vt else "unknown"
        entry["scope"] = (
            "profile" if "PROFILE_VALUE" in flags and "PROFILE_RATE" not in flags else
            "rateprofile" if "PROFILE_RATE_VALUE" in flags else
            "battery" if "PROFILE_BATTERY" in flags else "master"
        )
        if "MODE_LOOKUP" in flags or "MODE_BITSET" in flags:
            t = re.search(r"\.config\.lookup\s*=\s*\{\s*(TABLE_\w+)", rest)
            if t:
                entry["mode"] = "lookup"
                entry["values"] = tables.get(t.group(1))
                entry["table"] = t.group(1)
            if "MODE_BITSET" in flags:
                entry["mode"] = "bitset"
        elif "MODE_ARRAY" in flags:
            entry["mode"] = "array"
            ln = re.search(r"\.config\.array\.length\s*=\s*([^,]+)", rest)
            entry["length"] = eval_expr(ln.group(1), defs) if ln else None
        elif "MODE_STRING" in flags:
            entry["mode"] = "string"
        else:
            entry["mode"] = "direct"
            mm = re.search(r"\.config\.minmax(?:Unsigned)?\s*=\s*\{\s*([^,]+),\s*([^}]+)\}", rest)
            if mm:
                entry["min"] = eval_expr(mm.group(1), defs)
                entry["max"] = eval_expr(mm.group(2), defs)
            mu32 = re.search(r"\.config\.u32Max\s*=\s*([^,]+)", rest)
            if mu32:
                entry["min"] = 0
                entry["max"] = eval_expr(mu32.group(1), defs)
            md = re.search(r"\.config\.d32Max\s*=\s*([^,]+)", rest)
            if md:
                v = eval_expr(md.group(1), defs)
                entry["min"], entry["max"] = (-v if v is not None else None), v
        out[name] = entry
    json.dump({"firmware": version, "settings": out}, sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
