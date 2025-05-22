# Use an appropriate Python base image
FROM python:3.13-slim

# Set the working directory in the container
WORKDIR /app

# Copy pyproject.toml and poetry.lock files
COPY pyproject.toml poetry.lock* ./

# Copy the client script
COPY src/client/client.py ./src/client/
COPY src/client/__init__.py ./src/client/

# Copy the shared utilities if needed
COPY src/shared ./src/shared

# Install Poetry
RUN pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction --no-ansi

# Set default environment variables
ENV SERVER_URL="ws://server:8000/ws"
ENV STATUS_INTERVAL="10"
ENV RECONNECT_DELAY="5"

# Command to run the client script
CMD ["python", "src/client/client.py"]
