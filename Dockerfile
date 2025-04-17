# Use a slim Python base image
FROM python:3.10-slim

# Set a working directory
WORKDIR /app

# Install common plotting libraries
# Use --no-cache-dir to keep the image size smaller
RUN pip install --no-cache-dir matplotlib seaborn pandas numpy

# Use a non-interactive backend for Matplotlib suitable for scripts
ENV MPLBACKEND=Agg

# (Optional) Add a non-root user for better security - more advanced, skip for V1 simplicity if preferred
# RUN useradd --create-home appuser
# USER appuser

# The command to run the script will be provided at runtime by the orchestrator
# CMD ["python", "script.py"] # Example, not strictly needed here
