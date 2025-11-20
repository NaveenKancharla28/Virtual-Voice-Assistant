# Use a small Python image
FROM python:3.11-slim

# System deps (if you later need any)
RUN apt-get update && apt-get install -y \
    build-essential \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*


# Workdir inside container
WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Fly will send traffic to this port (matches fly.toml internal_port)
ENV PORT=8080

# Expose port (mainly documentation)
EXPOSE 8080

# Start FastAPI app; assumes `app = FastAPI()` lives in setup.py
CMD ["uvicorn", "setup:app", "--host", "0.0.0.0", "--port", "8080"]
