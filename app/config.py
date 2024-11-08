# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram Bot
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    BASE_WEBAPP_URL = os.getenv('BASE_WEBAPP_URL')

    # Paths
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    

    # Flask Settings
    FLASK_DEBUG = True
    
    # Video Generation Settings
    VIDEO_DURATION = 2
    VIDEO_PING_PONG = True
    VIDEO_FPS = 25
    VIDEO_CODEC = 'libx264'
    VIDEO_PRESET = 'ultrafast'
    VIDEO_THREADS = 12
    
    # Monitoring Settings
    MAX_VIDEO_PROCESSING_TIME = 300 # 5 минут
    MAX_WAIT_TIME = 300  # 5 минут
    MONITOR_SLEEP_INTERVAL = 2
    CLEANUP_DELAY = 2
    VIDEO_CHECK_RETRIES = 3
    VIDEO_CHECK_DELAY = 1

    MAX_ACTIVE_TASKS = 8
    MAX_QUEUE_SIZE = 10
    MAX_FILE_AGE = 3600  # 1 час
    # Output Video Dimensions
    VIDEO_WIDTH = 768
    VIDEO_HEIGHT = 1024
    

    
    # Logging
    LOGGING_LEVEL = "DEBUG"
