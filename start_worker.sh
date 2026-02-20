#!/bin/bash
# Start RQ worker with macOS fork() safety environment variable set
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
uv run worker.py
