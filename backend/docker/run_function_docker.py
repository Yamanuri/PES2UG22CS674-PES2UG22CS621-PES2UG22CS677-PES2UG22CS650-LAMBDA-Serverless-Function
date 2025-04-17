import subprocess
import uuid
import os
import time
import sqlite3
from typing import Dict, Tuple

# SQLite connection for metrics (passed from main.py to ensure single connection)
def initialize_database(conn: sqlite3.Connection):
    """Initialize the metrics table and ensure all required columns exist."""
    required_columns = {
        "function_name": "TEXT",
        "runtime": "TEXT",
        "response_time": "REAL",
        "error": "INTEGER",
        "stdout": "TEXT",
        "stderr": "TEXT",
        "memory_usage": "REAL",  # Store in MB
        "cpu_usage": "REAL"      # Store in percentage
    }
    
    # Create table if it doesn't exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            function_name TEXT,
            runtime TEXT,
            response_time REAL,
            error INTEGER,
            stdout TEXT,
            stderr TEXT
        )
    """)
    conn.commit()
    
    # Check existing columns
    cursor = conn.execute("PRAGMA table_info(metrics)")
    existing_columns = {row[1]: row[2] for row in cursor.fetchall()}
    
    # Add missing columns
    for col_name, col_type in required_columns.items():
        if col_name not in existing_columns:
            print(f"Adding missing column {col_name} to metrics table")
            conn.execute(f"ALTER TABLE metrics ADD COLUMN {col_name} {col_type}")
    conn.commit()

# Global container pool for warm-up
warm_containers = {"python": {"runc": [], "runsc": []}, "node": {"runc": [], "runsc": []}}

def ensure_docker_images():
    """Build Docker images if they don’t exist."""
    for lang, dockerfile, tag in [
        ("python", "Dockerfile.python", "code-runner-python"),
        ("node", "Dockerfile.node", "code-runner-node")
    ]:
        if not os.path.exists(dockerfile):
            raise FileNotFoundError(f"Dockerfile not found: {dockerfile}")
        
        try:
            subprocess.run(["docker", "image", "inspect", tag], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(f"Image {tag} already exists.")
        except subprocess.CalledProcessError:
            print(f"Building {tag} image from {dockerfile}...")
            subprocess.run(
                ["docker", "build", "-f", dockerfile, "-t", tag, "."],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

def prewarm_containers(language: str, runtime: str = "runc", count: int = 2):
    """Pre-warm containers for faster execution."""
    try:
        ensure_docker_images()
        image_tag = "code-runner-python" if language == "python" else "code-runner-node"
        for _ in range(count):
            result = subprocess.run(
                ["docker", "run", f"--runtime={runtime}", "-d", image_tag, "sleep", "300"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode != 0:
                print(f"Failed to pre-warm {language}/{runtime}: {result.stderr}")
                continue
            container_id = result.stdout.strip()
            warm_containers[language][runtime].append(container_id)
            print(f"Pre-warmed {language}/{runtime} container: {container_id}")
    except Exception as e:
        print(f"Error in prewarm_containers: {str(e)}")

def parse_docker_stats(stats_output: str) -> Tuple[float, float]:
    """Parse memory usage (MB) and CPU usage (%) from docker stats output."""
    try:
        mem_str, cpu_str = stats_output.strip().split(",")
        # Memory: "1.2MiB / 256MiB" -> extract used memory in MB
        mem_used = float(mem_str.split("MiB")[0].strip())
        # CPU: "0.05%" -> extract percentage
        cpu_used = float(cpu_str.replace("%", "").strip())
        return mem_used, cpu_used
    except Exception as e:
        print(f"Error parsing docker stats: {str(e)}")
        return 0.0, 0.0

def run_function(code: str, language: str, timeout: int, runtime: str = "runc", function_name: str = "unknown", conn: sqlite3.Connection = None) -> Tuple[str, Dict]:
    """Run code in a Docker or gVisor container with timeout, cleanup, and metrics."""
    print(f"Running function: {function_name}, language={language}, runtime={runtime}")
    ensure_docker_images()
    
    temp_id = str(uuid.uuid4())
    if language == "python":
        filename = f"/tmp/{temp_id}.py"
        image_tag = "code-runner-python"
        command = f"python {os.path.basename(filename)}"
    elif language == "node":
        filename = f"/tmp/{temp_id}.js"
        image_tag = "code-runner-node"
        command = f"node {os.path.basename(filename)}"
    else:
        raise ValueError(f"Unsupported language: {language}")
    
    with open(filename, "w") as f:
        f.write(code)
        print(f"Wrote code to {filename}")
    
    start_time = time.time()
    output, error_flag, stdout, stderr = "", False, "", ""
    memory_usage, cpu_usage = 0.0, 0.0
    
    try:
        if warm_containers[language][runtime]:
            container_id = warm_containers[language][runtime].pop()
            print(f"Using pre-warmed container: {container_id}")
            try:
                subprocess.run(
                    ["docker", "cp", filename, f"{container_id}:/app/{os.path.basename(filename)}"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                print(f"Copied {filename} to container {container_id}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to copy file to container {container_id}: {e.stderr}")
                raise
            result = subprocess.run(
                ["docker", "exec", container_id, "sh", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                text=True
            )
            stats = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}},{{.CPUPerc}}", container_id],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if stats.returncode == 0:
                memory_usage, cpu_usage = parse_docker_stats(stats.stdout)
        else:
            print(f"No pre-warmed container available, starting new {runtime} container")
            result = subprocess.run(
                ["docker", "run", f"--runtime={runtime}", "--rm",
                 "--memory=256m", "--cpu-quota=100000",
                 "-v", f"{os.path.dirname(os.path.abspath(filename))}:/app",
                 image_tag, "sh", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                text=True
            )
            # For --rm containers, stats aren't available post-execution; use defaults
            memory_usage, cpu_usage = 0.0, 0.0
        
        stdout = result.stdout
        stderr = result.stderr
        output = stdout if stdout else stderr
        error_flag = bool(stderr and not stdout)
        print(f"Execution completed: stdout={stdout}, stderr={stderr}, memory={memory_usage}MB, cpu={cpu_usage}%")
    except subprocess.TimeoutExpired:
        output = f"Execution timed out after {timeout} seconds."
        error_flag = True
        stderr = output
        print(f"Timeout: {output}")
    except subprocess.CalledProcessError as e:
        output = f"Execution failed: {e.stderr}"
        error_flag = True
        stderr = e.stderr
        print(f"Execution error: {output}")
    except Exception as e:
        output = f"Unexpected error: {str(e)}"
        error_flag = True
        stderr = str(e)
        print(f"Unexpected error: {output}")
    finally:
        if os.path.exists(filename):
            try:
                os.remove(filename)
                print(f"Cleaned up {filename}")
            except Exception as e:
                print(f"Failed to remove {filename}: {str(e)}")
    
    response_time = time.time() - start_time
    metrics = {
        "response_time": response_time,
        "error": int(error_flag),
        "stdout": stdout,
        "stderr": stderr,
        "memory_usage": memory_usage,
        "cpu_usage": cpu_usage
    }
    if conn:
        try:
            conn.execute(
                "INSERT INTO metrics (function_name, runtime, response_time, error, stdout, stderr, memory_usage, cpu_usage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (function_name, runtime, response_time, int(error_flag), stdout, stderr, memory_usage, cpu_usage)
            )
            conn.commit()
            print(f"Stored metrics for {function_name}: {metrics}")
        except sqlite3.Error as e:
            print(f"Failed to store metrics: {str(e)}")
    
    return output, metrics
