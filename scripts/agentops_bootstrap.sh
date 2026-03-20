#!/usr/bin/env bash
set -euo pipefail

# Five-role bootstrap:
# - leader stays in the main repo on main
# - builder/reviewer1/reviewer2/researcher each get a dedicated worktree + branch
# - tmux gets one 4-pane working window and optional single-role windows
#
# Usage:
#   ./agentops_bootstrap.sh --round r01
#
# Optional:
#   --session agentops
#   --main main
#   --no-launch
#   --builder-cmd claude
#   --reviewer1-cmd codex
#   --reviewer2-cmd gemini
#   --researcher-cmd codex

SESSION_NAME="agentops"
MAIN_BRANCH="main"
ROUND="r01"
LAUNCH_AGENTS=1
BUILDER_CMD="${BUILDER_CMD:-claude}"
REVIEWER1_CMD="${REVIEWER1_CMD:-codex}"
REVIEWER2_CMD="${REVIEWER2_CMD:-gemini}"
RESEARCHER_CMD="${RESEARCHER_CMD:-codex}"

usage() {
  cat <<'EOF'
Usage: agentops_bootstrap.sh [options]

Options:
  --round <rXX>              Round name, e.g. r01, r02
  --session <name>           tmux session name (default: agentops)
  --main <branch>            Main integration branch (default: main)
  --no-launch                Do not auto-start agents in panes
  --builder-cmd <cmd>        Builder command (default: claude)
  --reviewer1-cmd <cmd>      Reviewer-1 command (default: codex)
  --reviewer2-cmd <cmd>      Reviewer-2 command (default: gemini)
  --researcher-cmd <cmd>     Researcher command (default: codex)
  -h, --help                 Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --round) ROUND="${2:-}"; shift 2 ;;
    --session) SESSION_NAME="${2:-}"; shift 2 ;;
    --main) MAIN_BRANCH="${2:-}"; shift 2 ;;
    --no-launch) LAUNCH_AGENTS=0; shift 1 ;;
    --builder-cmd) BUILDER_CMD="${2:-}"; shift 2 ;;
    --reviewer1-cmd) REVIEWER1_CMD="${2:-}"; shift 2 ;;
    --reviewer2-cmd) REVIEWER2_CMD="${2:-}"; shift 2 ;;
    --researcher-cmd) RESEARCHER_CMD="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

ROUND="$(echo "$ROUND" | tr '[:upper:]' '[:lower:]')"
if [[ ! "$ROUND" =~ ^r[0-9]{2}$ ]]; then
  echo "Invalid --round '$ROUND' (expected rXX, e.g. r02)."
  exit 1
fi

command -v git >/dev/null 2>&1 || { echo "git not found"; exit 1; }
command -v tmux >/dev/null 2>&1 || { echo "tmux not found"; exit 1; }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$REPO_ROOT" ]] || { echo "Not inside a git repository."; exit 1; }
cd "$REPO_ROOT"

git show-ref --verify --quiet "refs/heads/${MAIN_BRANCH}" || {
  echo "Main branch '${MAIN_BRANCH}' not found locally."
  exit 1
}

REPO_NAME="$(basename "$REPO_ROOT")"
PARENT_DIR="$(dirname "$REPO_ROOT")"

BUILDER_BRANCH="feat/${ROUND}-builder"
REVIEWER1_BRANCH="review/${ROUND}-reviewer-1"
REVIEWER2_BRANCH="review/${ROUND}-reviewer-2"
RESEARCHER_BRANCH="research/${ROUND}-researcher"

PROJECT="${REPO_NAME%%-main}"
BUILDER_DIR="${PARENT_DIR}/${PROJECT}-builder"
REVIEWER1_DIR="${PARENT_DIR}/${PROJECT}-reviewer1"
REVIEWER2_DIR="${PARENT_DIR}/${PROJECT}-reviewer2"
RESEARCHER_DIR="${PARENT_DIR}/${PROJECT}-researcher"

ensure_branch() {
  local branch="$1"
  if ! git show-ref --verify --quiet "refs/heads/${branch}"; then
    git branch "${branch}" "${MAIN_BRANCH}"
  fi
}

ensure_worktree() {
  local path="$1"
  local branch="$2"
  if [[ -d "$path" ]]; then
    git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
      echo "Path exists but is not a git worktree: $path"
      exit 1
    }
    local current_branch
    current_branch="$(git -C "$path" rev-parse --abbrev-ref HEAD)"
    if [[ "$current_branch" != "$branch" ]]; then
      git -C "$path" checkout "$branch"
    fi
  else
    git worktree add "$path" "$branch"
  fi
}

ensure_window() {
  local session="$1"
  local name="$2"
  local dir="$3"
  if tmux list-windows -t "$session" -F '#W' | grep -qx "$name"; then
    tmux kill-window -t "${session}:${name}"
  fi
  tmux new-window -t "${session}:" -n "$name" -c "$dir"
}

start_in_target() {
  local target="$1"
  local dir="$2"
  local cmd="$3"
  tmux send-keys -t "$target" C-c
  tmux send-keys -t "$target" "cd '$dir'" C-m
  if [[ $LAUNCH_AGENTS -eq 1 ]]; then
    if command -v "$cmd" >/dev/null 2>&1; then
      tmux send-keys -t "$target" "$cmd" C-m
    else
      tmux send-keys -t "$target" "echo '$cmd not found; staying in shell'" C-m
    fi
  fi
}

echo "[1/4] Preparing branches from ${MAIN_BRANCH}..."
ensure_branch "$BUILDER_BRANCH"
ensure_branch "$REVIEWER1_BRANCH"
ensure_branch "$REVIEWER2_BRANCH"
ensure_branch "$RESEARCHER_BRANCH"

echo "[2/4] Preparing worktrees..."
ensure_worktree "$BUILDER_DIR" "$BUILDER_BRANCH"
ensure_worktree "$REVIEWER1_DIR" "$REVIEWER1_BRANCH"
ensure_worktree "$REVIEWER2_DIR" "$REVIEWER2_BRANCH"
ensure_worktree "$RESEARCHER_DIR" "$RESEARCHER_BRANCH"

echo "[3/4] Preparing tmux session '${SESSION_NAME}'..."
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  tmux new-session -d -s "$SESSION_NAME" -n "leader" -c "$REPO_ROOT"
fi

ensure_window "$SESSION_NAME" "builder" "$BUILDER_DIR"
ensure_window "$SESSION_NAME" "reviewer1" "$REVIEWER1_DIR"
ensure_window "$SESSION_NAME" "reviewer2" "$REVIEWER2_DIR"
ensure_window "$SESSION_NAME" "researcher" "$RESEARCHER_DIR"
ensure_window "$SESSION_NAME" "all-open-2" "$BUILDER_DIR"

tmux split-window -h -t "${SESSION_NAME}:all-open-2" -c "$REVIEWER1_DIR"
tmux select-pane -t "${SESSION_NAME}:all-open-2.0"
tmux split-window -v -t "${SESSION_NAME}:all-open-2.0" -c "$RESEARCHER_DIR"
tmux split-window -v -t "${SESSION_NAME}:all-open-2.1" -c "$REVIEWER2_DIR"
tmux select-layout -t "${SESSION_NAME}:all-open-2" tiled

tmux set-option -g mouse on
tmux set-option -g prefix2 C-a

echo "[4/4] Launching agent commands in panes..."
start_in_target "${SESSION_NAME}:builder" "$BUILDER_DIR" "$BUILDER_CMD"
start_in_target "${SESSION_NAME}:reviewer1" "$REVIEWER1_DIR" "$REVIEWER1_CMD"
start_in_target "${SESSION_NAME}:reviewer2" "$REVIEWER2_DIR" "$REVIEWER2_CMD"
start_in_target "${SESSION_NAME}:researcher" "$RESEARCHER_DIR" "$RESEARCHER_CMD"
start_in_target "${SESSION_NAME}:all-open-2.0" "$BUILDER_DIR" "$BUILDER_CMD"
start_in_target "${SESSION_NAME}:all-open-2.1" "$REVIEWER1_DIR" "$REVIEWER1_CMD"
start_in_target "${SESSION_NAME}:all-open-2.2" "$RESEARCHER_DIR" "$RESEARCHER_CMD"
start_in_target "${SESSION_NAME}:all-open-2.3" "$REVIEWER2_DIR" "$REVIEWER2_CMD"

tmux select-window -t "${SESSION_NAME}:all-open-2"

cat <<EOF

Agent team bootstrap ready

Repo root:         ${REPO_ROOT}
Main branch:       ${MAIN_BRANCH}
Round:             ${ROUND}
Leader branch:     ${MAIN_BRANCH}
Builder branch:    ${BUILDER_BRANCH}
Reviewer-1 branch: ${REVIEWER1_BRANCH}
Reviewer-2 branch: ${REVIEWER2_BRANCH}
Researcher branch: ${RESEARCHER_BRANCH}

Worktrees:
- ${BUILDER_DIR}
- ${REVIEWER1_DIR}
- ${REVIEWER2_DIR}
- ${RESEARCHER_DIR}

Tmux session:
- ${SESSION_NAME}
- windows: leader / builder / reviewer1 / reviewer2 / researcher / all-open-2

Attach:
  tmux attach -t ${SESSION_NAME}

EOF
