"""Hash an installed payload against its unchanged portable manifest."""
import argparse
import json
from pathlib import Path
import time

from build_distribution import digest, inventory


parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--manifest', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
started = time.monotonic()
manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
expected = {row['path']: row for row in manifest['files']}
actual = {row['path']: row for row in inventory(args.bundle)}
missing = sorted(set(expected) - set(actual))
different = sorted(name for name in expected.keys() & actual.keys()
                   if expected[name] != actual[name])
extras = sorted(set(actual) - set(expected))
result = dict(bundle=str(args.bundle.resolve()), files=len(expected),
              payload_bytes=manifest['bytes'], installed_files=len(actual),
              installed_bytes=sum(row['bytes'] for row in actual.values()),
              missing=missing, different=different, extras=extras,
              manifest_sha256=digest(args.manifest),
              seconds=round(time.monotonic() - started, 2),
              passed=not missing and not different)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=True))
raise SystemExit(0 if result['passed'] else 1)
