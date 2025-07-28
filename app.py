from flask import Flask, render_template, request, jsonify, send_from_directory
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

# History file setup
history_file = 'history.json'
history = []

if os.path.exists(history_file):
    with open(history_file, 'r') as f:
        try:
            history = json.load(f)
        except json.JSONDecodeError:
            history = []

# gTTS supported languages
gtts_supported = {
    'af', 'ar', 'bn', 'bs', 'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'eo', 'es',
    'et', 'fi', 'fr', 'gu', 'hi', 'hr', 'hu', 'hy', 'id', 'is', 'it', 'ja', 'jw',
    'km', 'kn', 'ko', 'la', 'lv', 'ml', 'mr', 'ms', 'my', 'ne', 'nl', 'no', 'pl',
    'pt', 'ro', 'ru', 'si', 'sk', 'sq', 'sr', 'su', 'sv', 'sw', 'ta', 'te', 'th',
    'tl', 'tr', 'uk', 'ur', 'vi', 'zh-CN', 'zh-TW'
}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

# === Single Language Translation ===
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
            # Generate next numbered audio file
            existing_files = os.listdir(app.config['UPLOAD_FOLDER'])
            numbers = [int(f.replace("audio", "").replace(".mp3", ""))
                       for f in existing_files if f.startswith("audio") and f.endswith(".mp3") and f.replace("audio", "").replace(".mp3", "").isdigit()]
            next_number = max(numbers, default=0) + 1

            filename = f"audio{next_number}.mp3"
            full_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

            tts = gTTS(text=translated, lang=language)
            tts.save(full_path)
            audio_path = f"/static/audio/{filename}"

        # Save to history
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

# === Multi-language Translation ===
@app.route('/translate-multi', methods=['POST'])
def translate_multi():
    data = request.get_json()
    text = data.get('text', '').strip()
    languages = data.get('languages', [])
    play_audio = data.get('playAudio', False)

    if not text or not languages:
        return jsonify({'error': 'Missing text or languages.'}), 400

    # Determine next audio number
    existing_files = os.listdir(app.config['UPLOAD_FOLDER'])
    numbers = [int(f.replace("audio", "").replace(".mp3", ""))
               for f in existing_files if f.startswith("audio") and f.endswith(".mp3") and f.replace("audio", "").replace(".mp3", "").isdigit()]
    next_number = max(numbers, default=0) + 1

    results = []

    for lang in languages:
        try:
            translated = GoogleTranslator(source='auto', target=lang).translate(text)
            audio_file = ''
            if play_audio and lang in gtts_supported:
                filename = f"audio{next_number}.mp3"
                next_number += 1
                full_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                tts = gTTS(text=translated, lang=lang)
                tts.save(full_path)
                audio_file = filename

            entry = {
                'target_lang': lang,
                'original_text': text,
                'translated_text': translated,
                'audio_file': audio_file,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }

            history.insert(0, entry)

            results.append({
                'language': lang,
                'translated_text': translated,
                'audio_path': f"/static/audio/{audio_file}" if audio_file else None
            })

        except Exception as e:
            results.append({
                'language': lang,
                'translated_text': f"Error: {str(e)}",
                'audio_path': None
            })

    return jsonify({'translations': results})

# === History Page ===
@app.route('/history')
def show_history():
    selected_lang = request.args.get('lang')
    filtered = [entry for entry in history if entry['target_lang'] == selected_lang] if selected_lang else history
    available_languages = sorted(set(item['target_lang'] for item in history))
    return render_template('history.html', history=filtered, selected_lang=selected_lang, available_languages=available_languages)

# === Serve Audio Files ===
@app.route('/static/audio/<filename>')
def get_audio(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# === Save history on exit ===
@atexit.register
def save_history():
    with open(history_file, 'w') as f:
        json.dump(history, f, indent=4)
    print("History saved to file.")

if __name__ == '__main__':
    app.run(debug=True)
