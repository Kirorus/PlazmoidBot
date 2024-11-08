from flask import Flask, render_template, request, jsonify, Response
import os, math
from moviepy.editor import ImageClip, VideoFileClip
import numpy as np
import logging
from skimage.transform import resize
import json
from .config import Config

logging.basicConfig(level=logging.DEBUG)

class VideoGeneratorApp:
    def __init__(self):
        self.app = Flask(__name__)
        self.UPLOAD_FOLDER = Config.UPLOAD_FOLDER
        os.makedirs(self.UPLOAD_FOLDER, exist_ok=True)
        self.setup_routes()

    def run(self):
        self.app.run(
            host='0.0.0.0',
            port=5000,
            debug=Config.FLASK_DEBUG,
            use_reloader=False  # Важно отключить reloader при запуске в потоке
            )

    def setup_routes(self):
        self.app.add_url_rule('/cropper/<chat_id>', 'cropper', self.cropper)
        self.app.add_url_rule('/generate_video', 'generate_video', self.generate_video, methods=['POST'])
        self.app.add_url_rule('/video_progress', 'video_progress', self.video_progress, methods=['GET'])

    def cropper(self, chat_id):
        return render_template('cropper.html', chat_id=chat_id)

    def send_progress_event(self, progress, status):
        return f"data: {json.dumps({'progress': progress, 'status': status})}\n\n"

    def video_progress(self):
        def generate():
            try:
                yield self.send_progress_event(0, "Starting video generation")
            except Exception as e:
                yield f"data: {json.dumps({'error': True, 'message': str(e)})}\n\n"
        return Response(generate(), mimetype='text/event-stream')



    def generate_video(self):
        try:
            data = request.json
            start_frame = data['startFrame']
            end_frame = data['endFrame']
            chat_id = data['chat_id']
    
            image_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_image.jpg")
            overlay_path_1 = os.path.join(os.path.dirname(self.UPLOAD_FOLDER), 'SIDE_ADDONS_shurehi_1.mov')
            overlay_path_2 = os.path.join(os.path.dirname(self.UPLOAD_FOLDER), 'SIDE_ADDONS_shurehi_2.mov')
            video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_video.mp4")
    
            base_clip = ImageClip(image_path)
            overlay_clip_1 = VideoFileClip(overlay_path_1)
            overlay_clip_2 = VideoFileClip(overlay_path_2)
    
            def soft_light_blend(base, overlay):
                base = base.astype(float) / 255
                overlay = overlay.astype(float) / 255
    
                mask = base <= 0.5
                result = np.zeros_like(base)
                result[mask] = 2 * base[mask] * overlay[mask] + base[mask]**2 * (1 - 2 * overlay[mask])
                result[~mask] = 2 * base[~mask] * (1 - overlay[~mask]) + np.sqrt(base[~mask]) * (2 * overlay[~mask] - 1)
    
                return np.clip(result * 255, 0, 255).astype(np.uint8)
    
            def screen_blend(base, overlay):
                # Формула для режима Screen: 1 - (1 - a) * (1 - b)
                base = base.astype(float) / 255
                overlay = overlay.astype(float) / 255
                
                result = 1 - (1 - base) * (1 - overlay)
                return np.clip(result * 255, 0, 255).astype(np.uint8)
    
            def make_frame(t):
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
                
                # Получаем базовый кадр
                base_frame = base_clip.get_frame(0)
                cropped = base_frame[int(y):int(y+h), int(x):int(x+w)]
                base_resized = resize(cropped, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 3), preserve_range=True)
                
                # Получаем первый оверлей (Soft Light)
                overlay_frame_1 = overlay_clip_1.get_frame(t % overlay_clip_1.duration)
                overlay_resized_1 = resize(overlay_frame_1, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 4), preserve_range=True)
                
                # Получаем второй оверлей (Screen)
                overlay_frame_2 = overlay_clip_2.get_frame(t % overlay_clip_2.duration)
                overlay_resized_2 = resize(overlay_frame_2, (Config.VIDEO_HEIGHT, Config.VIDEO_WIDTH, 4), preserve_range=True)
                
                # Разделяем RGB и альфа-каналы для обоих оверлеев
                overlay_rgb_1 = overlay_resized_1[..., :3]
                overlay_alpha_1 = overlay_resized_1[..., 3:] / 255.0
                
                overlay_rgb_2 = overlay_resized_2[..., :3]
                overlay_alpha_2 = overlay_resized_2[..., 3:] / 255.0
                
                # Применяем первый оверлей (Soft Light)
                blended_1 = soft_light_blend(base_resized.astype(np.uint8), overlay_rgb_1.astype(np.uint8))
                intermediate = base_resized * (1 - overlay_alpha_1) + blended_1 * overlay_alpha_1
                
                # Применяем второй оверлей (Screen)
                blended_2 = screen_blend(intermediate.astype(np.uint8), overlay_rgb_2.astype(np.uint8))
                final = intermediate * (1 - overlay_alpha_2) + blended_2 * overlay_alpha_2
                
                return final.astype(np.uint8)
    
            video = VideoFileClip(image_path, audio=False).set_duration(Config.VIDEO_DURATION)
            video = video.fl(lambda gf, t: make_frame(t))
            
            video.write_videofile(
                video_path,
                fps=Config.VIDEO_FPS,
                codec=Config.VIDEO_CODEC,
                audio=False,
                preset=Config.VIDEO_PRESET,
                threads=Config.VIDEO_THREADS
            )
            
            done_flag_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_video_done.txt")
            with open(done_flag_path, 'w') as f:
                f.write('done')
            
            return jsonify({'success': True, 'message': 'Video generated successfully!'})
            
        except Exception as e:
            logging.error(f"Error generating video: {e}")
            return jsonify({'success': False, 'message': str(e)})
    



if __name__ == '__main__':
    app_instance = VideoGeneratorApp()
    app_instance.run()
