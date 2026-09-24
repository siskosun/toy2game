from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import unicodedata
from pathlib import Path

SAFE_INT = (1 << 53) - 1
RESERVED = {"CON","PRN","AUX","NUL",*{f"COM{i}" for i in range(1,10)},*{f"LPT{i}" for i in range(1,10)}}
FORBIDDEN = set('<>:"\\|?*')

class ProbeError(ValueError):
    pass

def utf16_key(value: str):
    raw = value.encode("utf-16-be")
    return tuple(int.from_bytes(raw[i:i+2], "big") for i in range(0, len(raw), 2))

def json_string(value: str) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

def canonical(value) -> bytes:
    if value is None: return b"null"
    if value is True: return b"true"
    if value is False: return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > SAFE_INT: raise ProbeError("integer outside safe range")
        return str(value).encode("ascii")
    if isinstance(value, float): raise ProbeError("float not allowed")
    if isinstance(value, str): return json_string(value)
    if isinstance(value, list): return b"[" + b",".join(canonical(x) for x in value) + b"]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value): raise ProbeError("keys must be strings")
        return b"{" + b",".join(json_string(k)+b":"+canonical(value[k]) for k in sorted(value,key=utf16_key)) + b"}"
    raise ProbeError("unsupported type")

def normalize_path(path: str) -> str:
    if not path or "\\" in path: raise ProbeError("slash")
    if unicodedata.normalize("NFC", path) != path: raise ProbeError("nfc")
    if path.startswith("/") or path.endswith("/") or "//" in path: raise ProbeError("relative")
    for part in path.split("/"):
        if part in {"", ".", ".."}: raise ProbeError("component")
        if part.endswith((" ", ".")): raise ProbeError("trailing")
        if any(ch in FORBIDDEN or ord(ch) < 32 for ch in part): raise ProbeError("forbidden")
        if part.split(".",1)[0].upper() in RESERVED: raise ProbeError("reserved")
    return path

def collision(paths):
    seen = {}
    for p in paths:
        normalize_path(p)
        k = unicodedata.normalize("NFC", p).casefold()
        if k in seen and seen[k] != p: raise ProbeError("collision")
        seen[k] = p

def rejected(fn):
    try:
        fn()
    except Exception:
        return True
    return False

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args=ap.parse_args()
    vector={"z":"末","a":["Alpha",True,None,42],"unicode":"é/游戏/Ω","nested":{"β":"two","A":"one"}}
    digest=hashlib.sha256(canonical(vector)).hexdigest()
    checks={
        "safe_int_max": not rejected(lambda: canonical({"n":SAFE_INT})),
        "reject_2pow53": rejected(lambda: canonical({"n":SAFE_INT+1})),
        "reject_windows_reserved": rejected(lambda: normalize_path("CON/file.txt")),
        "reject_backslash": rejected(lambda: normalize_path("bad\\path.txt")),
        "reject_nfd": rejected(lambda: normalize_path(unicodedata.normalize("NFD","é")+"/file.txt")),
        "reject_casefold_collision": rejected(lambda: collision(["Foo.tscn","foo.tscn"])),
    }
    result={"os":os.environ.get("RUNNER_OS"),"python":platform.python_version(),"unicode":unicodedata.unidata_version,"digest":digest,"checks":checks}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
    if not all(checks.values()): raise SystemExit(1)

if __name__ == "__main__":
    main()
