#!/bin/bash
# Deploys the latest origin/main into the jack checkout and restarts the app when it changes.
# The body is wrapped in main() so bash parses the whole script before
# `git reset` can rewrite this file underneath it.
set -euo pipefail

main() {
  local repo_dir="${JACK_REPO_DIR:-/opt/jack}"
  local branch=main
  local service=jack.service
  local venv_dir="${JACK_VENV_DIR:-/opt/jack-venv}"

  cd "$repo_dir"
  git fetch --quiet origin "$branch"

  local current target
  current=$(git rev-parse HEAD)
  target=$(git rev-parse "origin/$branch")
  if [[ "$current" == "$target" ]]; then
    return 0
  fi

  echo "Deploying $target (was $current)"
  # Install changed Python pins (pymumble, python-osc) before switching code, so code never runs without them.
  # A failed install stops here (set -e): the old commit keeps running and the next poll retries.
  if ! git diff --quiet "$current" "$target" -- requirements-pi.txt; then
    local requirements="$repo_dir/.git/jack-requirements-pi.txt"
    git show "$target:requirements-pi.txt" > "$requirements"
    "$venv_dir/bin/pip" install --quiet --no-deps -r "$requirements"
  fi
  git reset --hard --quiet "origin/$branch"
  systemctl try-restart "$service"
  # Blink the onboard LEDs so someone at the bench can see a deploy landed.
  # The code is already deployed, so a flash failure must not fail the run.
  bash "$repo_dir/deploy/flash-leds.sh" || echo "LED flash failed; deploy of $target succeeded"
}

main "$@"
exit
