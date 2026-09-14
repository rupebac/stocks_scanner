#!/usr/bin/env bash
# Nightly scan + push (the deploy pipeline's data side).
#
# Why local: SEC EDGAR 403s datacenter IPs (verified: GitHub Actions runners
# get HTTP 403 on data.sec.gov even with a proper User-Agent), so the scan
# must run from a residential/office IP like this machine. The scan artifacts
# are committed to git; Streamlit Community Cloud redeploys on the push.
#
# Installed as: 15 0 * * 2-6   (00:15 Tue–Sat, covering Mon–Fri sessions —
# after the US close, FRED's daily DGS10 post, and the evening's SEC filings)
# Logs: data/logs/nightly.log
set -euo pipefail
cd "$(dirname "$0")/.."

exec 9>"/tmp/stocks_scanner_nightly.lock"
if ! flock -n 9; then
  echo "$(date -Is) another nightly run is active — skipping" >&2
  exit 0
fi

PY=".venv/bin/python"
[ -x "$PY" ] || { echo "$(date -Is) no venv — run ./control.sh start once first" >&2; exit 1; }

# load .env (SCANNER_UA) like control.sh does
if [ -f .env ]; then
  while IFS='=' read -r k v; do
    if [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then export "$k=$v"; fi
  done < .env
fi

echo "$(date -Is) scan starting"
"$PY" -m scanner.scan

# keep the last 5 scans so the repo stays ~25 MB
(cd data/scans && ls -1 | sort | head -n -5 | while read -r d; do
  git rm -r --quiet --ignore-unmatch "$d" && rm -rf "$d" && echo "pruned $d"
done)

git add data/scans
if git diff --cached --quiet; then
  echo "$(date -Is) no scan changes — nothing to push"
  exit 0
fi
git commit -m "scan artifacts: $(ls data/scans | sort | tail -1) [nightly]"
git push
echo "$(date -Is) pushed"
