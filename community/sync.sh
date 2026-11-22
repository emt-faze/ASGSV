#!/usr/bin/env bash
set -euo pipefail
# Public forks do not process the project's intake. Only the trusted default branch can publish.
[[ "${CNB_REPO_SLUG_LOWERCASE:-}" == "asgsv-studio/asgsv" && "${CNB_BRANCH:-}" == main ]] || exit 0
export GIT_LFS_SKIP_SMUDGE=1 GIT_TERMINAL_PROMPT=0
if python - <<'PY'
import json, os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
url = 'https://api.cnb.cool/asgsv-studio/ASGSV/-/issues?' + urlencode(dict(state='open', keyword='ASGSV 配音投稿', page_size=1))
req = Request(url, headers={'Authorization':'Bearer ' + os.environ['CNB_TOKEN'], 'Accept':'application/json'})
with urlopen(req, timeout=20) as response:
    issues = json.load(response)
if not isinstance(issues, list):
    raise ValueError('CNB 返回了无效的待处理投稿列表')
raise SystemExit(0 if issues else 3)
PY
then
    if ! git lfs version >/dev/null 2>&1; then
        apt-get -qq update
        apt-get -qq install -y git-lfs
    fi
    python -m pip --disable-pip-version-check --quiet install 'cryptography==46.0.5'
    python community/publish.py sync
else
    result=$?
    if [[ "$result" == 3 ]]; then
        echo '没有待处理的投稿'
    else
        exit "$result"
    fi
fi

