# main.py - FastAPI application for executing Python chart code in Docker
# Updated: Fixed temporary directory cleanup using BackgroundTasks

import os
import uuid
import shutil
import tempfile
import logging
from pathlib import Path

import docker
from docker.errors import ContainerError, ImageNotFound, APIError
# Import BackgroundTasks
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from fastapi.responses import FileResponse # Used to send the image file back
from pydantic import BaseModel, Field # For request body validation

# --- Configuration ---
SANDBOX_IMAGE_NAME = os.getenv("SANDBOX_IMAGE_NAME", "python-chart-sandbox:latest")
DOCKER_TIMEOUT = int(os.getenv("DOCKER_TIMEOUT", 60)) # Max execution time in seconds
OUTPUT_FILENAME = "output.png" # Expected output chart filename
WORKSPACE_DIR_INSIDE_CONTAINER = "/workspace" # Must match WORKDIR in Dockerfile if used

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Pydantic Models ---
class PythonCode(BaseModel):
    code: str = Field(..., description="Python code string to execute for generating a chart.")

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Python Chart Execution Service",
    description="API to execute Python plotting code in a Docker sandbox.",
    version="0.1.1", # Incremented version
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

# --- Cleanup Function ---
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
# Add BackgroundTasks to the function signature
@app.post(
    "/execute/python/chart",
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Successful execution. Returns the generated chart as a PNG image.",
        },
        400: {"description": "Bad Request (e.g., code execution failed due to user error)."},
        422: {"description": "Validation Error (Input JSON doesn't match expected format)."},
        500: {"description": "Internal Server Error (Docker issues, file system errors, etc.)."},
    }
)
async def execute_python_chart(payload: PythonCode, background_tasks: BackgroundTasks): # Added background_tasks
    """
    Accepts Python code, executes it in a sandboxed Docker container,
    and returns the generated Matplotlib chart as a PNG image.
    Uses background tasks for cleanup.
    """
    if not docker_client:
        logger.error("API call failed: Docker client is not available.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Docker client is not available. Cannot execute code.",
        )

    # 1. Create temporary directory MANUALLY instead of using 'with'
    temp_dir_host = tempfile.mkdtemp()
    temp_dir_path = Path(temp_dir_host)
    logger.info(f"Created temporary directory for execution: {temp_dir_host}")

    # Ensure cleanup happens even if errors occur before returning FileResponse
    try:
        script_filename = "script.py"
        script_path_host = temp_dir_path / script_filename
        output_path_host = temp_dir_path / OUTPUT_FILENAME
        container_name = f"sandbox-container-{uuid.uuid4()}"

        # 2. Prepare the Python script
        full_script_code = create_execution_script(
            payload.code,
            OUTPUT_FILENAME,
            WORKSPACE_DIR_INSIDE_CONTAINER
        )
        try:
            script_path_host.write_text(full_script_code)
            logger.info(f"Execution script written to: {script_path_host}")
        except IOError as e:
             logger.error(f"Failed to write script file '{script_path_host}': {e}", exc_info=True)
             raise HTTPException(
                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                 detail=f"Server error: Failed to write script file."
             )

        # 3. Execute the script inside a Docker container
        container_logs = ""
        container_exit_code = -1
        try:
            logger.info(f"Attempting to run container '{container_name}' from image '{SANDBOX_IMAGE_NAME}'...")
            logger.info(f"Host temporary directory resolved path: {temp_dir_path.resolve()}")
            logger.info(f"Mounting host path to '{WORKSPACE_DIR_INSIDE_CONTAINER}' in container.")

            container = docker_client.containers.run(
                image=SANDBOX_IMAGE_NAME,
                command=["python", f"{WORKSPACE_DIR_INSIDE_CONTAINER}/{script_filename}"],
                volumes={
                    str(temp_dir_path.resolve()): {
                        'bind': WORKSPACE_DIR_INSIDE_CONTAINER,
                        'mode': 'rw'
                    }
                },
                name=container_name,
                working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
                remove=True,
                detach=False,
                stdout=True,
                stderr=True,
                # Add resource limits here if needed
            )
            container_logs = container.decode('utf-8', errors='replace')
            container_exit_code = 0
            logger.info(f"Container '{container_name}' finished successfully (assumed exit code 0).")

        except ContainerError as e:
            container_exit_code = e.exit_status
            stdout_logs = e.stdout.decode('utf-8', errors='replace') if e.stdout else "[No stdout]"
            stderr_logs = e.stderr.decode('utf-8', errors='replace') if e.stderr else "[No stderr]"
            container_logs = f"STDOUT:\n{stdout_logs}\nSTDERR:\n{stderr_logs}"
            logger.warning(f"Container '{container_name}' exited with error code {container_exit_code}.")

        except ImageNotFound:
            logger.error(f"Fatal: Sandbox image '{SANDBOX_IMAGE_NAME}' not found.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Execution environment image '{SANDBOX_IMAGE_NAME}' not found on the host."
            )
        except APIError as e:
            logger.error(f"Docker API error during container execution: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Docker API error: {e}"
            )
        except Exception as e:
            logger.error(f"Unexpected error during Docker execution: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An unexpected server error occurred during execution."
            )
        finally:
            # Log container output regardless of success or failure
            if container_logs:
                 logger.info(f"--- Container Logs ('{container_name}') ---")
                 for line in container_logs.splitlines():
                     logger.info(f"  [Container] {line}")
                 logger.info(f"--- End Container Logs ---")
            else:
                 logger.info(f"No logs captured from container '{container_name}'.")
            logger.info(f"Container '{container_name}' final determined exit code: {container_exit_code}")


        # 4. Check if the output file was created and handle execution errors
        logger.info(f"Checking for output file at host path: {output_path_host}")
        if not output_path_host.is_file():
            logger.error(f"Output file '{output_path_host}' not found after execution.")
            error_detail = f"Code executed but failed to produce the expected output file ('{OUTPUT_FILENAME}')."
            if container_exit_code != 0:
                error_detail += f"\nContainer Exit Code: {container_exit_code}"
                if container_logs:
                    log_preview = '\n'.join(container_logs.splitlines()[-20:])
                    error_detail += f"\nExecution Logs (Last 20 lines):\n{log_preview}"
                else:
                    error_detail += "\nNo execution logs captured."
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if container_exit_code != 0:
                status_code = status.HTTP_400_BAD_REQUEST
            # No FileResponse to return, raise exception now
            raise HTTPException(status_code=status_code, detail=error_detail)

        # 5. Return the image file AND schedule cleanup
        logger.info(f"Execution successful. Returning output file: {output_path_host}")
        # Add the cleanup task to run AFTER the response is sent
        background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
        return FileResponse(
            path=output_path_host,
            media_type='image/png',
            filename=OUTPUT_FILENAME
        )

    except Exception as e:
        # Catch any unexpected errors outside the Docker execution block
        # Ensure cleanup is still scheduled if temp_dir_path was created
        if 'temp_dir_path' in locals() and temp_dir_path.is_dir():
             logger.warning(f"Scheduling cleanup for {temp_dir_path} after unexpected error.")
             background_tasks.add_task(cleanup_temp_dir, temp_dir_path)
        # Re-raise the exception to let FastAPI handle it (usually results in 500)
        raise e


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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) # Added reload=True here too
