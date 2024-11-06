# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram Bot
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    BASE_WEBAPP_URL = os.getenv('BASE_WEBAPP_URL')

    # Paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'app', 'static', 'uploads')

    # Flask Settings
    FLASK_DEBUG = True
    
    # Video Generation Settings
    VIDEO_DURATION = 12
    VIDEO_PING_PONG = True
    VIDEO_FPS = 25
    VIDEO_CODEC = 'libx264'
    VIDEO_PRESET = 'ultrafast'
    VIDEO_THREADS = 4
    
    # Output Video Dimensions
    VIDEO_WIDTH = 768
    VIDEO_HEIGHT = 1024
    
    # Monitoring Settings
    MAX_WAIT_TIME = 300  # seconds
    MONITOR_SLEEP_INTERVAL = 1  # seconds
    
    # Logging
    LOGGING_LEVEL = "DEBUG"
