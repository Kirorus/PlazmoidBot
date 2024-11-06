# Dockerfile
FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Создаем директорию для загрузок
RUN mkdir -p app/static/uploads && chmod 777 app/static/uploads

# Экспортируем порт для Flask
EXPOSE 5000

# Запускаем бота (который также запустит Flask)
CMD ["python", "-m", "app.plazmoid_bot"]
