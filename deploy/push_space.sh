#!/usr/bin/env bash
# Publish the last commit to a Hugging Face Space.
#
#     bash deploy/push_space.sh <hf-username>/<space-name>
#
# Run it from the project venv. A Space reads its settings from a block at the top
# of README.md, which the GitHub README should not carry, so the Space gets its own
# copy of the tree: the last commit, with deploy/space_header.md on the README.
# Only committed files go, so .env, tests/real and anything gitignored cannot.
set -e

as_space="$1"
if [ -z "$as_space" ]; then
  echo "usage: bash deploy/push_space.sh <hf-username>/<space-name>"
  exit 1
fi
a_remote="${SPACE_REMOTE:-https://huggingface.co/spaces/$as_space}"

python tools/prepush_check.py

if [ -n "$(git status --porcelain)" ]; then
  echo "uncommitted changes. Commit first: only the last commit is published."
  exit 1
fi

a_tmp=$(mktemp -d)
trap 'rm -rf "$a_tmp"' EXIT
git archive HEAD | tar -x -C "$a_tmp"

cat deploy/space_header.md "$a_tmp/README.md" > "$a_tmp/README.space"
mv "$a_tmp/README.space" "$a_tmp/README.md"

# Rebuilt from data/corpus on first start, and large enough to need Git LFS.
rm -f "$a_tmp/data/corpus_index.json"

# Pin the Space to the versions installed here, which are the ones just tested. A
# router pickled by one scikit-learn can fail to load under another.
python - "$a_tmp" <<'PY'
import re, sys
from importlib.metadata import version, PackageNotFoundError
as_out = []
for a_line in open("requirements.txt"):
    a_name = re.split(r"[<>=!~\[\s#]", a_line.strip(), maxsplit=1)[0]
    if not a_name:
        continue
    try:
        as_out.append(f"{a_name}=={version(a_name)}")
    except PackageNotFoundError:
        as_out.append(a_line.split("#")[0].strip())
open(sys.argv[1] + "/requirements-lock.txt", "w").write("\n".join(as_out) + "\n")
print(f"pinned {len(as_out)} packages to this machine's versions")
PY

# stop_words_ on a fitted TfidfVectorizer lists every n-gram the vectoriser dropped,
# which is most of the training text. scikit-learn documents it as introspection
# only and safe to remove before pickling.
python - "$a_tmp/data/router.joblib" <<'PY'
import os, sys
a_path = sys.argv[1]
if os.path.exists(a_path):
    import joblib
    a_blob = joblib.load(a_path)
    for _, a_step in getattr(a_blob.get("pipeline"), "steps", []):
        if hasattr(a_step, "stop_words_"):
            a_step.stop_words_ = None
    joblib.dump(a_blob, a_path)
    print(f"router: {os.path.getsize(a_path) // 1024} KB after dropping stop_words_")
PY

as_big=$(find "$a_tmp" -type f -size +10M)
if [ -n "$as_big" ]; then
  echo "Hugging Face refuses files over 10 MB without Git LFS:"
  echo "$as_big" | sed "s|$a_tmp/|  |"
  exit 1
fi

cd "$a_tmp"
git init -q
git checkout -q -b main
git add -A
git commit -qm "Clarity $(date +%Y-%m-%d)"
echo "pushing to $a_remote"
echo "if asked: username is your Hugging Face name, password is an access token with write access"
git push -f "$a_remote" main
