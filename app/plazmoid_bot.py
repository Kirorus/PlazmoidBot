# app/plazmoid_bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import os
import asyncio
import logging
from threading import Thread
from .config import Config
from .app import VideoGeneratorApp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_flask():
    app_instance = VideoGeneratorApp()
    app_instance.run()

class ImageBot:
    def __init__(self, token):
        self.application = ApplicationBuilder().token(token).build()
        self.upload_folder = Config.UPLOAD_FOLDER
        os.makedirs(self.upload_folder, exist_ok=True)
        self.user_states = {}
        self.register_handlers()
        self.BASE_WEBAPP_URL = Config.BASE_WEBAPP_URL
        
        # Запускаем Flask в отдельном потоке
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()


    async def monitor_video_generation(self, chat_id: int, max_wait_time: int = Config.MAX_WAIT_TIME):
        """
        Мониторит процесс генерации видео и отправляет его пользователю, когда оно готово
        """
        video_path = os.path.join(self.upload_folder, f"{chat_id}_video.mp4")
        done_flag_path = os.path.join(self.upload_folder, f"{chat_id}_video_done.txt")
        
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < max_wait_time:
            if os.path.exists(done_flag_path):
                try:
                    await asyncio.sleep(2)  # Небольшая задержка для завершения записи
                    
                    if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                        await self.send_video_to_user(chat_id, video_path)
                        self.cleanup_files(chat_id)
                        return True
                    
                except Exception as e:
                    logger.error(f"Error while sending video: {e}")
                    await self.send_error_message(chat_id)
                    return False
                
            await asyncio.sleep(1)
        
        await self.send_timeout_message(chat_id)
        return False

    async def send_video_to_user(self, chat_id: int, video_path: str):
        """Отправляет видео пользователю"""
        await self.application.bot.send_video(
            chat_id=chat_id,
            video=open(video_path, 'rb'),
            caption="Ваше видео готово! 🎉"
        )

    async def send_error_message(self, chat_id: int):
        """Отправляет сообщение об ошибке"""
        await self.application.bot.send_message(
            chat_id=chat_id,
            text="😕 Произошла ошибка при создании видео. Пожалуйста, попробуйте снова."
        )

    async def send_timeout_message(self, chat_id: int):
        """Отправляет сообщение о превышении времени ожидания"""
        await self.application.bot.send_message(
            chat_id=chat_id,
            text="⏰ Превышено время ожидания создания видео. Пожалуйста, попробуйте снова."
        )

    def cleanup_files(self, chat_id: int):
        """Очищает временные файлы"""
        files_to_remove = [
            f"{chat_id}_video.mp4",
            f"{chat_id}_image.jpg",
            f"{chat_id}_video_done.txt"
        ]
        
        for filename in files_to_remove:
            file_path = os.path.join(self.upload_folder, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Error removing file {filename}: {e}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        keyboard = [
            [InlineKeyboardButton("✨ Создать Плазму", callback_data='create_plasma')],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👋 Привет! Я помогу вам создать эффектную анимацию из вашего изображения.\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()

        if query.data == 'create_plasma':
            self.user_states[query.from_user.id] = 'awaiting_image'
            await query.edit_message_text(
                "📸 Отправьте мне изображение, и я помогу создать из него красивую анимацию."
            )
        elif query.data == 'help':
            await query.edit_message_text(
                "💡 Как пользоваться ботом:\n\n"
                "1. Нажмите 'Создать Плазму'\n"
                "2. Отправьте изображение\n"
                "3. Выберите начальный и конечный кадры\n"
                "4. Дождитесь создания видео\n\n"
                "Для начала работы нажмите /start"
            )

    async def handle_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик получения изображения"""
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id

        if self.user_states.get(user_id) == 'awaiting_image':
            try:
                # Сохраняем изображение
                photo = update.message.photo[-1]
                file = await photo.get_file()
                file_path = os.path.join(self.upload_folder, f"{chat_id}_image.jpg")
                await file.download_to_drive(file_path)

                # Создаем кнопку для веб-приложения
                keyboard = [[
                    InlineKeyboardButton(
                        "🎬 Открыть редактор",
                        web_app=WebAppInfo(url=f"{self.BASE_WEBAPP_URL}/cropper/{chat_id}")
                    )
                ]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    "🎨 Отлично! Теперь нажмите кнопку ниже, чтобы настроить анимацию.\n"
                    "После создания я автоматически отправлю вам видео.",
                    reply_markup=reply_markup
                )

                # Запускаем мониторинг создания видео
                asyncio.create_task(self.monitor_video_generation(chat_id))
                self.user_states[user_id] = None

            except Exception as e:
                logger.error(f"Error handling image: {e}")
                await update.message.reply_text(
                    "😕 Произошла ошибка при обработке изображения. Пожалуйста, попробуйте снова."
                )
        else:
            await update.message.reply_text(
                "Пожалуйста, сначала выберите 'Создать Плазму' в меню.\n"
                "Нажмите /start, чтобы начать."
            )

    def register_handlers(self):
        """Регистрация обработчиков команд и сообщений"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_image))

    def run(self):
        """Запуск бота"""
        logger.info("Bot started")
        self.application.run_polling()

if __name__ == '__main__':
    bot = ImageBot(Config.BOT_TOKEN)
    bot.run()