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
import mysql.connector

from deep_translator import GoogleTranslator
from gtts import gTTS

from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas

# =======================
# ENV + APP SETUP
# =======================

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
app.config["UPLOAD_FOLDER"] = "static/audio"
CORS(app)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs("static/receipts", exist_ok=True)

# =======================
# DATABASE (RAILWAY SAFE)
# =======================

DATABASE_URL = os.getenv("MYSQL_URL")

def get_db():
    if not DATABASE_URL:
        raise Exception("MYSQL_URL not set in Railway")

    parsed = urlparse(DATABASE_URL)

    return mysql.connector.connect(
        host=parsed.hostname,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/"),
        port=parsed.port or 3306,
    )

def init_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users2 (
            id INT AUTO_INCREMENT PRIMARY KEY,
            full_name VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            pass VARCHAR(255) NOT NULL,
            messages_left INT DEFAULT 3,
            translation_limit INT DEFAULT 3,
            translation_used INT DEFAULT 0
        )
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Database ready")
    except Exception as e:
        print("❌ DB init error:", e)

init_db()

# =======================
# PASSWORD VALIDATION
# =======================

def is_strong_password(password):
    return (
        len(password) >= 8 and
        re.search(r"[A-Z]", password) and
        re.search(r"[a-z]", password) and
        re.search(r"[0-9]", password) and
        re.search(r"[!@#$%^&*(),.?\":{}|<>]", password)
    )

# =======================
# TRANSLATION SUPPORT
# =======================

gtts_supported = {
    "af","ar","bn","de","en","es","fr","hi","it","ja",
    "ko","ml","mr","pt","ru","ta","te","tr","ur","zh-CN"
}

history = []
history_file = "history.json"

if os.path.exists(history_file):
    try:
        with open(history_file) as f:
            history = json.load(f)
    except:
        history = []

@atexit.register
def save_history():
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)

# =======================
# ROUTES
# =======================

@app.route("/")
def home():
    return redirect(url_for("index")) if session.get("email") else redirect(url_for("register_page"))

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

# =======================
# REGISTER
# =======================

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json() or request.form
    full_name = data.get("fullName")
    email = data.get("email")
    password = data.get("password")

    if not all([full_name, email, password]):
        return jsonify({"message": "All fields required"}), 400

    if not is_strong_password(password):
        return jsonify({"message": "Weak password"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users2 WHERE email=%s", (email,))
        if cursor.fetchone():
            return jsonify({"message": "Email already exists"}), 409

        cursor.execute("""
            INSERT INTO users2 (full_name,email,pass)
            VALUES (%s,%s,%s)
        """, (full_name, email, password))

        conn.commit()
        return jsonify({"message": "Registered successfully"}), 201

    except Exception as e:
        print("REGISTER ERROR:", e)
        return jsonify({"message": "Server error during registration"}), 500
    finally:
        cursor.close()
        conn.close()

# =======================
# LOGIN
# =======================

@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM users2 WHERE email=%s", (email,)
        )
        user = cursor.fetchone()

        if not user or user["pass"] != password:
            return jsonify({"message": "Invalid credentials"}), 401

        session["email"] = email
        session["full_name"] = user["full_name"]
        session["multi_limit"] = user["translation_limit"]
        session["multi_count"] = user["translation_used"]

        return jsonify({"message": "Login successful"})

    except Exception as e:
        print("LOGIN ERROR:", e)
        return jsonify({"message": "Server error"}), 500
    finally:
        cursor.close()
        conn.close()

# =======================
# TRANSLATE
# =======================

@app.route("/translate", methods=["POST"])
def translate():
    text = request.form.get("text")
    lang = request.form.get("language")

    translated = GoogleTranslator(source="auto", target=lang).translate(text)
    return jsonify({"translated": translated})

# =======================
# AUDIO FILES
# =======================

@app.route("/static/audio/<filename>")
def audio(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# =======================
# HISTORY
# =======================

@app.route("/history")
def history_page():
    return render_template("history.html", history=history)

# =======================
# BUY PLAN
# =======================

@app.route("/buy-plan", methods=["POST"])
def buy_plan():
    if not session.get("email"):
        return jsonify({"message": "Login required"}), 401

    data = request.get_json()
    credits = int(data.get("messages", 0))

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users2 SET translation_limit = translation_limit + %s WHERE email=%s",
            (credits, session["email"])
        )
        conn.commit()
        return jsonify({"message": "Plan updated"})
    except Exception as e:
        print("PLAN ERROR:", e)
        return jsonify({"message": "Server error"}), 500
    finally:
        cursor.close()
        conn.close()
