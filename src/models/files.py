# src/models/files.py - Pydantic models for File System API operations

from pydantic import BaseModel, Field
from typing import Literal, List, Optional

class FileEntry(BaseModel):
    """Represents an entry (file or directory) in a directory listing."""
    name: str = Field(..., description="Name of the file or directory.")
    type: Literal['file', 'directory'] = Field(..., description="Type of the entry.")
    # Future: Add size, permissions, modified_time etc.
    # size: Optional[int] = None

class FileListResponse(BaseModel):
    """Response model for listing directory contents."""
    path: str = Field(..., description="The path of the listed directory relative to the workspace root.")
    entries: List[FileEntry] = Field(..., description="List of files and directories.")

class FileContentResponse(BaseModel):
    """Response model for reading file content."""
    path: str = Field(..., description="The path of the file relative to the workspace root.")
    content: str = Field(..., description="The content of the file.")
    # Future: Add encoding, size etc.

class FileWriteRequest(BaseModel):
    """Request model for writing file content."""
    content: str = Field(..., description="The content to write to the file.")
    # Future: Add encoding, append flag etc.

# No specific model needed for Delete or Mkdir requests/responses beyond standard success/error.
# Could add simple success models if desired, e.g., class OperationSuccess(BaseModel): message: str
