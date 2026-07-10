#!/usr/bin/env python3
"""Launcher for local development: Phoenix, Backend API, and Frontend.

Starts:
1. Docker Phoenix (OTel tracing) - http://localhost:6006
2. FastAPI Backend API - http://127.0.0.1:5001
3. Vite Frontend - http://localhost:5173
"""

import signal
import subprocess
import sys
import time
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def free_port(port: int) -> None:
    """Kill any process listening on `port`."""
    result = subprocess.run(
        ["lsof", "-ti", f":{port}"],
        capture_output=True, text=True
    )
    pids = result.stdout.strip().split()
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
            print(f"  killed PID {pid} holding port {port}")
        except (ProcessLookupError, ValueError):
            pass


def run():
    processes = []
    
    # 1. Start Docker Phoenix
    print("🚀 Starting Phoenix (Docker)...")
    try:
        subprocess.run([
            "docker", "compose", 
            "-f", "docker/phoenix/docker-compose.yml", 
            "up", "-d"
        ], check=True)
    except subprocess.CalledProcessError:
        print("⚠️ Failed to start Docker Phoenix. Make sure Docker is running.")
    except FileNotFoundError:
        print("⚠️ 'docker' command not found. Skipping Phoenix.")

    # 2. Start FastAPI Backend
    free_port(5001)
    print("🚀 Starting Backend API (port 5001)...")
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "api.approval_api"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    processes.append(("API", api_proc))

    # 3. Start Frontend
    free_port(5173)
    print("🚀 Starting Frontend (port 5173)...")
    frontend_dir = PROJECT_ROOT / "frontend"
    if (frontend_dir / "node_modules").exists():
        frontend_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(frontend_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        processes.append(("Frontend", frontend_proc))
    else:
        print("⚠️ Frontend node_modules not found. Run 'npm install' in ./frontend first.")

    print("\n✅ All processes started. Press Ctrl+C to stop.\n")

    try:
        while True:
            for name, proc in processes:
                # Read output without blocking
                line = proc.stdout.readline()
                if line:
                    print(f"[{name}] {line.strip()}")

                # Check if process died — drain remaining output first
                if proc.poll() is not None:
                    for remaining in proc.stdout:
                        print(f"[{name}] {remaining.strip()}")
                    print(f"❌ {name} process died with exit code {proc.returncode}")
                    print("🛑 Terminating remaining processes...")
                    for other_name, other_proc in processes:
                        if other_proc is not proc:
                            other_proc.terminate()
                    sys.exit(1)
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n🛑 Stopping all processes...")
        for name, proc in processes:
            proc.terminate()
        
        # Stop Phoenix
        subprocess.run([
            "docker", "compose", 
            "-f", "docker/phoenix/docker-compose.yml", 
            "down"
        ], stderr=subprocess.DEVNULL)
        
        print("👋 Done.")

if __name__ == "__main__":
    run()
