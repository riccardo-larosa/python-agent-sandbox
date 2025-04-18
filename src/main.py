# src/main.py - Refactored FastAPI application
# Updated: Removed stray comments from endpoint decorators

import os
import uuid
import tempfile
import logging
import shlex
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional # Import Optional

from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse

# Import components from other modules
# Import updated models
from .models.execution import PythonCode, ShellCommand, ShellResult, PythonScript
from .core.docker_runner import run_in_container, docker_client, WORKSPACE_DIR_INSIDE_CONTAINER
from .core.scripting import create_execution_script
from .utils.cleanup import cleanup_temp_dir
from .api import files as files_api

# --- Configuration ---
OUTPUT_FILENAME = "output.png"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Lifespan Context Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup...")
    if not docker_client: logger.critical("Docker client failed to initialize during startup.")
    else: logger.info("Docker client check passed during startup.")
    yield
    logger.info("Application shutdown...")

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Code Execution Service",
    description="API to execute code and manage files in a Docker sandbox.",
    version="0.6.1", # Incremented version
    lifespan=lifespan
)

# --- Include API Routers ---
app.include_router(files_api.router)

# --- Core Execution Endpoints ---

@app.post("/execute/python/chart", tags=["Execution"]) # Removed trailing comment
async def execute_python_chart(payload: PythonCode, background_tasks: BackgroundTasks):
    # (This endpoint remains stateless and doesn't use session environment vars)
    temp_dir_host = tempfile.mkdtemp()
    temp_dir_path = Path(temp_dir_host)
    logger.info(f"Chart Execution: Created temporary directory: {temp_dir_host}")
    background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
    try:
        script_filename = "script.py"
        script_path_host = temp_dir_path / script_filename
        output_path_host = temp_dir_path / OUTPUT_FILENAME
        full_script_code = create_execution_script(payload.code, OUTPUT_FILENAME)
        try: script_path_host.write_text(full_script_code); logger.info(f"Chart Execution: Script written to: {script_path_host}")
        except IOError as e: logger.error(f"Chart Execution: Failed to write script file '{script_path_host}': {e}", exc_info=True); raise HTTPException(status_code=500, detail="Server error: Failed to write script file.")
        TEMP_CHART_WORKDIR = "/chart_temp"
        temp_volumes = {str(temp_dir_path.resolve()): {'bind': TEMP_CHART_WORKDIR, 'mode': 'rw'}}
        command = ["python", f"{TEMP_CHART_WORKDIR}/{script_filename}"]
        # Call run_in_container without session_id or environment
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=command, temp_volumes=temp_volumes, working_dir=TEMP_CHART_WORKDIR, network_mode="none"
        )
        logger.info(f"Chart Execution: Container stdout:\n{stdout_str}")
        if stderr_str: logger.warning(f"Chart Execution: Container stderr:\n{stderr_str}")
        if exit_code != 0:
            logger.error(f"Chart Execution: Script failed with exit code {exit_code}.")
            error_detail = f"Python script execution failed (Exit Code: {exit_code})."; log_preview = '\n'.join(stderr_str.splitlines()[-10:]); error_detail += f"\nStderr (Last 10 lines):\n{log_preview}"; raise HTTPException(status_code=400, detail=error_detail)
        logger.info(f"Chart Execution: Checking for output file at host path: {output_path_host}")
        if not output_path_host.is_file():
            logger.error(f"Chart Execution: Output file '{output_path_host}' not found despite exit code 0.")
            error_detail = f"Script executed successfully but failed to produce the expected output file ('{OUTPUT_FILENAME}')."; log_preview_stdout = '\n'.join(stdout_str.splitlines()[-10:]); log_preview_stderr = '\n'.join(stderr_str.splitlines()[-10:]); error_detail += f"\nStdout (Last 10 lines):\n{log_preview_stdout}"; error_detail += f"\nStderr (Last 10 lines):\n{log_preview_stderr}"; raise HTTPException(status_code=500, detail=error_detail)
        logger.info(f"Chart Execution: Success. Returning output file: {output_path_host}")
        return FileResponse(path=output_path_host, media_type='image/png', filename=OUTPUT_FILENAME)
    except HTTPException: raise
    except Exception as e: logger.error(f"Chart Execution: Unexpected error in endpoint: {e}", exc_info=True); raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")

@app.post("/execute/shell", response_model=ShellResult, tags=["Execution"]) # Removed trailing comment
async def execute_shell_command(payload: ShellCommand):
    """Executes shell command in session workspace, allows optional environment vars."""
    if not payload.command: raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Shell command cannot be empty.")
    if not payload.session_id: raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id cannot be empty.")
    shell_command_list = ["bash", "-c", f"set -e; set -o pipefail; {payload.command}"]
    try:
        # Pass session_id AND environment from payload to run_in_container
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=shell_command_list,
            session_id=payload.session_id,
            environment=payload.environment, # Pass optional user environment
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="bridge",
        )
        logger.info(f"Shell Execution (Session: {payload.session_id}): Command finished with exit code {exit_code}.")
        logger.info(f"Shell Execution (Session: {payload.session_id}): stdout:\n{stdout_str}")
        if stderr_str: logger.warning(f"Shell Execution (Session: {payload.session_id}): stderr:\n{stderr_str}")
        return ShellResult(stdout=stdout_str, stderr=stderr_str, exit_code=exit_code)
    except HTTPException: raise
    except Exception as e: logger.error(f"Shell Execution (Session: {payload.session_id}): Unexpected error: {e}", exc_info=True); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected server error occurred: {e}")

@app.post("/execute/python/script", response_model=ShellResult, tags=["Execution"]) # Removed trailing comment
async def execute_python_script(payload: PythonScript):
    """Executes Python script in session workspace, allows optional environment vars."""
    if not payload.code: raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Python code cannot be empty.")
    if not payload.session_id: raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id cannot be empty.")

    script_filename = "script.py"
    script_path_in_container = f"{WORKSPACE_DIR_INSIDE_CONTAINER}/{script_filename}"
    # Step 1: Write script using shell echo (needs session and environment handling)
    try:
        escaped_code = shlex.quote(payload.code)
        write_command = f"printf '%s' {escaped_code} > {script_filename}"
        write_command_list = ["bash", "-c", f"set -e; {write_command}"]
        logger.info(f"Python Script (Session: {payload.session_id}): Attempting to write script file via shell...")
        # Use run_in_container for writing, passing session_id but minimal env/network
        write_exit_code, write_stdout, write_stderr = await run_in_container(
            command=write_command_list,
            session_id=payload.session_id, # Write to the session volume
            environment=None, # Don't need user env for writing
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none"
        )
        if write_exit_code != 0:
            logger.error(f"Python Script (Session: {payload.session_id}): Failed to write script file. Exit Code: {write_exit_code}"); logger.error(f"Write Stderr:\n{write_stderr}"); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to write script to workspace (Exit Code: {write_exit_code}). Stderr: {write_stderr}")
        logger.info(f"Python Script (Session: {payload.session_id}): Successfully wrote script file.")
    except HTTPException: raise
    except Exception as e: logger.error(f"Python Script (Session: {payload.session_id}): Unexpected error during script write: {e}", exc_info=True); raise HTTPException(status_code=500, detail=f"Unexpected server error during script write phase: {e}")

    # Step 2: Execute the script (passing session_id and user environment)
    try:
        exec_command_list = ["python", script_filename]
        logger.info(f"Python Script (Session: {payload.session_id}): Attempting to execute script '{script_filename}'...")
        # Pass session_id AND environment from payload to run_in_container
        exec_exit_code, exec_stdout, exec_stderr = await run_in_container(
            command=exec_command_list,
            session_id=payload.session_id,
            environment=payload.environment, # Pass optional user environment
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="bridge"
        )
        logger.info(f"Python Script (Session: {payload.session_id}): Execution finished with exit code {exec_exit_code}.")
        logger.info(f"Python Script (Session: {payload.session_id}): stdout:\n{exec_stdout}")
        if exec_stderr: logger.warning(f"Python Script (Session: {payload.session_id}): stderr:\n{exec_stderr}")
        return ShellResult(stdout=exec_stdout, stderr=exec_stderr, exit_code=exec_exit_code)
    except HTTPException: raise
    except Exception as e: logger.error(f"Python Script (Session: {payload.session_id}): Unexpected error during script execution: {e}", exc_info=True); raise HTTPException(status_code=500, detail=f"An unexpected server error occurred during script execution: {e}")

@app.get("/health", status_code=status.HTTP_200_OK, tags=["Health"])
async def health_check():
    # ... (implementation remains the same) ...
    docker_status = "unavailable"
    if docker_client:
        try:
            if docker_client.ping(): docker_status = "available"
        except Exception: docker_status = "error connecting"
    return {"status": "ok", "docker_status": docker_status}

# --- Main execution block ---
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server directly...")
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)

