FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# системные пакеты для yt-dlp/ffmpeg/lxml
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    ffmpeg curl build-essential libxml2-dev libxslt-dev libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# каталоги для кеша и логов
RUN mkdir -p assets/temp logs

# задайте свои переменные в .env или через docker-compose
CMD ["python", "main.py"]