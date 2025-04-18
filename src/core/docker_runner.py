# src/core/docker_runner.py - Handles Docker container execution
# Updated: Minor consistency check for WORKSPACE_DIR

import os
import uuid
import logging
import re # For sanitization

import docker
from docker.errors import ContainerError, ImageNotFound, APIError, NotFound
from docker.models.volumes import Volume # For type hinting
from requests.exceptions import ReadTimeout, ConnectionError
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# --- Configuration ---
SANDBOX_IMAGE_NAME = os.getenv("SANDBOX_IMAGE_NAME", "python-chart-sandbox:latest")
CONTAINER_RUN_TIMEOUT = int(os.getenv("CONTAINER_RUN_TIMEOUT", 60))
# Define the standard workspace directory used inside containers for session/temp data
WORKSPACE_DIR_INSIDE_CONTAINER = "/workspace"
DEFAULT_MEM_LIMIT = "256m"
DEFAULT_NETWORK_MODE = "none"
SESSION_VOLUME_PREFIX = "sandbox_session_"

# --- Docker Client Initialization ---
try:
    docker_client = docker.from_env()
    docker_client.ping()
    logger.info("Docker client initialized and connected successfully in docker_runner.")
except Exception as e:
    logger.error(f"Fatal: Failed to initialize Docker client in docker_runner: {e}", exc_info=True)
    docker_client = None

# --- Volume Management ---

def sanitize_for_volume_name(name: str) -> str:
    """Basic sanitization for strings used in Docker volume names."""
    sanitized = re.sub(r'[^a-zA-Z0-9_\-.]', '_', name)
    return sanitized[:50]

def get_session_volume_name(session_id: str) -> str:
    """Constructs the Docker volume name for a given session ID."""
    return f"{SESSION_VOLUME_PREFIX}{sanitize_for_volume_name(session_id)}"

def get_or_create_session_volume(session_id: str) -> Volume:
    """
    Retrieves an existing Docker volume for the session or creates it if not found.
    """
    if not docker_client:
        raise HTTPException(status_code=500, detail="Docker client not available")

    volume_name = get_session_volume_name(session_id)
    try:
        logger.info(f"Checking for volume: {volume_name}")
        volume = docker_client.volumes.get(volume_name)
        logger.info(f"Found existing volume: {volume_name}")
        return volume
    except NotFound:
        logger.info(f"Volume '{volume_name}' not found. Creating...")
        try:
            volume = docker_client.volumes.create(name=volume_name, driver='local')
            logger.info(f"Successfully created volume: {volume_name}")
            return volume
        except APIError as e:
            logger.error(f"APIError creating volume '{volume_name}': {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to create session volume: {e}")
        except Exception as e:
            logger.error(f"Unexpected error creating volume '{volume_name}': {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Unexpected error during volume creation.")
    except APIError as e:
        logger.error(f"APIError getting volume '{volume_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get session volume: {e}")
    except Exception as e:
        logger.error(f"Unexpected error getting volume '{volume_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Unexpected error during volume retrieval.")


# --- Core Execution Function ---
async def run_in_container(
    command: list[str],
    session_id: str | None = None,
    image: str = SANDBOX_IMAGE_NAME,
    # Default working_dir now uses the constant
    working_dir: str = WORKSPACE_DIR_INSIDE_CONTAINER,
    # Renamed extra_volumes -> temp_volumes for clarity
    temp_volumes: dict = None, # For temporary mounts (like chart endpoint needs)
    timeout: int = CONTAINER_RUN_TIMEOUT,
    network_mode: str = DEFAULT_NETWORK_MODE,
    mem_limit: str = DEFAULT_MEM_LIMIT
) -> tuple[int, str, str]:
    """
    Runs a command in a temporary Docker container, potentially mounting a session volume.
    Returns exit code, stdout, stderr. Session volume mounts to `working_dir` if session_id provided.
    Temporary volumes mount according to the `temp_volumes` dict.
    """
    if not docker_client:
        logger.error("run_in_container called but Docker client is not available.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Docker client not available")

    container_name = f"sandbox-helper-{uuid.uuid4()}"
    container = None
    exit_code = -1
    stdout_str = ""
    stderr_str = ""

    # Prepare volumes
    volumes_to_mount = {}
    if temp_volumes: # Add temporary volumes first
        volumes_to_mount.update(temp_volumes)

    if session_id: # Handle session volume
        try:
            session_volume = get_or_create_session_volume(session_id)
            # Check if the target working_dir is already used by a temp mount
            if working_dir in [v_spec['bind'] for v_spec in volumes_to_mount.values()]:
                 logger.error(f"Volume mount conflict: Cannot mount session volume to '{working_dir}' as it's used by a temporary volume.")
                 raise HTTPException(status_code=500, detail="Volume mount configuration conflict.")

            volumes_to_mount[session_volume.name] = {
                'bind': working_dir, # Mount session vol to the working directory
                'mode': 'rw'
            }
            logger.info(f"Prepared session volume '{session_volume.name}' for mounting to {working_dir}")
        except HTTPException:
             raise
        except Exception as e:
            logger.error(f"Unexpected error preparing session volume for session '{session_id}': {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to prepare session volume.")

    logger.info(f"Final volumes for container '{container_name}': {volumes_to_mount}")

    try:
        logger.info(f"Running command in container '{container_name}': {command}")
        container = docker_client.containers.run(
            image=image,
            command=command,
            volumes=volumes_to_mount if volumes_to_mount else None,
            name=container_name,
            working_dir=working_dir, # Set working dir inside container
            remove=False,
            detach=True,
            stdout=True,
            stderr=True,
            network_mode=network_mode,
            mem_limit=mem_limit
        )

        # Wait for container completion
        try:
            logger.info(f"Waiting for container '{container_name}' to finish (timeout: {timeout}s)...")
            result = container.wait(timeout=timeout)
            exit_code = result.get('StatusCode', -1)
            logger.info(f"Container '{container_name}' finished with exit code: {exit_code}")
        except (ReadTimeout, ConnectionError) as e:
            logger.error(f"Timeout ({timeout}s) waiting for container '{container_name}'. Forcing removal.", exc_info=False)
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=f"Container execution timed out after {timeout} seconds."
            )
        except APIError as e:
             logger.error(f"APIError while waiting for container '{container_name}': {e}", exc_info=True)

        # Retrieve logs
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            stdout_str = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ""
            stderr_str = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ""
            logger.info(f"Retrieved logs for container '{container_name}'.")
        except APIError as e:
             logger.error(f"APIError retrieving logs for container '{container_name}': {e}", exc_info=True)

        return exit_code, stdout_str, stderr_str

    except ImageNotFound:
        logger.error(f"Fatal: Sandbox image '{image}' not found.")
        raise HTTPException(status_code=500, detail=f"Execution environment image '{image}' not found.")
    except APIError as e:
        logger.error(f"Docker API error during container run for '{container_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Docker API error: {e}")
    except TypeError as e:
         logger.error(f"TypeError calling docker_client.containers.run for '{container_name}': {e}", exc_info=True)
         raise HTTPException(status_code=500, detail=f"Server configuration error: Invalid argument passed to Docker run.")
    except Exception as e:
        logger.error(f"Unexpected error during container execution '{container_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected server error occurred.")
    finally:
        # Ensure container is removed
        if container:
            try:
                logger.info(f"Attempting to remove container '{container.name}'...")
                container.remove(force=True)
                logger.info(f"Successfully removed container '{container.name}'.")
            except APIError as e:
                logger.error(f"Failed to remove container '{container.name}': {e}", exc_info=False)
            except Exception as e:
                 logger.error(f"Unexpected error removing container '{container.name}': {e}", exc_info=True)
