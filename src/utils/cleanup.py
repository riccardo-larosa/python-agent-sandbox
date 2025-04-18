# src/utils/cleanup.py - Utility functions for cleaning up resources

import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def cleanup_temp_dir(temp_dir_path: Path):
    """Safely removes the temporary directory."""
    try:
        # Check if path exists and is a directory before attempting removal
        if temp_dir_path and temp_dir_path.is_dir():
            shutil.rmtree(temp_dir_path)
            logger.info(f"Background task: Cleaned up temporary directory: {temp_dir_path}")
        # Optional: Log if path doesn't exist or isn't a dir when cleanup is called
        # else:
        #     logger.warning(f"Background task: Cleanup requested, but path not found or not a directory: {temp_dir_path}")
    except Exception as e:
        # Log errors during cleanup but don't let them crash the background task runner
        logger.error(f"Background task: Error cleaning up temp dir {temp_dir_path}: {e}", exc_info=True)

