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
from authlib.integrations.flask_client import OAuth

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
# GOOGLE OAUTH CONFIG
# =========================
oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)
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
# ROUTES
# =========================

@app.route("/")
def home_redirect():
    return redirect(url_for("register_page"))

@app.route("/google-login")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/google/callback")
def google_callback():
    try:
        token = google.authorize_access_token()

        # Get user info safely
        user_info = token.get("userinfo")

        if not user_info:
            resp = google.get(
                "https://openidconnect.googleapis.com/v1/userinfo"
            )
            user_info = resp.json()

        if not user_info:
            return "Failed to fetch user info", 400

        email = user_info.get("email")
        full_name = user_info.get("name")

        if not email:
            return "Email not provided by Google", 400

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
# REGISTER (NORMAL)
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

    cursor.execute("SELECT id FROM users2 WHERE email=%s", (email,))
    if cursor.fetchone():
        return jsonify({"message": "Email already exists"}), 409

    cursor.execute("""
        INSERT INTO users2 (full_name, email, pass)
        VALUES (%s, %s, %s)
    """, (fullName, email, password))

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"message": "Registered successfully!"}), 201

# =========================
# LOGIN (NORMAL)
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
# RUN
# =========================
if __name__ == "__main__":
    app.run(port=3000, debug=True)
