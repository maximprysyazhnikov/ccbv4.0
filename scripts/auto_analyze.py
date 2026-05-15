import os
import re
import argparse
from collections import defaultdict
from datetime import datetime

PATTERNS = {
    "autopost": re.compile(r"\bautopost\b", re.I),
    "parse_func": re.compile(r"\b_parse\b|\bparse\(", re.I),
    "rr": re.compile(r"\brr\b|risk[_\s-]*reward|autopost[_\s-]*rr", re.I),
    "direction": re.compile(r"\bLONG\b|\bSHORT\b|\U0001F7E2|\U0001F534|BUY|SELL|\u2B06\uFE0F|\u2B07\uFE0F", re.I),
    "db_insert": re.compile(r"INSERT\s+INTO", re.I),
    "db_create_signals": re.compile(r"CREATE\s+TABLE\s+.*\bsignals\b", re.I),
    "transactions": re.compile(r"\bBEGIN\b|\bCOMMIT\b|\bROLLBACK\b|\bTRANSACTION\b", re.I),
    "signals_table": re.compile(r"\bsignals\b", re.I),
    "trades_table": re.compile(r"\btrades\b", re.I),
    "user_settings": re.compile(r"\buser[_\s-]*settings\b|\bpanel\b|/set_rr|set_rr\b", re.I),
}


def scan_file(path):
    hits = defaultdict(list)
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return hits
    for name, p in PATTERNS.items():
        for m in p.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            lines = text.splitlines()
            snippet = lines[line_no-1].strip() if 0 <= line_no-1 < len(lines) else ""
            hits[name].append((line_no, snippet))
    return hits


def walk_and_scan(root):
    found = defaultdict(list)
    for dirpath, _, filenames in os.walk(root):
        # skip venv, .git, __pycache__
        if any(x in dirpath for x in (os.path.sep + "venv" + os.path.sep, os.path.sep + ".git" + os.path.sep, "__pycache__")):
            continue
        for fn in filenames:
            if not fn.endswith((".py", ".sql", ".pyw", ".yml", ".yaml", ".json", ".md")):
                continue
            path = os.path.join(dirpath, fn)
            hits = scan_file(path)
            for k, v in hits.items():
                if v:
                    found[k].append((path, v))
    return found


def generate_report(found, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# Auto-analysis report\nGenerated: {datetime.utcnow().isoformat()}Z\n\n")
        for key in ["autopost","parse_func","direction","rr","user_settings","db_insert","transactions","db_create_signals","signals_table","trades_table"]:
            items = found.get(key, [])
            f.write(f"## {key} ({len(items)} files)\n")
            for path, hits in items:
                f.write(f"- {path}\n")
                for line_no, snippet in hits[:5]:
                    f.write(f"  - L{line_no}: {snippet}\n")
            f.write("\n")
        # heuristic checks
        f.write("## Heuristics\n")
        # check files that insert into signals/trades but have no transactions
        inserts = {p for p,_ in found.get("db_insert", [])}
        tx_files = {p for p,_ in found.get("transactions", [])}
        suspect = inserts - tx_files
        f.write(f"- Files with INSERT but no transaction keywords: {len(suspect)}\n")
        for p in sorted(suspect):
            f.write(f"  - {p}\n")
        # check if direction-only mentions LONG or only SHORT
        dir_files = found.get("direction", [])
        if dir_files:
            f.write(f"- Files mentioning directions (sample {min(10,len(dir_files))}):\n")
            for p,_ in dir_files[:10]:
                f.write(f"  - {p}\n")
        f.write("\n## Recommendations\n")
        f.write("- Review files listed above for: support of SHORT and LONG parsing, RR sourcing from user settings, DB transactions wrapping inserts.\n")
        f.write("- Run detailed iterator on candidate parser files and add unit tests for parse() to assert LONG/SHORT and rr extraction.\n")
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default=".", help="project root")
    parser.add_argument("--out", default="reports/auto_analyze_report.md", help="report path")
    args = parser.parse_args()
    found = walk_and_scan(args.path)
    out = generate_report(found, args.out)
    print(f"Report generated: {out}")


if __name__ == "__main__":
    main()
