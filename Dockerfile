FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for compiling extensions and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specifications first to leverage Docker layer caching
COPY pyproject.toml .

# Install dependencies into container
RUN pip install --no-cache-dir -e .

# Copy full application codebase, configurations, and corpus
COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
