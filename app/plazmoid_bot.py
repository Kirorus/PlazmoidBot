from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import os
import asyncio
import logging
from threading import Thread
from .config import Config
from .app import VideoGeneratorApp
import uuid
import time
from typing import Dict, Optional, Set
from dataclasses import dataclass
from enum import Enum

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = 'pending'
    COMPLETED = 'completed'
    ERROR = 'error'
    TIMEOUT = 'timeout'

@dataclass
class Task:
    status: TaskStatus
    start_time: float

def run_flask():
    app_instance = VideoGeneratorApp()
    app_instance.run()

class ImageBot:
    def __init__(self, token: str):
        try:
            if not token:
                raise ValueError("Bot token is not provided")
            self.application = ApplicationBuilder().token(token).build()
        except Exception as e:
            logger.critical(f"Failed to initialize bot: {e}")
            raise
    
        self.upload_folder = Config.UPLOAD_FOLDER
        self.base_webapp_url = Config.BASE_WEBAPP_URL
        self._ensure_upload_folder()
        
        self.user_states: Dict[int, str] = {}
        self.user_tasks: Dict[int, Dict[str, Task]] = {}
        self.monitoring_tasks: Set[int] = set()
        
        self._start_flask_server()

    def _ensure_upload_folder(self):
        os.makedirs(self.upload_folder, exist_ok=True)

    def _start_flask_server(self):
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()

    def _has_active_tasks(self, chat_id: int) -> bool:
        return chat_id in self.user_tasks and bool(self.user_tasks[chat_id])

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("✨ Создать Боковушку", callback_data='create_plasma')],
            [InlineKeyboardButton("📋 Мои задачи", callback_data='my_tasks')],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👋 Привет! Я ПлазмойдБот.\n"
            "Я помогу создать крутые анимации из ваших изображений!\n"
            "Можете отправлять несколько изображений подряд.",
            reply_markup=reply_markup
        )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        handlers = {
            'create_plasma': self._handle_create_plasma,
            'my_tasks': self._handle_my_tasks,
            'help': self._handle_help
        }

        handler = handlers.get(query.data)
        if handler:
            await handler(query)

    async def _handle_create_plasma(self, query):
        self.user_states[query.from_user.id] = 'awaiting_image'
        await query.edit_message_text(
            "📸 Отправляйте изображения, и я создам из них анимации.\n"
            "Можете отправить несколько изображений подряд.\n"
            "Рекомендуемое разрешение: 1280x720 пикселей."
        )

    async def _handle_my_tasks(self, query):
        # Очистка старых задач перед показом списка
        self.cleanup_old_tasks(query.message.chat_id)
        await self.show_user_tasks(query.message.chat_id)

    async def _handle_help(self, query):
        await query.edit_message_text(
            "💡 Как пользоваться ботом:\n\n"
            "1. Нажмите 'Создать Боковушку'\n"
            "2. Отправляйте изображения\n"
            "3. Настраивайте анимацию для каждого\n"
            "4. Получайте готовые видео\n\n"
            "❗️ Советы:\n"
            "• Можно отправлять несколько изображений подряд\n"
            "• Оптимальное разрешение: 1280x720\n"
            "• Время создания одного видео: 30-60 секунд\n\n"
            "Для начала работы нажмите /start"
        )

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id

        if self.user_states.get(user_id) == 'awaiting_image':
            try:
                task_id = str(uuid.uuid4())
                
                if chat_id not in self.user_tasks:
                    self.user_tasks[chat_id] = {}
                
                self.user_tasks[chat_id][task_id] = Task(
                    status=TaskStatus.PENDING,
                    start_time=time.time()
                )

                photo = update.message.photo[-1]
                file = await photo.get_file()
                file_path = os.path.join(self.upload_folder, f"{chat_id}_{task_id}_image.jpg")
                await file.download_to_drive(file_path)

                await self.create_monitoring_task(chat_id)

                keyboard = [[
                    InlineKeyboardButton(
                        "🎬 Открыть редактор",
                        web_app=WebAppInfo(url=f"{self.base_webapp_url}/cropper/{chat_id}/{task_id}")
                    )
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    "🎨 Изображение получено!\n"
                    "1. Откройте редактор\n"
                    "2. Настройте анимацию\n"
                    "3. Нажмите 'Создать'\n\n"
                    "Можете отправить следующее изображение, не дожидаясь готовности видео.",
                    reply_markup=reply_markup
                )

            except Exception as e:
                logger.error(f"Error handling image: {e}")
                if chat_id in self.user_tasks and task_id in self.user_tasks[chat_id]:
                    self.user_tasks[chat_id][task_id].status = TaskStatus.ERROR
                await update.message.reply_text(
                    "😕 Произошла ошибка при обработке изображения. "
                    "Пожалуйста, попробуйте снова."
                )
        else:
            await update.message.reply_text(
                "Пожалуйста, сначала выберите 'Создать Боковушку' в меню.\n"
                "Нажмите /start, чтобы начать."
            )

    async def send_video_to_user(self, chat_id: int, video_path: str, task_id: str):
        task = self.user_tasks[chat_id].get(task_id)
        if task and task.status == TaskStatus.COMPLETED:
            logger.info(f"Video for task {task_id} already sent, skipping")
            return True

        try:
            file_size = os.path.getsize(video_path)
            MAX_VIDEO_SIZE = 50 * 1024 * 1024

            if file_size <= MAX_VIDEO_SIZE:
                try:
                    with open(video_path, 'rb') as video_file:
                        await self.application.bot.send_video(
                            chat_id=chat_id,
                            video=video_file,
                            caption="✨ Видео готово! 🎉",
                            filename=f"plasma_effect_{task_id[:8]}.mp4",
                            supports_streaming=True
                        )
                    logger.info(f"Sent as video message for task {task_id}")
                    # Очистка старых задач после успешной отправки
                    self.cleanup_old_tasks(chat_id)
                    return True
                except Exception as e:
                    logger.warning(f"Failed to send as video, trying as document: {e}")

            with open(video_path, 'rb') as video_file:
                await self.application.bot.send_document(
                    chat_id=chat_id,
                    document=video_file,
                    caption="✨ Видео готово! 🎉\nФайл отправлен как документ из-за большого размера.",
                    filename=f"plasma_effect_{task_id[:8]}.mp4"
                )
            logger.info(f"Sent as document for task {task_id}")
            # Очистка старых задач после успешной отправки
            self.cleanup_old_tasks(chat_id)
            return True

        except Exception as e:
            logger.error(f"Error sending video/document: {e}")
            return False

    async def create_monitoring_task(self, chat_id: int):
        if chat_id not in self.monitoring_tasks:
            self.monitoring_tasks.add(chat_id)
            asyncio.create_task(self.monitor_user_tasks(chat_id))

    async def monitor_user_tasks(self, chat_id: int):
        try:
            logger.info(f"Starting monitor for chat_id: {chat_id}")
            
            while self._has_active_tasks(chat_id):
                # Очистка старых задач при каждой итерации мониторинга
                self.cleanup_old_tasks(chat_id)
                
                if chat_id not in self.user_tasks or not self.user_tasks[chat_id]:
                    logger.info(f"No tasks found for chat_id: {chat_id}")
                    break

                tasks_to_monitor = {
                    task_id: task 
                    for task_id, task in self.user_tasks[chat_id].items()
                    if task.status == TaskStatus.PENDING
                }

                if not tasks_to_monitor:
                    logger.info(f"No pending tasks for chat_id: {chat_id}")
                    break

                for task_id, task in tasks_to_monitor.items():
                    video_path = os.path.join(self.upload_folder, f"{chat_id}_{task_id}_video.mp4")
                    done_flag_path = os.path.join(self.upload_folder, f"{chat_id}_{task_id}_video_done.txt")

                    if os.path.exists(video_path) and os.path.exists(done_flag_path):
                        try:
                            if await self.send_video_to_user(chat_id, video_path, task_id):
                                task.status = TaskStatus.COMPLETED
                                logger.info(f"Video processed successfully for task {task_id}")
                            else:
                                task.status = TaskStatus.ERROR
                                await self.send_error_message(chat_id, task_id)
                        except Exception as e:
                            logger.error(f"Error processing task {task_id}: {e}")
                            task.status = TaskStatus.ERROR
                            await self.send_error_message(chat_id, task_id)
                        finally:
                            await self.cleanup_task_files(chat_id, task_id)
                    
                    elif time.time() - task.start_time > Config.MAX_WAIT_TIME:
                        logger.warning(f"Task {task_id} timed out")
                        task.status = TaskStatus.TIMEOUT
                        await self.send_timeout_message(chat_id, task_id)
                        await self.cleanup_task_files(chat_id, task_id)

                await asyncio.sleep(5)
        finally:
            self.monitoring_tasks.discard(chat_id)
            await self.cleanup_task_files(chat_id)

    def cleanup_old_tasks(self, chat_id: int, max_tasks: int = 10):
        """
        Очистка старых задач с учетом их статуса и времени создания
        
        Args:
            chat_id (int): ID чата пользователя
            max_tasks (int): Максимальное количество сохраняемых завершенных задач
        """
        if chat_id not in self.user_tasks:
            return

        tasks = self.user_tasks[chat_id]
        completed_tasks = {
            tid: task for tid, task in tasks.items()
            if task.status in [TaskStatus.COMPLETED, TaskStatus.TIMEOUT, TaskStatus.ERROR]
        }

        if len(completed_tasks) > max_tasks:
            sorted_tasks = sorted(
                completed_tasks.items(),
                key=lambda x: x[1].start_time
            )

            for task_id, _ in sorted_tasks[:-max_tasks]:
                del self.user_tasks[chat_id][task_id]
                asyncio.create_task(self.cleanup_task_files(chat_id, task_id))
                logger.debug(f"Removed old task {task_id} for chat {chat_id}")

    async def cleanup_task_files(self, chat_id: int, task_id: str):
        files_to_remove = [
            f"{chat_id}_{task_id}_video.mp4",
            f"{chat_id}_{task_id}_image.jpg",
            f"{chat_id}_{task_id}_video_done.txt"
        ]
        
        for filename in files_to_remove:
            file_path = os.path.join(self.upload_folder, filename)
            try:
                if os.path.exists(file_path):
                    await asyncio.sleep(0.5)
                    try:
                        os.remove(file_path)
                        logger.debug(f"Removed file: {filename}")
                    except PermissionError:
                        logger.warning(f"Permission denied while removing file: {filename}")
                        await asyncio.sleep(1)
                        os.remove(file_path)
            except FileNotFoundError:
                logger.debug(f"File not found (already removed): {filename}")
            except Exception as e:
                logger.error(f"Error removing file {filename}: {e}")

    async def show_user_tasks(self, chat_id: int):
        if not self._has_active_tasks(chat_id):
            await self.application.bot.send_message(
                chat_id=chat_id,
                text="У вас пока нет активных задач."
            )
            return

        tasks_text = "📋 Ваши задачи:\n\n"
        for task_id, task in self.user_tasks[chat_id].items():
            status_emoji = {
                TaskStatus.PENDING: '⏳',
                TaskStatus.COMPLETED: '✅',
                TaskStatus.ERROR: '❌',
                TaskStatus.TIMEOUT: '⏰'
            }.get(task.status, '❓')
            tasks_text += f"{status_emoji} Задача #{task_id[:8]}: {task.status.value}\n"

        

    async def send_error_message(self, chat_id: int, task_id: str = None):
        """Отправка сообщения об ошибке"""
        message = "😕 Произошла ошибка при создании видео."
        if task_id:
            message += f"\nЗадача #{task_id[:8]}"
        await self.application.bot.send_message(chat_id=chat_id, text=message)

    async def send_timeout_message(self, chat_id: int, task_id: str = None):
        """Отправка сообщения о таймауте"""
        message = "⏰ Превышено время ожидания создания видео."
        if task_id:
            message += f"\nЗадача #{task_id[:8]}"
        await self.application.bot.send_message(chat_id=chat_id, text=message)

    async def debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отладочная команда"""
        chat_id = update.message.chat_id
        debug_info = f"Chat ID: {chat_id}\n\n"
        
        if chat_id in self.user_tasks:
            debug_info += "Tasks:\n"
            for task_id, task in self.user_tasks[chat_id].items():
                video_path = os.path.join(self.upload_folder, f"{chat_id}_{task_id}_video.mp4")
                done_flag_path = os.path.join(self.upload_folder, f"{chat_id}_{task_id}_video_done.txt")
                
                debug_info += f"\nTask {task_id}:\n"
                debug_info += f"Status: {task.status.value}\n"
                debug_info += f"Video exists: {os.path.exists(video_path)}\n"
                debug_info += f"Done flag exists: {os.path.exists(done_flag_path)}\n"
        else:
            debug_info += "No tasks found"
            
        await update.message.reply_text(debug_info)

    def register_handlers(self):
        """Регистрация обработчиков команд"""
        handlers = [
            CommandHandler("start", self.start),
            CommandHandler("debug", self.debug),
            CallbackQueryHandler(self.button_callback),
            MessageHandler(filters.PHOTO, self.handle_image)
        ]
        for handler in handlers:
            self.application.add_handler(handler)

    def run(self):
        """Запуск бота"""
        self.register_handlers()
        logger.info("Bot started successfully")
        self.application.run_polling()


if __name__ == '__main__':
    bot = ImageBot(Config.BOT_TOKEN)
    bot.run()
