import os
import json
import time
import uuid
import atexit
import re
from io import BytesIO
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

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs("static/receipts", exist_ok=True)
os.makedirs("static/uploads", exist_ok=True)

# =========================
# DATABASE (Render PostgreSQL)
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")

    result = urlparse(DATABASE_URL)

    return psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )

# =========================
# AUTO CREATE TABLE
# =========================
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users2 (
            id SERIAL PRIMARY KEY,
            full_name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            pass VARCHAR(255) NOT NULL,
            messages_left INT DEFAULT 3,
            translation_limit INT DEFAULT 3,
            translation_used INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()

# SAFE INIT
try:
    init_db()
    print("Database initialized successfully.")
except Exception as e:
    print("Database initialization failed:", e)

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
# TRANSLATION SUPPORT
# =========================
gtts_supported = {
    "af","ar","bn","bs","ca","cs","cy","da","de","el","en","eo","es","et","fi",
    "fr","gu","hi","hr","hu","hy","id","is","it","ja","jw","km","kn","ko","la",
    "lv","ml","mr","ms","my","ne","nl","no","pl","pt","ro","ru","si","sk","sq",
    "sr","su","sv","sw","ta","te","th","tl","tr","uk","ur","vi","zh-CN","zh-TW"
}

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
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form
    
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
            (full_name, email, pass, messages_left, translation_limit, translation_used)
            VALUES (%s, %s, %s, 3, 3, 0)
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

    try:
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

        return jsonify({"message": "Login successful!"})

    except Exception as e:
        return jsonify({"message": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

# =========================
# TRANSLATE
# =========================
@app.route("/translate", methods=["POST"])
def translate():
    text = request.form.get("text")
    lang = request.form.get("language")

    translated = GoogleTranslator(source="auto", target=lang).translate(text)
    return jsonify({"translated": translated})

# =========================
# MULTI TRANSLATE
# =========================
@app.route("/translate-multi", methods=["POST"])
def translate_multi():
    if not session.get("email"):
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json()
    text = data.get("text")
    languages = data.get("languages", [])

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT translation_limit, translation_used
        FROM users2 WHERE email=%s
    """, (session["email"],))

    limit, used = cursor.fetchone()

    if used >= limit:
        return jsonify({"error": "Limit reached"}), 403

    cursor.execute("""
        UPDATE users2
        SET translation_used = translation_used + 1
        WHERE email=%s
    """, (session["email"],))
    conn.commit()

    results = []
    for lang in languages:
        translated = GoogleTranslator(source="auto", target=lang).translate(text)
        results.append({
            "language": lang,
            "translated_text": translated
        })

    cursor.close()
    conn.close()

    return jsonify({"translations": results})

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

    try:
        cursor.execute("""
            UPDATE users2
            SET translation_limit = translation_limit + %s
            WHERE email=%s
        """, (credits, session["email"]))

        conn.commit()
        return jsonify({"message": "Plan updated!"})

    except Exception as e:
        return jsonify({"message": str(e)}), 500

    finally:
        cursor.close()
        conn.close()

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(port=3000, debug=True)
