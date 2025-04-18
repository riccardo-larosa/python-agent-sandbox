# tests/test_api.py - Pytest tests for the execution API
# Updated: Reformatted long single-line tests for readability

import pytest
from fastapi.testclient import TestClient
import os
import uuid
import sys
from pathlib import Path
import shlex # Import needed for test_create_directory fix

# Use absolute imports starting from the 'src' directory
from src.main import app
from src.core.docker_runner import docker_client, get_session_volume_name, SESSION_VOLUME_PREFIX
from src.models.execution import PythonCode, ShellCommand, ShellResult, PythonScript
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
    if not docker_client: print("Warning: Docker client not available for volume cleanup."); return
    try:
        test_volume_prefix = f"{SESSION_VOLUME_PREFIX}test-session-"
        volumes = docker_client.volumes.list(filters={'name': test_volume_prefix})
        count = 0
        for volume in volumes:
            try: print(f"Removing test volume: {volume.name}"); volume.remove(force=True); count += 1
            except Exception as e: print(f"Error removing volume {volume.name}: {e}")
        print(f"Removed {count} test volumes matching prefix '{test_volume_prefix}*'.")
    except Exception as e: print(f"Error listing/cleaning test volumes: {e}")

# --- Test Functions ---
def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["status"] == "ok"
    assert "docker_status" in json_response

# --- Shell Execution Tests (Reformatted) ---
def test_execute_shell_success():
    session_id = f"test-session-shell-success-{uuid.uuid4()}"
    command = "echo 'Success!' && exit 0"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": command})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == "Success!\n"
    assert json_response["stderr"] == ""
    assert json_response["exit_code"] == 0

def test_execute_shell_failure_exit_code():
    session_id = f"test-session-shell-fail-{uuid.uuid4()}"
    command = "echo 'Error output' >&2 && exit 5"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": command})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == ""
    assert "Error output" in json_response["stderr"]
    assert json_response["exit_code"] == 5

def test_execute_shell_command_not_found():
    session_id = f"test-session-shell-notfound-{uuid.uuid4()}"
    command = "this_command_does_not_exist_hopefully"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": command})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == ""
    assert "not found" in json_response["stderr"]
    assert json_response["exit_code"] != 0

def test_execute_shell_persistence():
    session_id = f"test-session-persistence-{uuid.uuid4()}"
    filename = "persist_test.txt"
    file_content = f"Data for {session_id}"
    # Write using echo -n
    write_command = f"echo -n '{file_content}' > {filename}"
    response_write = client.post("/execute/shell", json={"session_id": session_id, "command": write_command})
    assert response_write.status_code == 200
    assert response_write.json()["exit_code"] == 0
    # Read using cat
    read_command = f"cat {filename}"
    response_read = client.post("/execute/shell", json={"session_id": session_id, "command": read_command})
    assert response_read.status_code == 200
    json_read = response_read.json()
    assert json_read["exit_code"] == 0
    assert json_read["stdout"] == file_content # Compare exact content
    assert json_read["stderr"] == ""
    # Try reading in different session
    other_session_id = f"test-session-other-{uuid.uuid4()}"
    response_other = client.post("/execute/shell", json={"session_id": other_session_id, "command": read_command})
    assert response_other.status_code == 200
    json_other = response_other.json()
    assert json_other["exit_code"] != 0
    assert "No such file or directory" in json_other["stderr"]
    # Verify volume exists (optional check)
    if docker_client:
        volume_name = get_session_volume_name(session_id)
        volume_exists = False
        try:
            docker_client.volumes.get(volume_name)
            volume_exists = True
        except NotFound:
            volume_exists = False
        assert volume_exists is True

def test_execute_shell_missing_session_id():
    response = client.post("/execute/shell", json={"command": "echo test"})
    assert response.status_code == 422

def test_execute_shell_empty_command():
    session_id = f"test-session-{uuid.uuid4()}"
    response = client.post("/execute/shell", json={"session_id": session_id, "command": ""})
    assert response.status_code == 422

# --- Python Chart Execution Tests (Stateless) ---
def test_execute_python_chart_success():
    response = client.post("/execute/python/chart", json={"code": SIMPLE_PLOT_CODE})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 100

def test_execute_python_chart_code_error():
    response = client.post("/execute/python/chart", json={"code": PYTHON_ERROR_CODE})
    assert response.status_code == 400
    json_response = response.json()
    assert "detail" in json_response
    assert "Python script execution failed" in json_response["detail"]
    assert "Exit Code: 1" in json_response["detail"]
    assert "division by zero" in json_response["detail"]

def test_execute_python_chart_no_plot():
    code_no_plot = "print('This script does nothing visual.')\nx = 1 + 1"
    response = client.post("/execute/python/chart", json={"code": code_no_plot})
    assert response.status_code == 500
    json_response = response.json()
    assert "detail" in json_response
    assert "failed to produce the expected output file" in json_response["detail"]

def test_execute_python_chart_missing_code():
    response = client.post("/execute/python/chart", json={})
    assert response.status_code == 422

# --- Python Script Execution Tests (Stateful) ---
def test_execute_python_script_success():
    session_id = f"test-session-py-success-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": PYTHON_SCRIPT_SUCCESS})
    assert response.status_code == 200
    json_response = response.json()
    assert "Hello from Python script!" in json_response["stdout"]
    assert "Current working directory: /workspace" in json_response["stdout"]
    assert "Test data on stderr" in json_response["stderr"]
    assert json_response["exit_code"] == 0

def test_execute_python_script_failure():
    session_id = f"test-session-py-fail-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": PYTHON_SCRIPT_FAILURE})
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["stdout"] == ""
    assert "About to exit with status 5" in json_response["stderr"]
    assert json_response["exit_code"] == 5

def test_execute_python_script_runtime_error():
    session_id = f"test-session-py-runtimeerror-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": PYTHON_ERROR_CODE})
    assert response.status_code == 200
    json_response = response.json()
    assert "Error Test" in json_response["stdout"]
    assert "Traceback" in json_response["stderr"]
    assert "ZeroDivisionError" in json_response["stderr"]
    assert json_response["exit_code"] != 0

def test_execute_python_script_persistence():
    session_id = f"test-session-py-persistence-{uuid.uuid4()}"
    filename = "py_persist_test.txt"
    file_content = f"Data from Python script for {session_id}"
    write_code = f"print('Writing file...')\nwith open('{filename}', 'w') as f:\n    f.write('{file_content}')\nprint('Write complete.')"
    response_write = client.post("/execute/python/script", json={"session_id": session_id, "code": write_code})
    assert response_write.status_code == 200
    assert response_write.json()["exit_code"] == 0
    assert "Write complete." in response_write.json()["stdout"]
    read_command = f"cat {filename}"
    response_read = client.post("/execute/shell", json={"session_id": session_id, "command": read_command})
    assert response_read.status_code == 200
    json_read = response_read.json()
    assert json_read["exit_code"] == 0
    assert json_read["stdout"] == file_content
    assert json_read["stderr"] == ""

def test_execute_python_script_missing_session_id():
    response = client.post("/execute/python/script", json={"code": "print('hello')"})
    assert response.status_code == 422

def test_execute_python_script_empty_code():
    session_id = f"test-session-{uuid.uuid4()}"
    response = client.post("/execute/python/script", json={"session_id": session_id, "code": ""})
    assert response.status_code == 422

# --- File System API Tests (Reformatted) ---
@pytest.fixture
def file_api_session_id():
    return f"test-session-files-{uuid.uuid4()}"

def test_create_directory(file_api_session_id):
    session_id = file_api_session_id
    dir_to_create = "test_dir"
    subdir_to_create = f"{dir_to_create}/subdir"
    # Create nested directory
    response = client.post(f"/sessions/{session_id}/files/directories?path={subdir_to_create}")
    assert response.status_code == 201
    assert response.json()["path"] == subdir_to_create
    # Verify using ls on parent
    parent_dir_path = str(Path(subdir_to_create).parent)
    response_ls = client.post("/execute/shell", json={"session_id": session_id, "command": f"ls -AF {shlex.quote(parent_dir_path)}"})
    assert response_ls.status_code == 200
    ls_json = response_ls.json()
    assert ls_json["exit_code"] == 0
    assert "subdir/" in ls_json["stdout"].splitlines()

def test_write_file(file_api_session_id):
    session_id = file_api_session_id
    file_path = "my_new_file.txt"
    file_content = "Hello from the file API!\nLine 2."
    response = client.put(
        f"/sessions/{session_id}/files/content?path={file_path}",
        json={"content": file_content}
    )
    assert response.status_code == 204

def test_read_file(file_api_session_id):
    session_id = file_api_session_id
    file_path = "read_test.txt"
    file_content = f"Content for read test - {session_id}"
    # Write first
    write_response = client.put(f"/sessions/{session_id}/files/content?path={file_path}", json={"content": file_content})
    assert write_response.status_code == 204
    # Read back
    read_response = client.get(f"/sessions/{session_id}/files/content?path={file_path}")
    assert read_response.status_code == 200
    json_response = read_response.json()
    assert json_response["path"] == file_path
    assert json_response["content"] == file_content

def test_read_nonexistent_file(file_api_session_id):
    session_id = file_api_session_id
    file_path = "nonexistent_file.txt"
    response = client.get(f"/sessions/{session_id}/files/content?path={file_path}")
    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]

def test_list_directory(file_api_session_id):
    session_id = file_api_session_id
    dir_path = "list_test_dir"
    file1 = f"{dir_path}/file1.txt"
    file2 = f"{dir_path}/file2.log"
    subdir = f"{dir_path}/sub"
    # Create items
    response_mkdir = client.post(f"/sessions/{session_id}/files/directories?path={subdir}")
    assert response_mkdir.status_code == 201
    response_write1 = client.put(f"/sessions/{session_id}/files/content?path={file1}", json={"content": "f1"})
    assert response_write1.status_code == 204
    response_write2 = client.put(f"/sessions/{session_id}/files/content?path={file2}", json={"content": "f2"})
    assert response_write2.status_code == 204
    # List directory
    response = client.get(f"/sessions/{session_id}/files?path={dir_path}")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["path"] == dir_path
    entries = {entry["name"]: entry["type"] for entry in json_response["entries"]}
    assert len(entries) == 3
    assert entries.get("file1.txt") == "file"
    assert entries.get("file2.log") == "file"
    assert entries.get("sub") == "directory"

def test_list_root_directory(file_api_session_id):
    session_id = file_api_session_id
    response_write = client.put(f"/sessions/{session_id}/files/content?path=root_file.txt", json={"content": "root"})
    assert response_write.status_code == 204
    response = client.get(f"/sessions/{session_id}/files?path=.")
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["path"] == "."
    entries = {entry["name"]: entry["type"] for entry in json_response["entries"]}
    assert "root_file.txt" in entries
    assert entries["root_file.txt"] == "file"

def test_delete_file(file_api_session_id):
    session_id = file_api_session_id
    file_path = "file_to_delete.txt"
    write_resp = client.put(f"/sessions/{session_id}/files/content?path={file_path}", json={"content": "delete me"})
    assert write_resp.status_code == 204
    delete_resp = client.delete(f"/sessions/{session_id}/files?path={file_path}")
    assert delete_resp.status_code == 204
    read_resp = client.get(f"/sessions/{session_id}/files/content?path={file_path}")
    assert read_resp.status_code == 404

def test_delete_directory(file_api_session_id):
    session_id = file_api_session_id
    dir_path = "dir_to_delete"
    file_in_dir = f"{dir_path}/some_file.txt"
    response_mkdir = client.post(f"/sessions/{session_id}/files/directories?path={dir_path}")
    assert response_mkdir.status_code == 201
    response_write = client.put(f"/sessions/{session_id}/files/content?path={file_in_dir}", json={"content": "in dir"})
    assert response_write.status_code == 204
    delete_resp = client.delete(f"/sessions/{session_id}/files?path={dir_path}")
    assert delete_resp.status_code == 204
    list_resp = client.get(f"/sessions/{session_id}/files?path={dir_path}")
    assert list_resp.status_code == 404

def test_path_traversal_prevention(file_api_session_id):
    session_id = file_api_session_id
    bad_paths = ["../outside.txt", "/etc/passwd", "/workspace/../etc/passwd", ".."]
    for path in bad_paths:
        print(f"Testing bad path: {path}")
        response_list = client.get(f"/sessions/{session_id}/files?path={path}")
        assert response_list.status_code == 400
        assert "Invalid path" in response_list.json()["detail"]
        response_read = client.get(f"/sessions/{session_id}/files/content?path={path}")
        assert response_read.status_code == 400
        assert "Invalid path" in response_read.json()["detail"]
        response_write = client.put(f"/sessions/{session_id}/files/content?path={path}", json={"content": "bad"})
        assert response_write.status_code == 400
        assert "Invalid path" in response_write.json()["detail"]
        response_delete = client.delete(f"/sessions/{session_id}/files?path={path}")
        assert response_delete.status_code == 400
        assert "Invalid path" in response_delete.json()["detail"]
        response_mkdir = client.post(f"/sessions/{session_id}/files/directories?path={path}")
        assert response_mkdir.status_code == 400
        assert "Invalid path" in response_mkdir.json()["detail"]

