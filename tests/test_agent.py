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

# --- Configuration ---
load_dotenv()
API_BASE_URL = os.getenv("SANDBOX_API_URL", "http://localhost:8002")
AGENT_SESSION_ID = f"agent-session-{uuid.uuid4()}"
print(f"Using Session ID: {AGENT_SESSION_ID}")
print(f"Connecting to Sandbox API at: {API_BASE_URL}")
if not os.getenv("OPENAI_API_KEY"): print("Error: OPENAI_API_KEY environment variable not set."); exit(1)

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
        except httpx.RequestError as e: return {"error": f"HTTP Request Error: {e}"}
        except httpx.HTTPStatusError as e:
            detail = "Unknown error";
            try: detail = e.response.json().get("detail", e.response.text)
            except: detail = e.response.text[:200]
            return {"error": f"API Error: Status {e.response.status_code}, Detail: {detail}"}
        except json.JSONDecodeError as e: return {"error": f"JSON Decode Error: Failed to parse response from {url}. Response text: {response.text[:200]}"}
        except Exception as e: return {"error": f"Unexpected Error: {type(e).__name__} - {e}"}

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
# (NavigateAndGetContentTool and ScreenshotBrowserTool remain the same as previous version)
class NavigateAndGetContentTool(SandboxApiTool):
    name: str = "navigate_and_get_content"
    description: str = "Navigates the headless browser to a specified URL and returns the full HTML content of the page."
    args_schema: Type[BaseModel] = NavigateAndGetContentInput
    def _run(self, url: str) -> str:
        try: validated_url = str(HttpUrl(url))
        except Exception as e: return f"Error: Invalid URL format - {e}"
        code = f"""
import sys
from playwright.sync_api import sync_playwright
print(f"Navigating to: {validated_url} and getting content...", flush=True)
page_content = ""
final_url = "{validated_url}"
exit_code = 0
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        response = page.goto('{validated_url}', wait_until='domcontentloaded', timeout=45000)
        status = response.status if response else 'unknown'
        final_url = page.url
        print(f"Navigation complete. Status: {{status}}, Final URL: {{final_url}}", flush=True)
        if response and response.ok:
            print("Retrieving page content...", flush=True)
            page_content = page.content()
            print("Content retrieval successful.", flush=True)
        else: print(f"Navigation failed or resulted in non-OK status: {{status}}", file=sys.stderr, flush=True); exit_code = 1
        browser.close()
except Exception as e: print(f"Playwright Error: {{type(e).__name__}} - {{e}}", file=sys.stderr, flush=True); exit_code = 1
finally:
    print("--- CONTENT START ---")
    print(page_content if exit_code == 0 else "Error occurred, content not retrieved.")
    print("--- CONTENT END ---")
    sys.exit(exit_code)
"""
        result = self._execute_python_script(code)
        stdout = result.get('stdout', ''); stderr = result.get('stderr', ''); exit_code_res = result.get('exit_code', -1)
        if exit_code_res == 0:
            start_marker = "--- CONTENT START ---"; end_marker = "--- CONTENT END ---"; start_index = stdout.find(start_marker); end_index = stdout.find(end_marker)
            if start_index != -1 and end_index != -1: content = stdout[start_index + len(start_marker):end_index].strip(); nav_info = "\\n".join(line for line in stdout[:start_index].strip().splitlines() if "Navigating" in line or "Navigation complete" in line); return f"Successfully retrieved content after navigation.\n{nav_info}\nPage Content:\n{content}"
            else: return f"Script executed successfully, but couldn't parse content. Full Stdout:\n{stdout}"
        else: error_info = stderr if stderr else stdout; return f"Failed to navigate or get content.\nExit Code: {exit_code_res}\nOutput:\n{error_info}"

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
tools = [ ShellTool(), PythonScriptTool(), ListFilesTool(), ReadFileTool(), WriteFileTool(), DeleteFileTool(), MakeDirectoryTool(), NavigateAndGetContentTool(), ScreenshotBrowserTool()]
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
prompt = ChatPromptTemplate.from_messages([("system", f"You are a helpful assistant... operating within session ID: {AGENT_SESSION_ID}... Use 'navigate_and_get_content' tool..."), ("human", "{input}"), ("placeholder", "{agent_scratchpad}")])
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

# --- Run Agent ---
if __name__ == "__main__":
    print("Agent initialized. Ready for query.")
    query_fs = "Create a directory 'web_data', then use the browser tool to navigate to example.com and get the content, and finally save that content to 'web_data/example.html'."
    query_screenshot = "Take a screenshot of playwright.dev and save it as 'playwright_home.png'."
    query_navigate_and_get = "Get the HTML content of the page at https://google.com."
    user_query = query_screenshot
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

