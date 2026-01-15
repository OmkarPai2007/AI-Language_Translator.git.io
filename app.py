import atexit
import json
import os
import time
import uuid
# from io import BytesIO
from flask import session, redirect, url_for
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch


'''import bcrypt'''
import mysql.connector
# import requests
from deep_translator import GoogleTranslator
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS
from gtts import gTTS
# from PIL import Image

load_dotenv()

app = Flask(__name__)
app.secret_key = "your_secret_key_here"
app.config["UPLOAD_FOLDER"] = "static/audio"
CORS(app)

# Hugging Face API Config
API_TOKEN = os.getenv("HF_API_KEY")
API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

# gTTS supported languages
gtts_supported = {
    "af", "ar", "bn", "bs", "ca", "cs", "cy", "da", "de", "el", "en",
    "eo", "es", "et", "fi", "fr", "gu", "hi", "hr", "hu", "hy", "id",
    "is", "it", "ja", "jw", "km", "kn", "ko", "la", "lv", "ml", "mr",
    "ms", "my", "ne", "nl", "no", "pl", "pt", "ro", "ru", "si", "sk",
    "sq", "sr", "su", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk",
    "ur", "vi", "zh-CN", "zh-TW",
}
db_config = {
    "host": os.getenv("MYSQLHOST") or os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("MYSQLPORT") or os.getenv("DB_PORT", 3306)),
    "user": os.getenv("MYSQLUSER") or os.getenv("DB_USER", "root"),
    "password": os.getenv("MYSQLPASSWORD") or os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("MYSQLDATABASE") or os.getenv("DB_NAME", "myapp"),
}


# Load history
history_file = "history.json"
history = []
if os.path.exists(history_file):
    with open(history_file, "r") as f:
        try:
            history = json.load(f)
        except json.JSONDecodeError:
            history = []

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home_redirect"))


# === Home Page (Translation Tool) ===
@app.route("/index")
def index():
    full_name = session.get("full_name")
    email = session.get("email")

    if not full_name or not email:
        return redirect(url_for("login"))

    return render_template("index.html", full_name=full_name, email=email)


@app.route("/")
def home_redirect():
    if session.get("email"):
        return redirect(url_for("index"))
    return redirect(url_for("register_page"))


# === Single Language Translation ===
@app.route("/translate", methods=["POST"])
def translate():
    text = request.form.get("text", "").strip()
    language = request.form.get("language", "en")
    play_audio = request.form.get("playAudio") == "true"

    if not text:
        return jsonify({"translated": "Error: No text provided."})

    try:
        translated = GoogleTranslator(source="auto", target=language).translate(text)
        audio_path = ""

        if play_audio and language in gtts_supported:
            existing_files = os.listdir(app.config["UPLOAD_FOLDER"])
            numbers = [
                int(f.replace("audio", "").replace(".mp3", ""))
                for f in existing_files
                if f.startswith("audio")
                and f.endswith(".mp3")
                and f.replace("audio", "").replace(".mp3", "").isdigit()
            ]
            next_number = max(numbers, default=0) + 1

            filename = f"audio{next_number}.mp3"
            full_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            tts = gTTS(text=translated, lang=language)
            tts.save(full_path)
            audio_path = f"/static/audio/{filename}"

        entry = {
            "target_lang": language,
            "original_text": text,
            "translated_text": translated,
            "audio_file": os.path.basename(audio_path) if audio_path else "",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        history.insert(0, entry)
        return jsonify({"translated": translated, "audio_path": audio_path})

    except Exception as e:
        return jsonify({"translated": f"Error: {str(e)}"})


# === Multi-language Translation ===
@app.route("/translate-multi", methods=["POST"])
def translate_multi():
    if "email" not in session:
        return jsonify({"error": "Not logged in."}), 401

    email = session["email"]
    conn = None
    cursor = None

    # Database check for credits
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT translation_limit, translation_used FROM users2 WHERE email = %s",
            (email,),
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "User not found."}), 404

        limit = user["translation_limit"]
        used = user["translation_used"]

        if used >= limit:
            return (
                jsonify(
                    {
                        "error": "You have reached your translation limit. Please upgrade.",
                        "limit_reached": True,
                    }
                ),
                403,
            )

        # If credit is available, increment usage
        cursor.execute(
            "UPDATE users2 SET translation_used = translation_used + 1 WHERE email = %s",
            (email,),
        )
        conn.commit()

        session["multi_limit"] = limit
        session["multi_count"] = used + 1

    except mysql.connector.Error as err:
        return jsonify({"error": f"Database error: {err}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    # Proceed with translation
    data = request.get_json()
    text = data.get("text", "").strip()
    languages = data.get("languages", [])
    play_audio = data.get("playAudio", False)

    if not text or not languages:
        return jsonify({"error": "Missing text or languages."}), 400

    results = []
    
    # Get the next available audio file number to avoid overwrites
    existing_files = os.listdir(app.config["UPLOAD_FOLDER"])
    numbers = [
        int(f.replace("audio", "").replace(".mp3", ""))
        for f in existing_files
        if f.startswith("audio")
        and f.endswith(".mp3")
        and f.replace("audio", "").replace(".mp3", "").isdigit()
    ]
    next_number = max(numbers, default=0) + 1

    for lang in languages:
        try:
            translated = GoogleTranslator(source="auto", target=lang).translate(text)
            audio_path = None
            audio_file_name = ""

            if play_audio and lang in gtts_supported:
                filename = f"audio{next_number}.mp3"
                next_number += 1 # Increment for the next language in the loop
                full_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                tts = gTTS(text=translated, lang=lang)
                tts.save(full_path)
                audio_path = f"/static/audio/{filename}"
                audio_file_name = filename

            # Save to history
            entry = {
                "target_lang": lang,
                "original_text": text,
                "translated_text": translated,
                "audio_file": audio_file_name,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            history.insert(0, entry)

            results.append({
                "language": lang,
                "translated_text": translated,
                "audio_path": audio_path,
            })
        except Exception as e:
            results.append({
                "language": lang,
                "translated_text": f"Error translating to {lang}",
                "audio_path": None,
            })

    return jsonify({"translations": results})


@app.route("/register_page")
def register_page():
    if session.get("email"):
        return redirect(url_for("index"))
    return render_template("signup.html")


# === History Page ===
@app.route("/history")
def show_history():
    selected_lang = request.args.get("lang")
    filtered = (
        [entry for entry in history if entry["target_lang"] == selected_lang]
        if selected_lang
        else history
    )
    available_languages = sorted(set(item["target_lang"] for item in history))
    return render_template(
        "history.html",
        history=filtered,
        selected_lang=selected_lang,
        available_languages=available_languages,
    )


# === Serve Audio Files ===
@app.route("/static/audio/<filename>")
def get_audio(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/login")
def login():
    if session.get("email"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"message": "Email and password are required."}), 400

    db_connection = None
    cursor = None

    try:
        db_connection = mysql.connector.connect(**db_config)
        cursor = db_connection.cursor(dictionary=True)

        cursor.execute("SELECT pass, full_name, translation_limit, translation_used FROM users2 WHERE email = %s", (email,))
        user_data = cursor.fetchone()

        if not user_data:
            return jsonify({"message": "No account found with this email."}), 404

        stored_password = user_data["pass"]

        if password == stored_password:
            session.permanent = False
            session["email"] = email
            session["full_name"] = user_data["full_name"]
            session["multi_limit"] = user_data["translation_limit"]
            session["multi_count"] = user_data["translation_used"]
            return jsonify({"message": "Login successful!"})
        else:
            return jsonify({"message": "Incorrect password."}), 401

    except mysql.connector.Error as err:
        print(f"Database Error: {err}")
        return jsonify({"message": "Server error during login."}), 500

    finally:
        if cursor:
            cursor.close()
        if db_connection and db_connection.is_connected():
            db_connection.close()


# === Save history on exit ===
@atexit.register
def save_history():
    with open(history_file, "w") as f:
        json.dump(history, f, indent=4)
    print("History saved to file.")


import re

def is_strong_password(password):
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False
    return True


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or request.form
    fullName = data.get("fullName")
    email = data.get("email")
    password = data.get("password")

    if not fullName or not email or not password:
        return jsonify({"message": "Please fill all required fields."}), 400

    if not is_strong_password(password):
        return (
            jsonify(
                {
                    "message": "Password must be at least 8 characters long and include uppercase, lowercase, a digit, and a special character."
                }
            ),
            400,
        )

    db_connection = None
    cursor = None

    try:
        db_connection = mysql.connector.connect(**db_config)
        cursor = db_connection.cursor()

        cursor.execute("SELECT email FROM users2 WHERE email = %s", (email,))
        if cursor.fetchone():
            return (
                jsonify({"message": "An account with this email already exists."}),
                409,
            )

        sql = """
            INSERT INTO users2 (full_name, email, pass, messages_left, translation_limit, translation_used)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (fullName, email, password, 3, 3, 0))
        db_connection.commit()

        return jsonify({"message": "User registered successfully!"}), 201

    except mysql.connector.Error as err:
        print(f"Database Error: {err}")
        return jsonify({"message": "Server error during registration."}), 500

    finally:
        if cursor:
            cursor.close()
        if db_connection and db_connection.is_connected():
            db_connection.close()


@app.route("/buy-plan", methods=["POST"])
def buy_plan():
    if not session.get("email"):
        return jsonify({"message": "Not logged in."}), 401

    data = request.get_json()
    extra_messages = int(data.get("messages", 0))

    if extra_messages not in [5, 10, 15]:
        return jsonify({"message": "Invalid plan selected."}), 400

    email = session["email"]
    
    # === Generate PDF Receipt ===
    full_name = session.get("full_name", "Unknown User")
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

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users2 SET translation_limit = translation_limit + %s WHERE email = %s",
            (extra_messages, email),
        )
        conn.commit()

        cursor.execute(
            "SELECT translation_limit, translation_used FROM users2 WHERE email = %s",
            (email,),
        )
        new_limit, new_used = cursor.fetchone()
        session["multi_limit"] = new_limit
        session["multi_count"] = new_used

    except Exception as e:
        return jsonify({"message": f"Database error: {e}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    receipt_url = f"/static/receipts/{filename}"
    return jsonify(
        {
            "message": f"{extra_messages} translation credits added!",
            "new_limit": session["multi_limit"],
            "receipt_url": receipt_url,
        }
    )


'''if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))'''
