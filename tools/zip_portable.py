# -*- coding: utf-8 -*-
"""Package a portable build dir into a UTF-8-named zip for distribution.

Runs after `build_portable.ps1` assembles the folder. Reads the build
directory from the EPM_BUILD environment variable (avoids passing a
non-ASCII path via argv, which PowerShell 5.1 would mangle).
"""
import os
import sys
import zipfile

ZIP_NAME = "\u82f1\u8bed\u5237\u9898\u673a-\u4fbf\u643a\u7248.zip"  # 英语刷题机-便携版.zip
PREFIX = "\u82f1\u8bed\u5237\u9898\u673a-\u4fbf\u643a\u7248/"  # 英语刷题机-便携版/


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    build = os.environ.get("EPM_BUILD")
    if not build or not os.path.isdir(build):
        print("EPM_BUILD not set or not a directory", file=sys.stderr)
        return 1

    out = os.path.join(os.path.dirname(build.rstrip("\\/")), ZIP_NAME)
    if os.path.exists(out):
        os.remove(out)

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(build):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            rel = os.path.relpath(root, build)
            rel = "" if rel == "." else rel
            if rel == "backend" and "data" in dirs:
                dirs.remove("data")
            for f in files:
                if f.endswith(".pyc") or f.endswith(".log"):
                    continue
                full = os.path.join(root, f)
                arc = (PREFIX + rel + "/" + f) if rel else (PREFIX + f)
                arc = arc.replace("\\", "/")
                zf.write(full, arc)
                count += 1

    print("packed %d files -> %s" % (count, out))
    print("size MB:", round(os.path.getsize(out) / 1048576, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())