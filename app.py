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

os.makedirs("static/audio", exist_ok=True)
os.makedirs("static/uploads", exist_ok=True)
os.makedirs("static/receipts", exist_ok=True)

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
# GOOGLE OAUTH
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
# AI CONFIG
# ============================================================
client = InferenceClient(
    provider="hf-inference",
    api_key=os.getenv("HF_TOKEN")
)

genai.configure(api_key=os.getenv("Gemini_API"))
chat_model = genai.GenerativeModel("gemini-2.5-flash")
chat = chat_model.start_chat(history=[])

# ============================================================
# HISTORY
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
# GOOGLE LOGIN
# ============================================================
@app.route("/google-login")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/google/callback")
def google_callback():
    token = google.authorize_access_token()
    user_info = token.get("userinfo")

    if not user_info:
        resp = google.get("https://openidconnect.googleapis.com/v1/userinfo")
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

# ============================================================
# REGISTER & LOGIN
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

    return jsonify({"message": "Registered successfully!"})

@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT full_name, pass
        FROM users2 WHERE email=%s
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
        gTTS(text=translated, lang=lang).save(full_path)
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
# MULTI LANGUAGE TRANSLATION (WITH LIMIT + AUDIO)
# ============================================================
@app.route("/translate-multi", methods=["POST"])
def translate_multi():
    if "email" not in session:
        return jsonify({"error": "Not logged in."}), 401

    email = session["email"]

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT translation_limit, translation_used
        FROM users2
        WHERE email=%s
    """, (email,))
    result = cursor.fetchone()

    if not result:
        cursor.close()
        conn.close()
        return jsonify({"error": "User not found."}), 404

    limit, used = result

    if used >= limit:
        cursor.close()
        conn.close()
        return jsonify({
            "error": "You have reached your translation limit.",
            "limit_reached": True
        }), 403

    # Increase usage count
    cursor.execute("""
        UPDATE users2
        SET translation_used = translation_used + 1
        WHERE email=%s
    """, (email,))
    conn.commit()
    cursor.close()
    conn.close()

    data = request.get_json()
    text = data.get("text", "").strip()
    languages = data.get("languages", [])
    play_audio = data.get("playAudio", False)

    results = []

    for lang in languages:
        translated = GoogleTranslator(source="auto", target=lang).translate(text)

        filename = ""
        audio_path = ""

        if play_audio:
            filename = f"audio_{uuid.uuid4().hex}.mp3"
            full_path = os.path.join("static/audio", filename)
            gTTS(text=translated, lang=lang).save(full_path)
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
# BUY PLAN (WITH PDF RECEIPT + SESSION UPDATE)
# ============================================================
@app.route("/buy-plan", methods=["POST"])
def buy_plan():
    if not session.get("email"):
        return jsonify({"message": "Not logged in."}), 401

    data = request.get_json()
    extra_messages = int(data.get("messages", 0))

    if extra_messages not in [5, 10, 15]:
        return jsonify({"message": "Invalid plan selected."}), 400

    email = session["email"]
    full_name = session.get("full_name", "Unknown User")

    # Generate PDF Receipt
    price_map = {5: "49/- INR", 10: "89/- INR", 15: "129/- INR"}
    plan_price = price_map.get(extra_messages, "Unknown")

    os.makedirs("static/receipts", exist_ok=True)
    filename = f"receipt_{uuid.uuid4().hex}.pdf"
    receipt_path = os.path.join("static", "receipts", filename)

    c = canvas.Canvas(receipt_path, pagesize=A5)
    width, height = A5

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, height - 40, "AI Language Translator")

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2, height - 60, "Receipt of Purchase")

    c.line(40, height - 70, width - 40, height - 70)

    c.setFont("Helvetica", 10)
    y = height - 100

    details = [
        f"Name of Purchaser: {full_name}",
        f"Email Address: {email}",
        f"Plan Purchased: {extra_messages} Translation Credits",
        f"Plan Price: {plan_price}",
        f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Receipt ID: {uuid.uuid4()}",
    ]

    for detail in details:
        c.drawString(50, y, detail)
        y -= 15

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, 30, "Thank you for your purchase!")
    c.save()

    # Update credits in PostgreSQL
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users2
        SET translation_limit = translation_limit + %s
        WHERE email=%s
    """, (extra_messages, email))
    conn.commit()

    cursor.execute("""
        SELECT translation_limit, translation_used
        FROM users2
        WHERE email=%s
    """, (email,))

    new_limit, new_used = cursor.fetchone()

    session["multi_limit"] = new_limit
    session["multi_count"] = new_used

    cursor.close()
    conn.close()

    receipt_url = f"/static/receipts/{filename}"

    return jsonify({
        "message": f"{extra_messages} translation credits added!",
        "new_limit": new_limit,
        "receipt_url": receipt_url
    })

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

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        response = chat.send_message(user_message)
        return jsonify({"response": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    app.run(port=3000, debug=True)
