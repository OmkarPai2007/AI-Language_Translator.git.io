from flask import Flask, render_template, request, jsonify, send_from_directory, session
from deep_translator import GoogleTranslator
from gtts import gTTS
import os
import uuid
import json
import time

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['UPLOAD_FOLDER'] = 'static/audio'

history_file = 'history.json'
allowed_languages = {'en', 'hi', 'mr', 'kn', 'gu', 'ml', 'de', 'fr'}

gtts_supported = {
    'af', 'ar', 'bn', 'bs', 'ca', 'cs', 'cy', 'da', 'de', 'el', 'en', 'eo', 'es',
    'et', 'fi', 'fr', 'gu', 'hi', 'hr', 'hu', 'hy', 'id', 'is', 'it', 'ja', 'jw',
    'km', 'kn', 'ko', 'la', 'lv', 'ml', 'mr', 'ms', 'my', 'ne', 'nl', 'no', 'pl',
    'pt', 'ro', 'ru', 'si', 'sk', 'sq', 'sr', 'su', 'sv', 'sw', 'ta', 'te', 'th',
    'tl', 'tr', 'uk', 'ur', 'vi', 'zh-CN', 'zh-TW'
}


@app.route('/')
def index():
    session.setdefault('history', [])
    return render_template('index.html')


@app.route('/translate', methods=['POST'])
def translate():
    if 'history' not in session:
        session['history'] = []

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

        # ✅ Store in session['history'] in proper format
        session['history'].insert(0, {
            'target_lang': language,
            'original_text': text,
            'translated_text': translated,
            'audio_file': os.path.basename(audio_path) if audio_path else '',
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        })

        return jsonify({'translated': translated, 'audio_path': audio_path})

    except Exception as e:
        return jsonify({'translated': f"Error: {str(e)}"})


@app.route('/translate-multi', methods=['POST'])
def translate_multi():
    global history
    if 'history' not in session:    
        session['history'] = [] 
    data = request.get_json()
    text = data.get('text', '').strip()
    selected_languages = data.get('languages', [])

    if not text or not selected_languages:
        return jsonify({'error': 'No text or languages provided.'}), 400

    results = []
    session_history = session.get('history', [])

    try:
        for lang in selected_languages:
            translated = GoogleTranslator(source='auto', target=lang).translate(text)

            # 🔊 Generate audio
            tts = gTTS(text=translated, lang=lang)
            filename = f"{uuid.uuid4()}.mp3"
            audio_path = os.path.join("static/audio", filename)
            tts.save(audio_path)

            results.append({
                'language': lang,
                'translated_text': translated,
                'audio_file': filename
            })

            # 📜 Save each to session history
            session_history.insert(0, {
                'target_lang': lang,
                'original_text': text,
                'translated_text': translated,
                'audio_file': filename
            })

        session['history'] = session_history

        # 🧠 Add global history entry
        history.append({
            "type": "multi",
            "original": text,
            "translations": results,
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        })

        return jsonify({
            'original': text,
            'results': results
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/history')
def show_history():
    selected_lang = request.args.get('lang')
    all_history = session.get('history', [])

    # Filter by language if selected
    if selected_lang:
        filtered = [entry for entry in all_history if entry['target_lang'] == selected_lang]
    else:
        filtered = all_history

    # Get available languages for dropdown
    available_languages = sorted(set(item['target_lang'] for item in all_history))

    return render_template('history.html',
                           history=filtered,
                           selected_lang=selected_lang,
                           available_languages=available_languages)


@app.route('/static/audio/<filename>')
def get_audio(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    app.run(debug=True)
# Load history from file if it exists
if os.path.exists(history_file):
    with open(history_file, 'r') as f:
        try:
            session['history'] = json.load(f)
        except json.JSONDecodeError:
            session['history'] = []