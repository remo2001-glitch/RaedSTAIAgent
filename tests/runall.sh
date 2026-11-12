#!/usr/bin/env bash
# تشغيل كل الاختبارات: bash tests/runall.sh  (بذور متعددة: for N in 0 7 21; do PYTHONHASHSEED=$N bash tests/runall.sh; done)
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
total=0; bad=0
for f in test_*.py; do
  out=$(BOT_TOKEN=x timeout 300 "$PY" "$f" 2>&1); rc=$?
  p=$(grep -c '^PASS' <<<"$out"); fl=$(grep -c '^FAIL' <<<"$out")
  total=$((total + p + fl))
  if [ "$rc" -ne 0 ] || [ "$fl" -ne 0 ]; then
    bad=$((bad + 1)); echo "[$f] rc=$rc PASS=$p FAIL=$fl"; grep -E '^FAIL|Error|Traceback' <<<"$out" | head -8
  fi
done
echo "TOTAL checks=$total  files_with_problems=$bad"
[ "$bad" -eq 0 ]
