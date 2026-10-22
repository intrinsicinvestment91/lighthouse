# Use Python 3.11+ to satisfy project requirements.
FROM python:3.11-slim

WORKDIR /app

# Copy project metadata and lock files before install.
COPY pyproject.toml requirements.txt requirements.lock README.md LICENSE ./
COPY main.py config.py ./
COPY modules ./modules
COPY portal ./portal
COPY seed ./seed

RUN pip install --no-cache-dir -r requirements.lock
RUN pip install --no-cache-dir --no-deps -e .

RUN mkdir -p data/patents data/archives data/nostr

CMD ["python", "main.py"]
