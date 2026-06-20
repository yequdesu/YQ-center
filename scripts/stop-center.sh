#!/usr/bin/env bash
PORT="${1:-9800}"
if pid=$(fuser "${PORT}/tcp" 2>/dev/null); then
    kill -9 $pid
    echo "Center on port ${PORT} stopped (pid $pid)"
else
    echo "No process on port ${PORT}"
fi
