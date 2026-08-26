# PhenoCleaning — containerised, relaunchable app
# Build:  docker build -t phenocleaning .
# Run:    docker run --rm -p 8501:8501 -v /path/to/your/data:/data:ro phenocleaning
# (or use docker-compose.yml, which also auto-restarts)
FROM python:3.12-slim

WORKDIR /app

# Pinned Python deps (numpy/pandas/scipy/matplotlib ship manylinux wheels — no compiler needed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (pe_data adds ../tools to sys.path, so both dirs must be present)
COPY app/ ./app/
COPY tools/ ./tools/

# Writable output dir for cleaned .ply / exports
RUN mkdir -p /app/out

EXPOSE 8501

# Container-level health so `docker ps` / compose knows when it's ready
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# 0.0.0.0 so the port is reachable from the host; headless (no server-side browser)
CMD ["python", "-m", "streamlit", "run", "app/pe_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
