# src/main.py - Refactored FastAPI application
# Updated: Added /execute/python/script endpoint

import os
import uuid
import tempfile
import logging
import shlex # Import for shell escaping
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse

# Import components from other modules
# Add PythonScript model
from .models.execution import PythonCode, ShellCommand, ShellResult, PythonScript
from .core.docker_runner import run_in_container, docker_client, WORKSPACE_DIR_INSIDE_CONTAINER
from .core.scripting import create_execution_script
from .utils.cleanup import cleanup_temp_dir

# --- Configuration ---
OUTPUT_FILENAME = "output.png" # Used by chart endpoint
# WORKSPACE_DIR_INSIDE_CONTAINER is imported from docker_runner

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Lifespan Context Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup...")
    if not docker_client:
        logger.critical("Docker client failed to initialize during startup. Service may not function correctly.")
    else:
        logger.info("Docker client check passed during startup.")
    yield
    logger.info("Application shutdown...")

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Code Execution Service",
    description="API to execute Python chart code, Shell commands, and Python scripts in a Docker sandbox.",
    version="0.5.0", # Incremented version
    lifespan=lifespan
)

# --- API Endpoints ---

@app.post(
    "/execute/python/chart",
    # ... (endpoint definition remains the same) ...
)
async def execute_python_chart(payload: PythonCode, background_tasks: BackgroundTasks):
    """
    Executes Python code designed to generate a Matplotlib chart in a Docker sandbox.
    This endpoint is STATELESS and uses a temporary volume.
    Returns the chart PNG or an error. Uses background tasks for cleanup.
    """
    temp_dir_host = tempfile.mkdtemp()
    temp_dir_path = Path(temp_dir_host)
    logger.info(f"Chart Execution: Created temporary directory: {temp_dir_host}")

    background_tasks.add_task(cleanup_temp_dir, temp_dir_path)

    try:
        script_filename = "script.py"
        script_path_host = temp_dir_path / script_filename
        output_path_host = temp_dir_path / OUTPUT_FILENAME

        # Prepare the script using helper from core.scripting
        full_script_code = create_execution_script(
            payload.code, OUTPUT_FILENAME
        )
        try:
            script_path_host.write_text(full_script_code)
            logger.info(f"Chart Execution: Script written to: {script_path_host}")
        except IOError as e:
             logger.error(f"Chart Execution: Failed to write script file '{script_path_host}': {e}", exc_info=True)
             raise HTTPException(status_code=500, detail="Server error: Failed to write script file.")

        TEMP_CHART_WORKDIR = "/chart_temp"
        temp_volumes = {
            str(temp_dir_path.resolve()): {
                'bind': TEMP_CHART_WORKDIR,
                'mode': 'rw'
            }
        }
        command = ["python", f"{TEMP_CHART_WORKDIR}/{script_filename}"]

        # Use the helper function, passing the temporary volumes via 'temp_volumes'
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=command,
            temp_volumes=temp_volumes, # Use temp_volumes for temp data
            working_dir=TEMP_CHART_WORKDIR,
            network_mode="none"
        )

        logger.info(f"Chart Execution: Container stdout:\n{stdout_str}")
        if stderr_str:
            logger.warning(f"Chart Execution: Container stderr:\n{stderr_str}")

        if exit_code != 0:
            logger.error(f"Chart Execution: Script failed with exit code {exit_code}.")
            error_detail = f"Python script execution failed (Exit Code: {exit_code})."
            log_preview = '\n'.join(stderr_str.splitlines()[-10:])
            error_detail += f"\nStderr (Last 10 lines):\n{log_preview}"
            raise HTTPException(status_code=400, detail=error_detail)

        logger.info(f"Chart Execution: Checking for output file at host path: {output_path_host}")
        if not output_path_host.is_file():
            logger.error(f"Chart Execution: Output file '{output_path_host}' not found despite exit code 0.")
            error_detail = f"Script executed successfully but failed to produce the expected output file ('{OUTPUT_FILENAME}')."
            log_preview_stdout = '\n'.join(stdout_str.splitlines()[-10:])
            log_preview_stderr = '\n'.join(stderr_str.splitlines()[-10:])
            error_detail += f"\nStdout (Last 10 lines):\n{log_preview_stdout}"
            error_detail += f"\nStderr (Last 10 lines):\n{log_preview_stderr}"
            raise HTTPException(status_code=500, detail=error_detail)

        logger.info(f"Chart Execution: Success. Returning output file: {output_path_host}")
        return FileResponse(
            path=output_path_host,
            media_type='image/png',
            filename=OUTPUT_FILENAME
        )

    except HTTPException:
         raise
    except Exception as e:
        logger.error(f"Chart Execution: Unexpected error in endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")


@app.post(
    "/execute/shell",
    response_model=ShellResult,
    # ... (endpoint definition remains the same) ...
)
async def execute_shell_command(payload: ShellCommand):
    """
    Executes a shell command string in a Docker sandbox, using a persistent
    session volume mounted at /workspace.
    Returns the command's stdout, stderr, and exit code.
    """
    if not payload.command:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Shell command cannot be empty.")
    if not payload.session_id:
         raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id cannot be empty.")

    shell_command_list = ["bash", "-c", f"set -e; set -o pipefail; {payload.command}"]

    try:
        # Pass the session_id to run_in_container
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=shell_command_list,
            session_id=payload.session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER, # Execute in the persistent workspace
            network_mode="bridge",
        )

        logger.info(f"Shell Execution (Session: {payload.session_id}): Command finished with exit code {exit_code}.")
        logger.info(f"Shell Execution (Session: {payload.session_id}): stdout:\n{stdout_str}")
        if stderr_str:
             logger.warning(f"Shell Execution (Session: {payload.session_id}): stderr:\n{stderr_str}")

        return ShellResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=exit_code
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Shell Execution (Session: {payload.session_id}): Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected server error occurred: {e}")


# NEW Python Script Execution Endpoint
@app.post(
    "/execute/python/script",
    response_model=ShellResult, # Reusing ShellResult for stdout/stderr/exit_code
    responses={
        200: {"description": "Script executed. Returns stdout, stderr, and exit code."},
        400: {"description": "Bad Request (e.g., Python script execution failed)."},
        408: {"description": "Request Timeout (Container execution took too long)."},
        422: {"description": "Validation Error."},
        500: {"description": "Internal Server Error (Docker issues, file write failed, etc.)."},
    }
)
async def execute_python_script(payload: PythonScript):
    """
    Executes a general Python script string in a Docker sandbox within a session workspace.
    Writes the script to 'script.py' in the session volume, then executes it.
    Returns the script's stdout, stderr, and exit code.
    """
    if not payload.code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Python code cannot be empty.")
    if not payload.session_id:
         raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id cannot be empty.")

    script_filename = "script.py" # Standard script name within the workspace
    script_path_in_container = f"{WORKSPACE_DIR_INSIDE_CONTAINER}/{script_filename}"

    # Step 1: Write the script content to the session volume using a shell command
    try:
        # Escape the Python code to be safely embedded in a shell command
        # shlex.quote is essential here to handle quotes, newlines, special chars
        escaped_code = shlex.quote(payload.code)
        write_command = f"printf '%s' {escaped_code} > {script_filename}" # Use printf for better portability than echo
        write_command_list = ["bash", "-c", f"set -e; {write_command}"]

        logger.info(f"Python Script (Session: {payload.session_id}): Attempting to write script file via shell...")
        write_exit_code, write_stdout, write_stderr = await run_in_container(
            command=write_command_list,
            session_id=payload.session_id, # Write to the session volume
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none" # Writing script doesn't need network
        )

        if write_exit_code != 0:
            logger.error(f"Python Script (Session: {payload.session_id}): Failed to write script file. Exit Code: {write_exit_code}")
            logger.error(f"Write Stderr:\n{write_stderr}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write script to workspace (Exit Code: {write_exit_code}). Stderr: {write_stderr}"
            )
        logger.info(f"Python Script (Session: {payload.session_id}): Successfully wrote script file.")

    except HTTPException:
        raise # Re-raise exceptions from run_in_container (e.g., timeout)
    except Exception as e:
        logger.error(f"Python Script (Session: {payload.session_id}): Unexpected error during script write: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected server error during script write phase: {e}")

    # Step 2: Execute the script file using the Python interpreter
    try:
        exec_command_list = ["python", script_filename] # Execute the script saved in the workspace

        logger.info(f"Python Script (Session: {payload.session_id}): Attempting to execute script '{script_filename}'...")
        exec_exit_code, exec_stdout, exec_stderr = await run_in_container(
            command=exec_command_list,
            session_id=payload.session_id, # Execute within the same session volume
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="bridge" # Allow network for the actual script execution if needed
        )

        logger.info(f"Python Script (Session: {payload.session_id}): Execution finished with exit code {exec_exit_code}.")
        logger.info(f"Python Script (Session: {payload.session_id}): stdout:\n{exec_stdout}")
        if exec_stderr:
             logger.warning(f"Python Script (Session: {payload.session_id}): stderr:\n{exec_stderr}")

        # Return the results of the script execution
        return ShellResult(
            stdout=exec_stdout,
            stderr=exec_stderr,
            exit_code=exec_exit_code
        )
    except HTTPException:
        # Re-raise exceptions from run_in_container (e.g., timeout)
        # Note: Cleanup of the script file isn't strictly necessary as it will be overwritten,
        # but could be added via another shell command if desired.
        raise
    except Exception as e:
        logger.error(f"Python Script (Session: {payload.session_id}): Unexpected error during script execution: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred during script execution: {e}")


@app.get("/health", status_code=status.HTTP_200_OK)
# ... (health check remains the same) ...
async def health_check():
    """Provides a basic health check for the service and Docker connectivity."""
    docker_status = "unavailable"
    if docker_client:
        try:
            if docker_client.ping():
                docker_status = "available"
        except Exception:
            docker_status = "error connecting"
    return {"status": "ok", "docker_status": docker_status}

# --- Main execution block ---
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server directly...")
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)

