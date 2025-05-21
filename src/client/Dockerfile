# Use an appropriate Python base image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the client script and requirements file
COPY src/client/client.py ./client/
COPY src/client/requirements.txt ./client/

# Copy the shared utilities
# Ensure the target directory structure in the container matches how it's imported
COPY src/shared ./shared

# Install Python dependencies
RUN pip install --no-cache-dir -r client/requirements.txt

# Add the application's root directory to PYTHONPATH to allow imports from shared
ENV PYTHONPATH "${PYTHONPATH}:/app"

# Command to run the client script
CMD ["python", "client/client.py"]
