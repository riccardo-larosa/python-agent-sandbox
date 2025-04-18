# test_agent.py - Example Langchain Agent using the Sandbox API Service

import os
import uuid
import httpx # For making HTTP requests to the API
from typing import Type, Optional, Dict
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain import hub
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.prompts import ChatPromptTemplate
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv() # Load environment variables from .env file (e.g., OPENAI_API_KEY)

# URL of the running FastAPI sandbox service
API_BASE_URL = "http://localhost:8002" # Make sure this matches your running service port

# Generate a unique session ID for this agent run
AGENT_SESSION_ID = f"agent-session-{uuid.uuid4()}"
print(f"Using Session ID: {AGENT_SESSION_ID}")

# --- Tool Definitions ---
# Each tool corresponds to an API endpoint

# Tool Argument Schemas (using Pydantic)
class ShellToolInput(BaseModel):
    command: str = Field(description="The shell command to execute.")
    # environment: Optional[Dict[str, str]] = Field(None, description="Optional environment variables.") # Add later if needed

class PythonScriptToolInput(BaseModel):
    code: str = Field(description="The Python code to execute.")
    # environment: Optional[Dict[str, str]] = Field(None, description="Optional environment variables.") # Add later if needed

class ListFilesToolInput(BaseModel):
    path: str = Field(".", description="Directory path relative to the workspace root. Defaults to '.' (root).")

class ReadFileToolInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")

class WriteFileToolInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root. Parent directories will be created.")
    content: str = Field(description="The content to write to the file.")

class DeleteFileToolInput(BaseModel):
    path: str = Field(description="Path to the file or directory to delete, relative to the workspace root.")

class MakeDirectoryToolInput(BaseModel):
    path: str = Field(description="Directory path to create, relative to the workspace root.")


# Base Tool with common HTTP logic (optional, but good practice)
class SandboxApiTool(BaseTool):
    """Base class for tools interacting with the sandbox API."""
    session_id: str = AGENT_SESSION_ID # Inject session ID automatically
    client: httpx.Client = Field(default_factory=httpx.Client)
    base_url: str = API_BASE_URL

    def _call_api(self, method: str, endpoint: str, **kwargs) -> Dict:
        """Helper to make HTTP requests and handle common errors."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status() # Raise exception for 4xx/5xx errors
            # Handle 204 No Content specifically
            if response.status_code == 204:
                return {"status": "success", "message": "Operation successful (No Content)"}
            return response.json()
        except httpx.RequestError as e:
            return {"error": f"HTTP Request Error: {e}"}
        except httpx.HTTPStatusError as e:
            # Try to get detail from response, fallback to generic message
            detail = "Unknown error"
            try: detail = e.response.json().get("detail", e.response.text)
            except: detail = e.response.text[:200] # Limit error message length
            return {"error": f"API Error: Status {e.response.status_code}, Detail: {detail}"}
        except Exception as e:
            return {"error": f"Unexpected Error: {type(e).__name__} - {e}"}

    def _format_result(self, result: Dict) -> str:
        """Formats the API result dictionary into a string for the LLM."""
        if "error" in result:
            return f"Error: {result['error']}"
        # Customize formatting based on expected successful result structure
        if "stdout" in result and "stderr" in result and "exit_code" in result:
            output = f"Exit Code: {result['exit_code']}\n"
            if result["stdout"]: output += f"STDOUT:\n{result['stdout']}\n"
            if result["stderr"]: output += f"STDERR:\n{result['stderr']}\n"
            return output.strip()
        elif "entries" in result:
            entry_list = "\n".join([f"- {e['name']} ({e['type']})" for e in result['entries']])
            return f"Directory listing for '{result.get('path', '?')}':\n{entry_list if entry_list else '(empty)'}"
        elif "content" in result:
            return f"Content of '{result.get('path', '?')}':\n{result['content']}"
        elif "message" in result: # For 201/204 responses
             return result["message"]
        else:
            import json # Fallback for unexpected success structures
            return f"Success (raw output): {json.dumps(result)}"


# Specific Tool Implementations
class ShellTool(SandboxApiTool):
    name: str = "execute_shell"
    description: str = "Executes a shell command (bash) within the persistent session workspace. Use for file manipulation (ls, rm, mkdir, mv), running git, checking tools, etc."
    args_schema: Type[BaseModel] = ShellToolInput

    def _run(self, command: str) -> str:
        endpoint = "/execute/shell"
        payload = {"session_id": self.session_id, "command": command}
        result = self._call_api("POST", endpoint, json=payload)
        return self._format_result(result)

class PythonScriptTool(SandboxApiTool):
    name: str = "execute_python_script"
    description: str = "Executes a Python script string within the persistent session workspace. Use for calculations, data processing, or complex logic. Receives stdout, stderr, and exit code."
    args_schema: Type[BaseModel] = PythonScriptToolInput

    def _run(self, code: str) -> str:
        endpoint = "/execute/python/script"
        payload = {"session_id": self.session_id, "code": code}
        result = self._call_api("POST", endpoint, json=payload)
        return self._format_result(result)

class ListFilesTool(SandboxApiTool):
    name: str = "list_files"
    description: str = "Lists files and directories at a specified path within the session workspace."
    args_schema: Type[BaseModel] = ListFilesToolInput

    def _run(self, path: str = ".") -> str:
        endpoint = f"/sessions/{self.session_id}/files"
        result = self._call_api("GET", endpoint, params={"path": path})
        return self._format_result(result)

class ReadFileTool(SandboxApiTool):
    name: str = "read_file"
    description: str = "Reads the content of a specified file within the session workspace."
    args_schema: Type[BaseModel] = ReadFileToolInput

    def _run(self, path: str) -> str:
        endpoint = f"/sessions/{self.session_id}/files/content"
        result = self._call_api("GET", endpoint, params={"path": path})
        return self._format_result(result)

class WriteFileTool(SandboxApiTool):
    name: str = "write_file"
    description: str = "Writes (or overwrites) content to a specified file within the session workspace. Creates parent directories if needed."
    args_schema: Type[BaseModel] = WriteFileToolInput

    def _run(self, path: str, content: str) -> str:
        endpoint = f"/sessions/{self.session_id}/files/content"
        result = self._call_api("PUT", endpoint, params={"path": path}, json={"content": content})
        return self._format_result(result) # Will return success/error message

class DeleteFileTool(SandboxApiTool):
    name: str = "delete_file"
    description: str = "Deletes a specified file or directory (recursively) within the session workspace."
    args_schema: Type[BaseModel] = DeleteFileToolInput

    def _run(self, path: str) -> str:
        endpoint = f"/sessions/{self.session_id}/files"
        result = self._call_api("DELETE", endpoint, params={"path": path})
        return self._format_result(result) # Will return success/error message

class MakeDirectoryTool(SandboxApiTool):
    name: str = "make_directory"
    description: str = "Creates a directory (including parent directories) at a specified path within the session workspace."
    args_schema: Type[BaseModel] = MakeDirectoryToolInput

    def _run(self, path: str) -> str:
        endpoint = f"/sessions/{self.session_id}/files/directories"
        result = self._call_api("POST", endpoint, params={"path": path})
        return self._format_result(result) # Will return success/error message


# --- Agent Setup ---

# Instantiate tools
tools = [
    ShellTool(),
    PythonScriptTool(),
    ListFilesTool(),
    ReadFileTool(),
    WriteFileTool(),
    DeleteFileTool(),
    MakeDirectoryTool(),
]

# Choose LLM
# Make sure OPENAI_API_KEY is set in your environment or .env file
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0) # Or "gpt-3.5-turbo", "gpt-4" etc.

# Create Prompt Template
# Using a basic template, can be customized further
# Ensure the prompt clearly states the available tools and their purpose
prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful assistant that can execute code, interact with a file system, "
            "and run shell commands within a secure sandbox environment for a specific session. "
            f"You are operating within session ID: {AGENT_SESSION_ID}. " # Inform agent of its session
            "Use the available tools to fulfill the user's request. "
            "The workspace root directory is '/workspace'."
        ),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"), # Required for agent execution history
    ]
)

# Create Agent
# Using OpenAI Functions agent type as it's generally good with tool use
agent = create_openai_functions_agent(llm, tools, prompt)

# Create Agent Executor
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)


# --- Run Agent ---

if __name__ == "__main__":
    print("Agent initialized. Ready for query.")
    # Example Query:
    # user_query = "List the files in the root of the workspace."
    # user_query = "Create a file named 'hello.txt' in the workspace root with the content 'Hello, Sandbox!' and then read it back."
    # user_query = "Run a python script that prints the first 10 fibonacci numbers."
    user_query = (
        "Create a directory named 'analysis', then write a python script into "
        "'analysis/analyzer.py' that defines a function `count_lines(filepath)` which reads a file "
        "and returns the number of lines. After writing the script, create another file "
        "'analysis/data.txt' with 5 lines of text. Finally, execute a python script that imports "
        "`analyzer` from the 'analysis' directory (make sure python can find it) and uses it to count the lines in 'analysis/data.txt', printing the result."
    )


    print(f"\n--- Running Agent with Query ---\n{user_query}\n-------------------------------\n")

    # Ensure the FastAPI server (src/main.py) is running in a separate terminal before executing this.
    try:
        result = agent_executor.invoke({"input": user_query})
        print("\n--- Agent Result ---")
        print(result.get("output", "No output field found."))
        print("--------------------\n")
    except Exception as e:
        print(f"\n--- Agent Execution Error ---")
        print(f"An error occurred: {e}")
        print("---------------------------\n")

    # You can check the contents of the Docker volume manually after execution:
    # 1. Find volume name: Use the session ID printed at the start, e.g., sandbox_session_agent-session-<uuid>
    #    docker volume ls | grep sandbox_session_agent-session
    # 2. Inspect volume (find mount point on host): docker volume inspect <volume_name>
    # 3. Browse the host mount point (might require root/sudo)

