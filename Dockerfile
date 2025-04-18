# Dockerfile for the Python execution sandbox
# Updated: Added Playwright and browser dependencies

# Use a slim Python base image
FROM python:3.10-slim

# Set environment variable to skip interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Set a working directory inside the container
WORKDIR /workspace

# Install necessary system packages for Playwright browser dependencies first
# See: https://playwright.dev/docs/docker
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Browsers dependencies
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libasound2 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libxshmfence1 \
    libxtst6 \
    # Other dependencies
    xvfb \
    # Clean up apt cache
    && rm -rf /var/lib/apt/lists/*

# Install Python libraries: plotting libs, uv, and playwright
# Use --no-cache-dir to keep the image size smaller
RUN pip install --no-cache-dir \
    matplotlib \
    seaborn \
    pandas \
    numpy \
    uv \
    playwright==1.40.* # Pin playwright version for consistency if needed

# Install Playwright browsers (e.g., Chromium) and their OS dependencies
# Using --with-deps helps ensure all necessary OS packages are present
RUN playwright install --with-deps chromium
# Alternatively, run install-deps separately if needed:
# RUN playwright install-deps chromium
# RUN playwright install chromium

# Set the Matplotlib backend to 'Agg' for non-interactive plotting
ENV MPLBACKEND=Agg

# Reset to default frontend after installs
ENV DEBIAN_FRONTEND=dialog

# Optional: Add a non-root user for better security practices
# RUN useradd --create-home --shell /bin/bash appuser
# USER appuser
# If using non-root user, ensure permissions are correct for /workspace and /home/appuser/.local

# The entrypoint/command will be specified by the Docker run command from the host
