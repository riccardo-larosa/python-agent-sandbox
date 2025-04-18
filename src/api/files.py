# src/api/files.py - API Router for File System Operations

import logging
import shlex
from pathlib import Path, PurePosixPath # Use PurePosixPath for manipulation

from fastapi import APIRouter, Depends, HTTPException, Query, Body, status

# Import models and helpers
from ..models.files import (
    FileListResponse, FileEntry, FileContentResponse, FileWriteRequest
)
from ..core.docker_runner import run_in_container, WORKSPACE_DIR_INSIDE_CONTAINER

logger = logging.getLogger(__name__)

# Create an API router
router = APIRouter(
    prefix="/sessions/{session_id}/files", # Prefix for all routes in this file
    tags=["File System"], # Tag for OpenAPI documentation
)

# --- Path Validation Helper ---
def validate_and_resolve_path(session_id: str, user_path: str) -> PurePosixPath:
    """
    Validates a user-provided path and resolves it relative to the workspace root.
    Prevents path traversal attacks.

    Args:
        session_id: The session ID (used for logging).
        user_path: The path provided by the user (e.g., from query param).

    Returns:
        A PurePosixPath object representing the absolute path inside the container's workspace.

    Raises:
        HTTPException: If the path is invalid or attempts to escape the workspace.
    """
    if not user_path:
        user_path = "." # Default to current directory if empty

    base_workspace = PurePosixPath(WORKSPACE_DIR_INSIDE_CONTAINER)
    try:
        # Normalize the path (handles '..', '.', '//') and join with workspace base
        # IMPORTANT: Treat user_path as relative *within* the workspace
        absolute_requested_path = (base_workspace / user_path).resolve()

        # Check if the resolved path is still within the workspace directory
        # This comparison prevents escaping via '..' or absolute paths
        if base_workspace not in absolute_requested_path.parents and absolute_requested_path != base_workspace:
             logger.warning(f"Path traversal attempt denied for session '{session_id}': User path '{user_path}' resolved outside workspace to '{absolute_requested_path}'")
             raise HTTPException(
                 status_code=status.HTTP_400_BAD_REQUEST,
                 detail=f"Invalid path: Access denied outside workspace for path '{user_path}'."
             )

        logger.debug(f"Resolved path for session '{session_id}': '{user_path}' -> '{absolute_requested_path}'")
        return absolute_requested_path

    except Exception as e: # Catch potential errors during path resolution
        logger.error(f"Error resolving path for session '{session_id}', user path '{user_path}': {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid path format or resolution error for '{user_path}'."
        )

# --- API Endpoints ---

@router.get(
    "",
    response_model=FileListResponse,
    summary="List directory contents",
    description=f"Lists files and subdirectories within the specified path relative to the session workspace ({WORKSPACE_DIR_INSIDE_CONTAINER}). Defaults to the workspace root."
)
async def list_directory(
    session_id: str,
    path: str = Query(".", description=f"Directory path relative to the workspace root ({WORKSPACE_DIR_INSIDE_CONTAINER}). Defaults to '.' (workspace root).")
):
    """Lists files and directories within the session workspace."""
    resolved_path = validate_and_resolve_path(session_id, path)
    # Use ls -pA: -p adds '/' to dirs, -A includes hidden files except . and ..
    # Use --full-time for potentially parsing dates later
    # Need to change directory first, then list relative path '.' within it
    # This avoids issues with ls potentially showing the target dir itself
    command = f"cd {shlex.quote(str(resolved_path))} && ls -pA --full-time"
    shell_command_list = ["bash", "-c", f"set -e; set -o pipefail; {command}"]

    try:
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=shell_command_list,
            session_id=session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER, # Start in workspace
            network_mode="none" # Listing doesn't need network
        )

        if exit_code != 0:
            logger.warning(f"List Directory failed for session '{session_id}', path '{path}'. Exit: {exit_code}, Stderr: {stderr_str}")
            # Check common errors
            if "No such file or directory" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Path not found: '{path}'")
            elif "Permission denied" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied for path: '{path}'")
            else:
                 raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list directory. Exit: {exit_code}, Stderr: {stderr_str}")

        # Parse ls output
        entries = []
        lines = stdout_str.strip().splitlines()
        # Skip header line if present (ls doesn't usually add one with -A unless using -l)
        # Let's assume no header for 'ls -pA'
        for line in lines:
             # Example line processing (basic):
             # If line ends with '/', it's a directory
             if not line: continue # Skip empty lines
             if line.endswith('/'):
                 entry_name = line[:-1] # Remove trailing slash
                 entry_type = 'directory'
             else:
                 entry_name = line
                 entry_type = 'file'
             # Skip '.' and '..' if they somehow appear despite -A
             if entry_name in ['.', '..']: continue
             entries.append(FileEntry(name=entry_name, type=entry_type))

        # Return path relative to workspace for user clarity
        relative_path = str(resolved_path.relative_to(PurePosixPath(WORKSPACE_DIR_INSIDE_CONTAINER)))
        return FileListResponse(path=relative_path, entries=entries)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error listing directory for session '{session_id}', path '{path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred while listing directory.")


@router.get(
    "/content",
    response_model=FileContentResponse,
    summary="Read file content",
    description=f"Reads the content of the specified file relative to the session workspace ({WORKSPACE_DIR_INSIDE_CONTAINER})."
)
async def read_file(
    session_id: str,
    path: str = Query(..., description=f"File path relative to the workspace root ({WORKSPACE_DIR_INSIDE_CONTAINER}).")
):
    """Reads content of a file within the session workspace."""
    resolved_path = validate_and_resolve_path(session_id, path)
    # Use cat to read the file
    command = f"cat -- {shlex.quote(str(resolved_path))}" # Use -- to handle filenames starting with -
    shell_command_list = ["bash", "-c", f"set -e; set -o pipefail; {command}"]

    try:
        exit_code, stdout_str, stderr_str = await run_in_container(
            command=shell_command_list,
            session_id=session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none"
        )

        if exit_code != 0:
            logger.warning(f"Read File failed for session '{session_id}', path '{path}'. Exit: {exit_code}, Stderr: {stderr_str}")
            if "No such file or directory" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: '{path}'")
            elif "Is a directory" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Path is a directory, not a file: '{path}'")
            elif "Permission denied" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied for file: '{path}'")
            else:
                 raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to read file. Exit: {exit_code}, Stderr: {stderr_str}")

        relative_path = str(resolved_path.relative_to(PurePosixPath(WORKSPACE_DIR_INSIDE_CONTAINER)))
        return FileContentResponse(path=relative_path, content=stdout_str)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error reading file for session '{session_id}', path '{path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred while reading file.")


@router.put(
    "/content",
    status_code=status.HTTP_204_NO_CONTENT, # Return 204 No Content on success
    summary="Write file content",
    description=f"Writes (or overwrites) the content of the specified file relative to the session workspace ({WORKSPACE_DIR_INSIDE_CONTAINER}). Creates parent directories if needed."
)
async def write_file(
    session_id: str,
    payload: FileWriteRequest, # Content is in the request body
    path: str = Query(..., description=f"File path relative to the workspace root ({WORKSPACE_DIR_INSIDE_CONTAINER}). Parent directories will be created.")
):
    """Writes content to a file within the session workspace."""
    resolved_path = validate_and_resolve_path(session_id, path)

    # Ensure parent directory exists before writing
    parent_dir = resolved_path.parent
    mkdir_command = f"mkdir -p {shlex.quote(str(parent_dir))}"
    mkdir_shell_command = ["bash", "-c", f"set -e; {mkdir_command}"]

    # Write content using printf > file for safety with special characters
    # Note: This approach might have limitations with extremely large file content
    # due to shell command length limits. Consider docker cp for large files later.
    write_command = f"printf '%s' {shlex.quote(payload.content)} > {shlex.quote(str(resolved_path))}"
    write_shell_command = ["bash", "-c", f"set -e; {write_command}"]

    try:
        # Step 1: Ensure directory exists
        logger.info(f"Ensuring directory exists for session '{session_id}', path '{parent_dir}'")
        exit_code_mkdir, _, stderr_mkdir = await run_in_container(
            command=mkdir_shell_command,
            session_id=session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none"
        )
        if exit_code_mkdir != 0:
             logger.error(f"Write File: Failed to create parent directory '{parent_dir}' for session '{session_id}'. Exit: {exit_code_mkdir}, Stderr: {stderr_mkdir}")
             raise HTTPException(status_code=500, detail=f"Failed to create parent directory. Stderr: {stderr_mkdir}")

        # Step 2: Write the file content
        logger.info(f"Writing file content for session '{session_id}', path '{resolved_path}'")
        exit_code_write, _, stderr_write = await run_in_container(
            command=write_shell_command,
            session_id=session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none"
        )
        if exit_code_write != 0:
            logger.error(f"Write File failed for session '{session_id}', path '{path}'. Exit: {exit_code_write}, Stderr: {stderr_write}")
            if "Permission denied" in stderr_write:
                 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied writing to file: '{path}'")
            elif "Is a directory" in stderr_write:
                 raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Path is a directory, cannot write file: '{path}'")
            else:
                 raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to write file. Exit: {exit_code_write}, Stderr: {stderr_write}")

        # If successful, return No Content
        return None # FastAPI handles the 204 automatically based on status_code

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error writing file for session '{session_id}', path '{path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred while writing file.")


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT, # Return 204 No Content on success
    summary="Delete file or directory",
    description=f"Deletes the specified file or directory (recursively) relative to the session workspace ({WORKSPACE_DIR_INSIDE_CONTAINER})."
)
async def delete_path(
    session_id: str,
    path: str = Query(..., description=f"Path to the file or directory to delete, relative to the workspace root ({WORKSPACE_DIR_INSIDE_CONTAINER}).")
):
    """Deletes a file or directory within the session workspace."""
    resolved_path = validate_and_resolve_path(session_id, path)

    # Prevent deleting the workspace root itself
    if resolved_path == PurePosixPath(WORKSPACE_DIR_INSIDE_CONTAINER):
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the workspace root directory.")

    # Use rm -rf for recursive deletion
    command = f"rm -rf -- {shlex.quote(str(resolved_path))}" # Use -- to handle names starting with -
    shell_command_list = ["bash", "-c", f"set -e; {command}"]

    try:
        exit_code, _, stderr_str = await run_in_container(
            command=shell_command_list,
            session_id=session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none"
        )

        if exit_code != 0:
            logger.warning(f"Delete Path failed for session '{session_id}', path '{path}'. Exit: {exit_code}, Stderr: {stderr_str}")
            # rm -rf usually doesn't fail on non-existent paths unless permissions issue
            if "Permission denied" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied deleting path: '{path}'")
            else:
                 # It might succeed even if path didn't exist, but log warning just in case
                 logger.warning(f"Delete command exited non-zero ({exit_code}) but may have partially succeeded or path didn't exist. Stderr: {stderr_str}")
                 # Still return success (204) as the goal is for the path to be gone.
                 # If stricter error handling on non-existent path is needed, check existence first.

        # If successful or non-critical error, return No Content
        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error deleting path for session '{session_id}', path '{path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred while deleting path.")


@router.post(
    "/directories",
    status_code=status.HTTP_201_CREATED, # Return 201 Created on success
    summary="Create directory",
    description=f"Creates a directory (including parent directories) at the specified path relative to the session workspace ({WORKSPACE_DIR_INSIDE_CONTAINER})."
)
async def create_directory(
    session_id: str,
    path: str = Query(..., description=f"Directory path to create, relative to the workspace root ({WORKSPACE_DIR_INSIDE_CONTAINER}).")
    # Add recursive flag later if needed, mkdir -p does this by default
):
    """Creates a directory within the session workspace."""
    resolved_path = validate_and_resolve_path(session_id, path)

    # Use mkdir -p to create parent directories as needed and not fail if exists
    command = f"mkdir -p -- {shlex.quote(str(resolved_path))}"
    shell_command_list = ["bash", "-c", f"set -e; {command}"]

    try:
        exit_code, _, stderr_str = await run_in_container(
            command=shell_command_list,
            session_id=session_id,
            working_dir=WORKSPACE_DIR_INSIDE_CONTAINER,
            network_mode="none"
        )

        if exit_code != 0:
            logger.error(f"Create Directory failed for session '{session_id}', path '{path}'. Exit: {exit_code}, Stderr: {stderr_str}")
            if "Permission denied" in stderr_str:
                 raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied creating directory: '{path}'")
            elif "File exists" in stderr_str: # mkdir -p shouldn't fail, but just in case
                 raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Path already exists and is not a directory: '{path}'")
            else:
                 raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create directory. Exit: {exit_code}, Stderr: {stderr_str}")

        # If successful, return Created status (handled by FastAPI)
        relative_path = str(resolved_path.relative_to(PurePosixPath(WORKSPACE_DIR_INSIDE_CONTAINER)))
        return {"message": "Directory created successfully", "path": relative_path} # Return 201 with optional body

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating directory for session '{session_id}', path '{path}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected server error occurred while creating directory.")

