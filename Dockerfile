FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    fonts-liberation \
    libasound2 \
    libasound2-plugins \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnss3 \
    libpango-1.0-0 \
    libportaudio2 \
    libx11-6 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    portaudio19-dev \
    pulseaudio-utils \
    python3-dev \
    xauth \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel poetry

COPY pyproject.toml poetry.lock* /app/

RUN poetry install --no-interaction --no-ansi --no-root \
    && python -m playwright install chromium

COPY . /app

EXPOSE 8000

CMD ["sh", "-lc", "Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp & export DISPLAY=:99; exec python api_run.py"]
