"""
tools/count_claims.py  (ban sua loi v2)

Dem so test theo tung nhom de doi chieu voi cac con so trong bao cao.
Chay offline, khong goi API.

    python -m tools.count_claims

v2: sua loi bo sot test parametrize co dau cach trong id,
    vi du  test_parse[bat den phong khach].
    Doi chieu them voi dong tong ket cua chinh pytest.
"""

import re
import subprocess
import sys
from collections import Counter

GROUPS = {
    "Multi-turn (bao cao ghi 19)": ["test_multi_turn"],
    "Adversarial / security (bao cao ghi 20)": ["test_security_policy"],
    "Validator": ["test_validator", "test_capability_validation"],
    "Design patterns": ["tests/pattern/"],
    "Database": ["tests/db/"],
}


def collect():
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("Khong tim thay pytest. Cai bang: pip install pytest")
        sys.exit(1)

    out = proc.stdout

    # v2: bat moi dong co chua '.py::' thay vi doi khop ca dong khong dau cach.
    ids = [ln.strip() for ln in out.splitlines() if ".py::" in ln]

    # pytest tu in ra tong so -> dung lam nguon doi chieu.
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    reported = int(m.group(1)) if m else None

    if not ids:
        print("Khong parse duoc ket qua pytest. Output tho:\n")
        print(out[-2000:] or proc.stderr[-2000:])
        sys.exit(1)

    return ids, reported


def main():
    ids, reported = collect()
    total = len(ids)

    print("=" * 62)
    print(f"TONG SO TEST (dem duoc)   : {total}")
    if reported is not None:
        print(f"TONG SO TEST (pytest bao) : {reported}")
        if reported != total:
            print(f"  !! LECH {abs(reported - total)} -> tin con so cua pytest")
    print("=" * 62)

    for label, keywords in GROUPS.items():
        hits = [n for n in ids if any(k in n.replace("\\", "/") for k in keywords)]
        print(f"\n{label}: {len(hits)}")
        for f, n in sorted(Counter(h.split("::", 1)[0] for h in hits).items()):
            print(f"    {n:>4}  {f}")

    print("\n" + "-" * 62)
    print("Chi tiet theo file:")
    per_file = Counter(n.split("::", 1)[0] for n in ids)
    for f, n in per_file.most_common():
        print(f"    {n:>4}  {f}")


if __name__ == "__main__":
    main()