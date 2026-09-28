#!/bin/bash
# Deploys the latest origin/main into the jack checkout and restarts the app when it changes.
# The body is wrapped in main() so bash parses the whole script before
# `git reset` can rewrite this file underneath it.
set -euo pipefail

main() {
  local repo_dir="${JACK_REPO_DIR:-/opt/jack}"
  local branch=main
  local service=jack.service

  cd "$repo_dir"
  git fetch --quiet origin "$branch"

  local current target
  current=$(git rev-parse HEAD)
  target=$(git rev-parse "origin/$branch")
  if [[ "$current" == "$target" ]]; then
    return 0
  fi

  echo "Deploying $target (was $current)"
  git reset --hard --quiet "origin/$branch"
  systemctl try-restart "$service"
  # Blink the onboard LEDs so someone at the bench can see a deploy landed.
  # The code is already deployed, so a flash failure must not fail the run.
  bash "$repo_dir/deploy/flash-leds.sh" || echo "LED flash failed; deploy of $target succeeded"
}

main "$@"
exit
