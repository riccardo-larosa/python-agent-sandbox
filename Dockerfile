# Dockerfile for the Python chart execution sandbox
# Updated: Added 'uv' package installer

# Use a slim Python base image for smaller size
FROM python:3.10-slim

# Set a working directory inside the container
WORKDIR /workspace

# Install common plotting libraries and the 'uv' installer
# Use --no-cache-dir to keep the image size smaller
RUN pip install --no-cache-dir matplotlib seaborn pandas numpy uv

# Set the Matplotlib backend to 'Agg'
# This is a non-interactive backend suitable for generating images in scripts
# without needing a graphical display.
ENV MPLBACKEND=Agg

# Optional: Add a non-root user for better security practices in later versions
# RUN useradd --create-home appuser
# USER appuser

# The entrypoint/command will be specified by the Docker run command from the host