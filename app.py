import os
import json
import time
import uuid
import atexit
import re
from urllib.parse import urlparse
from flask import (
    Flask, jsonify, render_template, request,
    redirect, url_for, session, send_from_directory
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

# =========================
# LOAD ENV
# =========================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
app.config["UPLOAD_FOLDER"] = "static/audio"

CORS(app)

os.makedirs("static/audio", exist_ok=True)
os.makedirs("static/uploads", exist_ok=True)
os.makedirs("static/receipts", exist_ok=True)

# =========================
# DATABASE (Render PostgreSQL)
# =========================
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
            messages_left INT DEFAULT 3,
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

# =========================
# HISTORY
# =========================
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

# =========================
# AI CONFIG
# =========================
client = InferenceClient(
    provider="hf-inference",
    api_key=os.getenv("HF_TOKEN")
)

genai.configure(api_key=os.getenv("Gemini_API"))
chat_model = genai.GenerativeModel("gemini-2.5-flash")
chat = chat_model.start_chat(history=[])

# =========================
# ROUTES
# =========================

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

# =========================
# REGISTER
# =========================
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

    if not fullName or not email or not password:
        return jsonify({"message": "All fields required"}), 400

    if not is_strong_password(password):
        return jsonify({"message": "Weak password"}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM users2 WHERE email=%s", (email,))
        if cursor.fetchone():
            return jsonify({"message": "Email already exists"}), 409

        cursor.execute("""
            INSERT INTO users2
            (full_name, email, pass)
            VALUES (%s, %s, %s)
        """, (fullName, email, password))

        conn.commit()
        return jsonify({"message": "Registered successfully!"}), 201

    except Exception as e:
        return jsonify({"message": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

# =========================
# LOGIN
# =========================
@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT full_name, pass, translation_limit, translation_used
        FROM users2 WHERE email=%s
    """, (email,))

    user = cursor.fetchone()

    if not user:
        return jsonify({"message": "Invalid credentials"}), 401

    full_name, stored_password, limit, used = user

    if stored_password != password:
        return jsonify({"message": "Invalid credentials"}), 401

    session["email"] = email
    session["full_name"] = full_name
    session["multi_limit"] = limit
    session["multi_count"] = used

    cursor.close()
    conn.close()

    return jsonify({"message": "Login successful!"})

# =========================
# TRANSLATE
# =========================
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

# =========================
# HISTORY
# =========================
@app.route("/history")
def show_history():
    return render_template("history.html", history=history)

# =========================
# TEXT TO IMAGE
# =========================
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

# =========================
# IMAGE TO TEXT
# =========================
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

# =========================
# CHATBOT
# =========================
@app.route("/chatbot")
def chatbot_interface():
    return render_template("chatbot.html")

@app.route("/chat", methods=["POST"])
def handle_chat():
    user_message = request.json.get("message")

    try:
        response = chat.send_message(user_message)
        return jsonify({"response": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# BUY PLAN
# =========================
@app.route("/buy-plan", methods=["POST"])
def buy_plan():
    if not session.get("email"):
        return jsonify({"message": "Login required"}), 401

    credits = int(request.get_json().get("messages", 0))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users2
        SET translation_limit = translation_limit + %s
        WHERE email=%s
    """, (credits, session["email"]))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"message": "Plan updated!"})

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(port=3000, debug=True)
