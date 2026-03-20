#!/usr/bin/env bash

# Replace <repo> with your repository name before running.
# Layout:
# pane 0 = builder
# pane 1 = reviewer-1
# pane 2 = researcher
# pane 3 = reviewer-2

tmux new-session -d -s agentops-grid -n all-open-2 'cd "../<repo>-claude" && claude' \; \
split-window -h 'cd "../<repo>-codex" && codex' \; \
select-pane -t 0 \; \
split-window -v 'cd "../<repo>-codex-research" && codex' \; \
select-pane -t 1 \; \
split-window -v 'cd "../<repo>-gemini" && gemini' \; \
select-layout tiled \; \
attach -t agentops-grid
