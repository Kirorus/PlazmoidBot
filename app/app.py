from flask import Flask, render_template, request, jsonify, Response
import os
import math
from moviepy.editor import ImageClip, VideoFileClip
import numpy as np
import logging
from skimage.transform import resize
import json
from .config import Config
import time
from concurrent.futures import ThreadPoolExecutor
import subprocess
from contextlib import contextmanager
from threading import Timer, Event, Lock
from flask_cors import CORS

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def check_ffmpeg_version():
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, 
                              text=True)
        logger.info(f"FFMPEG version: {result.stdout.split('\n')[0]}")
    except Exception as e:
        logger.error(f"Error checking FFMPEG: {e}")



class VideoGeneratorApp:
    def __init__(self):
        self.app = Flask(__name__,
                        static_folder=Config.STATIC_FOLDER,
                        template_folder=Config.TEMPLATE_FOLDER)
        CORS(self.app)  # Добавляем поддержку CORS 
        self.UPLOAD_FOLDER = Config.UPLOAD_FOLDER
        self.user_tasks_lock = Lock()
        os.makedirs(self.UPLOAD_FOLDER, exist_ok=True)
        
        # Создаем необходимые директории
        os.makedirs(self.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.STATIC_FOLDER, exist_ok=True)
        os.makedirs(Config.TEMPLATE_FOLDER, exist_ok=True)

        check_ffmpeg_version()
        
        self.overlay_paths = {
            'soft_light': os.path.join(os.path.dirname(self.UPLOAD_FOLDER), 'SIDE_ADDONS_shurehi_soft_light.mov'),
            'screen': os.path.join(os.path.dirname(self.UPLOAD_FOLDER), 'SIDE_ADDONS_shurehi_screen.mov')
        }
        
        self._check_overlay_files()
        
        self.user_tasks = {}
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.setup_routes()

    @contextmanager
    def timeout_threading(self, seconds):
        timeout_event = Event()
        timer = Timer(seconds, timeout_event.set)
        timer.start()
        
        try:
            yield timeout_event
        finally:
            timer.cancel()

    @contextmanager
    def create_clips(self, image_path):
        clips = {}
        try:
            clips['soft_light'] = VideoFileClip(self.overlay_paths['soft_light'])
            clips['screen'] = VideoFileClip(self.overlay_paths['screen'])
            clips['base'] = ImageClip(image_path)
            yield clips
        finally:
            for name, clip in clips.items():
                try:
                    if clip is not None:
                        clip.close()
                        logger.debug(f"Closed clip: {name}")
                except Exception as e:
                    logger.error(f"Error closing clip {name}: {e}")

    def _check_overlay_files(self):
        for path in self.overlay_paths.values():
            if not os.path.exists(path):
                raise FileNotFoundError(f"Overlay file not found: {path}")
            if not os.access(path, os.R_OK):
                raise PermissionError(f"Cannot read overlay file: {path}")

    def run(self):
        try:
            logger.info("Starting Flask application...")
            logger.info(f"Static folder: {Config.STATIC_FOLDER}")
            logger.info(f"Template folder: {Config.TEMPLATE_FOLDER}")
            logger.info(f"Upload folder: {Config.UPLOAD_FOLDER}")
            
            self.app.run(
                host='0.0.0.0',
                port=5000,
                debug=Config.FLASK_DEBUG,
                use_reloader=False
            )
        except Exception as e:
            logger.error(f"Error starting Flask app: {e}")
            raise
    
    

    def setup_routes(self):
        self.app.add_url_rule('/cropper/<chat_id>/<task_id>', 'cropper', self.cropper)
        self.app.add_url_rule('/generate_video', 'generate_video', self.generate_video, methods=['POST'])
        self.app.add_url_rule('/video_progress/<chat_id>', 'video_progress', self.video_progress, methods=['GET'])
        self.app.add_url_rule('/user_tasks/<chat_id>', 'get_user_tasks', self.get_user_tasks, methods=['GET'])

    def adjust_saturation(self, image, saturation_value):
        """Регулировка насыщенности изображения"""
        # Преобразуем значение насыщенности из диапазона [-100, 100] в коэффициент
        adjustment = (saturation_value + 100) / 100

        # Конвертируем в float для вычислений
        img_float = image.astype(float)

        # Вычисляем яркость (grayscale)
        gray = 0.2989 * img_float[..., 0] + 0.5870 * img_float[..., 1] + 0.1140 * img_float[..., 2]
        gray = np.expand_dims(gray, axis=-1)

        # Применяем насыщенность
        adjusted = gray + (img_float - gray) * adjustment
        
        # Возвращаем значения в допустимый диапазон
        return np.clip(adjusted, 0, 255).astype(np.uint8)

    def soft_light_blend(self, base, overlay):
        base = base.astype(float) / 255
        overlay = overlay.astype(float) / 255
        
        overlay = overlay * 1.3
        overlay = np.clip(overlay, 0, 1)
    
        mask = base <= 0.5
        result = np.zeros_like(base)
        result[mask] = 2 * base[mask] * overlay[mask] + base[mask]**2 * (1 - 2 * overlay[mask])
        result[~mask] = 2 * base[~mask] * (1 - overlay[~mask]) + np.sqrt(base[~mask]) * (2 * overlay[~mask] - 1)
    
        result = result * 1.1
        return np.clip(result * 255, 0, 255).astype(np.uint8)

    def screen_blend(self, base, overlay):
        base = base.astype(float) / 255
        overlay = overlay.astype(float) / 255
        
        overlay = overlay * 1.5
        overlay = np.clip(overlay, 0, 1)
        
        result = 1 - (1 - base) * (1 - overlay)
        
        result = result * 1.3
        return np.clip(result * 255, 0, 255).astype(np.uint8)

    def make_frame(self, t, clips, start_frame, end_frame, saturation_value):
        try:
            half_duration = Config.VIDEO_DURATION / 2
            if t <= half_duration:
                factor = t / half_duration
            else:
                factor = 2.0 - (t / half_duration)
    
            factor = -(math.cos(math.pi * factor) - 1) / 2
    
            x = start_frame['x'] + (end_frame['x'] - start_frame['x']) * factor
            y = start_frame['y'] + (end_frame['y'] - start_frame['y']) * factor
            w = start_frame['width'] + (end_frame['width'] - start_frame['width']) * factor
            h = start_frame['height'] + (end_frame['height'] - start_frame['height']) * factor
    
            base_frame = clips['base'].get_frame(0)
            cropped = base_frame[int(y):int(y+h), int(x):int(x+w)]
    
            base_resized = resize(cropped, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 3),
                                preserve_range=True, anti_aliasing=True, mode='reflect')
    
            # Применяем настройку насыщенности
            base_resized = self.adjust_saturation(base_resized.astype(np.uint8), saturation_value)
    
            overlay_frame_1 = clips['soft_light'].get_frame(t % clips['soft_light'].duration)
            overlay_resized_1 = resize(overlay_frame_1, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 4),
                                     preserve_range=True)
    
            overlay_frame_2 = clips['screen'].get_frame(t % clips['screen'].duration)
            overlay_resized_2 = resize(overlay_frame_2, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 4),
                                     preserve_range=True)
    
            base_resized = base_resized.astype(np.uint8)
            overlay_resized_1 = overlay_resized_1.astype(np.uint8)
            overlay_resized_2 = overlay_resized_2.astype(np.uint8)
    
            overlay_rgb_1 = overlay_resized_1[..., :3]
            overlay_alpha_1 = overlay_resized_1[..., 3:] / 255.0
            overlay_alpha_1 = np.clip(overlay_alpha_1 * 1.1, 0, 1)
            
            overlay_rgb_2 = overlay_resized_2[..., :3]
            overlay_alpha_2 = overlay_resized_2[..., 3:] / 255.0
            overlay_alpha_2 = np.clip(overlay_alpha_2 * 1.5, 0, 1)
    
            blended_1 = self.soft_light_blend(base_resized, overlay_rgb_1)
            intermediate_1 = base_resized * (1 - overlay_alpha_1) + blended_1 * overlay_alpha_1
            
            blended_2 = self.screen_blend(intermediate_1.astype(np.uint8), overlay_rgb_2)
            final = intermediate_1 * (1 - overlay_alpha_2) + blended_2 * overlay_alpha_2
    
            return final.astype(np.uint8)
        except Exception as e:
            logger.error(f"Error in make_frame: {e}")
            raise

    def process_video(self, chat_id, task_id, image_path, start_frame, end_frame, saturation_value):
        try:
            with self.create_clips(image_path) as clips:
                def frame_generator(t):
                    progress = int((t / Config.VIDEO_DURATION) * 100)
                    self.update_task_status(chat_id, task_id, 'processing', progress)
                    return self.make_frame(t, clips, start_frame, end_frame, saturation_value)
    
                temp_video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_temp.mp4")
                final_video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_video.mp4")
    
                try:
                    video = VideoFileClip(image_path, audio=False).set_duration(Config.VIDEO_DURATION)
                    video = video.fl(lambda gf, t: frame_generator(t))
                    
                    video.write_videofile(
                        temp_video_path,
                        fps=Config.VIDEO_FPS,
                        codec=Config.VIDEO_CODEC,
                        audio=False,
                        preset=Config.VIDEO_PRESET,
                        threads=Config.VIDEO_THREADS
                    )
                finally:
                    if 'video' in locals():
                        video.close()
    
                if os.path.exists(temp_video_path) and os.path.getsize(temp_video_path) > 0:
                    os.rename(temp_video_path, final_video_path)
                    self.create_completion_flag(chat_id, task_id)
                    self.update_task_status(chat_id, task_id, 'completed', 100)
                else:
                    raise RuntimeError("Failed to create video file")
    
        except Exception as e:
            logger.error(f"Error in process_video for task {task_id}: {e}")
            self.update_task_status(chat_id, task_id, 'error', 0)
            raise

    def generate_video(self):
        chat_id = None
        task_id = None
        try:
            data = request.json
            if not data:
                raise ValueError("No data provided")
                
            chat_id = data.get('chat_id')
            task_id = data.get('task_id')
            start_frame = data.get('startFrame')
            end_frame = data.get('endFrame')
            saturation_value = data.get('saturation', -10)  # Значение по умолчанию -10

            if not all([start_frame, end_frame, chat_id, task_id]):
                raise ValueError("Missing required parameters")

            active_tasks = sum(1 for task in self.user_tasks.get(chat_id, {}).values()
                             if task['status'] in ['pending', 'processing'])
                             
            if active_tasks >= Config.MAX_ACTIVE_TASKS:
                raise ValueError("Too many active tasks. Please wait for some tasks to complete.")

            if self.executor._work_queue.qsize() >= Config.MAX_QUEUE_SIZE:
                raise ValueError("Server is busy. Please wait a moment and try again.")

            if chat_id not in self.user_tasks:
                self.user_tasks[chat_id] = {}
            
            self.user_tasks[chat_id][task_id] = {
                'status': 'pending',
                'progress': 0,
                'created_at': time.time()
            }

            image_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_image.jpg")

            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image not found for chat_id: {chat_id}, task_id: {task_id}")

            self.executor.submit(
                self.process_video,
                chat_id,
                task_id,
                image_path,
                start_frame,
                end_frame,
                saturation_value
            )

            return jsonify({'success': True, 'message': 'Video generation started'})

        except Exception as e:
            logger.error(f"Error generating video: {e}")
            if chat_id and task_id:
                self.update_task_status(chat_id, task_id, 'error', 0)
            return jsonify({'success': False, 'message': str(e)})

    def update_task_status(self, chat_id, task_id, status, progress):
        with self.user_tasks_lock:
            if chat_id in self.user_tasks:
                self.user_tasks[chat_id][task_id].update({
                    'status': status,
                    'progress': progress
                })

    def create_completion_flag(self, chat_id, task_id):
        done_flag_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_video_done.txt")
        with open(done_flag_path, 'w') as f:
            f.write('done')

    def cropper(self, chat_id, task_id):
        try:
            return render_template('cropper.html', chat_id=chat_id, task_id=task_id)
        except Exception as e:
            logger.error(f"Error rendering cropper template: {e}")
            return jsonify({'error': 'Internal server error'}), 500

    def get_user_tasks(self, chat_id):
        tasks = self.user_tasks.get(chat_id, {})
        return jsonify({
            'tasks': [
                {
                    'task_id': task_id,
                    'status': task_info['status'],
                    'progress': task_info['progress'],
                    'created_at': task_info['created_at']
                }
                for task_id, task_info in tasks.items()
            ]
        })

    def video_progress(self, chat_id):
        def generate():
            try:
                while True:
                    tasks = self.user_tasks.get(chat_id, {})
                    yield f"data: {json.dumps({'tasks': [{'task_id': tid, **tinfo} for tid, tinfo in tasks.items()]})}\n\n"
                    
                    if not any(t['status'] in ['pending', 'processing'] for t in tasks.values()):
                        break
                        
                    time.sleep(0.5)
            except Exception as e:
                logger.error(f"Error in progress stream: {e}")
                yield f"data: {json.dumps({'error': True, 'message': str(e)})}\n\n"
        
        return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    app_instance = VideoGeneratorApp()
    app_instance.run()
