# src/models/execution.py - Pydantic models for API requests and responses
# Updated: Added PythonScript model

from pydantic import BaseModel, Field
import re

# --- Utility for Sanitization ---
def sanitize_session_id(session_id: str) -> str:
    """Basic sanitization for session IDs used in volume names."""
    sanitized = re.sub(r'[^a-zA-Z0-9_\-.]', '_', session_id)
    return sanitized[:50]

# --- Execution Input Models ---

class PythonCode(BaseModel):
    """Model for executing Python code that generates a chart (Stateless)."""
    code: str = Field(..., description="Python code string to execute for generating a chart.")

class ShellCommand(BaseModel):
    """Model for executing a shell command within a specific session."""
    session_id: str = Field(..., description="Identifier for the persistent session workspace.")
    command: str = Field(..., description="Shell command string to execute.")

# New model for general Python script execution
class PythonScript(BaseModel):
    """Model for executing a general Python script within a specific session."""
    session_id: str = Field(..., description="Identifier for the persistent session workspace.")
    code: str = Field(..., description="Python code string to execute.")


# --- Execution Result Models ---

class ShellResult(BaseModel):
    """Model for the result of a shell command or Python script execution."""
    stdout: str
    stderr: str
    exit_code: int

# ShellResult is suitable for Python script output as well.

