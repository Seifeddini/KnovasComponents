"""Refuse non-ASCII added to .py files on a range. Usage: py -3 scripts/check_ascii_py.py [RANGE]"""
import subprocess, sys

rng = sys.argv[1] if len(sys.argv) > 1 else "2845a9a..HEAD"
diff = subprocess.run(["git", "diff", rng, "--", "*.py"], capture_output=True).stdout.decode("utf-8", "replace")
path, hits = "?", 0
for line in diff.splitlines():
    if line.startswith("+++ "):
        path = line[6:]
    elif line.startswith("+") and any(ord(c) > 127 for c in line):
        print(f"{path}: {line[:100]}")
        hits += 1
print(f"{hits} added line(s) with a byte > 0x7F in {rng}")
sys.exit(1 if hits else 0)
