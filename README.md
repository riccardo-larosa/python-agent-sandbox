# Python Agent Sandbox

## Overview

This project provides a secure sandbox environment for executing code (Shell, Python) and performing browser automation tasks initiated by AI agents, such as those built with Langchain. It uses Docker to isolate execution, a FastAPI backend to manage sessions and tasks, and provides various tools for agents to interact with the sandbox.

The primary goal is to allow agents to safely perform tasks like file manipulation, code execution, web scraping, and complex browser interactions without compromising the host system.

## Features

*   **Session Management:** Isolates user workspaces using Docker volumes.
*   **Shell Execution:** Allows running arbitrary shell commands within the sandbox.
*   **Python Execution:** Executes Python scripts within the sandbox.
*   **File System Operations:** Read, write, list, delete files, and create directories within the session's workspace.
*   **Browser Automation:** High-level browser interaction using the `browser-use` library, enabling tasks like navigation, form filling, and data extraction driven by natural language.
*   **Screenshots:** Takes screenshots of web pages.

## Prerequisites

*   **Docker:** Required to build and run the sandbox execution environment. Install Docker Desktop or Docker Engine.
*   **Python:** Python 3.10 or higher for running the FastAPI server and the example agent.
*   **Python Package Manager:** `uv` (recommended) or `pip` with `venv`.
*   **OpenAI API Key:** Required for the Langchain agent and the `browser-use` tool's internal LLM calls.

## Setup

1.  **Clone the Repository:**
    ```bash
    git clone <your-repo-url>
    cd python-agent-sandbox
    ```

2.  **Create Environment File:**
    Create a `.env` file in the project root directory and add your OpenAI API key:
    ```dotenv
    # .env
    OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ```
    *Note: Ensure this file is added to your `.gitignore`.*

3.  **Build the Docker Image:**
    This image contains the Python environment, dependencies (like Playwright, browser-use), and necessary browsers for the sandbox.
    ```bash
    docker build -t python-agent-sandbox:latest .
    ```
    *(The image tag `python-agent-sandbox:latest` should ideally match the `SANDBOX_IMAGE_NAME` expected by the server. Check `src/core/docker_runner.py` - the default might be `python-chart-sandbox:latest`, so update either the build tag or the variable in `docker_runner.py` if they don't match).*

4.  **Install Dependencies (Optional but Recommended):**
    Create separate requirements files for the server and agent for clarity.

    *   **Server Requirements (`requirements-server.txt`):**
        Create this file with contents like:
        ```txt
        fastapi
        uvicorn[standard]
        docker
        python-dotenv
        # Add other server-specific dependencies
        ```
    *   **Agent Requirements (`requirements-agent.txt`):**
        Create this file with contents like:
        ```txt
        langchain
        langchain-openai
        httpx
        python-dotenv
        browser-use # Dependency for BrowserTool definitions
        # Add other agent-specific dependencies
        ```
    *   **Install using a virtual environment:**
        ```bash
        # Using uv (recommended)
        uv venv
        source .venv/bin/activate # Or activate script for your shell
        uv pip install -r requirements-server.txt -r requirements-agent.txt

        # Or using venv + pip
        # python -m venv .venv
        # source .venv/bin/activate
        # pip install -r requirements-server.txt -r requirements-agent.txt
        ```

## Running the Sandbox Server

The FastAPI server listens for requests from the agent and orchestrates Docker container execution.

1.  **Ensure Docker Engine is running.**
2.  **Ensure the `.env` file exists in the project root** (the server reads it to pass the API key to the sandbox container for the BrowserTool).
3.  **Activate your virtual environment:** `source .venv/bin/activate`.
4.  **Run the server:**
    Choose ONE of the following methods:

    *   **Run Directly on Port 8002:** (Recommended if running agent locally)
        ```bash
        uvicorn src.main:app --host 0.0.0.0 --port 8002 --reload
        ```
        This makes the server directly listen on port 8002, which matches the agent's default `SANDBOX_API_URL`.

    *   **Run Directly on Port 8000:** (If you prefer port 8000)
        ```bash
        uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
        ```
        If you use this, you *must* either:
        a) Change `SANDBOX_API_URL` in `tests/test_agent.py` to `http://localhost:8000`.
        b) Set the `SANDBOX_API_URL` environment variable when running the agent: `SANDBOX_API_URL=http://localhost:8000 python tests/test_agent.py`.

    *   **Run Server Inside Docker (Advanced):** If you containerize the server itself, ensure you map the host port (e.g., 8002) to the container's port (e.g., 8000) using `docker run -p 8002:8000 ...`. You also need to handle Docker-in-Docker or mount the host's Docker socket (`-v /var/run/docker.sock:/var/run/docker.sock`).

## Running the Example Agent

The `tests/test_agent.py` script demonstrates how to use the tools via a Langchain agent.

1.  **Ensure the Sandbox Server is running and accessible** at the URL the agent expects (default: `http://localhost:8002`).
2.  **Ensure the `.env` file exists** in the directory where you run the agent (it loads the key for the main Langchain LLM).
3.  **Activate your virtual environment:** `source .venv/bin/activate`.
4.  **Run the agent script:**
    ```bash
    python tests/test_agent.py
    ```
    *   You can change the `user_query` variable near the end of `test_agent.py` to test different tasks.

## Configuration Summary

*   **`OPENAI_API_KEY`**: Set in the `.env` file (needed in both server and agent environments).
*   **`SANDBOX_API_URL`**: Set via environment variable or directly in `tests/test_agent.py` (default `http://localhost:8002`). Must match the running server address.
*   **`SANDBOX_IMAGE_NAME`**: Set via environment variable or directly in `src/core/docker_runner.py` (default `python-chart-sandbox:latest`). Must match the tag used during `docker build`.
*   **Container Settings**: Memory limits (`DEFAULT_MEM_LIMIT`), timeouts (`CONTAINER_RUN_TIMEOUT`), network mode (`DEFAULT_NETWORK_MODE`) etc., are configured within `src/core/docker_runner.py`. Ensure these are sufficient for browser tasks (e.g., `mem_limit="1g"`, `timeout=180`).

## Available Tools (via Agent)

*   `execute_shell`: Executes shell commands.
*   `execute_python_script`: Executes Python code.
*   `list_files`: Lists files/directories.
*   `read_file`: Reads file content.
*   `write_file`: Writes content to a file.
*   `delete_file`: Deletes files/directories.
*   `make_directory`: Creates directories.
*   `browser_tool`: Performs complex browser automation via `browser-use` using natural language tasks. Needs `OPENAI_API_KEY` passed via server.
*   `screenshot_browser`: Takes screenshots of web pages.
