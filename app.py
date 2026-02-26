# ============================================================
# IMPORTS
# ============================================================
import os
import json
import time
import uuid
import atexit
import re
from urllib.parse import urlparse

from flask import (
    Flask, jsonify, render_template, request,
    redirect, url_for, session
)
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
from deep_translator import GoogleTranslator
from gtts import gTTS
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from PIL import Image
from werkzeug.utils import secure_filename
from huggingface_hub import InferenceClient
import google.generativeai as genai
from authlib.integrations.flask_client import OAuth

# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
CORS(app)

# Create required folders
os.makedirs("static/audio", exist_ok=True)
os.makedirs("static/uploads", exist_ok=True)
os.makedirs("static/receipts", exist_ok=True)

# ============================================================
# GOOGLE OAUTH CONFIGURATION
# ============================================================
oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# ============================================================
# DATABASE CONFIGURATION
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    result = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )

# ============================================================
# AI CONFIGURATION
# ============================================================
client = InferenceClient(
    provider="hf-inference",
    api_key=os.getenv("HF_TOKEN")
)

genai.configure(api_key=os.getenv("Gemini_API"))
chat_model = genai.GenerativeModel("gemini-2.5-flash")
chat = chat_model.start_chat(history=[])

# ============================================================
# HISTORY STORAGE
# ============================================================
history_file = "history.json"
history = []

if os.path.exists(history_file):
    try:
        with open(history_file, "r") as f:
            history = json.load(f)
    except:
        history = []

@atexit.register
def save_history():
    with open(history_file, "w") as f:
        json.dump(history, f, indent=4)

# ============================================================
# BASIC ROUTES
# ============================================================
@app.route("/")
def home_redirect():
    return redirect(url_for("register_page"))

@app.route("/index")
def index():
    if not session.get("email"):
        return redirect(url_for("login"))
    return render_template("index.html",
        full_name=session["full_name"],
        email=session["email"]
    )

@app.route("/register_page")
def register_page():
    return render_template("signup.html")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ============================================================
# TRANSLATION (WITH AUDIO)
# ============================================================
@app.route("/translate", methods=["POST"])
def translate():
    text = request.form.get("text", "").strip()
    lang = request.form.get("language")
    play_audio = request.form.get("playAudio") == "true"

    if not text:
        return jsonify({"translated": "No text provided."})

    translated = GoogleTranslator(source="auto", target=lang).translate(text)

    filename = ""
    audio_path = ""

    if play_audio:
        filename = f"audio_{uuid.uuid4().hex}.mp3"
        full_path = os.path.join("static/audio", filename)

        tts = gTTS(text=translated, lang=lang)
        tts.save(full_path)

        audio_path = f"/static/audio/{filename}"

    history.insert(0, {
        "target_lang": lang,
        "original_text": text,
        "translated_text": translated,
        "audio_file": filename,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    return jsonify({
        "translated": translated,
        "audio_path": audio_path
    })

# ============================================================
# MULTI LANGUAGE TRANSLATION (WITH AUDIO)
# ============================================================
@app.route("/translate-multi", methods=["POST"])
def translate_multi():
    if "email" not in session:
        return jsonify({"error": "Not logged in."}), 401

    data = request.get_json()
    text = data.get("text", "").strip()
    languages = data.get("languages", [])
    play_audio = data.get("playAudio", False)

    if not text or not languages:
        return jsonify({"error": "Missing text or languages."}), 400

    results = []

    for lang in languages:
        translated = GoogleTranslator(source="auto", target=lang).translate(text)

        filename = ""
        audio_path = ""

        if play_audio:
            filename = f"audio_{uuid.uuid4().hex}.mp3"
            full_path = os.path.join("static/audio", filename)

            tts = gTTS(text=translated, lang=lang)
            tts.save(full_path)

            audio_path = f"/static/audio/{filename}"

        history.insert(0, {
            "target_lang": lang,
            "original_text": text,
            "translated_text": translated,
            "audio_file": filename,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        })

        results.append({
            "language": lang,
            "translated_text": translated,
            "audio_path": audio_path
        })

    return jsonify({"translations": results})

# ============================================================
# HISTORY WITH FILTER
# ============================================================
@app.route("/history")
def show_history():
    selected_lang = request.args.get("lang")

    filtered = (
        [entry for entry in history if entry["target_lang"] == selected_lang]
        if selected_lang and selected_lang != "All"
        else history
    )

    available_languages = sorted(set(item["target_lang"] for item in history))

    return render_template(
        "history.html",
        history=filtered,
        selected_lang=selected_lang,
        available_languages=available_languages
    )

# ============================================================
# IMAGE GENERATION
# ============================================================
@app.route("/image-gen", methods=["GET", "POST"])
def image_gen():
    image_path = None
    error = None

    if request.method == "POST":
        prompt = request.form.get("prompt")
        try:
            image = client.text_to_image(
                prompt=prompt,
                model="stabilityai/stable-diffusion-xl-base-1.0"
            )
            image_path = "static/generated.png"
            image.save(image_path)
        except Exception as e:
            error = str(e)

    return render_template("image-gen.html",
        image_path=image_path,
        error=error
    )

# ============================================================
# IMAGE TO TEXT
# ============================================================
@app.route("/image-analyze", methods=["GET", "POST"])
def image_analyze():
    result = None
    error = None

    if request.method == "POST":
        file = request.files["image"]
        filename = secure_filename(file.filename)
        save_path = os.path.join("static/uploads", filename)
        file.save(save_path)

        try:
            image = Image.open(save_path)
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(["Describe this image", image])
            result = response.text
        except Exception as e:
            error = str(e)

    return render_template("image-to-text.html",
        result=result,
        error=error
    )

# ============================================================
# CHATBOT
# ============================================================
@app.route("/chatbot")
def chatbot_interface():
    return render_template("chatbot.html")

@app.route("/chat", methods=["POST"])
def handle_chat():
    user_message = request.json.get("message")
    response = chat.send_message(user_message)
    return jsonify({"response": response.text})

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    app.run(port=3000, debug=True)
