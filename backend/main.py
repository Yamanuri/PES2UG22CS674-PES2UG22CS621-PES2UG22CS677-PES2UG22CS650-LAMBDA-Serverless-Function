from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import subprocess
import sqlite3
from run_function_docker import run_function, ensure_docker_images, prewarm_containers, initialize_database

app = FastAPI()
functions = []

# SQLite connection
conn = sqlite3.connect("metrics.db", check_same_thread=False)

class Function(BaseModel):
    name: str
    route: str
    language: str  # "python" or "node"
    timeout: int
    runtime: str = "runc"  # Default to Docker, can be "runsc" for gVisor
    settings: Optional[Dict[str, str]] = {}  # Code stored in settings

@app.on_event("startup")
async def startup_event():
    """Validate environment, build Docker images, pre-warm containers, and initialize database."""
    print("Starting up FastAPI server...")
    try:
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("Docker is running and accessible.")
    except subprocess.CalledProcessError:
        raise RuntimeError("Docker is not running or accessible.")
    
    try:
        initialize_database(conn)
        print("Database schema verified and initialized.")
        ensure_docker_images()
        print("Docker images ensured.")
        prewarm_containers("python", "runc")
        prewarm_containers("python", "runsc")
        prewarm_containers("node", "runc")
        prewarm_containers("node", "runsc")
        print("Pre-warming containers completed.")
    except FileNotFoundError as e:
        raise RuntimeError(str(e))
    except RuntimeError as e:
        raise RuntimeError(f"Docker image build failed: {str(e)}")
    except Exception as e:
        print(f"Startup error: {str(e)}")
        raise RuntimeError(f"Startup failed: {str(e)}")

@app.post("/functions/", status_code=201)
async def create_function(function: Function):
    """Create a new function with metadata and code in settings."""
    func_id = len(functions) + 1
    function_data = function.dict()
    function_data["id"] = func_id
    functions.append(function_data)
    print(f"Created function: {function_data}")
    return {"message": "Function created", "id": func_id}

@app.get("/functions/", response_model=List[Function])
async def get_all_functions():
    """Retrieve all stored functions."""
    print(f"Returning all functions: {functions}")
    return functions

@app.get("/functions/{func_id}", response_model=Function)
async def get_function(func_id: int):
    """Retrieve a specific function by ID."""
    if func_id > len(functions) or func_id <= 0:
        raise HTTPException(status_code=404, detail="Function not found")
    print(f"Returning function ID {func_id}: {functions[func_id - 1]}")
    return functions[func_id - 1]

@app.put("/functions/{func_id}")
async def update_function(func_id: int, function: Function):
    """Update an existing function."""
    if func_id > len(functions) or func_id <= 0:
        raise HTTPException(status_code=404, detail="Function not found")
    updated_function = function.dict()
    updated_function["id"] = func_id
    functions[func_id - 1] = updated_function
    print(f"Updated function ID {func_id}: {updated_function}")
    return {"message": "Function updated", "function": updated_function}

@app.delete("/functions/{func_id}")
async def delete_function(func_id: int):
    """Delete a function by ID."""
    if func_id > len(functions) or func_id <= 0:
        raise HTTPException(status_code=404, detail="Function not found")
    deleted_function = functions.pop(func_id - 1)
    print(f"Deleted function ID {func_id}: {deleted_function}")
    return {"message": "Function deleted"}

@app.post("/functions/{func_id}/run")
async def run_function_endpoint(func_id: int):
    """Execute a function and return only the output."""
    if func_id > len(functions) or func_id <= 0:
        raise HTTPException(status_code=404, detail="Function not found")
    
    function = functions[func_id - 1]
    code = function.get("settings", {}).get("code")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided in function settings")
    
    print(f"Executing function ID {func_id}: {function['name']} with runtime {function['runtime']}")
    try:
        output, _ = run_function(
            code,
            function["language"],
            function["timeout"],
            function["runtime"],
            function["name"],
            conn
        )
        print(f"Execution result for {function['name']}: output={output}")
        return {"output": output}
    except ValueError as e:
        print(f"ValueError during execution: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Execution error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")

@app.get("/functions/{func_id}/metrics")
async def get_function_metrics(func_id: int):
    """Retrieve the most recent metrics for a specific function."""
    if func_id > len(functions) or func_id <= 0:
        raise HTTPException(status_code=404, detail="Function not found")
    
    function = functions[func_id - 1]
    function_name = function["name"]
    try:
        cursor = conn.execute(
            "SELECT response_time, error, stdout, stderr, memory_usage, cpu_usage FROM metrics WHERE function_name = ? ORDER BY rowid DESC LIMIT 1",
            (function_name,)
        )
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail=f"No metrics found for function {function_name}")
        
        metrics = {
            "response_time": result[0],
            "error": result[1],
            "stdout": result[2],
            "stderr": result[3],
            "memory_usage": result[4],
            "cpu_usage": result[5]
        }
        print(f"Returning metrics for {function_name}: {metrics}")
        return {"metrics": metrics}
    except sqlite3.Error as e:
        print(f"Database error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")

@app.get("/functions/{func_id}/compare")
async def compare_performance(func_id: int):
    """Compare performance of a function between runc and runsc runtimes."""
    if func_id > len(functions) or func_id <= 0:
        raise HTTPException(status_code=404, detail="Function not found")
    
    function = functions[func_id - 1]
    code = function.get("settings", {}).get("code")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided in function settings")
    
    print(f"Comparing performance for function ID {func_id}: {function['name']}")
    
    # Execute with runc
    runc_output, runc_metrics = run_function(code, function["language"], function["timeout"], "runc", function["name"], conn)
    
    # Execute with runsc
    runsc_output, runsc_metrics = run_function(code, function["language"], function["timeout"], "runsc", function["name"], conn)
    
    comparison = {
        "runc": {
            "response_time": runc_metrics["response_time"],
            "memory_usage": runc_metrics["memory_usage"],
            "cpu_usage": runc_metrics["cpu_usage"],
            "output": runc_output
        },
        "runsc": {
            "response_time": runsc_metrics["response_time"],
            "memory_usage": runsc_metrics["memory_usage"],
            "cpu_usage": runsc_metrics["cpu_usage"],
            "output": runsc_output
        }
    }
    print(f"Performance comparison for {function['name']}: {comparison}")
    return {"comparison": comparison}

@app.get("/metrics/")
async def get_all_metrics():
    """Retrieve aggregated metrics for all functions."""
    try:
        cursor = conn.execute(
            "SELECT function_name, runtime, AVG(response_time), SUM(error), AVG(memory_usage), AVG(cpu_usage) "
            "FROM metrics GROUP BY function_name, runtime"
        )
        results = [
            {
                "function_name": row[0],
                "runtime": row[1],
                "avg_response_time": row[2],
                "error_count": row[3],
                "avg_memory_usage_mb": row[4],
                "avg_cpu_usage_percent": row[5]
            }
            for row in cursor.fetchall()
        ]
        print(f"Returning all metrics: {results}")
        return {"metrics": results}
    except sqlite3.Error as e:
        print(f"Database error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve metrics: {str(e)}")
