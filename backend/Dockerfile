FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user; pre-create the audio chunk dir it needs to write to
# (matches AUDIO_CHUNK_DIR default and the audio-chunks volume mount).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /tmp/twitch-audio \
    && chown -R appuser:appuser /app /tmp/twitch-audio
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
