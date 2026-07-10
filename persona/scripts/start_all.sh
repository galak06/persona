#!/bin/bash

# Start Phoenix (Tracing)
docker compose -f docker/phoenix/docker-compose.yml up -d

# Start API in background
python -m api.approval_api &

# Start Frontend
cd frontend && npm run dev
