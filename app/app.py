from flask import Flask, render_template, request, jsonify, Response
import os
from moviepy.editor import ImageClip, VideoFileClip
import numpy as np
import logging
from skimage.transform import resize
import json
from config import Config

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
            logging.debug(f"Looking for image at: {image_path}")
            logging.debug(f"Current working directory: {os.getcwd()}")

            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image file not found: {image_path}")
            video_path = os.path.join(self.UPLOAD_FOLDER, f"{chat_id}_video.mp4")

            base_clip = ImageClip(image_path)
            
            def make_frame(t):
                factor = t / 10.0
                x = start_frame['x'] + (end_frame['x'] - start_frame['x']) * factor
                y = start_frame['y'] + (end_frame['y'] - start_frame['y']) * factor
                w = start_frame['width'] + (end_frame['width'] - start_frame['width']) * factor
                h = start_frame['height'] + (end_frame['height'] - start_frame['height']) * factor
                frame = base_clip.get_frame(0)
                cropped = frame[int(y):int(y+h), int(x):int(x+w)]
                resized = resize(cropped, (1024, 768, 3), preserve_range=True)
                return resized.astype(np.uint8)

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
