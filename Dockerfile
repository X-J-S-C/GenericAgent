# GenericAgent Dockerfile
# Multi-stage build for optimal image size

FROM python:3.10-slim AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV GA_LANG=zh

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Build stage: install all dependencies
FROM base AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir --user -e ".[ui]" 2>/dev/null || \
    pip install --no-cache-dir --user requests beautifulsoup4 bottle simple-websocket-server streamlit pywebview

# Final stage: minimal runtime
FROM base AS runtime

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Add local bin to PATH
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 agent && \
    chown -R agent:agent /app
USER agent

# Expose ports
# 8501: Streamlit UI
# 8765: WebSocket server
EXPOSE 8501 8765

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Default command: show help
CMD ["python", "-c", "print('GenericAgent Docker Image'); print('Usage:'); print('  python agentmain.py           - Agent REPL'); print('  streamlit run frontends/stapp.py --server.port 8501 - Streamlit UI'); print('  python ws_server.py          - WebSocket server')"]
