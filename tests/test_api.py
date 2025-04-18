# tests/test_api.py - Pytest tests for the execution API
# Updated: Corrected assertion in test_execute_python_script_persistence

import pytest
from fastapi.testclient import TestClient
import os
import uuid # For unique session IDs in tests
import sys # To check python version for print differences

# Import the FastAPI app instance and docker client for cleanup
from src.main import app
from src.core.docker_runner import docker_client, get_session_volume_name, SESSION_VOLUME_PREFIX
from docker.errors import NotFound

# Create a TestClient instance
client = TestClient(app)

# --- Test Data ---

SIMPLE_PLOT_CODE = """
import matplotlib.pyplot as plt
print("--- Plotting Test ---")
plt.plot([10, 20, 5, 15])
plt.title("Simple Test Plot")
print("--- Plotting Done ---")
"""

PYTHON_ERROR_CODE = """
print("--- Error Test ---")
x = 1 / 0
print("--- This won't print ---")
"""

PYTHON_SCRIPT_SUCCESS = """
import sys
import os
print("Hello from Python script!")
print("Current working directory:", os.getcwd(), flush=True)
print("Arguments:", sys.argv)
print("Test data on stderr", file=sys.stderr)
"""

PYTHON_SCRIPT_FAILURE = """
import sys
print("About to exit with status 5", file=sys.stderr)
sys.exit(5)
"""

# --- Fixtures ---

@pytest.fixture(scope="session", autouse=True)
def cleanup_test_volumes():
    """Pytest fixture to automatically clean up test volumes after tests run."""
    yield
    print("\nCleaning up test Docker volumes...")
    if not docker_client:
        print("Warning: Docker client not available for volume cleanup.")
        return
    try:
        # Add prefix used by tests to filter volumes
        test_volume_prefix = f"{SESSION_VOLUME_PREFIX}test-session-"
        volumes = docker_client.volumes.list(filters={'name': test_volume_prefix})
        count = 0
        for volume in volumes:
            try:
                print(f"Removing test volume: {volume.name}")
                volume.remove(force=True)
                count += 1
            except Exception as e:
                print(f"Error removing volume {volume.name}: {e}")
        print(f"Removed {count} test volumes matching prefix '{test_volume_prefix}*'.")
    except Exception as e:
        print(f"Error listing/cleaning test volumes: {e}")


# --- Test Functions ---

def test_health_check():
    """Test the /health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "ok"
    assert "docker_status" in json_response
    print(f"Health check response: {json_response}")

# --- Shell Execution Tests ---

def test_execute_shell_success():
    """Test successful shell command execution within a session."""
    session_id = f"test-session-shell-success-{uuid.uuid4()}"
    command = "echo 'Success!' && exit 0"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": command})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == "Success!\n" # echo adds newline
    assert json_response["stderr"] == ""
    assert json_response["exit_code"] == 0

def test_execute_shell_failure_exit_code():
    """Test shell command execution that exits non-zero within a session."""
    session_id = f"test-session-shell-fail-{uuid.uuid4()}"
    command = "echo 'Error output' >&2 && exit 5"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": command})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == ""
    assert "Error output" in json_response["stderr"]
    assert json_response["exit_code"] == 5

def test_execute_shell_command_not_found():
    """Test shell command execution where the command doesn't exist."""
    session_id = f"test-session-shell-notfound-{uuid.uuid4()}"
    command = "this_command_does_not_exist_hopefully"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": command})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == ""
    assert "not found" in json_response["stderr"]
    assert json_response["exit_code"] != 0

def test_execute_shell_persistence():
    """Test if files persist within the same session volume."""
    session_id = f"test-session-persistence-{uuid.uuid4()}"
    filename = "persist_test.txt"
    file_content = f"Data for {session_id}" # No newline here

    # 1. Write to a file using echo -n to avoid adding a newline
    write_command = f"echo -n '{file_content}' > {filename}"
    response_write = client.post("/execute/shell", json={"session_id": session_id, "command": write_command})
    assert response_write.status_code == 200
    assert response_write.json()["exit_code"] == 0

    # 2. Read the file back in the SAME session using cat
    read_command = f"cat {filename}"
    response_read = client.post("/execute/shell", json={"session_id": session_id, "command": read_command})
    assert response_read.status_code == 200
    json_read = response_read.json()
    assert json_read["exit_code"] == 0
    # Assert exact content without assuming cat adds a newline
    assert json_read["stdout"] == file_content
    assert json_read["stderr"] == ""

    # 3. Try to read the file in a DIFFERENT session (should fail)
    other_session_id = f"test-session-other-{uuid.uuid4()}"
    response_other = client.post("/execute/shell", json={"session_id": other_session_id, "command": read_command})
    assert response_other.status_code == 200
    json_other = response_other.json()
    assert json_other["exit_code"] != 0
    assert "No such file or directory" in json_other["stderr"]

    if docker_client:
        volume_name = get_session_volume_name(session_id)
        try:
             docker_client.volumes.get(volume_name)
             volume_exists = True
        except NotFound:
             volume_exists = False
        assert volume_exists is True

def test_execute_shell_missing_session_id():
    """Test sending payload without the 'session_id' field."""
    response = client.post("/execute/shell", json={"command": "echo test"})
    assert response.status_code == 422

def test_execute_shell_empty_command():
    """Test sending an empty command string."""
    session_id = f"test-session-{uuid.uuid4()}"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": ""})
    assert response.status_code == 422

# --- Python Chart Execution Tests (Stateless) ---

def test_execute_python_chart_success():
    """Test successful Python chart generation."""
    response = client.post("/execute/python/chart", json={"code": SIMPLE_PLOT_CODE})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 100

def test_execute_python_chart_code_error():
    """Test Python code execution that results in a runtime error."""
    response = client.post("/execute/python/chart", json={"code": PYTHON_ERROR_CODE})
    assert response.status_code == 400
    json_response = response.json()
    assert "detail" in json_response
    assert "Python script execution failed" in json_response["detail"]
    assert "Exit Code: 1" in json_response["detail"]
    assert "division by zero" in json_response["detail"]

def test_execute_python_chart_no_plot():
    """Test Python code that runs successfully but doesn't create a plot."""
    code_no_plot = "print('This script does nothing visual.')\nx = 1 + 1"
    response = client.post("/execute/python/chart", json={"code": code_no_plot})
    assert response.status_code == 500
    json_response = response.json()
    assert "detail" in json_response
    assert "failed to produce the expected output file" in json_response["detail"]

def test_execute_python_chart_missing_code():
    """Test sending payload without the 'code' field."""
    response = client.post("/execute/python/chart", json={})
    assert response.status_code == 422

# --- Python Script Execution Tests (Stateful) ---

def test_execute_python_script_success():
    """Test successful execution of a general Python script."""
    session_id = f"test-session-py-success-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": PYTHON_SCRIPT_SUCCESS})
    assert response.status_code == 200
    json_response = response.json()
    assert "Hello from Python script!" in json_response["stdout"]
    assert "Current working directory: /workspace" in json_response["stdout"]
    assert "Test data on stderr" in json_response["stderr"]
    assert json_response["exit_code"] == 0

def test_execute_python_script_failure():
    """Test Python script execution that exits non-zero."""
    session_id = f"test-session-py-fail-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": PYTHON_SCRIPT_FAILURE})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == ""
    assert "About to exit with status 5" in json_response["stderr"]
    assert json_response["exit_code"] == 5

def test_execute_python_script_runtime_error():
    """Test Python script execution that causes a runtime error."""
    session_id = f"test-session-py-runtimeerror-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": PYTHON_ERROR_CODE})
    assert response.status_code == 200
    json_response = response.json()
    assert "Error Test" in json_response["stdout"]
    assert "Traceback" in json_response["stderr"]
    assert "ZeroDivisionError" in json_response["stderr"]
    assert json_response["exit_code"] != 0

def test_execute_python_script_persistence():
    """Test that python scripts can interact with files in the session volume."""
    session_id = f"test-session-py-persistence-{uuid.uuid4()}"
    filename = "py_persist_test.txt"
    file_content = f"Data from Python script for {session_id}" # No newline here

    # 1. Write via Python script
    write_code = f"""
print("Writing file...")
with open('{filename}', 'w') as f:
    f.write('{file_content}') # Write exact content, no newline added by write()
print("Write complete.")
"""
    response_write = client.post("/execute/python/script", json={"session_id": session_id, "code": write_code})
    assert response_write.status_code == 200
    assert response_write.json()["exit_code"] == 0
    assert "Write complete." in response_write.json()["stdout"]

    # 2. Read via Shell command in the SAME session using cat
    read_command = f"cat {filename}"
    response_read = client.post("/execute/shell", json={"session_id": session_id, "command": read_command})
    assert response_read.status_code == 200
    json_read = response_read.json()
    assert json_read["exit_code"] == 0
    # --- FIXED ASSERTION ---
    # Assert that stdout exactly matches the content written, without the extra newline
    assert json_read["stdout"] == file_content
    # ---                 ---
    assert json_read["stderr"] == "" # Expect empty stderr from successful cat

def test_execute_python_script_missing_session_id():
    """Test sending payload without the 'session_id' field."""
    response = client.post("/execute/python/script", json={"code": "print('hello')"})
    assert response.status_code == 422

def test_execute_python_script_empty_code():
    """Test sending an empty code string."""
    session_id = f"test-session-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": ""})
    assert response.status_code == 422

