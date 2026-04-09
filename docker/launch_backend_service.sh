#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Function to load environment variables from .env file
load_env_file() {
    # Get the directory of the current script
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local env_file="$script_dir/.env"

    # Check if .env file exists
    if [ -f "$env_file" ]; then
        echo "Loading environment variables from: $env_file"
        # Source the .env file
        set -a
        source "$env_file" 
        set +a
    else
        echo "Warning: .env file not found at: $env_file"
    fi
}

# Load environment variables
load_env_file

# Repo root (so this script works no matter current directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Unset HTTP proxies that might be set by Docker daemon
export http_proxy=""; export https_proxy=""; export no_proxy=""; export HTTP_PROXY=""; export HTTPS_PROXY=""; export NO_PROXY=""
export PYTHONPATH="$REPO_ROOT"

# Linux: optional jemalloc for task_executor (LD_PRELOAD). macOS usually has no pkg-config/jemalloc here — skip preload.
JEMALLOC_PATH=""
if [ "$(uname -s)" = "Linux" ]; then
  export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu/
  if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists jemalloc 2>/dev/null; then
    JEMALLOC_PATH="$(pkg-config --variable=libdir jemalloc)/libjemalloc.so"
  elif [ -f /usr/lib/x86_64-linux-gnu/libjemalloc.so.2 ]; then
    JEMALLOC_PATH=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2
  elif [ -f /usr/lib/x86_64-linux-gnu/libjemalloc.so ]; then
    JEMALLOC_PATH=/usr/lib/x86_64-linux-gnu/libjemalloc.so
  fi
fi
if [ -z "$JEMALLOC_PATH" ] || [ ! -f "$JEMALLOC_PATH" ]; then
  JEMALLOC_PATH=""
  echo "Note: jemalloc preload skipped (normal on macOS or if pkg-config/jemalloc is missing)."
fi

PY=python3

# Set default number of workers if WS is not set or less than 1
if [[ -z "$WS" || $WS -lt 1 ]]; then
  WS=1
fi

# Maximum number of retries for each task executor and server
MAX_RETRIES=5

# Flag to control termination
STOP=false

# Array to keep track of child PIDs
PIDS=()

# Set the path to the NLTK data directory
export NLTK_DATA="./nltk_data"

# Function to handle termination signals
cleanup() {
  echo "Termination signal received. Shutting down..."
  STOP=true
  # Terminate all child processes
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "Killing process $pid"
      kill "$pid"
    fi
  done
  exit 0
}

# Trap SIGINT and SIGTERM to invoke cleanup
trap cleanup SIGINT SIGTERM

# Function to execute task_executor with retry logic
task_exe(){
    local task_id=$1
    local retry_count=0
    while ! $STOP && [ $retry_count -lt $MAX_RETRIES ]; do
        echo "Starting task_executor.py for task $task_id (Attempt $((retry_count+1)))"
        if [ -n "$JEMALLOC_PATH" ]; then
          LD_PRELOAD="$JEMALLOC_PATH" $PY rag/svr/task_executor.py "$task_id"
        else
          $PY rag/svr/task_executor.py "$task_id"
        fi
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 0 ]; then
            echo "task_executor.py for task $task_id exited successfully."
            break
        else
            echo "task_executor.py for task $task_id failed with exit code $EXIT_CODE. Retrying..." >&2
            retry_count=$((retry_count + 1))
            sleep 2
        fi
    done

    if [ $retry_count -ge $MAX_RETRIES ]; then
        echo "task_executor.py for task $task_id failed after $MAX_RETRIES attempts. Exiting..." >&2
        cleanup
    fi
}

# Function to execute main API server with retry logic
run_server(){
    local retry_count=0
    while ! $STOP && [ $retry_count -lt $MAX_RETRIES ]; do
        echo "Starting api/data-knowledge-api_server.py (Attempt $((retry_count+1)))"
        $PY api/data-knowledge-api_server.py
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 0 ]; then
            echo "data-knowledge-api_server.py exited successfully."
            break
        else
            echo "data-knowledge-api_server.py failed with exit code $EXIT_CODE. Retrying..." >&2
            retry_count=$((retry_count + 1))
            sleep 2
        fi
    done

    if [ $retry_count -ge $MAX_RETRIES ]; then
        echo "data-knowledge-api_server.py failed after $MAX_RETRIES attempts. Exiting..." >&2
        cleanup
    fi
}

# Start task executors
for ((i=0;i<WS;i++))
do
  task_exe "$i" &
  PIDS+=($!)
done

# Start the main server
run_server &
PIDS+=($!)

# Wait for all background processes to finish
wait
