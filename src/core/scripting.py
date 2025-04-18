# src/core/scripting.py - Helper functions for creating execution scripts
# Updated: Removed workdir parameter and save directly to output_filename

import os

# Removed 'workdir' parameter from function signature
def create_execution_script(user_code: str, output_filename: str) -> str:
    """
    Wraps the user's Python code with necessary boilerplate for execution
    within the sandbox, including saving the plot to the current working directory.
    """
    boilerplate_header = f"""
import matplotlib
matplotlib.use('Agg') # Ensure non-interactive backend is used
import matplotlib.pyplot as plt
import pandas as pd # Make common libraries available
import numpy as np
import sys
import os

# Container's working directory is set by the calling process (docker_runner)
# No need to change directory here or use absolute paths for output

print("--- Starting User Code Execution ---", flush=True)
try:
    # --- User code starts ---
"""
    indented_user_code = "\n".join(["    " + line for line in user_code.strip().splitlines()])
    boilerplate_footer = f"""
    # --- User code ends ---
except Exception as e:
    print(f"Error during user code execution: {{e}}", file=sys.stderr, flush=True)
    sys.exit(1) # Exit with error code if user code fails

print("--- User Code Finished ---", flush=True)

# --- Saving the plot ---
try:
    # Save directly using the filename in the container's current working directory
    output_path = '{output_filename}'
    if plt.get_fignums():
        print(f"Saving plot to {{output_path}}...", flush=True)
        plt.savefig(output_path, format='png', bbox_inches='tight')
        print(f"Plot saved successfully.", flush=True)
    else:
        print("No matplotlib plot detected to save.", file=sys.stderr, flush=True)
        # sys.exit(2) # Optional: Exit if no plot created

except Exception as e:
    print(f"Error saving plot: {{e}}", file=sys.stderr, flush=True)
    sys.exit(3) # Exit with error code if saving fails
finally:
    plt.close('all') # Ensure plot is closed

print("--- Script Finished Successfully ---", flush=True)
sys.exit(0) # Explicitly exit with success code
"""
    return boilerplate_header + indented_user_code + boilerplate_footer
