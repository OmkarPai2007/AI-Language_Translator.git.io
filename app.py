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
# DATABASE CONFIGURATION (Render PostgreSQL)
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

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users2 (
            id SERIAL PRIMARY KEY,
            full_name VARCHAR(255),
            email VARCHAR(255) UNIQUE,
            pass VARCHAR(255),
            translation_limit INT DEFAULT 3,
            translation_used INT DEFAULT 0
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()

try:
    init_db()
except Exception as e:
    print("DB Init Error:", e)

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
# GOOGLE LOGIN ROUTES
# ============================================================
@app.route("/google-login")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/google/callback")
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")

        if not user_info:
            resp = google.get(
                "https://openidconnect.googleapis.com/v1/userinfo"
            )
            user_info = resp.json()

        email = user_info.get("email")
        full_name = user_info.get("name")

        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users2 WHERE email=%s", (email,))
        user = cursor.fetchone()

        if not user:
            cursor.execute("""
                INSERT INTO users2 (full_name, email, pass)
                VALUES (%s, %s, %s)
            """, (full_name, email, "google_auth"))
            conn.commit()

        session["email"] = email
        session["full_name"] = full_name

        cursor.close()
        conn.close()

        return redirect(url_for("index"))

    except Exception as e:
        return f"Google login error: {str(e)}"

# ============================================================
# REGISTER & LOGIN (NORMAL)
# ============================================================
def is_strong_password(password):
    return (
        len(password) >= 8 and
        re.search(r"[A-Z]", password) and
        re.search(r"[a-z]", password) and
        re.search(r"[0-9]", password) and
        re.search(r"[!@#$%^&*(),.?\":{}|<>]", password)
    )

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    fullName = data.get("fullName")
    email = data.get("email")
    password = data.get("password")

    if not is_strong_password(password):
        return jsonify({"message": "Weak password"}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users2 WHERE email=%s", (email,))
    if cursor.fetchone():
        return jsonify({"message": "Email exists"}), 409

    cursor.execute("""
        INSERT INTO users2 (full_name, email, pass)
        VALUES (%s, %s, %s)
    """, (fullName, email, password))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"message": "Registered successfully!"}), 201

@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT full_name, pass FROM users2 WHERE email=%s
    """, (email,))
    user = cursor.fetchone()

    if not user or user[1] != password:
        return jsonify({"message": "Invalid credentials"}), 401

    session["email"] = email
    session["full_name"] = user[0]

    cursor.close()
    conn.close()

    return jsonify({"message": "Login successful!"})

# ============================================================
# TRANSLATION
# ============================================================
@app.route("/translate", methods=["POST"])
def translate():
    text = request.form.get("text")
    lang = request.form.get("language")

    translated = GoogleTranslator(source="auto", target=lang).translate(text)

    history.insert(0, {
        "target_lang": lang,
        "original_text": text,
        "translated_text": translated,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    return jsonify({"translated": translated})

# ============================================================
# MULTI LANGUAGE TRANSLATION
# ============================================================
@app.route("/translate-multi", methods=["POST"])
def translate_multi():
    if "email" not in session:
        return jsonify({"error": "Not logged in."}), 401

    data = request.get_json()
    text = data.get("text", "").strip()
    languages = data.get("languages", [])

    if not text or not languages:
        return jsonify({"error": "Missing text or languages."}), 400

    results = []

    for lang in languages:
        try:
            translated = GoogleTranslator(
                source="auto",
                target=lang
            ).translate(text)

            history.insert(0, {
                "target_lang": lang,
                "original_text": text,
                "translated_text": translated,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            })

            results.append({
                "language": lang,
                "translated_text": translated
            })

        except Exception as e:
            results.append({
                "language": lang,
                "translated_text": f"Error translating to {lang}"
            })

    return jsonify({"translations": results})
    
@app.route("/history")
def show_history():
    return render_template("history.html", history=history)

# ============================================================
# TEXT TO IMAGE
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
