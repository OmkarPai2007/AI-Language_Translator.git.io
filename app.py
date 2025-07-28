from flask import Flask, render_template, request, jsonify, send_from_directory, session
from deep_translator import GoogleTranslator
from gtts import gTTS
import os
import uuid
import json
import time
import atexit

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['UPLOAD_FOLDER'] = 'static/audio'

history_file = 'history.json'
history = []

if os.path.exists(history_file):
    with open(history_file, 'r') as f:
        try:
            history = json.load(f)
        except json.JSONDecodeError:
            history = []

gtts_supported = {
    'af', 'ar', 'bn', 'bs', 'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'eo', 'es',
    'et', 'fi', 'fr', 'gu', 'hi', 'hr', 'hu', 'hy', 'id', 'is', 'it', 'ja', 'jw',
    'km', 'kn', 'ko', 'la', 'lv', 'ml', 'mr', 'ms', 'my', 'ne', 'nl', 'no', 'pl',
    'pt', 'ro', 'ru', 'si', 'sk', 'sq', 'sr', 'su', 'sv', 'sw', 'ta', 'te', 'th',
    'tl', 'tr', 'uk', 'ur', 'vi', 'zh-CN', 'zh-TW'
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/translate', methods=['POST'])
def translate():
    text = request.form.get('text', '').strip()
    language = request.form.get('language', 'en')
    play_audio = request.form.get('playAudio') == 'true'

    if not text:
        return jsonify({'translated': 'Error: No text provided.'})

    try:
        translated = GoogleTranslator(source='auto', target=language).translate(text)
        audio_path = ''

        if play_audio and language in gtts_supported:
            tts = gTTS(text=translated, lang=language)
            filename = f"{uuid.uuid4()}.mp3"
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            tts.save(full_path)
            audio_path = f"/static/audio/{filename}"

        entry = {
            'target_lang': language,
            'original_text': text,
            'translated_text': translated,
            'audio_file': os.path.basename(audio_path) if audio_path else '',
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }

        history.insert(0, entry)
        return jsonify({'translated': translated, 'audio_path': audio_path})

    except Exception as e:
        return jsonify({'translated': f"Error: {str(e)}"})

@app.route('/history')
def show_history():
    selected_lang = request.args.get('lang')
    filtered = [entry for entry in history if entry['target_lang'] == selected_lang] if selected_lang else history
    available_languages = sorted(set(item['target_lang'] for item in history))
    return render_template('history.html', history=filtered, selected_lang=selected_lang, available_languages=available_languages)

@app.route('/static/audio/<filename>')
def get_audio(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@atexit.register
def save_history():
    with open(history_file, 'w') as f:
        json.dump(history, f, indent=4)
    print("History saved to file.")

if __name__ == '__main__':
    app.run(debug=True)
