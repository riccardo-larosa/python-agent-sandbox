# test_agent.py - Example Langchain Agent using the Sandbox API Service
# Updated: Corrected standard tool formatting AND includes screenshot existence check

import os
import uuid
import httpx
import json
from typing import Type, Optional, Dict, Any
from pydantic import BaseModel, Field, HttpUrl
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langchain import hub
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.prompts import ChatPromptTemplate
from dotenv import load_dotenv
import sys
import asyncio
# from browser_use import Agent, Browser, BrowserConfig

# --- Configuration ---
load_dotenv()
API_BASE_URL = os.getenv("SANDBOX_API_URL", "http://localhost:8002")
AGENT_SESSION_ID = f"agent-session-{uuid.uuid4()}"
print(f"Using Session ID: {AGENT_SESSION_ID}")
print(f"Connecting to Sandbox API at: {API_BASE_URL}")
if not os.getenv("OPENAI_API_KEY"): 
    print("Error: OPENAI_API_KEY environment variable not set.")
    exit(1)

# --- Tool Definitions ---

# Tool Argument Schemas
class ShellToolInput(BaseModel): command: str = Field(description="The shell command to execute.")
class PythonScriptToolInput(BaseModel): code: str = Field(description="The Python code to execute.")
class ListFilesToolInput(BaseModel): path: str = Field(".", description="Directory path relative to the workspace root. Defaults to '.' (root).")
class ReadFileToolInput(BaseModel): path: str = Field(description="File path relative to the workspace root.")
class WriteFileToolInput(BaseModel): path: str = Field(description="File path relative to the workspace root. Parent directories will be created."); content: str = Field(description="The content to write to the file.")
class DeleteFileToolInput(BaseModel): path: str = Field(description="Path to the file or directory to delete, relative to the workspace root.")
class MakeDirectoryToolInput(BaseModel): path: str = Field(description="Directory path to create, relative to the workspace root.")
class NavigateAndGetContentInput(BaseModel): url: str = Field(description="The URL to navigate the browser to and retrieve content from.")
class ScreenshotToolInput(BaseModel): path: str = Field(description="File path relative to the workspace root where the PNG screenshot should be saved."); url: Optional[str] = Field(None, description="Optional URL to navigate to before taking the screenshot.")


# Base Tool
class SandboxApiTool(BaseTool):
    session_id: str = AGENT_SESSION_ID
    base_url: str = API_BASE_URL
    def _call_api(self, method: str, endpoint: str, **kwargs) -> Dict:
        url = f"{self.base_url}{endpoint}"
        try:
            with httpx.Client(timeout=90.0) as client:
                 response = client.request(method, url, **kwargs)
                 print(f"DEBUG: API Call {method} {url} -> Status {response.status_code}")
                 response.raise_for_status()
                 if response.status_code == 204: return {"status": "success", "message": "Operation successful (No Content)"}
                 if not response.content: return {"status": "success", "message": "Operation successful (Empty Response)"}
                 return response.json()
        except httpx.RequestError as e: 
            return {"error": f"HTTP Request Error: {e}"}
        except httpx.HTTPStatusError as e:
            detail = "Unknown error"
            try: 
                detail = e.response.json().get("detail", e.response.text)
                print(f"DEBUG: API Error Detail: {detail}")
            except: 
                detail = e.response.text[:200]
            return {"error": f"API Error: Status {e.response.status_code}, Detail: {detail}"}
        except json.JSONDecodeError as e: 
            return {"error": f"JSON Decode Error: Failed to parse response from {url}. Response text: {response.text[:200]}"}
        except Exception as e: 
            return {"error": f"Unexpected Error: {type(e).__name__} - {e}"}

    def _format_result(self, result: Dict) -> str:
        if "error" in result: return f"Error: {result['error']}"
        if "stdout" in result and "stderr" in result and "exit_code" in result:
            output = f"Exit Code: {result['exit_code']}\n"
            if result["exit_code"] != 0 and result["stderr"]: output += f"STDERR:\n{result['stderr']}\n"
            if result["stdout"]: output += f"STDOUT:\n{result['stdout']}\n"
            if result["exit_code"] == 0 and result["stderr"]: output += f"STDERR (Warnings):\n{result['stderr']}\n"
            return output.strip()
        elif "entries" in result: entry_list = "\n".join([f"- {e['name']} ({e['type']})" for e in result['entries']]); return f"Directory listing for '{result.get('path', '?')}':\n{entry_list if entry_list else '(empty)'}"
        elif "content" in result: return f"Content of '{result.get('path', '?')}':\n{result['content']}"
        elif "message" in result: return result["message"]
        else: return f"Success (raw output): {json.dumps(result)}"

    def _execute_python_script(self, code: str) -> Dict:
        endpoint = "/execute/python/script"
        payload = {"session_id": self.session_id, "code": code}
        return self._call_api("POST", endpoint, json=payload)


# --- Standard Tools (Reformatted) ---

class ShellTool(SandboxApiTool):
    name: str = "execute_shell"
    description: str = "Executes a shell command (bash) within the persistent session workspace. Use for file manipulation (ls, rm, mkdir, mv), running git, checking tools, installing packages (pip/uv), etc."
    args_schema: Type[BaseModel] = ShellToolInput

    def _run(self, command: str) -> str:
        payload = {"session_id": self.session_id, "command": command}
        result = self._call_api("POST", "/execute/shell", json=payload)
        return self._format_result(result)

class PythonScriptTool(SandboxApiTool):
    name: str = "execute_python_script"
    description: str = "Executes a Python script string within the persistent session workspace. Use for calculations, data processing, or complex logic not easily done with shell commands. Receives stdout, stderr, and exit code."
    args_schema: Type[BaseModel] = PythonScriptToolInput

    def _run(self, code: str) -> str:
        # Use internal helper which already formats payload
        result = self._execute_python_script(code)
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
        return self._format_result(result)

class DeleteFileTool(SandboxApiTool):
    name: str = "delete_file"
    description: str = "Deletes a specified file or directory (recursively) within the session workspace."
    args_schema: Type[BaseModel] = DeleteFileToolInput

    def _run(self, path: str) -> str:
        endpoint = f"/sessions/{self.session_id}/files"
        result = self._call_api("DELETE", endpoint, params={"path": path})
        return self._format_result(result)

class MakeDirectoryTool(SandboxApiTool):
    name: str = "make_directory"
    description: str = "Creates a directory (including parent directories) at a specified path within the session workspace."
    args_schema: Type[BaseModel] = MakeDirectoryToolInput

    def _run(self, path: str) -> str:
        endpoint = f"/sessions/{self.session_id}/files/directories"
        result = self._call_api("POST", endpoint, params={"path": path})
        return self._format_result(result)


# --- Browser Tools ---

# Input Schema for the new BrowserTool
class BrowserToolInput(BaseModel):
    task: str = Field(description="The high-level browser task to perform, described in natural language (e.g., 'Find flights on kayak.com from Zurich to Beijing', 'Get the top 3 headlines from bbc.com/news').")
    # Note: We could add target_url as an optional param if needed later

# Tool using browser-use library
class BrowserTool(SandboxApiTool):
    name: str = "browser_tool"
    description: str = (
        "Handles complex, multi-step browser automation tasks described in natural language. "
        "Uses an AI agent internally to interact with web pages (navigate, click, type, extract info). "
        "Ideal for tasks like form filling, complex searches, data extraction across pages, etc. "
        "Requires OPENAI_API_KEY to be set in the environment for its internal agent."
    )
    args_schema: Type[BaseModel] = BrowserToolInput

    def _run(self, task: str) -> str:
        # Ensure OPENAI_API_KEY is available to the script
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            return "Error: OPENAI_API_KEY environment variable is not set. The BrowserTool requires it for its internal operation."

        # Construct the Python script to be executed in the sandbox
        # It uses browser-use's Agent with ChatOpenAI
        code = f"""
import sys
import os
import asyncio
from browser_use import Agent, Browser, BrowserConfig
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# Load environment variables within the script's context if needed
# (though ideally set in the sandbox environment itself)
load_dotenv()

# Ensure API key is available inside the script execution
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("Error: OPENAI_API_KEY not found within the execution environment.", file=sys.stderr)
    sys.exit(1)

async def run_browser_task():
    print(f"Starting browser task: {task!r}", flush=True)
    agent_output = ""
    try:
        # Configure the browser
        browser_config = BrowserConfig(
            headless=True,
            extra_browser_args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        browser = Browser(config=browser_config)

        # Using a capable model like gpt-4o is recommended for browser-use
        # Ensure the model name matches what's available/intended
        llm = ChatOpenAI(model="gpt-4o", temperature=0, openai_api_key=api_key)

        # Pass the configured browser instance to the Agent
        agent = Agent(
            task="{task}",
            llm=llm,
            browser=browser # Use the configured browser
        )
        agent_output = await agent.run()
        print("\\\\n--- Browser Agent Run Complete ---", flush=True)
        if isinstance(agent_output, dict):
             # Process structured output if necessary, or just serialize
             import json
             print(json.dumps(agent_output, indent=2))
        elif agent_output:
             print(str(agent_output))
        else:
             print("Browser agent finished with no specific return value.")

        # --- Force exit immediately after successful agent run ---
        print("Task successful, attempting immediate exit (os._exit(0)).", flush=True)
        os._exit(0) # Exit immediately without cleanup
        # --- End Force Exit ---

    except ImportError as e:
        print(f"Import Error: {{e}}. Make sure 'browser-use' and its dependencies are installed in the environment.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Error during browser task execution: {{type(e).__name__}} - {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(3)
    finally:
        # Ensure browser is closed even if errors occurred (if browser object exists)
        if 'browser' in locals() and browser:
            try:
                print("Attempting to close browser explicitly...", flush=True)
                await browser.close()
                print("Browser closed successfully.", flush=True)
            except Exception as close_err:
                print(f"Error closing browser: {{type(close_err).__name__}} - {{close_err}}", file=sys.stderr)

# Run the async function
exit_code = 0
try:
    asyncio.run(run_browser_task())
    print("Browser task completed successfully.", flush=True)
except SystemExit as e:
    exit_code = e.code # Capture exit code from within run_browser_task
except Exception as e:
    print(f"Unhandled exception running asyncio task: {{type(e).__name__}} - {{e}}", file=sys.stderr)
    exit_code = 4 # Assign a generic error code
finally:
    # Ensure the script exits with the determined code
    print(f"Script finished. Exiting with code: {{exit_code}}", flush=True)
    sys.exit(exit_code)


"""
        # Execute the script using the base tool's helper method
        result = self._execute_python_script(code)

        # Format the result, prioritizing clarity for the main agent
        stdout = result.get('stdout', '')
        stderr = result.get('stderr', '')
        exit_code_res = result.get('exit_code', -1)

        output = f"BrowserTool Execution Result (Exit Code: {exit_code_res})\n"
        if stdout:
            output += f"--- STDOUT ---\n{stdout}\n--------------\n"
        if stderr:
            output += f"--- STDERR ---\n{stderr}\n--------------\n"
        if exit_code_res != 0:
             output += f"Task likely failed or encountered errors (Exit Code: {exit_code_res}). Check STDERR."
        elif not stdout and not stderr:
             output += "Task completed with no output."


        # Potentially summarize or extract key info from stdout if needed,
        # but for now, returning the full output gives the agent max context.
        return output.strip()


class ScreenshotBrowserTool(SandboxApiTool):
    name: str = "screenshot_browser"
    description: str = "Navigates to an optional URL, then takes a screenshot of the browser page and saves it to the specified path in the session workspace."
    args_schema: Type[BaseModel] = ScreenshotToolInput
    def _run(self, path: str, url: Optional[str] = None) -> str:
        navigate_logic = "";
        if url:
             try: validated_url = str(HttpUrl(url)); navigate_logic = f"""
        print(f"Navigating to {validated_url} for screenshot.", flush=True)
        response = page.goto('{validated_url}', wait_until='domcontentloaded', timeout=30000)
        print(f"Navigation status: {{response.status if response else 'unknown'}}", flush=True)"""
             except Exception as e: return f"Error: Invalid URL format - {e}"
        else: navigate_logic = "        pass # No navigation requested"
        code = f"""
import sys
import os
from playwright.sync_api import sync_playwright
workspace = os.environ.get("WORKSPACE", "/workspace")
if ".." in "{path}" or "{path}".startswith("/"): print(f"Error: Invalid screenshot path format: '{path}'", file=sys.stderr); sys.exit(2)
save_path = os.path.abspath(os.path.join(workspace, "{path}"))
if not save_path.startswith(workspace): print(f"Error: Invalid screenshot path '{path}' attempts to escape workspace.", file=sys.stderr); sys.exit(2)
parent_dir = os.path.dirname(save_path)
if not os.path.exists(parent_dir):
    try: os.makedirs(parent_dir, exist_ok=True); print(f"Created directory {{parent_dir}}", flush=True)
    except OSError as e: print(f"Error creating directory {{parent_dir}}: {{e}}", file=sys.stderr); sys.exit(4)
print(f"Taking screenshot and saving to: {{save_path}}", flush=True)
screenshot_success = False; exit_code = 0
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
{navigate_logic}
        page.screenshot(path=save_path, full_page=True)
        print(f"Screenshot command executed.", flush=True)
        if os.path.exists(save_path): print(f"Verified screenshot file exists at {{save_path}}", flush=True); screenshot_success = True
        else: print(f"Error: Screenshot file NOT found at {{save_path}} after command.", file=sys.stderr, flush=True); exit_code = 5
        browser.close()
except Exception as e: print(f"Playwright Error: {{type(e).__name__}} - {{e}}", file=sys.stderr, flush=True); exit_code = 1
finally:
    if exit_code == 0 and not screenshot_success: exit_code = 5
    sys.exit(exit_code)
"""
        result = self._execute_python_script(code)
        return self._format_result(result)


# --- Agent Setup ---
# Removed NavigateAndGetContentTool, added BrowserTool
tools = [
    ShellTool(),
    PythonScriptTool(),
    ListFilesTool(),
    ReadFileTool(),
    WriteFileTool(),
    DeleteFileTool(),
    MakeDirectoryTool(),
    BrowserTool(), # New high-level browser tool
    ScreenshotBrowserTool() # Keep screenshot tool for specific screenshot tasks
]
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
# Updated system prompt slightly
prompt = ChatPromptTemplate.from_messages([
    ("system", f"You are a helpful assistant operating within session ID: {AGENT_SESSION_ID}. Use 'browser_tool' for complex web interactions described in natural language. Use other tools for file operations, execution, etc."),
    ("human", "{input}"),
    ("placeholder", "{agent_scratchpad}")
])
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

# --- Run Agent ---
if __name__ == "__main__":
    print("Agent initialized. Ready for query.")
    query_fs = "Create a directory 'web_data', then use the browser tool to navigate to example.com and get the content, and finally save that content to 'web_data/example.html'."
    query_screenshot = "Take a screenshot of playwright.dev and save it as 'playwright_home.png'."
    query_browser_use = "find authentication api at elasticpath.dev" #find flights from Zurich (ZRH) to Boston (BOS) for tomorrow."

    user_query = query_browser_use
    print(f"\n--- Running Agent with Query ---\n{user_query}\n-------------------------------\n")
    try:
        result = agent_executor.invoke({"input": user_query})
        print("\n--- Agent Result ---"); print(result.get("output", "No output field found.")); 
        print("--------------------\n")
    except Exception as e:
        print("\n--- Agent Execution Error ---"); 
        print(f"An error occurred: {type(e).__name__} - {e}"); 
        import traceback; 
        traceback.print_exc(); 
        print("---------------------------\n")

