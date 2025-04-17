# main.py - FastAPI application for executing Python/Shell code in Docker
# Updated: Removed unsupported 'cpus' argument from docker run

import os
import uuid
import shutil
import tempfile
import logging
from pathlib import Path
import time # For potential timeouts if needed directly

import docker
from docker.errors import ContainerError, ImageNotFound, APIError
from requests.exceptions import ReadTimeout # Specific timeout exception from docker-py's wait()
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# --- Configuration ---
SANDBOX_IMAGE_NAME = os.getenv("SANDBOX_IMAGE_NAME", "python-chart-sandbox:latest")
# Timeout for waiting for container to finish execution
CONTAINER_RUN_TIMEOUT = int(os.getenv("CONTAINER_RUN_TIMEOUT", 60))
OUTPUT_FILENAME = "output.png" # Expected output chart filename for chart endpoint
WORKSPACE_DIR_INSIDE_CONTAINER = "/workspace"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Pydantic Models ---
class PythonCode(BaseModel):
    code: str = Field(..., description="Python code string to execute for generating a chart.")

# New models for Shell execution
class ShellCommand(BaseModel):
    command: str = Field(..., description="Shell command string to execute.")
    # Future: Add workdir, env_vars etc.

class ShellResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Code Execution Service",
    description="API to execute Python chart code and Shell commands in a Docker sandbox.",
    version="0.2.1", # Incremented version
)

# --- Docker Client Initialization ---
try:
    docker_client = docker.from_env()
    docker_client.ping()
    logger.info("Docker client initialized and connected successfully.")
except Exception as e:
    logger.error(f"Fatal: Failed to initialize Docker client: {e}", exc_info=True)
    docker_client = None

# --- Helper Functions ---

# Refactored Docker Execution Logic
async def run_in_container(
    command: list[str],
    image: str = SANDBOX_IMAGE_NAME,
    working_dir: str = WORKSPACE_DIR_INSIDE_CONTAINER,
    volumes: dict = None,
    timeout: int = CONTAINER_RUN_TIMEOUT,
    network_mode: str = "none", # Default to no network for security
    mem_limit: str = "256m" # Keep mem_limit
    # cpus: float = 1.0 # Removed unsupported cpus parameter
) -> tuple[int, str, str]:
    """
    Runs a command in a temporary Docker container and returns exit code, stdout, stderr.

    Args:
        command: The command and arguments to run as a list of strings.
        image: The Docker image to use.
        working_dir: The working directory inside the container.
        volumes: A dictionary defining volume mounts (host_path: {bind: container_path, mode: 'rw'/'ro'}).
        timeout: Maximum time in seconds to wait for the container to finish.
        network_mode: Docker network mode (e.g., 'none', 'bridge').
        mem_limit: Memory limit (e.g., "256m").
        # cpus: CPU limit (e.g., 1.0) - This parameter is NOT directly supported by docker-py run.
               Use cpu_quota/cpu_period if needed.

    Returns:
        A tuple containing (exit_code, stdout_string, stderr_string).
        exit_code is -1 if status couldn't be retrieved (e.g., timeout before wait).
    """
    if not docker_client:
        raise HTTPException(status_code=500, detail="Docker client not available")

    container_name = f"sandbox-helper-{uuid.uuid4()}"
    container = None
    exit_code = -1
    stdout_str = ""
    stderr_str = ""

    try:
        logger.info(f"Running command in container '{container_name}': {command}")
        container = docker_client.containers.run(
            image=image,
            command=command,
            volumes=volumes,
            name=container_name,
            working_dir=working_dir,
            remove=False,       # Set remove=False, we'll remove manually in finally
            detach=True,        # Detach to run in background and wait later
            stdout=True,        # Capture stdout
            stderr=True,        # Capture stderr
            network_mode=network_mode,
            mem_limit=mem_limit
            # cpus=cpus, # <--- REMOVED THIS LINE
        )

        # Wait for container completion and get status code
        try:
            logger.info(f"Waiting for container '{container_name}' to finish (timeout: {timeout}s)...")
            result = container.wait(timeout=timeout)
            exit_code = result.get('StatusCode', -1)
            logger.info(f"Container '{container_name}' finished with exit code: {exit_code}")
        except (ReadTimeout, ConnectionError) as e: # docker-py wait() raises requests.exceptions.ReadTimeout
            logger.error(f"Timeout ({timeout}s) waiting for container '{container_name}'. Forcing removal.", exc_info=True)
            # Exit code remains -1 or its default
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Container execution timed out after {timeout} seconds."
            )
        except APIError as e:
             logger.error(f"APIError while waiting for container '{container_name}': {e}", exc_info=True)
             # Exit code remains -1
             # Allow finally block to attempt removal

        # Retrieve logs after waiting
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            stdout_str = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ""
            stderr_str = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ""
            logger.info(f"Retrieved logs for container '{container_name}'.")
        except APIError as e:
             logger.error(f"APIError retrieving logs for container '{container_name}': {e}", exc_info=True)
             # Keep logs empty, but we might have the exit code

        return exit_code, stdout_str, stderr_str

    except ImageNotFound:
        logger.error(f"Fatal: Sandbox image '{image}' not found.")
        raise HTTPException(status_code=500, detail=f"Execution environment image '{image}' not found.")
    except APIError as e:
        logger.error(f"Docker API error during container run for '{container_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Docker API error: {e}")
    except TypeError as e: # Specifically catch TypeError which indicates wrong arguments
         logger.error(f"TypeError calling docker_client.containers.run for '{container_name}': {e}", exc_info=True)
         raise HTTPException(status_code=500, detail=f"Server configuration error: Invalid argument passed to Docker run.")
    except Exception as e: # Catch other unexpected errors
        logger.error(f"Unexpected error during container execution '{container_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred.")
    finally:
        # Ensure container is removed
        if container:
            try:
                logger.info(f"Attempting to remove container '{container.name}'...")
                container.remove(force=True) # Force remove in case it timed out or errored
                logger.info(f"Successfully removed container '{container.name}'.")
            except APIError as e:
                # Log error but don't raise exception from finally block
                logger.error(f"Failed to remove container '{container.name}': {e}", exc_info=True)
            except Exception as e:
                 logger.error(f"Unexpected error removing container '{container.name}': {e}", exc_info=True)


def create_execution_script(user_code: str, output_filename: str, workdir: str) -> str:
    """
    Wraps the user's Python code with necessary boilerplate for execution
    within the sandbox, including saving the plot. (No changes needed here)
    """
    boilerplate_header = f"""
import matplotlib
matplotlib.use('Agg') # Ensure non-interactive backend is used
import matplotlib.pyplot as plt
import pandas as pd # Make common libraries available
import numpy as np
import sys
import os
print("--- Starting User Code Execution ---", flush=True)
try:
    # --- User code starts ---
"""
    indented_user_code = "\n".join(["    " + line for line in user_code.strip().splitlines()])
    boilerplate_footer = f"""
    # --- User code ends ---
except Exception as e:
    print(f"Error during user code execution: {{e}}", file=sys.stderr, flush=True)
    sys.exit(1)
print("--- User Code Finished ---", flush=True)
# --- Saving the plot ---
try:
    output_path = os.path.join('{workdir}', '{output_filename}')
    if plt.get_fignums():
        print(f"Saving plot to {{output_path}}...", flush=True)
        plt.savefig(output_path, format='png', bbox_inches='tight')
        print(f"Plot saved successfully.", flush=True)
    else:
        print("No matplotlib plot detected to save.", file=sys.stderr, flush=True)
except Exception as e:
    print(f"Error saving plot: {{e}}", file=sys.stderr, flush=True)
    sys.exit(3)
finally:
    plt.close('all')
print("--- Script Finished Successfully ---", flush=True)
sys.exit(0)
"""
    return boilerplate_header + indented_user_code + boilerplate_footer

def cleanup_temp_dir(temp_dir_path: Path):
    """Safely removes the temporary directory."""
    try:
        if temp_dir_path and temp_dir_path.is_dir():
            shutil.rmtree(temp_dir_path)
            logger.info(f"Background task: Cleaned up temporary directory: {temp_dir_path}")
        else:
             logger.warning(f"Background task: Temporary directory not found or not a directory: {temp_dir_path}")
    except Exception as e:
        logger.error(f"Background task: Error cleaning up temp dir {temp_dir_path}: {e}", exc_info=True)


# --- API Endpoints ---

@app.post(
    "/execute/python/chart",
    responses={ # Keep responses documentation updated
        200: {"content": {"image/png": {}}, "description": "Success. Returns chart PNG."},
        400: {"description": "Bad Request (e.g., Python code execution failed)."},
        408: {"description": "Request Timeout (Container execution took too long)."},
        422: {"description": "Validation Error."},
        500: {"description": "Internal Server Error (Docker issues, etc.)."},
    }
)
async def execute_python_chart(payload: PythonCode, background_tasks: BackgroundTasks):
    """
    Executes Python code designed to generate a Matplotlib chart in a Docker sandbox.
    Returns the chart PNG or an error. Uses background tasks for cleanup.
    """
    temp_dir_host = tempfile.mkdtemp()
    temp_dir_path = Path(temp_dir_host)
    logger.info(f"Chart Execution: Created temporary directory: {temp_dir_host}")

    try:
        script_filename = "script.py"
        script_path_host = temp_dir_path / script_filename
        output_path_host = temp_dir_path / OUTPUT_FILENAME

        # Prepare the script
        full_script_code = create_execution_script(
            payload.code, OUTPUT_FILENAME, WORKSPACE_DIR_INSIDE_CONTAINER
        )
        try:
            script_path_host.write_text(full_script_code)
            logger.info(f"Chart Execution: Script written to: {script_path_host}")
        except IOError as e:
             logger.error(f"Chart Execution: Failed to write script file '{script_path_host}': {e}", exc_info=True)
             background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
             raise HTTPException(status_code=500, detail="Server error: Failed to write script file.")

        volumes = {
            str(temp_dir_path.resolve()): {
                'bind': WORKSPACE_DIR_INSIDE_CONTAINER,
                'mode': 'rw'
            }
        }
        command = ["python", f"{WORKSPACE_DIR_INSIDE_CONTAINER}/{script_filename}"]

        # Use the refactored helper function to run the container
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=command,
            volumes=volumes,
            # network_mode="bridge" # Keep default 'none' unless needed
        )

        logger.info(f"Chart Execution: Container stdout:\n{stdout_str}")
        if stderr_str:
            logger.warning(f"Chart Execution: Container stderr:\n{stderr_str}")

        if exit_code != 0:
            logger.error(f"Chart Execution: Script failed with exit code {exit_code}.")
            error_detail = f"Python script execution failed (Exit Code: {exit_code})."
            log_preview = '\n'.join(stderr_str.splitlines()[-10:])
            error_detail += f"\nStderr (Last 10 lines):\n{log_preview}"
            background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
            raise HTTPException(status_code=400, detail=error_detail)

        logger.info(f"Chart Execution: Checking for output file at host path: {output_path_host}")
        if not output_path_host.is_file():
            logger.error(f"Chart Execution: Output file '{output_path_host}' not found despite exit code 0.")
            error_detail = f"Script executed successfully (Exit Code: 0) but failed to produce the expected output file ('{OUTPUT_FILENAME}'). Check script logic."
            log_preview_stdout = '\n'.join(stdout_str.splitlines()[-10:])
            log_preview_stderr = '\n'.join(stderr_str.splitlines()[-10:])
            error_detail += f"\nStdout (Last 10 lines):\n{log_preview_stdout}"
            error_detail += f"\nStderr (Last 10 lines):\n{log_preview_stderr}"
            background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
            raise HTTPException(status_code=500, detail=error_detail)

        logger.info(f"Chart Execution: Success. Returning output file: {output_path_host}")
        background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
        return FileResponse(
            path=output_path_host,
            media_type='image/png',
            filename=OUTPUT_FILENAME
        )

    except HTTPException:
         logger.warning(f"Chart Execution: Handling HTTPException, ensuring cleanup for {temp_dir_path}")
         background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
         raise
    except Exception as e:
        logger.error(f"Chart Execution: Unexpected error in endpoint: {e}", exc_info=True)
        background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")


# NEW Shell Execution Endpoint
@app.post(
    "/execute/shell",
    response_model=ShellResult,
    responses={
        200: {"description": "Command executed. Returns stdout, stderr, and exit code."},
        400: {"description": "Bad Request (e.g., invalid shell command leading to non-zero exit)."},
        408: {"description": "Request Timeout (Container execution took too long)."},
        422: {"description": "Validation Error."},
        500: {"description": "Internal Server Error (Docker issues, etc.)."},
    }
)
async def execute_shell_command(payload: ShellCommand):
    """
    Executes a shell command string in a Docker sandbox.
    Returns the command's stdout, stderr, and exit code.
    """
    if not payload.command:
        raise HTTPException(status_code=422, detail="Shell command cannot be empty.")

    shell_command_list = ["bash", "-c", f"set -e; set -o pipefail; {payload.command}"]

    try:
        # Use the refactored helper function
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=shell_command_list,
            network_mode="bridge", # Keep network enabled for shell examples like curl
        )

        logger.info(f"Shell Execution: Command '{payload.command}' finished with exit code {exit_code}.")
        logger.info(f"Shell Execution: stdout:\n{stdout_str}")
        if stderr_str:
             logger.warning(f"Shell Execution: stderr:\n{stderr_str}")

        # Return the results using the Pydantic model
        # Note: Even if exit_code is non-zero, we return 200 OK here.
        # The caller (the agent) is responsible for interpreting the exit code.
        # We only raise 4xx/5xx for *infrastructure* or *request validation* errors.
        return ShellResult(
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=exit_code
        )
    except HTTPException as e:
        logger.error(f"Shell Execution: HTTPException occurred: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Shell Execution: Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred: {e}")


@app.get("/health", status_code=status.HTTP_200_OK)
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
    # Remember to use src.main:app if main.py is in src directory
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)

