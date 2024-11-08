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
        self.app = Flask(__name__)
        self.UPLOAD_FOLDER = Config.UPLOAD_FOLDER
        os.makedirs(self.UPLOAD_FOLDER, exist_ok=True)
        
        # Проверка FFMPEG
        check_ffmpeg_version()
        
        # Пути к оверлеям
        self.overlay_paths = {
            'soft_light': os.path.join(os.path.dirname(self.UPLOAD_FOLDER), 'SIDE_ADDONS_shurehi_soft_light.mov'),
            'screen': os.path.join(os.path.dirname(self.UPLOAD_FOLDER), 'SIDE_ADDONS_shurehi_screen.mov')
        }
        
        # Проверка наличия файлов при запуске
        self._check_overlay_files()
        
        # Инициализация компонентов
        self.user_tasks = {}
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.setup_routes()

    def _check_overlay_files(self):
        """Проверка наличия и доступности файлов оверлеев"""
        for path in self.overlay_paths.values():
            if not os.path.exists(path):
                raise FileNotFoundError(f"Overlay file not found: {path}")
            if not os.access(path, os.R_OK):
                raise PermissionError(f"Cannot read overlay file: {path}")

    def run(self):
        self.app.run(
            host='0.0.0.0',
            port=5000,
            debug=Config.FLASK_DEBUG,
            use_reloader=False
        )

    def setup_routes(self):
        self.app.add_url_rule('/cropper/<chat_id>/<task_id>', 'cropper', self.cropper)
        self.app.add_url_rule('/generate_video', 'generate_video', self.generate_video, methods=['POST'])
        self.app.add_url_rule('/video_progress/<chat_id>', 'video_progress', self.video_progress, methods=['GET'])
        self.app.add_url_rule('/user_tasks/<chat_id>', 'get_user_tasks', self.get_user_tasks, methods=['GET'])

    def cropper(self, chat_id, task_id):
        try:
            return render_template('cropper.html', chat_id=chat_id, task_id=task_id)
        except Exception as e:
            logger.error(f"Error rendering cropper template: {e}")
            return jsonify({'error': 'Internal server error'}), 500

    def get_user_tasks(self, chat_id):
        """Получение статуса всех задач пользователя"""
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

    def make_frame(self, t, base_clip, overlay_clip_1, overlay_clip_2, start_frame, end_frame):
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

            base_frame = base_clip.get_frame(0)
            cropped = base_frame[int(y):int(y+h), int(x):int(x+w)]

            base_resized = resize(cropped, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 3),
                                preserve_range=True, anti_aliasing=True, mode='reflect')

            overlay_frame_1 = overlay_clip_1.get_frame(t % overlay_clip_1.duration)
            overlay_resized_1 = resize(overlay_frame_1, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 4), 
                                     preserve_range=True)

            overlay_frame_2 = overlay_clip_2.get_frame(t % overlay_clip_2.duration)
            overlay_resized_2 = resize(overlay_frame_2, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 4), 
                                     preserve_range=True)

            overlay_rgb_1 = overlay_resized_1[..., :3]
            overlay_alpha_1 = overlay_resized_1[..., 3:] / 255.0
            overlay_alpha_1 = np.clip(overlay_alpha_1 * 1.1, 0, 1)
            
            overlay_rgb_2 = overlay_resized_2[..., :3]
            overlay_alpha_2 = overlay_resized_2[..., 3:] / 255.0
            overlay_alpha_2 = np.clip(overlay_alpha_2 * 1.5, 0, 1)

            blended_1 = self.soft_light_blend(base_resized.astype(np.uint8), overlay_rgb_1.astype(np.uint8))
            intermediate_1 = base_resized * (1 - overlay_alpha_1) + blended_1 * overlay_alpha_1
            
            blended_2 = self.screen_blend(intermediate_1.astype(np.uint8), overlay_rgb_2.astype(np.uint8))
            final = intermediate_1 * (1 - overlay_alpha_2) + blended_2 * overlay_alpha_2

            return final.astype(np.uint8)
        except Exception as e:
            logger.error(f"Error in make_frame: {e}")
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

            if not all([start_frame, end_frame, chat_id, task_id]):
                raise ValueError("Missing required parameters")

            # Проверяем количество активных задач пользователя
            active_tasks = sum(1 for task in self.user_tasks.get(chat_id, {}).values()
                             if task['status'] in ['pending', 'processing'])
                             
            if active_tasks >= 8:  # Максимум 8 активных задач
                raise ValueError("Too many active tasks. Please wait for some tasks to complete.")

            # Проверяем, не достигнут ли лимит выполняющихся задач
            if self.executor._work_queue.qsize() >= 10:
                raise ValueError("Server is busy. Please wait a moment and try again.")

            # Инициализация задачи
            if chat_id not in self.user_tasks:
                self.user_tasks[chat_id] = {}
            
            self.user_tasks[chat_id][task_id] = {
                'status': 'processing',
                'progress': 0,
                'created_at': time.time()
            }

            image_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_image.jpg")
            video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_video.mp4")

            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image not found for chat_id: {chat_id}, task_id: {task_id}")

            def process_video():
                overlay_clips = {}
                base_clip = None
                try:
                    logger.info(f"Starting video processing for task {task_id}")
                    
                    # Создаем новые экземпляры клипов для каждой задачи
                    overlay_clips['soft_light'] = VideoFileClip(self.overlay_paths['soft_light'])
                    overlay_clips['screen'] = VideoFileClip(self.overlay_paths['screen'])
                    base_clip = ImageClip(image_path)
                    
                    def frame_generator(t):
                        progress = int((t / Config.VIDEO_DURATION) * 100)
                        self.user_tasks[chat_id][task_id]['progress'] = progress
                        return self.make_frame(t, base_clip, 
                                            overlay_clips['soft_light'], 
                                            overlay_clips['screen'],
                                            start_frame, end_frame)
            
                    video = VideoFileClip(image_path, audio=False).set_duration(Config.VIDEO_DURATION)
                    video = video.fl(lambda gf, t: frame_generator(t))
            
                    # Сначала создаем временный файл
                    temp_video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_temp.mp4")
                    video.write_videofile(
                        temp_video_path,
                        fps=Config.VIDEO_FPS,
                        codec=Config.VIDEO_CODEC,
                        audio=False,
                        preset=Config.VIDEO_PRESET,
                        threads=Config.VIDEO_THREADS
                    )
            
                    # Проверяем временный файл
                    if os.path.exists(temp_video_path) and os.path.getsize(temp_video_path) > 0:
                        # Переименовываем в финальный файл
                        final_video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_video.mp4")
                        os.rename(temp_video_path, final_video_path)
                        
                        # Создаем флаг завершения только после успешного создания видео
                        done_flag_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_{task_id}_video_done.txt")
                        with open(done_flag_path, 'w') as f:
                            f.write('done')
                        
                        self.user_tasks[chat_id][task_id]['status'] = 'completed'
                        self.user_tasks[chat_id][task_id]['progress'] = 100
                        logger.info(f"Video processing completed for task {task_id}")
                    else:
                        raise RuntimeError("Failed to create video file")
            
                except Exception as e:
                    logger.error(f"Error in process_video for chat_id {chat_id}, task_id {task_id}: {e}")
                    self.user_tasks[chat_id][task_id]['status'] = 'error'
                    raise
                finally:
                    # Закрываем все клипы
                    try:
                        if base_clip:
                            base_clip.close()
                        for clip in overlay_clips.values():
                            clip.close()
                    except Exception as e:
                        logger.error(f"Error closing clips: {e}")

            

            self.executor.submit(process_video)
            return jsonify({'success': True, 'message': 'Video generation started'})

        except Exception as e:
            logger.error(f"Error generating video: {e}")
            if chat_id and task_id:
                if chat_id not in self.user_tasks:
                    self.user_tasks[chat_id] = {}
                self.user_tasks[chat_id][task_id] = {
                    'status': 'error',
                    'progress': 0,
                    'created_at': time.time()
                }
            return jsonify({'success': False, 'message': str(e)})

if __name__ == '__main__':
    app_instance = VideoGeneratorApp()
    app_instance.run()
