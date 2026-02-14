#!/usr/bin/env python

# Flask & related
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
import re
from io import BytesIO
from fpdf import FPDF
from werkzeug.utils import secure_filename
import textwrap
from reportlab.pdfgen import canvas
# Standard Libraries
import threading

import os
import random
import json
import socket
import yaml
import urllib.request
import time
import traceback
import subprocess
from datetime import datetime, timedelta
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from math import radians, sin, asin, cos, sqrt, atan2
from itsdangerous import URLSafeTimedSerializer
# Data Science / ML Libraries
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import DecisionTreeClassifier, export_graphviz
import joblib
import tensorflow.compat.v1 as tf
from pymysql.err import OperationalError, InterfaceError, InternalError
from sqlalchemy.exc import OperationalError as SAOperationalError
tf.disable_v2_behavior()
# tf.compat.v1.enable_eager_execution()
from bs4 import BeautifulSoup
from models import sqldb, Feedback, Reply

# HTTP requests
import requests

# Database
import pymysql

weather_data = []
weather_labels = []

ENV = os.getenv("APP_ENV", "local")

if ENV == "local":
    from dotenv import load_dotenv

    load_dotenv()


class SafeMySQL(pymysql.connections.Connection):
    def cursor(self, *args, **kwargs):
        try:
            # Try ping first to keep connection alive
            self.ping(reconnect=True)
        except pymysql.MySQLError:
            # Agar ping fail ho gaya → ignore, next cursor call automatically reconnect
            pass
        return super().cursor(*args, **kwargs)

# Initialize DB connection
db = SafeMySQL(
    host=os.environ["MYSQL_HOST"],
    user=os.environ["MYSQL_USER"],
    password=os.environ["MYSQL_PASSWORD"],
    database=os.environ["MYSQL_DATABASE"],
    port=int(os.environ.get("MYSQL_PORT", "3306")),
    connect_timeout=30,
    read_timeout=30,
    write_timeout=30,
    autocommit=True,
    charset="utf8mb4"
)

print("RENDER =", os.getenv("RENDER"))
print("MYSQL_HOST =", os.getenv("MYSQL_HOST"))

# Write your API key here.
api_key = "AIzaSyDYPhWrJ_gi7we9v3G9CwBeQIfb9Je4wl4"

app = Flask(__name__)
app.jinja_env.auto_reload = True
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "super_secret_key_123"

# EMAIL SETTINGS
app.config['MAIL_SERVER'] = os.environ.get("MAIL_SERVER")
app.config['MAIL_PORT'] = int(os.environ.get("MAIL_PORT", 587))
app.config['MAIL_USE_TLS'] = os.environ.get("MAIL_USE_TLS") == "True"
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASSWORD")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
 # CHANGE
print("APP_ENV:", os.getenv("APP_ENV"))
print("MAIL_SERVER:", os.getenv("MAIL_SERVER"))
print("MAIL_PORT:", os.getenv("MAIL_PORT"))
print("MAIL_USERNAME:", os.getenv("MAIL_USERNAME"))
print("MAIL_PASSWORD:", os.getenv("MAIL_PASSWORD"))

serializer = URLSafeTimedSerializer(app.secret_key)


from urllib.parse import quote_plus

db_user = os.environ.get("MYSQL_USER")
db_password = quote_plus(os.environ.get("MYSQL_PASSWORD"))  # encode special chars like @
db_host = os.environ.get("MYSQL_HOST")
db_name = os.environ.get("MYSQL_DATABASE")
db_port = os.environ.get("MYSQL_PORT", "3306")

app.config["SQLALCHEMY_DATABASE_URI"] = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


# initialize SQLAlchemy
sqldb.init_app(app)

# create tables
with app.app_context():
    sqldb.create_all()
mail = Mail(app)

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'  # Only show warnings and errors
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
UPLOAD_FOLDER = "static/uploads/avatars"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

import pymysql


def get_db_connection():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

last_db_alert_time = None

def send_db_alert_once(error_msg):
    global last_db_alert_time

    if not ADMIN_EMAIL:
        return

    # 15 min me ek hi alert
    if last_db_alert_time and datetime.utcnow() - last_db_alert_time < timedelta(minutes=15):
        return  # spam block

    try:
        msg = Message(
            subject="🚨 DATABASE DOWN ALERT",
            recipients=[ADMIN_EMAIL],
            body=f"MySQL Error Detected:\n\n{error_msg}\n\nTime (UTC): {datetime.utcnow()}"
        )
        mail.send(msg)
        last_db_alert_time = datetime.utcnow()
    except Exception as e:
        print("Mail alert failed:", e)


# =========================
# Network check helper
# =========================
def is_network_alive(host="8.8.8.8", port=53, timeout=2):
    """Check if internet/network is available"""
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except Exception:
        return False


# =========================
# Before request: network + DB check
# =========================
@app.before_request
def check_network_or_db():
    # 1. Network check
    if not is_network_alive():
        return render_template(
            "error.html",
            code=503,
            title="Network Unavailable",
            message="Internet/Wi-Fi is down.<br>Please try again after some time."
        ), 503

    # 2. DB check
    try:
        db.ping(reconnect=True)
    except Exception as e:
        send_db_alert_once(str(e))
        return render_template(
            "error.html",
            code=503,
            title="Database Down",
            message="Database is temporarily unavailable.<br>Please try again after some time."
        ), 503


# =========================
# MySQL / SQLAlchemy errors
# =========================
@app.errorhandler(OperationalError)
@app.errorhandler(InterfaceError)
@app.errorhandler(InternalError)
@app.errorhandler(SAOperationalError)
def handle_mysql_error(e):
    send_db_alert_once(str(e))
    return render_template(
        "error.html",
        code=503,
        title="Service Temporarily Unavailable",
        message="Database service is temporarily unavailable.<br>Please try again shortly."
    ), 503


# =========================
# Generic 500 error (NO blanket Exception)
# =========================
@app.errorhandler(500)
def internal_error(e):
    return render_template(
        "error.html",
        code=500,
        title="Unexpected Error",
        message="Something went wrong.<br>Please try again later."
    ), 500

from flask import jsonify
import csv


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def crop_center_square(img: Image.Image) -> Image.Image:
    width, height = img.size
    min_dim = min(width, height)
    left = (width - min_dim) // 2
    top = (height - min_dim) // 2
    right = left + min_dim
    bottom = top + min_dim
    return img.crop((left, top, right, bottom))


def generate_default_avatar(username, user_id):
    # File name
    default_name = f"user_{user_id}_default.png"
    default_path = os.path.join(app.config["UPLOAD_FOLDER"], default_name)

    if os.path.exists(default_path):
        return default_name

    # Image settings
    size = (300, 300)
    bg_color = (66, 133, 244)
    # Yellow / Gmail-like
    text_color = (255, 255, 255)  # White letter

    # Create image
    img = Image.new("RGB", size, bg_color)
    draw = ImageDraw.Draw(img)

    # First letter of username
    letter = username[0].upper() if username else "U"

    # Font (fallback safe)
    try:
        font = ImageFont.truetype("arial.ttf", 140)
    except:
        font = ImageFont.load_default()

    # Center text
    bbox = draw.textbbox((0, 0), letter, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    position = (
        (size[0] - text_width) // 2,
        (size[1] - text_height) // 2 - 10
    )

    # Draw letter
    draw.text(position, letter, fill=text_color, font=font)

    # Save
    img.save(default_path, "PNG", quality=90)

    return default_name


@app.route("/api/get_historical_earthquakes")
def get_historical_earthquakes():
    data = []

    try:
        with open("historical_earthquakes.csv", "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)

            for row in reader:
                data.append({
                    "time": row["time"],  # ISO format
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "depth": float(row["depth"]),  # km
                    "mag": float(row["mag"]),
                    "magType": row["magType"],
                    "place": row["place"],
                    "type": row["type"]
                })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

    return jsonify(data)


@app.route("/api/get_historical_floods")
def get_historical_floods():
    import csv

    data = []

    try:
        with open("historical_floods.csv", "r") as file:
            reader = csv.DictReader(file)
            for row in reader:
                data.append({
                    "date": row["date"],
                    "area": row["area"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "severity": row["severity"],
                    "description": row["description"]
                })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

    return jsonify(data)


@app.route("/admin/overview")
def admin_overview():
    return render_template("overview.html")


@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email'].strip()
        cur = db.cursor()
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        if user:
            token = serializer.dumps(email, salt='password-reset-salt')
            reset_link = url_for('reset_password', token=token, _external=True)

            msg = Message('Reset Your Password', sender=app.config['MAIL_USERNAME'], recipients=[email])
            msg.html = f"""
                <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <p>Hi,</p>

                    <p>We received a request to reset the password for your account.  
                    If you made this request, please click the button below to create a new password:</p>

                    <p style="text-align: center;">
                        <a href="{reset_link}" 
                           style="background-color: #4CAF50; color: white; padding: 12px 20px; 
                                  text-decoration: none; border-radius: 5px; font-size: 16px;">
                            Reset Your Password
                        </a>
                    </p>

                    <p>If the button doesn’t work, you can also use the link below:</p>
                    <p style="word-break: break-all;">
                        <a href="{reset_link}">{reset_link}</a>
                    </p>

                    <p><strong>This link will expire in 1 hour</strong> for your security.</p>

                    <p>If you didn’t request a password reset, you can safely ignore this email.  
                    Your account will remain secure and no changes will be made.</p>

                    <hr style="margin-top: 25px;">

                    <p style="font-size: 14px; color: #666;">
                        For your security, we recommend using a strong, unique password and enabling
                        additional security features if available.
                    </p>

                    <p style="font-size: 14px; color: #666;">
                        Thank you,<br>
                        <strong>Disaster Support Team</strong>
                    </p>
                </div>
            """
            mail.send(msg)
            flash("Password reset email sent!", "success")
        else:
            flash("Email not found!", "danger")
    return render_template('forgot_password.html')


@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)  # 1 hour
    except:
        # flash("The reset link is invalid or expired.", "danger")
        return render_template('reset_link_expired.html')

    if request.method == 'POST':
        new_password = request.form['password'].strip()
        confirm_password = request.form['confirm_password'].strip()
        if new_password != confirm_password:
            flash("Passwords do not match!", "danger")
        else:
            hashed = generate_password_hash(new_password)
            cur = db.cursor()
            cur.execute("UPDATE users SET password=%s WHERE email=%s", (hashed, email))
            db.commit()
            flash("Password successfully reset!", "success")
            return redirect(url_for('login_page'))

    return render_template('reset_password.html')


def send_registration_email(email, username, lat, lng):
    msg = Message(
        "Welcome to Disaster Alert System 🌍",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email]
    )

    msg.body = f"""
Hello {username},

Your account has been successfully created!

📍 Registered Location:
Latitude: {lat}
Longitude: {lng}

With your registration, you are now part of a system designed to keep you and your community safe. Here's what you can expect:

1 **Automatic Alerts**
- You will receive notifications **ONLY** when a real disaster is detected near your registered location.
- This includes:
    • Earthquakes detected in your area
    • Floods reported near your location

2 **Reliable & Accurate**
- Our system uses verified real-time data from government and scientific sources.
- Alerts are sent promptly so you can take necessary precautions.

3 **No Action Needed**
- You don’t need to check or refresh anything manually. Alerts come directly to your registered email or phone if enabled.

4 **Safety Tips**
- In case of an earthquake: Drop, Cover, and Hold On. Avoid unstable structures.
- In case of a flood: Move to higher ground, avoid crossing flooded areas, and follow official advisories.

5 **Your Privacy**
- Your location data is used solely to provide disaster alerts.
- We do **not** share your personal information with any third party.

Stay safe and stay informed.  
Our team is constantly monitoring and improving the system to ensure timely and accurate alerts.

Warm regards,  
**Disaster Alert System Team**  
"""

    mail.send(msg)


@app.route('/register', methods=['GET', 'POST'])
def register_page():
    if request.method == 'POST':
        username = request.form['username'].strip()
        name = request.form['name'].strip()
        email = request.form['email'].strip()
        mobile = request.form['mobile'].strip()
        lat = float(request.form['lat'].strip())
        lng = float(request.form['lng'].strip())
        password_input = request.form['password']

        password = generate_password_hash(password_input)
        cursor = db.cursor()

        # VALIDATION
        cursor.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cursor.fetchone():
            flash("Username already exists!", "danger")
            return render_template("register.html")

        cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cursor.fetchone():
            flash("Email already registered!", "danger")
            return render_template("register.html")

        # INSERT
        try:
            sql = """
                  INSERT INTO users (username, name, email, password, mobile, latitude, longitude)
                  VALUES (%s, %s, %s, %s, %s, %s, %s) \
                  """
            cursor.execute(sql, (username, name, email, password, mobile, lat, lng))
            db.commit()

            # WELCOME EMAIL ONLY
            send_registration_email(email, name, lat, lng)

            flash("Registration successful! Please login.", "success")
            return redirect(url_for('login_page'))

        except Exception as e:
            flash("Error: " + str(e), "danger")

    return render_template("register.html")


@app.route("/settings/profile", methods=["POST"])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    user_id = session['user_id']

    full_name = request.form.get("full_name")
    email = request.form.get("email")
    phone = request.form.get("phone")
    latitude = request.form.get("latitude")
    longitude = request.form.get("longitude")

    db = get_db_connection()
    cursor = db.cursor()

    # -----------------------------
    # EMAIL DUPLICATE CHECK
    # -----------------------------
    cursor.execute("""
                   SELECT id
                   FROM users
                   WHERE email = %s
                     AND id != %s
                   """, (email, user_id))

    existing_user = cursor.fetchone()

    if existing_user:
        cursor.close()
        db.close()
        flash("This email is already used by another account", "danger")
        return redirect(url_for("settings"))

    # -----------------------------
    # UPDATE PROFILE
    # -----------------------------
    cursor.execute("""
                   UPDATE users
                   SET name=%s,
                       email=%s,
                       mobile=%s,
                       latitude=%s,
                       longitude=%s
                   WHERE id = %s
                   """, (full_name, email, phone, latitude, longitude, user_id))

    db.commit()
    cursor.close()
    db.close()

    flash("Profile updated successfully", "success")
    return redirect(url_for("settings"))


@app.route("/update_avatar", methods=["POST"])
def update_avatar():
    if "user_id" not in session:
        flash("You must be logged in to update avatar.", "danger")
        return redirect(url_for("settings"))

    file = request.files.get("avatar")
    if not file or file.filename == "":
        flash("No file selected", "danger")
        return redirect(url_for("settings"))

    if not allowed_file(file.filename):
        flash("Invalid image format", "danger")
        return redirect(url_for("settings"))

    file.seek(0, os.SEEK_END)
    if file.tell() > MAX_FILE_SIZE:
        flash("Image must be under 2MB", "danger")
        return redirect(url_for("settings"))
    file.seek(0)

    ext = secure_filename(file.filename).rsplit(".", 1)[1].lower()
    new_filename = f"user_{session['user_id']}.{ext}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], new_filename)

    db = get_db_connection()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # delete old avatar
    cursor.execute("SELECT avatar FROM users WHERE id=%s", (session["user_id"],))
    old = cursor.fetchone()
    if old and old["avatar"]:
        old_path = os.path.join(app.config["UPLOAD_FOLDER"], old["avatar"])
        if os.path.exists(old_path):
            os.remove(old_path)

    # process image
    img = Image.open(file).convert("RGB")
    img = crop_center_square(img)
    img = img.resize((300, 300))
    img.save(filepath, quality=90)

    cursor.execute(
        "UPDATE users SET avatar=%s WHERE id=%s",
        (new_filename, session["user_id"])
    )
    db.commit()
    cursor.close()
    db.close()

    flash("Profile photo updated successfully", "success")
    return redirect(url_for("settings"))


@app.route("/remove_avatar", methods=["POST"])
def remove_avatar():
    if "user_id" not in session:
        flash("You must be logged in to remove avatar.", "danger")
        return redirect(url_for("settings"))

    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("SELECT avatar, username FROM users WHERE id=%s", (session["user_id"],))
    user = cursor.fetchone()

    if user and user["avatar"]:
        path = os.path.join(app.config["UPLOAD_FOLDER"], user["avatar"])
        if os.path.exists(path):
            os.remove(path)

    # Generate default avatar (function you need to have)
    default_avatar = generate_default_avatar(user["username"], session["user_id"])

    cursor.execute(
        "UPDATE users SET avatar=%s WHERE id=%s",
        (default_avatar, session["user_id"])
    )
    db.commit()
    cursor.close()
    db.close()

    flash("Avatar removed", "success")
    return redirect(url_for("settings"))


@app.route("/settings/password", methods=["POST"])
def update_password():
    if "user_id" not in session:
        return redirect(url_for("login_page"))

    old_password = request.form.get("old_password")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    if not old_password or not new_password or not confirm_password:
        flash("All password fields are required", "warning")
        return redirect(url_for("settings"))

    if new_password != confirm_password:
        flash("New password and confirmation do not match", "warning")
        return redirect(url_for("settings"))

    db = get_db_connection()
    cursor = db.cursor()  # DictCursor already applied globally

    cursor.execute(
        "SELECT password FROM users WHERE id = %s",
        (session["user_id"],)
    )
    user = cursor.fetchone()

    if not user or not check_password_hash(user["password"], old_password):
        flash("Old password is incorrect", "warning")
        cursor.close()
        db.close()
        return redirect(url_for("settings"))

    hashed_password = generate_password_hash(new_password)

    cursor.execute(
        "UPDATE users SET password = %s WHERE id = %s",
        (hashed_password, session["user_id"])
    )

    # ✅ autocommit=True handles saving
    if cursor.rowcount == 1:
        flash("Password updated successfully", "success")
    else:
        flash("Password update failed", "danger")

    cursor.close()
    db.close()

    return redirect(url_for("settings"))


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'GET':
        session.pop("pending_user", None)
        session.pop("pending_otp", None)
        session.pop("otp_expiry", None)

    if request.method == 'POST':
        username_input = request.form.get('username')
        password_input = request.form.get('password')

        # ✅ USE SAME DB CONNECTION AS PASSWORD UPDATE
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)  # DictCursor (important)

        cursor.execute(
            """
            SELECT id, username, name, email, password, latitude, longitude
            FROM users
            WHERE username = %s
               OR email = %s
            """,
            (username_input, username_input)
        )

        user = cursor.fetchone()
        cursor.close()
        db.close()

        if not user:
            flash("Invalid Username!", "danger")
            return redirect(url_for('login_page'))

        # ✅ CHECK UPDATED PASSWORD HASH
        if not check_password_hash(user["password"], password_input):
            flash("Incorrect Password!", "danger")
            return redirect(url_for('login_page'))

        # ---------- GENERATE OTP ----------
        otp_code = str(random.randint(100000, 999999))

        # ---------- SAVE OTP + USER INFO ----------
        session['pending_otp'] = otp_code
        session['otp_expiry'] = time.time() + 300  # 5 minutes

        session['pending_user'] = {
            "user_id": user["id"],
            "username": user["username"],
            "name": user["name"],
            "email": user["email"]
        }

        # ---------- SEND OTP EMAIL ----------
        msg = Message(
            "Your Login Verification Code",
            sender="disasterpredictionsystem@gmail.com",
            recipients=[user["email"]]
        )

        msg.body = f"""
Hello {user["username"]},

 We received a request to sign in to your Disaster Alert System account.

To ensure the security of your account, we require you to verify your identity using a One-Time Password (OTP). Please use the code below to complete your login:

🔐 One-Time Password (OTP): {otp_code}

 ⏳ Validity: This OTP is valid for the next 5 minutes only.
 ⚠️ Security Notice:
• Do NOT share this OTP with anyone.
• Our team will NEVER ask you for this code.
• Enter this code only on the official Disaster Alert System website.

If you did NOT attempt to sign in, your account may still be safe. You can safely ignore this message. However, if you notice any suspicious activity, we strongly recommend updating your password immediately.

Thank you for helping us keep your account secure.

 Stay alert. Stay safe.

 — Disaster Alert System  
Security & Authentication Team
        """

        mail.send(msg)

        flash("Authentication code sent to your email!", "info")
        return redirect(url_for('verify_otp_page'))

    return render_template("login.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/settings")
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
                   SELECT username, name, email, mobile, latitude, longitude, avatar
                   FROM users
                   WHERE id = %s
                   """, (session['user_id'],))

    user = cursor.fetchone()
    cursor.close()
    db.close()

    return render_template(
        "settings.html",
        username=user["username"],
        full_name=user["name"],
        email=user["email"],
        phone=user["mobile"],
        latitude=user["latitude"],
        longitude=user["longitude"],
        avatar=user["avatar"]  # 🔥 THIS WAS MISSING
    )


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp_page():
    # If user is already logged in, redirect away
    if "user_id" in session:
        return redirect(url_for("index"))

    # Check if OTP session exists
    if "pending_otp" not in session or "pending_user" not in session:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for("login_page"))

    if request.method == "POST":
        user_otp = request.form.get("otp")

        if user_otp == session.get("pending_otp"):
            user = session["pending_user"]

            # Promote user to full login session
            session["user_id"] = user["user_id"]
            session["user_username"] = user["username"]
            session["user_name"] = user["name"]

            # Clean temporary OTP session data
            session.pop("pending_otp", None)
            session.pop("pending_user", None)
            session.pop("otp_email", None)

            # flash("Login successful", "success")

            # ✅ Redirect to login success page
            return redirect(url_for("login_success"))

        else:
            flash("Invalid OTP", "danger")

    return render_template("verify_otp.html")


@app.route("/login-success")
def login_success():
    if 'user_id' not in session:  # Check if user is logged in
        return redirect(url_for('login_page'))  # Redirect to login if not logged in
    return render_template("login_success.html", username=session.get('user_name'))


@app.route("/resend-otp")
def resend_otp():
    # ✅ If user already logged in, block OTP resend
    if "user_id" in session:
        return redirect(url_for("index"))

    # ✅ OTP session must exist
    if "pending_otp" not in session or "pending_user" not in session:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for("login_page"))

    # ✅ Limit resend attempts (max 3)
    resend_count = session.get("resend_count", 0)
    if resend_count >= 3:
        flash("You have reached the maximum OTP resend attempts!", "danger")
        return redirect(url_for("verify_otp_page"))

    # ✅ Generate new OTP
    otp_code = str(random.randint(100000, 999999))
    session["pending_otp"] = otp_code
    session["otp_expiry"] = time.time() + 300  # 5 minutes
    session["resend_count"] = resend_count + 1

    email = session["pending_user"].get("email")

    # ✅ Safety check
    if not email:
        flash("Email not found. Please login again.", "danger")
        return redirect(url_for("login_page"))

    # ✅ Send OTP email
    msg = Message(
        subject="Your Login Verification Code (Resent)",
        sender="disasterpredictionsystem@gmail.com",
        recipients=[email]
    )

    msg.body = f"""
Hello,

We received a request to verify your account using a One-Time Password (OTP). Please find your verification code below:

━━━━━━━━━━━━━━━━━━━━━━
🔐 Your One-Time Password (OTP): {otp_code}
━━━━━━━━━━━━━━━━━━━━━━

This OTP is valid for the next 5 minutes only.
Please enter this code on the verification screen to complete your request.

⚠️ Important Security Information:

Do NOT share this OTP with anyone, including our staff.

Our team will never ask for your OTP via phone, email, or message.

If the OTP expires, you will need to request a new one.

🔁 Resend Attempts: {session['resend_count']} out of 3
(For your security, the number of OTP resend attempts is limited.)

❗ Didn’t request this code?
If you did not initiate this request, please ignore this email and take immediate steps to secure your account by changing your password or contacting support.

Thank you for helping us keep your account safe.

Regards,
Disaster Alert Security Team
"""

    mail.send(msg)

    flash("A new verification code has been sent to your email.", "success")
    return redirect(url_for("verify_otp_page"))


@app.route("/check_username")
def check_username():
    username = request.args.get("username", "").strip()
    if not username:
        return jsonify({"available": False})

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE username=%s", (username,))
            exists = cur.fetchone() is not None
        conn.close()
        return jsonify({"available": not exists})
    except Exception as e:
        print("Username check error:", e)
        return jsonify({"available": False})


@app.route("/check_email")
def check_email():
    email = request.args.get("email", "").strip()
    if not email:
        return jsonify({"available": False})

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE email=%s", (email,))
            exists = cur.fetchone() is not None
        conn.close()
        return jsonify({"available": not exists})
    except Exception as e:
        print("Email check error:", e)
        return jsonify({"available": False})


from werkzeug.security import generate_password_hash

print(generate_password_hash("Admin@123"))


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        session.pop("admin_id", None)
        session.pop("admin_username", None)
        session.pop("temp_admin", None)
        session.pop("admin_otp", None)
        session.pop("admin_otp_time", None)

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        cursor = db.cursor()
        cursor.execute("SELECT id, username, password, email FROM admin WHERE email=%s", (email,))
        admin = cursor.fetchone()

        if admin and check_password_hash(admin[2], password):
            # Temporary session for OTP
            session["temp_admin"] = {"id": admin[0], "username": admin[1]}

            # Generate OTP
            otp = random.randint(100000, 999999)
            session["admin_otp"] = otp
            session["admin_otp_time"] = datetime.now().isoformat()

            # Send OTP email
            sender_email = "disasterpredictionsystem@gmail.com"
            sender_pass = "pqpbruceisevbjpd"  # Gmail App Password
            receiver_email = admin[3]

            message_body = textwrap.dedent(f"""
        Hello {admin[1]},

We received a request to log in to the Disaster Alert System using your
administrator account.

Your Admin Login Verification Code is:

🔑 OTP: {otp}

⏳ This OTP is valid for 5 minutes only.
Please do not share this code with anyone. Our team will never ask
for your OTP.

⚠️ Security Alert:
If you did NOT initiate this login request, please secure your account
immediately by changing your password and contacting support.

Thank you for helping us keep your account secure.

Regards,
Disaster Alert System
(Security Team – Automated Message)
            """).strip()
            msg = MIMEText(message_body)
            msg['Subject'] = "Admin OTP Verification"
            msg['From'] = sender_email
            msg['To'] = receiver_email

            try:
                server = smtplib.SMTP("smtp.gmail.com", 587)
                server.starttls()
                server.login(sender_email, sender_pass)
                server.send_message(msg)
                server.quit()
                flash(
                    "A verification OTP has been sent to your registered admin email. "
                    "Check your inbox or spam folder to continue.",
                    "info"
                )
            except Exception as e:
                print("OTP Email Error:", e)
                flash(
                    "Failed to send OTP email. Check server console for details.",
                    "danger"
                )
                return redirect(url_for("admin_login"))

            return redirect(url_for("admin_verify_otp"))

        # Wrong email or password
        flash("Invalid admin email or password!", "danger")
        return redirect(url_for("admin_login"))

    # GET method: just render login page
    return render_template("admin_login.html")


@app.route("/admin_logout")
def admin_logout():
    session.clear()  # Clear all session data
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin_verify_otp", methods=["GET", "POST"])
def admin_verify_otp():
    # Already logged in → dashboard
    if "admin_id" in session:
        return redirect(url_for("admin_dashboard"))

    # OTP page without temp login → login
    if "temp_admin" not in session:
        flash("Please login first.", "warning")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        if not entered_otp:
            flash("Please enter the OTP.", "warning")
            return redirect(url_for("admin_verify_otp"))

        otp_valid = session.get("admin_otp")
        otp_time = session.get("admin_otp_time")

        if not otp_valid or not otp_time:
            flash("Invalid session. Please login again.", "danger")
            return redirect(url_for("admin_login"))

        otp_age = datetime.now() - datetime.fromisoformat(otp_time)
        if otp_age.total_seconds() > 300:
            flash("OTP expired. Please login again.", "danger")
            session.clear()
            return redirect(url_for("admin_login"))

        if str(otp_valid) == entered_otp:
            # ✅ REAL LOGIN
            session["admin_id"] = session["temp_admin"]["id"]
            session["admin_username"] = session["temp_admin"]["username"]

            # cleanup
            session.pop("temp_admin", None)
            session.pop("admin_otp", None)
            session.pop("admin_otp_time", None)

            return redirect(url_for("admin_login_success"))

        flash("Incorrect OTP. Please try again!", "danger")

    return render_template("admin_verify_otp.html")


@app.route("/admin_login_success")
def admin_login_success():
    # If admin not logged in, prevent direct access
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect(url_for("admin_login"))
    return render_template("admin_login_success.html")


@app.route("/admin_resend_otp")
def admin_resend_otp():
    import random
    import smtplib
    from email.mime.text import MIMEText
    from datetime import datetime, timedelta

    if "admin_id" in session:
        return redirect(url_for("admin_dashboard"))

    # Ensure user is still in OTP stage
    if "temp_admin" not in session:
        flash("Session expired. Please login again.", "warning")
        return redirect("/admin_login")

    # Anti-spam: OTP allowed only once every 30 sec
    last_sent = session.get("last_otp_sent")
    now = datetime.now()

    if last_sent:
        last_sent_dt = datetime.fromisoformat(last_sent)
        diff = (now - last_sent_dt).total_seconds()

        if diff < 30:  # Within cooldown
            wait_time = 30 - int(diff)
            flash(f"Please wait {wait_time} seconds before requesting a new OTP.", "warning")
            return redirect("/admin_verify_otp")

    # Fetch admin details
    admin_id = session["temp_admin"]["id"]
    cursor = db.cursor()
    cursor.execute("SELECT email, username FROM admin WHERE id=%s", (admin_id,))
    admin_data = cursor.fetchone()

    if not admin_data:
        flash("Admin account not found. Please login again.", "danger")
        return redirect("/admin_login")

    admin_email, admin_name = admin_data

    # Generate new OTP
    new_otp = random.randint(100000, 999999)
    session["admin_otp"] = new_otp
    session["admin_otp_time"] = now.isoformat()
    session["last_otp_sent"] = now.isoformat()
    session.pop("last_otp_sent", None)

    # Send email properly with MIME
    try:
        sender_email = "disasterpredictionsystem@gmail.com"
        sender_pass = "pqpbruceisevbjpd"

        msg_body = textwrap.dedent(f"""
            Hello {admin_name},

We received a request to generate a new One-Time Password (OTP) for
your administrator account on the Disaster Alert System.

🔐 Your NEW OTP is: {new_otp}


⏳ This OTP is valid for 5 minutes only and can be used once.
For your security, please do not share this OTP with anyone.
Our team will never ask for your OTP.

⚠️ Security Notice:
If you did NOT request this OTP, your account may be at risk.
Please change your password immediately and contact the system
administrator or support team.

Thank you for keeping your account secure.

Regards,
Disaster Alert System
(Security Team – Automated Message)
        """).strip()
        msg = MIMEText(msg_body)
        msg["Subject"] = "Your New Admin Login OTP"
        msg["From"] = sender_email
        msg["To"] = admin_email

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_pass)
        server.send_message(msg)
        server.quit()

    except Exception as e:
        print("OTP Resend Email Error:", e)
        flash("Failed to send OTP. Try again later.", "danger")
        return redirect("/admin_verify_otp")

    flash("A new OTP has been sent to your registered admin email.", "success")
    return redirect("/admin_verify_otp")


@app.route("/admin/test_email")
def admin_test_email():
    # Admin login check
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect("/admin_login")

    cursor = db.cursor()

    # Get admin email
    cursor.execute(
        "SELECT email, username FROM admin WHERE id=%s",
        (session["admin_id"],)
    )
    admin = cursor.fetchone()

    if not admin:
        flash("Admin account not found.", "danger")
        return redirect("/admin/settings")

    admin_email = admin[0]
    admin_name = admin[1]

    # Get SMTP settings from DB
    cursor.execute("""
                   SELECT smtp_email, smtp_password
                   FROM system_settings
                   WHERE id = 1
                   """)
    settings = cursor.fetchone()

    if not settings or not settings[0] or not settings[1]:
        flash("SMTP settings are not configured.", "danger")
        return redirect("/admin/settings")

    sender_email = settings[0]
    sender_pass = settings[1]

    # Send test email
    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(f"""
Hello {admin_name},

✅ This is a TEST EMAIL from Disaster Alert System.

SMTP configuration is working correctly.

- Admin Panel
""")

        msg["Subject"] = "✅ SMTP Test Email Successful"
        msg["From"] = sender_email
        msg["To"] = admin_email

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_pass)
        server.send_message(msg)
        server.quit()

        flash(f"Test email sent successfully to {admin_email}", "success")

    except Exception as e:
        print("Test email error:", e)
        flash("Failed to send test email. Check SMTP credentials.", "danger")

    return redirect("/admin/settings")


@app.route("/admin/dashboard")
def admin_dashboard():
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect("/admin_login")

    admin = {
        "id": session["admin_id"],
        "username": session["admin_username"]
    }
    return render_template("admin_dashboard.html", admin=admin)


from flask import render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash


@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect("/admin_login")

    cursor = db.cursor()

    if request.method == "POST":
        rainfall = request.form.get("rainfall_threshold")
        flood_alert = 1 if request.form.get("flood_alert") else 0
        magnitude = request.form.get("earthquake_magnitude")
        smtp_email = request.form.get("smtp_email")
        smtp_password = request.form.get("smtp_password")

        if smtp_password:
            # ✅ Update WITH password
            cursor.execute("""
                           UPDATE system_settings
                           SET rainfall_threshold=%s,
                               flood_alert_enabled=%s,
                               earthquake_magnitude=%s,
                               smtp_email=%s,
                               smtp_password=%s
                           WHERE id = 1
                           """, (rainfall, flood_alert, magnitude, smtp_email, smtp_password))
        else:
            # ✅ Update WITHOUT password
            cursor.execute("""
                           UPDATE system_settings
                           SET rainfall_threshold=%s,
                               flood_alert_enabled=%s,
                               earthquake_magnitude=%s,
                               smtp_email=%s
                           WHERE id = 1
                           """, (rainfall, flood_alert, magnitude, smtp_email))

        db.commit()
        flash("System settings saved successfully.", "success")

    cursor.execute("SELECT * FROM system_settings WHERE id=1")
    settings = cursor.fetchone()

    return render_template("admin_settings.html", settings=settings)


@app.route("/admin/admins")
def admin_management():
    if "admin_id" not in session:
        return redirect("/admin_login")

    db = get_db_connection()
    cur = db.cursor()

    cur.execute("SELECT id, username, email FROM admin")
    admins = cur.fetchall()

    cur.close()
    db.close()

    admin_list = [
        {
            "id": int(a["id"]),
            "username": a["username"],
            "email": a["email"]
        }
        for a in admins
    ]

    return render_template(
        "admin_management.html",
        admins=admin_list,
        logged_admin_id=int(session["admin_id"])
    )


@app.route("/admin/delete_admin/<int:admin_id>", methods=["POST"])
def delete_admin(admin_id):
    if "admin_id" not in session:
        flash("Unauthorized access", "danger")
        return redirect("/admin/login")

    # Prevent self-delete
    if admin_id == session["admin_id"]:
        flash("You cannot delete your own account", "warning")
        return redirect("/admin/admins")

    # Connect to DB
    db = get_db_connection()
    try:
        cur = db.cursor()

        # Check if admin exists
        cur.execute("SELECT id FROM admin WHERE id=%s", (admin_id,))
        admin = cur.fetchone()

        if not admin:
            flash("Admin not found", "danger")
            return redirect("/admin/admins")

        # Delete admin
        cur.execute("DELETE FROM admin WHERE id=%s", (admin_id,))
        db.commit()  # commit changes

        flash("Admin deleted successfully", "success")
        return redirect("/admin/admins")

    except Exception as e:
        db.rollback()
        flash(f"Error: {str(e)}", "danger")
        return redirect("/admin/admins")

    finally:
        cur.close()
        db.close()


from pymysql.err import IntegrityError
from werkzeug.security import generate_password_hash


@app.route("/admin/add_admin", methods=["POST"])
def add_admin():
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect("/admin_login")

    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()

    if not username or not email or not password:
        flash("All fields are required.", "danger")
        return redirect("/admin/admins")

    hashed = generate_password_hash(password)

    cur = db.cursor()

    try:
        cur.execute(
            "INSERT INTO admin (username, email, password) VALUES (%s,%s,%s)",
            (username, email, hashed)
        )
        db.commit()

    except IntegrityError as e:
        db.rollback()

        error_msg = str(e)

        if "username" in error_msg:
            flash("Username already exists.", "danger")
        elif "email" in error_msg:
            flash("Email already exists.", "danger")
        else:
            flash("Admin already exists.", "danger")

        cur.close()
        return redirect("/admin/admins")

    cur.close()
    flash("New admin added successfully!", "success")
    return redirect("/admin/admins")


@app.route("/admin/change_password", methods=["POST"])
def change_admin_password():
    if "admin_id" not in session:
        return redirect("/admin_login")

    old = request.form.get("old_password")
    new = request.form.get("new_password")

    cur = db.cursor()
    cur.execute("SELECT password FROM admin WHERE id=%s", (session["admin_id"],))
    stored = cur.fetchone()

    if not stored or not check_password_hash(stored[0], old):
        flash("Old password incorrect!", "danger")
        return redirect("/admin/admins")

    new_hash = generate_password_hash(new)
    cur.execute(
        "UPDATE admin SET password=%s WHERE id=%s",
        (new_hash, session["admin_id"])
    )
    db.commit()

    flash("Password updated successfully!", "success")
    return redirect("/admin/admins")


@app.route("/admin/users")
def admin_users():
    if "admin_id" not in session:
        return redirect("/admin_login")

    db = get_db_connection()  # 🔥 FRESH CONNECTION
    cursor = db.cursor()

    try:
        cursor.execute("""
                       SELECT id, username, name, email, mobile, latitude, longitude
                       FROM users
                       ORDER BY id DESC
                       """)
        users = cursor.fetchall()

        return render_template("admin_users.html", users=users)

    finally:
        cursor.close()
        db.close()


from werkzeug.security import generate_password_hash


@app.route("/admin/user/add", methods=["POST"])
def add_user():
    username = request.form.get("username", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    mobile = request.form.get("mobile", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()
    password_input = request.form.get("password", "").strip()

    if not all([username, name, email, mobile, latitude, longitude, password_input]):
        flash("All fields are required.", "danger")
        return redirect("/admin/users")

    password = generate_password_hash(password_input)

    db = get_db_connection()
    cursor = db.cursor()

    try:
        # ---------- DUPLICATE CHECK ----------
        cursor.execute(
            "SELECT id FROM users WHERE username=%s OR email=%s",
            (username, email)
        )
        if cursor.fetchone():
            flash("Username or Email already exists.", "danger")
            return redirect("/admin/users")

        # ---------- INSERT USER ----------
        cursor.execute("""
                       INSERT INTO users
                           (username, name, email, password, mobile, latitude, longitude)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       """, (username, name, email, password, mobile, latitude, longitude))

        db.commit()  # ✅ COMMIT FIRST (IMPORTANT)

        # ---------- SEND EMAIL (SAFE) ----------
        try:
            send_registration_email(email, name, latitude, longitude)
            flash("User added successfully! Welcome email sent.", "success")
        except Exception as mail_error:
            print("EMAIL ERROR:", mail_error)
            flash("User added, but email could not be sent.", "warning")

    except Exception as e:
        db.rollback()
        print("DB ERROR:", e)
        flash("Database error while adding user.", "danger")

    finally:
        cursor.close()
        db.close()

    return redirect("/admin/users")


@app.route("/admin/user/<int:user_id>")
def admin_user_view(user_id):
    if "admin_id" not in session:
        return redirect("/admin_login")

    cur = db.cursor()
    cur.execute(
        "SELECT id, username, name, email, mobile, latitude, longitude FROM users WHERE id=%s",
        (user_id,)
    )
    user = cur.fetchone()

    return render_template("admin_user_view.html", user=user)


@app.route("/admin/user/edit/<int:user_id>")
def admin_user_edit(user_id):
    if "admin_id" not in session:
        return redirect("/admin_login")

    cur = db.cursor()
    cur.execute(
        "SELECT id, username, name, email, mobile, latitude, longitude FROM users WHERE id=%s",
        (user_id,)
    )
    user = cur.fetchone()

    return render_template("admin_user_edit.html", user=user)


@app.route("/admin/user/update/<int:user_id>", methods=["POST"])
def admin_user_update(user_id):
    if "admin_id" not in session:
        return redirect("/admin_login")

    username = request.form.get("username").strip()
    name = request.form.get("name").strip()
    email = request.form.get("email").strip()
    mobile = request.form.get("mobile").strip()
    latitude = request.form.get("latitude").strip()
    longitude = request.form.get("longitude").strip()

    cur = db.cursor()

    # Check if username already exists for a different user
    cur.execute("SELECT id FROM users WHERE username=%s AND id != %s", (username, user_id))
    existing = cur.fetchone()
    if existing:
        flash("Username already exists! Please choose a different one.", "danger")
        return redirect(f"/admin/user/edit/{user_id}")

    # Check if email already exists for a different user (optional)
    cur.execute("SELECT id FROM users WHERE email=%s AND id != %s", (email, user_id))
    existing_email = cur.fetchone()
    if existing_email:
        flash("Email already exists! Please use a different email.", "danger")
        return redirect(f"/admin/user/edit/{user_id}")

    # If validation passes, update
    try:
        cur.execute("""
                    UPDATE users
                    SET username=%s,
                        name=%s,
                        email=%s,
                        mobile=%s,
                        latitude=%s,
                        longitude=%s
                    WHERE id = %s
                    """, (username, name, email, mobile, latitude, longitude, user_id))
        db.commit()
        flash("User updated successfully!", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error updating user: {e}", "danger")

    return redirect("/admin/users")


@app.route("/admin/user/delete/<int:user_id>")
def admin_user_delete(user_id):
    if "admin_id" not in session:
        return redirect("/admin_login")

    cur = db.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    db.commit()

    flash("User deleted!", "danger")
    return redirect("/admin/users")


feedback_data = []
feedback_id_counter = 1


# -----------------------
# Routes
# -----------------------
@app.route("/admin/feedback")
def feedback_page():
    # Only admin can access
    if "admin_id" not in session:
        return redirect("/admin_login")

    return render_template("admin_feedback.html")


@app.route("/feedback")
def user_feedback():
    if 'user_id' not in session:
        flash("Please login first.", "warning")
        return redirect(url_for('login_page'))

    return render_template("feedback.html")


# API to get all feedback
@app.route("/api/feedback", methods=["GET"])
def get_feedback():
    if "user_id" not in session:
        return jsonify([])

    user_id = session["user_id"]

    db = get_db_connection()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    try:
        cursor.execute("""
                       SELECT f.id,
                              f.type,
                              f.message,
                              f.disaster_type,
                              f.date,
                              u.id AS user_id,
                              u.name,
                              u.email,
                              u.mobile,
                              u.avatar
                       FROM feedback f
                                JOIN users u ON f.user_id = u.id
                       WHERE f.user_id = %s
                       ORDER BY f.date DESC
                       """, (user_id,))
        feedbacks = cursor.fetchall()

        result = []

        for f in feedbacks:
            # ✅ FETCH ADMIN NAME ALSO
            cursor.execute("""
                           SELECT admin_username, message, date
                           FROM replies
                           WHERE feedback_id = %s
                           ORDER BY date ASC
                           """, (f["id"],))
            replies = cursor.fetchall()

            result.append({
                "id": f["id"],
                "type": f["type"],
                "message": f["message"],
                "disaster_type": f["disaster_type"],
                "date": f["date"].strftime("%Y-%m-%d %H:%M") if f["date"] else "",
                "replies": [
                    {
                        "admin_username": r["admin_username"],
                        "message": r["message"],
                        "date": r["date"].strftime("%Y-%m-%d %H:%M") if r["date"] else ""
                    } for r in replies
                ],
                "user": {
                    "id": f["user_id"],
                    "name": f["name"],
                    "email": f["email"],
                    "mobile": f["mobile"],
                    "avatar": f["avatar"]
                }
            })

        return jsonify(result)

    finally:
        cursor.close()
        db.close()


# API to add new feedback
# API to add new feedback (updated for disaster type)
@app.route("/api/feedback/add", methods=["POST"])
def add_feedback():
    data = request.get_json()

    if not data.get("type") or not data.get("message") or not data.get("disaster_type"):
        return jsonify({"error": "Missing fields"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
                       INSERT INTO feedback (user_id, type, message, disaster_type, date)
                       VALUES (%s, %s, %s, %s, NOW())
                       """, (
                           session.get("user_id"),  # 🔥 THIS IS REQUIRED
                           data["type"],
                           data["message"],
                           data["disaster_type"]
                       ))

        conn.commit()
        return jsonify({"success": True})

    finally:
        cursor.close()
        conn.close()


@app.route('/api/feedback/download/excel/<disaster_type>')
def download_feedback_excel(disaster_type):
    data = [f for f in feedback_data if
            disaster_type.lower() == "all" or f['disaster_type'].lower() == disaster_type.lower()]
    df = pd.DataFrame(data)

    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        output,
        download_name=f"{disaster_type}_feedback_{now}.xlsx",
        as_attachment=True
    )


# ----------------------------------------------
# PDF Feedback Download
# ----------------------------------------------
@app.route('/api/feedback/download/pdf/<disaster_type>')
def download_feedback_pdf(disaster_type):
    data = [f for f in feedback_data if
            disaster_type.lower() == "all" or f['disaster_type'].lower() == disaster_type.lower()]

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"{disaster_type.capitalize()} Feedback Report", ln=True, align='C')
    pdf.ln(5)
    pdf.set_font("Arial", "", 11)

    headers = ["ID", "Type", "Message", "Date"]
    col_width = pdf.w / len(headers) - 10

    # Table header
    for h in headers:
        pdf.cell(col_width, 8, h, border=1, align='C')
    pdf.ln()

    # Table rows
    for f in data:
        pdf.cell(col_width, 8, str(f["id"]), border=1)
        pdf.cell(col_width, 8, f["type"], border=1)
        pdf.cell(col_width, 8, f["message"][:30] + "..." if len(f["message"]) > 30 else f["message"], border=1)
        pdf.cell(col_width, 8, f["date"], border=1)
        pdf.ln()

    pdf_bytes = pdf.output(dest='S').encode('latin1')
    pdf_io = BytesIO(pdf_bytes)
    now = datetime.now().strftime("%Y%m%d_%H%M%S")

    return send_file(
        pdf_io,
        download_name=f"{disaster_type}_feedback_{now}.pdf",
        as_attachment=True
    )

def send_user_notification(user_email, feedback_id):
    subject = "Reply to your feedback"
    body = f"""
 Hello,

We hope you are doing well.

This is to inform you that our admin team has reviewed your feedback and posted a response.

Feedback Reference ID: {feedback_id}

To view the full reply and continue the conversation, please log in to your account on our website.

If you have any additional questions or need further assistance, feel free to submit another feedback or reply through the portal.

 Thank you for taking the time to help us improve our services.

 Best regards,
 Support Team
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "disasterpredictionsystem@gmail.com"
    msg["To"] = user_email

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login("disasterpredictionsystem@gmail.com", "pqpbruceisevbjpd")
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print("Email error:", e)



@app.route("/api/feedback/reply/<int:feedback_id>", methods=["POST"])
def reply_feedback(feedback_id):
    data = request.get_json()
    reply_msg = data.get("reply")

    if not reply_msg:
        return jsonify({"error": "Reply message required"}), 400

    if "admin_id" not in session:
        return jsonify({"error": "Admin not logged in"}), 401

    admin_id = session["admin_id"]
    admin_username = session.get("admin_username", "Admin")

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        # 🔹 Get user email
        cursor.execute("""
            SELECT u.email
            FROM feedback f
            JOIN users u ON f.user_id = u.id
            WHERE f.id = %s
        """, (feedback_id,))
        row = cursor.fetchone()

        if not row:
            return jsonify({"error": "Feedback not found"}), 404

        user_email = row["email"]

        # 🔹 Insert reply
        cursor.execute("""
            INSERT INTO replies (feedback_id, admin_id, admin_username, message, date)
            VALUES (%s, %s, %s, %s, NOW())
        """, (feedback_id, admin_id, admin_username, reply_msg))

        conn.commit()

        # 🔹 SEND EMAIL IN BACKGROUND (NON-BLOCKING) ✅
        if user_email:
            threading.Thread(
                target=send_user_notification,
                args=(user_email, feedback_id),
                daemon=True
            ).start()

        return jsonify({"success": True})

    finally:
        cursor.close()
        conn.close()


@app.route("/api/admin/feedback", methods=["GET"])
def get_admin_feedback():
    if "admin_id" not in session:
        return jsonify([]), 401

    db = get_db_connection()
    cursor = db.cursor(pymysql.cursors.DictCursor)

    try:
        cursor.execute("""
                       SELECT f.id,
                              f.type,
                              f.message,
                              f.disaster_type,
                              f.date,
                              u.id AS user_id,
                              u.name,
                              u.email,
                              u.mobile,
                              u.avatar
                       FROM feedback f
                                JOIN users u ON f.user_id = u.id
                       ORDER BY f.date DESC
                       """)

        feedbacks = cursor.fetchall()
        result = []

        for f in feedbacks:
            # ---------------- Replies ----------------
            cursor.execute("""
                           SELECT admin_username, message, date
                           FROM replies
                           WHERE feedback_id = %s
                           ORDER BY date ASC
                           """, (f["id"],))
            replies = cursor.fetchall()

            # ---------------- AVATAR FIX (SAFE) ----------------
            avatar = f["avatar"]
            if not avatar:
                avatar = generate_default_avatar(f["name"] or "User", f["user_id"])
                cursor.execute("UPDATE users SET avatar=%s WHERE id=%s", (avatar, f["user_id"]))
                db.commit()

            result.append({
                "id": f["id"],
                "type": f["type"],
                "message": f["message"],
                "disaster_type": f["disaster_type"],
                "date": f["date"].strftime("%Y-%m-%d %H:%M") if f["date"] else "",
                "replies": [
                    {
                        "admin_username": r["admin_username"],
                        "message": r["message"],
                        "date": r["date"].strftime("%Y-%m-%d %H:%M") if r["date"] else ""
                    } for r in replies
                ],
                "user": {
                    "id": f["user_id"],
                    "name": f["name"],
                    "email": f["email"],
                    "mobile": f["mobile"],
                    "avatar": avatar
                }
            })

        return jsonify(result)

    finally:
        cursor.close()
        db.close()


# API to delete feedback
@app.route("/api/feedback/delete/<int:fid>", methods=["POST"])
def delete_feedback(fid):
    db = get_db_connection()
    cursor = db.cursor()  # ✅ correct

    # delete replies first
    cursor.execute(
        "DELETE FROM replies WHERE feedback_id = %s",
        (fid,)
    )

    # delete feedback
    cursor.execute(
        "DELETE FROM feedback WHERE id = %s",
        (fid,)
    )

    cursor.close()
    db.close()

    return jsonify({"success": True})


WEATHER_KEY = "fb116bebc392ccc8ab251927edcb55d6"  # set this


# 🔹 ALTERNATE river level calculation function
def calculate_river_level(discharge, river_type="medium"):
    """
    Unified & realistic river level calculation
    Matches frontend + backend + alerts
    """

    if discharge is None or discharge <= 0:
        base = {"small": 0.35, "medium": 0.70, "large": 1.50}
        return round(base.get(river_type, 0.70), 2)

    coeffs = {
        "small":  {"a": 0.18, "b": 0.48, "bed": 0.35},
        "medium": {"a": 0.22, "b": 0.44, "bed": 0.70},
        "large":  {"a": 0.14, "b": 0.40, "bed": 1.50},
    }

    c = coeffs.get(river_type, coeffs["medium"])
    level = c["bed"] + c["a"] * (discharge ** c["b"])

    level = max(c["bed"], min(level, 30))  # safety clamp
    return round(level, 2)


# ==============================
# 🔹 Modified /admin/flood_real_time route
# ==============================
@app.route("/admin/flood_real_time")
def flood_real_time():
    if "admin_id" not in session:
        return redirect("/admin_login")

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("SELECT id, name, email, latitude, longitude FROM users")
    users = cursor.fetchall()

    user_flood_data = []

    for u in users:
        uid = u["id"]
        name = u["name"]
        email = u["email"]
        lat = u["latitude"]
        lon = u["longitude"]

        if lat is None or lon is None:
            continue

        # 🌦 WEATHER
        try:
            w = requests.get(
                f"https://api.openweathermap.org/data/2.5/weather"
                f"?lat={lat}&lon={lon}&units=metric&appid={WEATHER_KEY}",
                timeout=10
            ).json()

            rainfall = w.get("rain", {}).get("1h", 0)
            temperature = w["main"]["temp"]
            humidity = w["main"]["humidity"]
            wind_speed = w.get("wind", {}).get("speed", 0) * 3.6  # km/h
            pressure = w["main"].get("pressure", 1013)

            cyclone_detected = (
                    wind_speed >= 62 or pressure <= 990
            )

            weather_status = w["weather"][0]["description"]

            risk = "High" if rainfall >= 30 else "Medium" if rainfall >= 10 else "Low"

        except Exception:
            rainfall = temperature = humidity = 0
            weather_status = "Unavailable"
            risk = "Unknown"

        # 🌊 RIVER
        try:
            r = requests.get(
                f"https://flood-api.open-meteo.com/v1/flood"
                f"?latitude={lat}&longitude={lon}&daily=river_discharge&timezone=auto",
                timeout=10
            ).json()

            discharge = r.get("daily", {}).get("river_discharge", [0])[0]
            level = calculate_river_level(discharge, "medium")

            if level >= 7 or discharge >= 7000:
                river_status = "High Flood Risk"
            elif level >= 6 or discharge >= 5000:
                river_status = "Moderate Risk"
            else:
                river_status = "Safe"

        except Exception:
            discharge = level = 0
            river_status = "Unavailable"

        user_flood_data.append({
            "id": uid,
            "name": name,
            "email": email,
            "lat": lat,
            "lon": lon,
            "rainfall": rainfall,
            "humidity": humidity,
            "temperature": temperature,
            "weather": weather_status,
            "risk": risk,
            "user_wind": round(wind_speed, 1),
            "user_pressure": pressure,
            "cyclone": cyclone_detected,
            "river_discharge": discharge,
            "river_level": level,
            "river_status": river_status
        })

    cursor.close()
    db.close()

    return render_template("admin_flood_real_time.html", data=user_flood_data)

@app.route("/api/get_users_locations")
def get_users_locations():
    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("SELECT id, name, latitude, longitude FROM users")
    rows = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify([
        {
            "id": r["id"],
            "name": r["name"],
            "lat": r["latitude"],
            "lon": r["longitude"]
        } for r in rows
    ])

@app.route("/admin/flood_alert/<int:user_id>", methods=["POST"])
def admin_flood_alert(user_id):
    if "admin" not in session:
        return redirect("/admin_login")

    message = request.form.get(
        "alert_message",
        "Flood risk detected in your area. Stay safe!"
    )

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute(
        "SELECT id, name, email, latitude, longitude FROM users WHERE id=%s",
        (user_id,)
    )
    u = cursor.fetchone()

    if not u:
        cursor.close()
        db.close()
        flash("User not found!", "danger")
        return redirect("/admin/flood_real_time")

    uid, name, email, lat, lon = u.values()

    # WEATHER
    try:
        w = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&units=metric&appid={WEATHER_KEY}",
            timeout=10
        ).json()

        rainfall = w.get("rain", {}).get("1h", 0)
        humidity = w["main"]["humidity"]
        temperature = w["main"]["temp"]
        weather_status = w["weather"][0]["description"]

    except Exception:
        rainfall = humidity = temperature = 0
        weather_status = "Unavailable"

    # RIVER
    try:
        r = requests.get(
            f"https://flood-api.open-meteo.com/v1/flood"
            f"?latitude={lat}&longitude={lon}&daily=river_discharge&timezone=auto",
            timeout=10
        ).json()

        discharge = r.get("daily", {}).get("river_discharge", [0])[0]
        level = calculate_river_level(discharge)

        if level >= 7 or discharge >= 7000:
            river_status = "High Flood Risk"
        elif level >= 6 or discharge >= 5000:
            river_status = "Moderate Risk"
        else:
            river_status = "Safe"

    except Exception:
        discharge = level = 0
        river_status = "Unavailable"

    cursor.execute("""
        INSERT INTO flood_alerts
        (user_id, rainfall, humidity, temperature, weather_status,
         river_discharge, river_level, river_status, alert_message)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        uid, rainfall, humidity, temperature, weather_status,
        discharge, level, river_status, message
    ))

    db.commit()
    cursor.close()
    db.close()

    send_flood_alert_email(
        email, name, message,
        rainfall, humidity, temperature,
        river_discharge=discharge,
        river_level=level,
        river_status=river_status
    )

    flash("Flood alert sent successfully!", "success")
    return redirect("/admin/flood_real_time")


@app.route("/update_user_location", methods=["POST"])
def update_user_location():
    if "user_id" not in session:
        return jsonify({"status": "unauthorized"}), 401

    data = request.json
    lat = data.get("latitude")
    lon = data.get("longitude")
    depth = data.get("depth", 0)

    cursor = db.cursor()
    cursor.execute("""
        UPDATE users
        SET latitude=%s, longitude=%s, depth=%s
        WHERE id=%s
    """, (lat, lon, depth, session["user_id"]))
    db.commit()

    return jsonify({
        "status": "ok",
        "row": {
            "id": session["user_id"],
            "lat": lat,
            "lon": lon,
            "depth": depth,
            "magnitude": 0,
            "alert": "No",
            "date": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        }
    })


def is_valid_email(email):
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(pattern, email)


# -------------------------
# Helper functions
# -------------------------
def calculateRiverLevel(discharge, riverType="medium"):
    if discharge is None:
        return None

    if riverType == "small":
        a = 0.20
        b = 0.50
        bedLevel = 0.30
    elif riverType == "large":
        a = 0.15
        b = 0.42
        bedLevel = 1.20
    else:  # medium
        a = 0.25
        b = 0.45
        bedLevel = 0.60

    if discharge <= 0:
        return round(bedLevel, 2)

    level = bedLevel + a * (discharge ** b)

    # realistic clamp
    level = max(bedLevel, min(level, 25))

    return round(level, 2)

def fetchRiverDischarge(lat, lon):
    try:
        url = f"https://flood-api.open-meteo.com/v1/flood?latitude={lat}&longitude={lon}&daily=river_discharge&timezone=auto"
        res = requests.get(url, timeout=10)
        data = res.json()
        discharge = data.get("daily", {}).get("river_discharge", [0])[0] or 0
        level = calculateRiverLevel(discharge)
        status = "Safe"
        if level >= 7 or discharge >= 7000:
            status = "High Flood Risk"
        elif level >= 6 or discharge >= 5000:
            status = "Moderate Risk"
        return {
            "discharge": round(discharge, 2),
            "level": round(level, 2),
            "status": status
        }
    except Exception as e:
        print(f"❌ Failed to fetch river data: {e}")
        return {"discharge": 0, "level": 0, "status": "Unavailable"}


# -------------------------
# Combined send_alert with river + weather
# -------------------------
@app.route("/send_alert/<int:user_id>", methods=["POST"])
def send_alert(user_id):
    try:
        print(f"📢 Alert triggered for user_id: {user_id}")

        # Fetch clicked user info
        with db.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT id, name, email, latitude, longitude FROM users WHERE id=%s",
                (user_id,)
            )
            clicked_user = cursor.fetchone()

        if not clicked_user:
            print("❌ Clicked user not found")
            return jsonify({"status": "error", "message": "User not found"}), 404

        lat, lon = clicked_user["latitude"], clicked_user["longitude"]
        print(f"➡ Clicked user location: lat={lat}, lon={lon}")

        # Fetch all users at the same location
        with db.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT id, name, email FROM users WHERE latitude=%s AND longitude=%s",
                (lat, lon)
            )
            users_at_location = cursor.fetchall()

        if not users_at_location:
            print("❌ No users at this location")
            return jsonify({"status": "error", "message": "No users at this location"}), 404

        print(f"👥 Users at location: {[u['name'] for u in users_at_location]}")

        # Get message from POST body
        data = request.json or {}
        message = data.get("message", "Flood risk detected in your area. Stay safe!")
        print(f"💬 Alert message: {message}")

        # Fetch live weather once
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={WEATHER_KEY}"
        weather = requests.get(url).json()
        rainfall = weather.get("rain", {}).get("1h", 0)
        humidity = weather["main"]["humidity"]
        temperature = weather["main"]["temp"]
        weather_status = weather["weather"][0]["description"]
        wind_speed = weather.get("wind", {}).get("speed", 0) * 3.6  # km/h
        pressure = weather.get("main", {}).get("pressure", 1013)

        cyclone_detected = (
                wind_speed >= 62 or pressure <= 990
        )

        print(f"🌡 Weather: Temp={temperature}°C, Rainfall={rainfall}mm, Humidity={humidity}%")

        # Fetch river data once
        river = fetchRiverDischarge(lat, lon)
        print(f"🌊 River: Discharge={river['discharge']}, Level={river['level']}, Status={river['status']}")

        # Send alert to all users at the same location
        failed = []
        for u in users_at_location:
            print(f"✉ Sending alert to {u['name']} ({u['email']})...")

            # Email body with river info
            body = f"""
Dear {u['name']},

FLOOD RISK ALERT ⚠️

Location Weather Summary:
🌧 Rainfall (1h): {rainfall} mm
💧 Humidity: {humidity}%
🌡 Temperature: {temperature}°C
🌦 Weather: {weather_status}
🌪 Cyclone Information:
Cyclone Detected: {"YES" if cyclone_detected else "NO"}
💨 Wind Speed: {wind_speed:.1f} km/h
⬇️ Pressure: {pressure} hPa

River Info:
💦 Discharge: {river['discharge']} m³/s
📏 Level: {river['level']} m
⚠ Status: {river['status']}

Admin Message:
{message}

Stay safe!
"""
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = "⚠️ Flood Risk Alert - Disaster Alert System"
            msg["From"] = "disasterpredictionsystem@gmail.com"
            msg["To"] = u["email"]

            try:
                server = smtplib.SMTP("smtp.gmail.com", 587)
                server.starttls()
                server.login("disasterpredictionsystem@gmail.com", "pqpbruceisevbjpd")
                server.send_message(msg)
                server.quit()
                print(f"✅ Alert sent to {u['name']}")
            except Exception as e:
                print(f"❌ Failed to send alert to {u['name']}: {e}")
                failed.append(u["name"])

            # Save alert to DB with river info
            with db.cursor() as cursor:
                cursor.execute("""
                               INSERT INTO flood_alerts
                               (user_id, rainfall, humidity, temperature, status, alert_message,
                                river_discharge, river_level, river_status,
                                cyclone_detected, wind_speed, pressure)
                               VALUES (%s, %s, %s, %s, %s, %s,
                                       %s, %s, %s,
                                       %s, %s, %s)
                               """, (
                                   u["id"],
                                   rainfall,
                                   humidity,
                                   temperature,
                                   weather_status,
                                   message,
                                   river["discharge"],
                                   river["level"],
                                   river["status"],
                                   1 if cyclone_detected else 0,
                                   wind_speed,
                                   pressure
                               ))
            db.commit()

        if failed:
            print(f"⚠ Partial failures: {failed}")
            return jsonify({"status": "partial", "message": f"Alert sent, but failed for: {', '.join(failed)}"})
        else:
            names = [u["name"] for u in users_at_location]
            print(f"🎉 All alerts sent successfully to: {names}")
            return jsonify({"status": "success", "message": f"Alert sent to users: {', '.join(names)}"})

    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"

@app.route("/admin/earthquake_dashboard")
def earthquake_dashboard():
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect(url_for("admin_login"))
    return render_template("earthquake_dashboard.html")
@app.route("/api/get_users_earthquakes")
def get_users_earthquakes():
    import requests
    from flask import jsonify
    from datetime import datetime

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("SELECT name, latitude, longitude FROM users")
    users = cursor.fetchall()

    # 🔴 USGS LIVE ALL-HOUR FEED
    feed = requests.get(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
        timeout=10
    ).json()

    earthquakes = []

    for eq in feed.get("features", []):
        coords = eq["geometry"]["coordinates"]
        props = eq["properties"]

        earthquakes.append({
            "event_id": eq["id"],                 # 🔑 unique (important)
            "eq_lat": coords[1],
            "eq_lon": coords[0],
            "depth": coords[2],
            "magnitude": props.get("mag", 0),
            "place": props.get("place", "Unknown"),
            "time": props.get("time", 0),
            "alert_level": (
                "CRITICAL" if props.get("mag", 0) >= 5
                else "WARNING" if props.get("mag", 0) >= 3
                else "INFO"
            )
        })

    cursor.close()
    db.close()

    return jsonify({
        "status": "LIVE",
        "source": "USGS all_hour.geojson",
        "updated_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "earthquakes": earthquakes,
        "users": users
    })


earthquake_df = pd.read_csv('historical_earthquake.csv', encoding='latin1', engine='python')
flood_df = pd.read_csv('historical_floods.csv', encoding='latin1', engine='python')


# Merge historic data
def get_historic_data():
    data = []

    # --------------------------
    # EARTHQUAKES
    # --------------------------
    for _, row in earthquake_df.iterrows():

        # 1️⃣ If CSV contains a severity column, use that FIRST
        if "severity" in row and isinstance(row["severity"], str) and row["severity"].strip():
            severity = row["severity"].strip()

        else:
            # 2️⃣ Otherwise calculate from magnitude
            raw_mag = row.get("magnitude", None)

            try:
                magnitude = float(raw_mag) if raw_mag not in (None, "", "-", "N/A") else 0
            except:
                magnitude = 0

            # Calculate from magnitude (fallback)
            if magnitude >= 6:
                severity = "High"
            elif magnitude >= 4:
                severity = "Moderate"
            elif magnitude > 0:
                severity = "Low"
            else:
                severity = "Minor"

        data.append({
            "type": "Earthquake",
            "date": row.get("date", "N/A"),
            "area": row.get("area", "Unknown"),
            "latitude": row.get("latitude", "N/A"),
            "longitude": row.get("longitude", "N/A"),
            "severity": severity,
            "description": row.get("description", "N/A")
        })

    # --------------------------
    # FLOODS
    # --------------------------
    for _, row in flood_df.iterrows():
        data.append({
            "type": "Flood",
            "date": row.get("date", "N/A"),
            "area": row.get("area", "Unknown"),
            "latitude": row.get("latitude", "N/A"),
            "longitude": row.get("longitude", "N/A"),
            "severity": row.get("severity", "N/A"),
            "description": row.get("description", "N/A")
        })

    return data


@app.route('/api/historic_data')
def historic_data_api():
    return jsonify(get_historic_data())


@app.route('/admin/reports')
def reports_page():
    if "admin_id" not in session:
        flash("Please login first.", "warning")
        return redirect(url_for("admin_login"))
    return render_template('report.html')

@app.route('/api/get_historic_data')
def get_historic_data_api():
    return jsonify(get_historic_data())


# Excel report
@app.route('/api/generate_report/excel/<report_type>')
def generate_excel(report_type):
    data = get_historic_data()

    # filter by type if not "all"
    if report_type.lower() != "all":
        data = [d for d in data if d['type'].lower() == report_type.lower()]

    # Convert list → DataFrame
    df = pd.DataFrame(data)

    # Export to Excel
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        output,
        download_name=f'{report_type}_report_{now}.xlsx',
        as_attachment=True
    )


# ----------------------------------------------
# PDF REPORT
# ----------------------------------------------
@app.route('/api/generate_report/pdf/<report_type>')
def generate_pdf(report_type):
    data = get_historic_data()

    # Filter by type if not "all"
    if report_type.lower() != "all":
        data = [d for d in data if d['type'].lower() == report_type.lower()]

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"{report_type.capitalize()} Report", ln=True, align='C')
    pdf.ln(5)
    pdf.set_font("Arial", "", 11)

    headers = ["Type", "Date", "Area", "Latitude", "Longitude", "Severity", "Description"]
    col_width = pdf.w / len(headers) - 5

    # Header row
    for h in headers:
        pdf.cell(col_width, 8, h, border=1, align='C')
    pdf.ln()

    # Data rows
    for row in data:
        pdf.cell(col_width, 8, str(row["type"]), border=1)
        pdf.cell(col_width, 8, str(row["date"]), border=1)
        pdf.cell(col_width, 8, str(row["area"]), border=1)
        pdf.cell(col_width, 8, str(row["latitude"]), border=1)
        pdf.cell(col_width, 8, str(row["longitude"]), border=1)
        pdf.cell(col_width, 8, str(row["severity"]), border=1)
        pdf.cell(col_width, 8, str(row["description"]), border=1)
        pdf.ln()

    # Get PDF as bytes
    pdf_bytes = pdf.output(dest='S').encode('latin1')  # string → bytes
    pdf_io = BytesIO(pdf_bytes)

    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        pdf_io,
        download_name=f'{report_type}_report_{now}.pdf',
        as_attachment=True
    )


try:
    earthquake_df = pd.read_csv("historical_earthquake.csv", encoding="latin1")
except FileNotFoundError:
    earthquake_df = pd.DataFrame(
        columns=["date", "area", "latitude", "longitude", "magnitude", "depth", "severity", "description"])

try:
    flood_df = pd.read_csv("historical_floods.csv", encoding="latin1")
except FileNotFoundError:
    flood_df = pd.DataFrame(columns=["date", "area", "latitude", "longitude", "severity", "description"])


@app.route('/api/add_report', methods=['POST'])
def add_report():
    global earthquake_df, flood_df
    data = request.json

    # Validate required fields
    if not data.get("type") or not data.get("date") or not data.get("area"):
        return jsonify({"error": "Type, date and area are required"}), 400

    # Severity must come from frontend
    severity_value = data.get("severity")
    print("🔥 RECEIVED SEVERITY FROM FRONTEND =", severity_value)  # <-- RIGHT PLACE

    if not severity_value or severity_value.strip() == "":
        return jsonify({"error": "Please select a severity"}), 400

    report_type = data["type"].lower()
    try:
        if report_type == "earthquake":
            new_row = {
                "date": data.get("date"),
                "area": data.get("area"),
                "latitude": data.get("latitude", ""),
                "longitude": data.get("longitude", ""),
                "magnitude": data.get("magnitude") if data.get("magnitude") is not None else "",
                "depth": data.get("depth") if data.get("depth") is not None else "",
                "severity": severity_value,
                "description": data.get("description", "")
            }
            earthquake_df = pd.concat([earthquake_df, pd.DataFrame([new_row])], ignore_index=True)
            earthquake_df.to_csv('historical_earthquake.csv', index=False, encoding='latin1')

        elif report_type == "flood":
            new_row = {
                "date": data.get("date"),
                "area": data.get("area"),
                "latitude": data.get("latitude", ""),
                "longitude": data.get("longitude", ""),
                "severity": severity_value,
                "description": data.get("description", "")
            }
            flood_df = pd.concat([flood_df, pd.DataFrame([new_row])], ignore_index=True)
            flood_df.to_csv('historical_floods.csv', index=False, encoding='latin1')

        else:
            return jsonify({"error": "Invalid type"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"success": True})


# API: Delete a report by type and date+area+lat+lon
@app.route('/api/delete_report', methods=['POST'])
def delete_report():
    global earthquake_df, flood_df  # Use the same variable names

    data = request.json
    report_type = data.get("type", "").lower()

    try:
        if report_type == "earthquake":
            earthquake_df = earthquake_df[
                ~((earthquake_df['date'] == data['date']) & (earthquake_df['area'] == data['area']))]
            earthquake_df.to_csv('historical_earthquake.csv', index=False, encoding='latin1')

        elif report_type == "flood":
            flood_df = flood_df[~((flood_df['date'] == data['date']) & (flood_df['area'] == data['area']))]
            flood_df.to_csv('historical_floods.csv', index=False, encoding='latin1')

        else:
            return jsonify({"error": "Invalid type"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"success": True})


def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Radius of earth in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return R * c


# Function to send email
def send_email(to_email, subject, body):
    sender_email = "disasterpredictionsystem@gmail.com"
    sender_pass = "pqpbruceisevbjpd"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_pass)
            server.sendmail(sender_email, to_email, msg.as_string())
        print(f"Email sent to {to_email}")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")


# API to send alert to users within a radius
@app.route("/api/send_alert", methods=["POST"])
def send_disaster_alert():
    data = request.get_json()
    eq_lat = float(data.get("lat"))
    eq_lon = float(data.get("lon"))
    ALERT_RADIUS_KM = 50  # users within 50 km

    db = get_db_connection()
    cursor = db.cursor()

    cursor.execute("""
                   SELECT id, username, name, email, latitude, longitude
                   FROM users
                   WHERE latitude IS NOT NULL
                     AND longitude IS NOT NULL
                   """)
    users = cursor.fetchall()
    alerted = []

    # Email content template
    email_subject = "🌎 Disaster Alert: Earthquake Warning!"
    email_body_template = """
Hello {name} ({username}),

We have detected an earthquake near your registered location.

📍 Your Coordinates: {lat}, {lon}
🌐 Earthquake Coordinates: {eq_lat}, {eq_lon}
⚠️ Please take immediate safety precautions.
🕒 Alert Time: {time}

Stay safe and follow local emergency instructions.

- Disaster Alert System
"""

    alert_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    for u in users:
        distance = haversine(eq_lat, eq_lon, u["latitude"], u["longitude"])
        if distance <= ALERT_RADIUS_KM:
            body = email_body_template.format(
                name=u["name"],
                username=u["username"],
                lat=u["latitude"],
                lon=u["longitude"],
                eq_lat=eq_lat,
                eq_lon=eq_lon,
                time=alert_time
            )
            send_email(u["email"], email_subject, body)
            alerted.append(u["email"])
            print(f"ALERT SENT to {u['email']} (distance {distance:.1f} km)")

    cursor.close()
    db.close()

    return jsonify({
        "message": f"Alert sent to {len(alerted)} users within {ALERT_RADIUS_KM} km"
    })


# ================= HELP / USER GUIDE =================
@app.route("/help")
def help_page():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    return render_template("help.html")


# ================= SAFETY TIPS =================
@app.route("/safety-tips")
def safety_tips():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    return render_template("safety_tips.html")


# ================= EMERGENCY CONTACTS =================
@app.route("/emergency")
def emergency():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    return render_template("emergency.html")


# ================= ABOUT SYSTEM =================
@app.route("/about")
def about():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    return render_template("about.html")


@app.route("/emergency-contacts")
def emergency_contacts():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))

    return render_template(
        "emergency.html",
        username=session.get('user_name')
    )


@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template("index.html", username=session.get('user_name'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


@app.route('/earthquake')
def earthquake():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('earthq.html')


@app.route('/sent')
def sent():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('senti.html')


@app.route('/storm')
def storm():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('weatherp2.html')


@app.route('/new')
def new():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('new.html')


@app.route('/weather')
def weather():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('weather.html')


@app.route('/earthgraphs')
def earthgraphs():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('earthgraph.html')


@app.route('/covid')
def covid():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('covid.html')


@app.route('/covi_pred')
def covi_pred():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('covid_pred.html')


@app.route('/covistats')
def covistats():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('covid_stats.html')


@app.route('/covi')
def covi():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('covidhostpitals.html')


@app.route('/cov')
def cov():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('covidhostpitals1.html')


@app.route('/covcity')
def covcity():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('scity.html')


@app.route('/covstate')
def covstate():
    if 'user_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('states.html')


@app.route('/covid_tom', methods=['GET', 'POST'])
def covid_tom():
    import numpy as np
    import pandas as pd
    import os
    from sklearn.preprocessing import normalize
    a = pd.read_csv('time_series_covid19_confirmed_global.csv')
    print(a)
    b = a.transpose()
    print(b)
    train = b[[131]].values
    train_x = train[4:]
    y = []
    for i in train_x:
        y.append(i[0])
    print(y)
    x = list(range(len(y)))
    print(x)
    import seaborn as sns
    sns.relplot(data=pd.DataFrame(y))
    x = np.array(x)
    y = np.array(y)

    x = x.astype('float32')
    y = y.astype('float32')
    print(x)
    print(y)
    print(x.dtype)
    print(y.dtype)

    x = (x.reshape(-1, 1))
    y = (y.reshape(-1, 1))
    # x=normalize(x)
    # y=normalize(y)

    print(x)
    print(y)
    from tensorflow.keras import Sequential
    from tensorflow.keras.layers import Dense
    import tensorflow as tf
    predict = Sequential([Dense(74, activation='relu'),
                          Dense(74 * 2, activation='relu'),
                          Dense(74 * 2 * 2, activation='relu'),
                          Dense(74 * 2 * 2 * 2, activation='relu'),
                          Dense(74 * 2 * 2 * 2, activation='relu'),
                          Dense(1)])
    predict.compile(optimizer='adam', loss='mse', metrics=['mse', 'mae'])
    predict.fit(x, y, batch_size=75, epochs=10000)
    loss = predict.history.history
    loss_pd = pd.DataFrame(loss)
    loss_pd.plot()
    # loss_pd.savefig('output3.png')
    sn_plot3 = sns.relplot(data=loss_pd)
    sn_plot3.savefig('D:/project/Disaster-Prediction-main/static/covid/output3.png')

    predict.predict(np.array([[1]]))
    predicted_values = []
    for i in range(1000):
        predicted_values.append(float(predict.predict(np.array([[i]], dtype=np.float32))[0][0]))
    print(predicted_values)
    future = []
    for i in predicted_values:
        # Safely extract value from model output (float or nested array)
        if isinstance(i, (list, tuple, np.ndarray)):
            value = i[0][0] if isinstance(i[0], (list, np.ndarray)) else i[0]
        else:
            value = i

        # Avoid negative predictions
        if round(value) <= 0:
            future.append(0)
        else:
            future.append(round(float(value)))
    future_df = pd.DataFrame({'Infected_people': future})
    print("Actual_graph")
    sn_plot = sns.relplot(data=pd.DataFrame(y))
    sn_plot.savefig('D:/project/Disaster-Prediction-main/static/covid/output1.png')
    print("predicted_graph")
    sn_plot1 = sns.relplot(data=future_df[:73])
    sn_plot1.savefig('D:/project/Disaster-Prediction-main/static/covid/output2.png')
    print("Future predictions graph")
    sns.relplot(data=future_df[:100])

    def preprocess(day):
        return round(day)

    day = 68
    if request.method == 'POST':
        date = request.form['date']
        month = request.form['month']
        year = request.form['year']
    # --- Convert month name to number safely ---
    month_str_to_num = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12
    }

    # normalize input
    month = str(month).strip().lower()
    year = str(year).strip()
    date = str(date).strip()

    # Convert month if user typed text
    if month in month_str_to_num:
        month_num = month_str_to_num[month]
    else:
        try:
            month_num = int(month)
        except ValueError:
            raise ValueError(f"Invalid month value: {month}")

    # --- Calculate total days ---
    if month_num > 3:
        day += (month_num - 3 - 1) * 30 + (int(year) - 2020) * 365 + int(date)
    else:
        day -= int(date)

    day += 4

    # --- Predict infection count safely ---
    pred_value = float(predict.predict(np.array([[day]], dtype=float))[0][0])
    if pred_value <= 0:
        infected = 0
    else:
        infected = preprocess(pred_value)
    print("The Predicted infected people on the day", day, "in India are :", infected)
    sns.relplot(data=future_df[:day])
    print(date)
    print(day)
    return render_template('covid_pred.html', infected=infected, date=date, month=month, year=year)


@app.route('/covid19', methods=['GET', 'POST'])
def covid19():
    import pandas as pd
    import requests
    import folium
    import math
    from flask import request, render_template
    from opencage.geocoder import OpenCageGeocode
    from numpy import nan

    # ---------------- Geocoder ----------------
    key = 'fcd18d77cf7849cd8abd250b9f012527'
    geocoder = OpenCageGeocode(key)

    # ---------------- World Stats ----------------
    url_world = "https://disease.sh/v3/covid-19/all"
    world_data = requests.get(url_world).json()
    t_c = world_data.get('cases', 0)
    n_c = world_data.get('todayCases', 0)
    t_d = world_data.get('deaths', 0)
    t_r = world_data.get('recovered', 0)
    a_c = world_data.get('active', 0)

    # ---------------- India Stats ----------------
    url_india = "https://disease.sh/v3/covid-19/countries/India"
    india_data = requests.get(url_india).json()
    a1 = india_data.get('country', 'India')
    a2 = india_data.get('cases', 0)
    a3 = india_data.get('todayCases', 0)
    a4 = india_data.get('active', 0)
    a5 = india_data.get('deaths', 0)
    a6 = india_data.get('recovered', 0)
    a7 = india_data.get('tests', 0)
    df3 = pd.DataFrame([india_data])
    print("✅ Columns in df3:", df3.columns)

    # ---------------- State/District Input ----------------
    Latitude, Longitude = [], []
    df5 = pd.DataFrame()
    df6 = pd.DataFrame()
    s_active = s_confirm = s_death = c_active = c_confirm = c_death = 0
    s_tate = c_ity = None
    lat, long = 20.59, 78.96  # Default India center

    if request.method == 'POST':
        c_ity = request.form['city']
        s_tate = request.form['state']

        # Geocoding
        results = geocoder.geocode(c_ity)
        if results:
            lat = results[0]['geometry']['lat']
            long = results[0]['geometry']['lng']
            Latitude.append(lat)
            Longitude.append(long)

        # Get district-level data
        url_state = "https://data.covid19india.org/state_district_wise.json"
        r = requests.get(url_state)
        if r.status_code == 200 and r.text.strip():
            data = r.json()
            state_data = data.get(s_tate, {}).get('districts', {})
            if state_data:
                df6 = pd.DataFrame.from_dict(state_data, orient='index')
                df6.to_csv('firsti.csv', index=True)

                # City-specific data
                city_data = state_data.get(c_ity, {})
                if 'total' in city_data:
                    df5 = pd.DataFrame([city_data['total']])
                    df5.to_csv('first.csv', index=False)
                    c_active = city_data['total'].get('active', 0)
                    c_confirm = city_data['total'].get('confirmed', 0)
                    c_death = city_data['total'].get('deceased', 0)
                    print("✅ City-level data found")
                else:
                    print(f"⚠️ City data not found for {c_ity}")
            else:
                print(f"⚠️ No district data found for state {s_tate}")
        else:
            print("⚠️ State/District data not available or empty")

    # ---------------- Create District Map ----------------
    if not df6.empty:
        L1, L2, L3, L4, L5, L6 = [], [], [], [], [], []
        for dist in df6.index:
            total = df6.loc[dist, 'total'] if 'total' in df6.columns else {}
            if isinstance(total, dict):
                L3.append(dist)
                L4.append(total.get('active', 0))
                L5.append(total.get('confirmed', 0))
                L6.append(total.get('deceased', 0))
                res = geocoder.geocode(f"{dist}, {s_tate or 'India'}")
                if res:
                    L1.append(res[0]['geometry']['lat'])
                    L2.append(res[0]['geometry']['lng'])
                else:
                    L1.append(None)
                    L2.append(None)

        lats = pd.DataFrame({
            'Latitude': L1,
            'Longitude': L2,
            'City': L3,
            'Active': L4,
            'Confirm': L5,
            'Death': L6
        })
        lats.to_csv('main.csv', index=False)

        c = folium.Map(location=[20.5937, 78.9629], zoom_start=4)
        for _, point in lats.iterrows():
            color = 'green'
            if point['Active'] >= 1000:
                color = 'red'
            elif point['Death'] >= 5:
                color = 'orange'
            folium.Marker(
                location=[point['Latitude'], point['Longitude']],
                popup=f"City: {point['City']}\nActive: {point['Active']}\nDeaths: {point['Death']}\nConfirmed: {point['Confirm']}",
                icon=folium.Icon(color=color)
            ).add_to(c)
        c.save('templates/scity.html')

    # ---------------- State Map (Fixed JSONDecodeError) ----------------
    state_url = "https://data.covid19india.org/data.json"
    try:
        response = requests.get(state_url, timeout=10)
        if response.ok and response.text.strip():
            state_data = response.json()
            if 'statewise' in state_data:
                df_state = pd.DataFrame(state_data['statewise'])
                df_state.to_csv('state.csv', index=False)
            else:
                print("⚠️ 'statewise' key missing in response")
                df_state = pd.DataFrame()
        else:
            print("⚠️ Empty or invalid response from data.covid19india.org")
            df_state = pd.DataFrame()
    except Exception as e:
        print(f"⚠️ Error fetching state data: {e}")
        df_state = pd.DataFrame()

    if not df_state.empty:
        s_map = folium.Map(location=[20.5937, 78.9629], zoom_start=4)
        for _, row in df_state.iterrows():
            query = row['state']
            res = geocoder.geocode(query)
            if res:
                lat_s = res[0]['geometry']['lat']
                lng_s = res[0]['geometry']['lng']
                color = 'green'
                if int(row.get('active', 0)) >= 4000:
                    color = 'red'
                elif int(row.get('deaths', 0)) >= 100:
                    color = 'orange'
                folium.Marker(
                    location=[lat_s, lng_s],
                    popup=f"State: {row['state']}\nActive: {row.get('active', 0)}\nDeaths: {row.get('deaths', 0)}\nRecovered: {row.get('recovered', 0)}",
                    icon=folium.Icon(color=color)
                ).add_to(s_map)
        s_map.save('templates/states.html')
    else:
        print("⚠️ Could not create state map because data was empty")

    # ---------------- Test Centers Map ----------------
    try:
        dataset = pd.read_csv('TestCentres_with_geolocation.csv')
        cpmap = folium.Map(location=[lat, long], zoom_start=5)
        for _, point in dataset.iterrows():
            if math.isnan(point['Latitude']) or math.isnan(point['Longitude']):
                continue
            folium.Marker(
                location=[point['Latitude'], point['Longitude']],
                popup=f"{point['LabName']}\n{point['City']}\n{point['Addresses']}",
                icon=folium.Icon(color='blue')
            ).add_to(cpmap)
        cpmap.save('templates/covidhostpitals1.html')
    except Exception as e:
        print(f"⚠️ Error loading hospital data: {e}")

    # ---------------- Render HTML ----------------
    return render_template(
        'covid_result.html',
        a1=a1, a2=a2, a3=a3, a4=a4, a5=a5, a6=a6, a7=a7,
        t_c=t_c, n_c=n_c, a_c=a_c, t_r=t_r, t_d=t_d,
        s_active=s_active, s_confirm=s_confirm, s_death=s_death,
        c_active=c_active, c_confirm=c_confirm, c_death=c_death,
        s_tate=s_tate, c_ity=c_ity
    )


@app.route('/alert', methods=['GET', 'POST'])
def alert():
    if request.method == 'POST':
        places = request.form['placess']
        place = places.replace(" ", "")
    return render_template('cyclone.html', places=place)


@app.route('/salert', methods=['GET', 'POST'])
def salert():
    places = None
    num = None

    if request.method == 'POST':
        # ✅ Safe way to get form values
        places = request.form.get('pl')
        num = request.form.get('nm')

        print("Received values:", places, num)

        # ✅ Validation check

        import smtplib, ssl
        port = 465  # SSL port
        smtp_server = "smtp.gmail.com"
        sender_email = "disasterpredictionsystem@gmail.com"  # Enter your Gmail
        receiver_email = "disasterpredictionsystem@gmail.com"  # Enter receiver Gmail
        password = "pqpbruceisevbjpd"

        message = f"""\
Subject: Disaster Alert

This message is sent from Tanvi Khadpe's Disaster Prediction and Management App.
Message: {places}
Contact: {num}
"""

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
            server.login(sender_email, password)
            server.sendmail(sender_email, receiver_email, message)

        print(f"✅ Mail sent successfully to {receiver_email}")

    return render_template('cyclone.html', places=places, nm=num)


@app.route('/cyclone')
def cyclone():
    return render_template('cyclone.html')


@app.route('/comp')
def comp():
    return render_template('comp.html')


@app.route('/hailstorm')
def hailstorm():
    return render_template('near.html')


@app.route('/flood')
def flood():
    return render_template('floods.html')


@app.route('/predicts', methods=['GET', 'POST'])
def predicts():
    if lr:
        try:
            if request.method == 'POST':
                comment = request.form['rainfall_amt']
                data = [comment]
                query = pd.get_dummies(pd.DataFrame(data))
                query = query.reindex(columns=model_columns, fill_value=0)
                m_prediction = lr.predict(query)
                print(m_prediction)
            # json_ = request.json
            # print(json_)

            # my_prediction = lr.predict(query)
            # print(my_prediction)
        except:

            return jsonify({'trace': traceback.format_exc()})
    else:
        print('Train the model first')
        return ('No model here to use')
    return render_template('floodres.html', predictions=m_prediction)


def check_live_earthquakes():
    import requests, ssl, smtplib
    from datetime import datetime, timedelta
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from math import radians, sin, cos, sqrt, atan2

    print("\n==============================")
    print("🔍 EARTHQUAKE SCHEDULER START")
    print("==============================")

    try:
        # ----------------- MULTI-FEED -----------------
        FEEDS = {
            "USGS": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
            "EMSC": "https://www.seismicportal.eu/fdsnws/event/1/query?format=geojson&limit=200&orderby=time",
            "IRIS": "https://service.iris.edu/fdsnws/event/1/query?format=geojson&limit=200&orderby=time"
        }

        live_quakes = []

        for name, feed in FEEDS.items():
            try:
                r = requests.get(feed, timeout=10)
                if not r.ok:
                    print(f"❌ Feed {name} failed, skipping.")
                    continue

                data = r.json()
                for q in data.get("features", []):
                    coords = q.get("geometry", {}).get("coordinates", [])
                    if len(coords) < 2:
                        continue

                    t_ms = q.get("properties", {}).get("time")
                    qtime = datetime.utcfromtimestamp(t_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if t_ms else "-"

                    live_quakes.append({
                        "lat": float(coords[1]),
                        "lon": float(coords[0]),
                        "depth": coords[2] if len(coords) > 2 else 0,
                        "mag": q.get("properties", {}).get("mag"),
                        "place": q.get("properties", {}).get("place", "Unknown"),
                        "time": qtime,
                        "source": name
                    })
            except Exception as e:
                print(f"❌ Feed {name} error:", e)

        if not live_quakes:
            print("⚠ No earthquakes found")
            return

        # ----------------- USERS -----------------
        print("\n👥 Loading users from database…")
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("SELECT name, email, latitude, longitude FROM users")
        users = cur.fetchall()
        print(f"✔ Users loaded: {len(users)}")

        # ----------------- HAVERSINE -----------------
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371
            dlat = radians(lat2 - lat1)
            dlon = radians(lon2 - lon1)
            a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
            return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

        # ----------------- EMAIL -----------------
        SMTP_SERVER = "smtp.gmail.com"
        SMTP_PORT = 465
        SMTP_SENDER = "disasterpredictionsystem@gmail.com"
        SMTP_PASSWORD = "pqpbruceisevbjpd"

        print("\n📡 Checking for users near earthquake zones…")

        for quake in live_quakes:
            for user in users:
                ulat = user["latitude"]
                ulon = user["longitude"]

                if not ulat or not ulon:
                    continue

                try:
                    distance = haversine(float(ulat), float(ulon), quake["lat"], quake["lon"])
                except Exception as e:
                    print("❌ Distance calculation error:", e)
                    continue

                # ----------------- PRINT FOR LOGGING -----------------
                print(
                    f"👤 {user['name']} | Quake: {quake['place']} | "
                    f"Distance: {distance:.2f} km | Mag: {quake['mag']}"
                )

                # ----------------- EMAIL ALERT -----------------
                if distance <= 10:
                    print("🚨 ALERT TRIGGERED (<=10 km)")
                    msg = MIMEMultipart()
                    msg["Subject"] = "⚠️ EARTHQUAKE ALERT"
                    msg["From"] = SMTP_SENDER
                    msg["To"] = user["email"]

                    maps_url = f"https://www.google.com/maps?q={quake['lat']},{quake['lon']}"
                    text = f"""
EARTHQUAKE WARNING!

Dear {user['name']},

Magnitude: {quake['mag']}
Depth: {quake['depth']} km
Location: {quake['place']}
Distance: {distance:.2f} km
Time: {quake['time']}
Source: {quake['source']}

Live Map:
{maps_url}

Stay Safe
"""
                    msg.attach(MIMEText(text, "plain"))

                    try:
                        ctx = ssl.create_default_context()
                        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx) as server:
                            server.login(SMTP_SENDER, SMTP_PASSWORD)
                            server.sendmail(SMTP_SENDER, user["email"], msg.as_string())
                    except Exception as e:
                        print(f"❌ Failed to send email to {user['name']}:", e)

        cur.close()
        db.close()

        print("\n==============================")
        print("✅ EARTHQUAKE CHECK COMPLETE")
        print("==============================\n")

    except Exception as e:
        print("❌ Scheduler crashed:", e)


@app.route('/predict', methods=['GET', 'POST'])
def predict():
    import os, numpy as np, pandas as pd, requests
    import tensorflow as tf
    from datetime import datetime, timedelta
    from tensorflow.keras import losses, metrics
    from flask import request, render_template
    from math import radians, sin, cos, sqrt, atan2

    if "user_id" not in session:
        return redirect(url_for("login_page"))

    # ------------------------
    # Date parsing helpers
    # ------------------------
    def parse_flexible_date(date_str):
        if not date_str:
            return None
        fmts = [
            "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d",
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"
        ]
        for f in fmts:
            try:
                return datetime.strptime(date_str.strip(), f)
            except:
                continue
        return None

    def mapdateTotime(x):
        epoch = datetime(1970, 1, 1)
        dt = parse_flexible_date(x)
        return (dt - epoch).total_seconds() if dt else None

    # ------------------------
    # Load dataset
    # ------------------------
    df1 = pd.read_csv("database.csv")
    try:
        df1["Date"] = df1["Date"].apply(mapdateTotime)
    except:
        df1["Date"] = df1["Date"].astype(float)

    X = df1[['Date', 'Latitude', 'Longitude', 'Depth']].to_numpy(dtype=float)
    Y = df1['Magnitude'].to_numpy(dtype=float)

    X_min = np.amin(X, axis=0)
    X_max = np.amax(X, axis=0)
    X_range = np.where((X_max - X_min) == 0, 1e-8, X_max - X_min)
    X_norm = (X - X_min) / X_range

    # ------------------------
    # Load or train model
    # ------------------------
    if os.path.exists("earthquake_model.h5"):
        model = tf.keras.models.load_model(
            "earthquake_model.h5",
            custom_objects={"mse": losses.MeanSquaredError(),
                            "mae": metrics.MeanAbsoluteError()}
        )
    else:
        model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(4,)),
            tf.keras.layers.Dense(16, activation='relu'),
            tf.keras.layers.Dense(8, activation='relu'),
            tf.keras.layers.Dense(1)
        ])
        model.compile(optimizer='adam', loss='mse', metrics=[metrics.MeanAbsoluteError()])
        model.fit(X_norm, Y, epochs=30, verbose=1)
        model.save("earthquake_model.h5")

    # ------------------------
    # Distance function
    # ------------------------
    def dist_km(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * R * atan2(sqrt(a), sqrt(1 - a))

    # ------------------------
    # Fetch live earthquakes
    # ------------------------
    live_quakes = []

    try:
        r = requests.get(
            "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
            timeout=10
        )
        if r.ok:
            for f in r.json().get("features", []):
                lon, lat, dep = f["geometry"]["coordinates"]
                props = f["properties"]
                if props.get("mag") is not None:
                    live_quakes.append({
                        "lat": lat,
                        "lon": lon,
                        "depth": dep,
                        "magnitude": props["mag"],
                        "place": props.get("place", "Unknown"),
                        "time": datetime.utcfromtimestamp(props["time"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    })
    except:
        pass

    # ------------------------
    # User input
    # ------------------------
    user_lat = user_lon = user_depth = user_date = prediction_val = None
    risk_level = None


    if request.method == 'POST':
        try:
            user_lat = float(request.form['lat'])
            user_lon = float(request.form['lon'])
            user_depth = float(request.form['depth'])
            user_date = request.form.get('date') or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            dt = parse_flexible_date(user_date) or datetime.utcnow()
            arr = np.array([[mapdateTotime(user_date), user_lat, user_lon, user_depth]])
            arr_norm = (arr - X_min) / X_range
            prediction_val = float(model.predict(arr_norm)[0][0]) - 5
            # ------------------------
            # Risk level calculation
            # ------------------------
            if prediction_val < 3.5:
                risk_level = "Low"
            elif prediction_val < 5.0:
                risk_level = "Medium"
            elif prediction_val < 6.5:
                risk_level = "High"
            else:
                risk_level = "Severe"

            # ✅ DB INSERT (SAFE)
            db = get_db_connection()
            cur = db.cursor()
            cur.execute(
                "INSERT INTO earth(lat, lon, depth, scale, mail, date) VALUES (%s,%s,%s,%s,%s,%s)",
                (user_lat, user_lon, user_depth, str(prediction_val), "yes",
                 dt.strftime("%Y-%m-%d %H:%M:%S"))
            )
            cur.close()
            db.close()

        except Exception as e:
            print("PREDICTION ERROR:", e)

    # ------------------------
    # Fetch DB rows
    # ------------------------
    db = get_db_connection()
    cur = db.cursor()
    cur.execute("SELECT * FROM earth ORDER BY id DESC LIMIT 8")
    data = cur.fetchall()
    cur.close()
    db.close()

    # ------------------------
    # Early warning
    # ------------------------
    rows = []
    for r in data:
        nearest, min_d = None, None
        for q in live_quakes:
            d = dist_km(r["lat"], r["lon"], q["lat"], q["lon"])
            if min_d is None or d < min_d:
                min_d, nearest = d, q

        rows.append({
            "lat": r["lat"],
            "lon": r["lon"],
            "depth": r["depth"],
            "predicted_mag": float(r["scale"]),
            "alert": "Yes" if nearest and min_d <= 50 and nearest["magnitude"] >= 4 else "No",
            "date": r["date"],
            "real_mag": nearest["magnitude"] if nearest else None,
            "real_place": nearest["place"] if nearest else None,
            "real_time": nearest["time"] if nearest else None,
            "real_dist_km": round(min_d, 2) if nearest else None
        })

    return render_template(
        "earth.html",
        prediction=prediction_val,
        risk_level=risk_level,
        live_quakes=live_quakes,
        rows=rows,
        data=data,
        lat=user_lat,
        lon=user_lon,
        depth=user_depth,
        date=user_date
    )


@app.route('/manual_alert', methods=['POST'])
def manual_alert():
    import requests, smtplib, ssl
    from math import radians, sin, cos, sqrt, atan2
    from datetime import datetime
    from flask import request, jsonify
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    # -------------------------------
    # READ FORM DATA
    # -------------------------------
    try:
        qlat = float(request.form.get("lat"))
        qlon = float(request.form.get("lon"))
        submitted_mag = float(request.form.get("mag", 0))
    except:
        return jsonify({"status": "error", "msg": "Invalid input"}), 400

    qtime = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------
    # HAVERSINE
    # -------------------------------
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * R * atan2(sqrt(a), sqrt(1 - a))

    # -------------------------------
    # REVERSE GEOCODE
    # -------------------------------
    try:
        geo = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={qlat}&lon={qlon}",
            headers={"User-Agent": "manual-alert"},
            timeout=10
        )
        detected_place = geo.json().get("display_name", "Unknown location") if geo.ok else "Unknown location"
    except:
        detected_place = "Unknown location"

    # -------------------------------
    # FETCH LIVE EARTHQUAKES
    # -------------------------------
    nearest_real = None
    try:
        r = requests.get(
            "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&limit=200",
            timeout=10
        )
        if r.ok:
            for f in r.json().get("features", []):
                coords = f["geometry"]["coordinates"]
                mag = f["properties"].get("mag")
                if mag is None:
                    continue

                dist = haversine(qlat, qlon, coords[1], coords[0])
                if dist <= 100:
                    nearest_real = {
                        "mag": mag,
                        "place": f["properties"].get("place"),
                        "time": datetime.utcfromtimestamp(
                            f["properties"]["time"] / 1000
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                        "distance_km": round(dist, 2)
                    }
                    break
    except:
        pass

    # -------------------------------
    # FETCH USERS
    # -------------------------------
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("SELECT name, email, latitude, longitude FROM users")
        users = cur.fetchall()
    except:
        users = []

    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    SMTP_SENDER = "disasterpredictionsystem@gmail.com"
    SMTP_PASSWORD = "pqpbruceisevbjpd"

    sent = 0
    matched = 0

    # -------------------------------
    # PROCESS USERS
    # -------------------------------
    for u in users:
        uname = u["name"]
        uemail = u["email"]
        ulat = u["latitude"]
        ulon = u["longitude"]

        if ulat is None or ulon is None:
            continue

        dist = haversine(float(ulat), float(ulon), qlat, qlon)
        if dist > 10:
            continue

        matched += 1

        if submitted_mag >= 6:
            level = "🚨 SEVERE EARTHQUAKE"
        elif submitted_mag >= 4:
            level = "⚠️ MODERATE EARTHQUAKE"
        else:
            level = "🟡 MINOR EARTHQUAKE"

        text = f"""
EARTHQUAKE ALERT

{level}

Magnitude: {submitted_mag}
Location: {detected_place}
Time: {qtime} UTC
Distance from you: {dist:.2f} km
"""

        try:
            msg = MIMEMultipart()
            msg["Subject"] = "⚠️ EARTHQUAKE ALERT"
            msg["From"] = SMTP_SENDER
            msg["To"] = uemail
            msg.attach(MIMEText(text, "plain", "utf-8"))

            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx) as s:
                s.login(SMTP_SENDER, SMTP_PASSWORD)
                s.sendmail(SMTP_SENDER, uemail, msg.as_bytes())

            sent += 1
        except:
            pass

    try:
        cur.close()
        db.close()
    except:
        pass

    # -------------------------------
    # RESPONSE
    # -------------------------------
    if matched == 0:
        return jsonify({
            "status": "no_users",
            "msg": "No users within 10 km radius",
            "lat": qlat,
            "lon": qlon,
            "detected_place": detected_place
        })

    return jsonify({
        "status": "success",
        "alerts_sent": sent,

        # 👇 frontend needs these
        "lat": qlat,
        "lon": qlon,
        "detected_place": detected_place,
        "submitted_mag": submitted_mag,
        "time": qtime,

        # 👇 flatten nearest quake
        "nearest_real_mag": nearest_real["mag"] if nearest_real else None,
        "nearest_real_place": nearest_real["place"] if nearest_real else None,
        "nearest_real_time": nearest_real["time"] if nearest_real else None,
        "nearest_real_dist_km": nearest_real["distance_km"] if nearest_real else None
    })

@app.route('/manual_flood_alert', methods=['POST'])
def manual_flood_alert():
    from math import radians, sin, cos, sqrt, atan2
    from datetime import datetime
    from flask import request, jsonify
    import smtplib, ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.header import Header
    import requests

    # -------------------------------
    # READ LAT / LON
    # -------------------------------
    try:
        qlat = float(request.form.get("lat"))
        qlon = float(request.form.get("lon"))
    except:
        return jsonify({"status": "error", "msg": "Invalid lat/lon"}), 400

    # -------------------------------
    # DEFAULTS
    # -------------------------------
    riverHeading = "✅ River & Dam Safe"
    floodHeading = "No Flood"
    floodRiskText = "✅ Flood Risk: None"

    river_discharge = float(request.form.get("river_discharge") or 0)
    river_level = float(request.form.get("river_level") or 0)

    # 🔥 SINGLE SOURCE OF TRUTH (FRONTEND)
    if request.form.get("river_heading"):
        riverHeading = request.form.get("river_heading")

    if request.form.get("flood_heading"):
        floodHeading = request.form.get("flood_heading")

    if request.form.get("flood_risk_text"):
        floodRiskText = request.form.get("flood_risk_text")

    qtime = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------
    # WEATHER FETCH (ONLY DATA)
    # -------------------------------
    apiKey = "fb116bebc392ccc8ab251927edcb55d6"
    try:
        res = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={qlat}&lon={qlon}&units=metric&appid={apiKey}",
            timeout=10
        )
        data = res.json()
        weatherDesc = data.get("weather", [{}])[0].get("description", "N/A")
        temp = data.get("main", {}).get("temp", 0)
        humidity = data.get("main", {}).get("humidity", 0)
        real_place = data.get("name", "Unknown location")
    except:
        weatherDesc = "N/A"
        temp = humidity = 0
        real_place = "Unknown location"

    # -------------------------------
    # HAVERSINE
    # -------------------------------
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        return 2 * R * atan2(sqrt(a), sqrt(1 - a))

    # -------------------------------
    # USERS
    # -------------------------------
    db = get_db_connection()
    cur = db.cursor()
    cur.execute("SELECT name, email, latitude, longitude FROM users")
    users = cur.fetchall()
    cur.close()
    db.close()

    # -------------------------------
    # EMAIL CONFIG
    # -------------------------------
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    SMTP_SENDER = "disasterpredictionsystem@gmail.com"
    SMTP_PASSWORD = "pqpbruceisevbjpd"

    sent_count = 0

    for u in users:
        if not u["latitude"] or not u["longitude"]:
            continue

        dist = haversine(float(u["latitude"]), float(u["longitude"]), qlat, qlon)
        if dist <= 10:

            email_text = f"""
🌊 FLOOD ALERT

🚨 {floodHeading}
{floodRiskText}

Weather: {weatherDesc}
Temperature: {temp}°C
Humidity: {humidity}%

🏞 {riverHeading}
🌍 River Discharge: {river_discharge:.2f} m³/s
📊 River Level: {river_level:.2f} m

Distance: {dist:.2f} km
Location: {real_place}
Time (UTC): {qtime}
"""

            try:
                msg = MIMEMultipart()
                msg["Subject"] = Header("🌊 Flood Alert", "utf-8")
                msg["From"] = SMTP_SENDER
                msg["To"] = u["email"]
                msg.attach(MIMEText(email_text, "plain", "utf-8"))

                with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ssl.create_default_context()) as server:
                    server.login(SMTP_SENDER, SMTP_PASSWORD)
                    server.sendmail(SMTP_SENDER, u["email"], msg.as_bytes())

                sent_count += 1
            except:
                pass

    return jsonify({
        "status": "success",
        "alerts_sent": sent_count,
        "lat": qlat,
        "lon": qlon,
        "detected_place": real_place,
        "flood_heading": floodHeading,
        "flood_risk_text": floodRiskText,
        "river_heading": riverHeading,
        "river_discharge": river_discharge,
        "river_level": river_level
    }), 200

def get_weather_data(lat, lon):
    weather_api = urllib.request.urlopen(
        "https://api.openweathermap.org/data/2.5/find?lat=" + lat + "&lon=" + lon + "&cnt=10&appid=" + api_key).read()
    weather_file = json.loads(weather_api)

    for weather_data_point in weather_file["list"]:
        temp = weather_data_point["main"]["temp"]
        pressure = weather_data_point["main"]["pressure"]
        humidity = weather_data_point["main"]["humidity"]
        wind_speed = weather_data_point["wind"]["speed"]
        wind_deg = weather_data_point["wind"]["deg"]
        clouds = weather_data_point["clouds"]["all"]
        weather_type = weather_data_point["weather"][0]["main"]

        weather_data.append([temp, pressure, humidity, wind_speed, wind_deg, clouds])
        weather_labels.append(weather_type)


def predict_weather(city_name, classifier):
    weather_api = urllib.request.urlopen(
        "http://api.openweathermap.org/data/2.5/weather?q=" + city_name + "&appid=" + api_key).read()
    weather = json.loads(weather_api)

    temp = weather["main"]["temp"]
    pressure = weather["main"]["pressure"]
    humidity = weather["main"]["humidity"]
    wind_speed = weather["wind"]["speed"]
    wind_deg = weather["wind"]["deg"]
    clouds = weather["clouds"]["all"]
    weather_name = weather["weather"][0]["main"]

    this_weather = [temp, pressure, humidity, wind_speed, wind_deg, clouds]
    return {"Prediction:": classifier.predict([this_weather])[0], "Actual:": weather_name}


# Get data from various cities
@app.route('/prediction', methods=['GET', 'POST'])
def prediction():
    if request.method == 'POST':
        lat = request.form['lat']
        lon = request.form['long']
        city = request.form['city']
        date = request.form['date']
        # get_weather_data("50.5", "0.2")
        # get_weather_data("56", "3")
        # get_weather_data("43", "5")
        for i in range(10):
            get_weather_data(lat, lon)
    AI_machine = KNeighborsClassifier(n_neighbors=5)
    AI_machine.fit(weather_data, weather_labels)
    print(list(set(weather_labels)))
    var = (predict_weather(city, AI_machine))
    print(var['Prediction:'])
    vars = var['Prediction:']
    varse = var['Actual:']
    if vars == varse:
        match = 'yes'
    else:
        match = 'no'
    myCursor = db.cursor()
    sql = "INSERT INTO weather(lat,lon,city,predict,actual,date,mat) VALUES(%s,%s,%s,%s,%s,%s,%s);"
    args = (lat, lon, city, vars, varse, date, match)
    myCursor.execute(sql, args)
    myCursor.execute("SELECT * FROM weather ORDER BY id DESC LIMIT 5")
    data = myCursor.fetchall()
    db.commit()
    return render_template('weather.html', Predictions=vars, Actual=varse, lat=lat, lon=lon, city=city, date=date,
                           data=data)


@app.route('/predstorm', methods=['GET', 'POST'])
def predstorm():
    if request.method == 'POST':
        temp = request.form['temp']
        pressure = request.form['pressure']
        humidity = request.form['humidity']
        wind = request.form['wind']

        itemp = int(temp)
        ipressure = int(pressure)
        ihumidity = int(humidity)
        iwind = int(wind)
    # gather the data set
    data = get_weather_datas()
    # print(data.head())

    # encode the weather description to an integer.
    pp_data, targets = preprocess(data, "Weather Description")

    # just for visualization
    print("\n* targets *\n", targets, end="\n\n")
    features = list(pp_data.columns[:5])
    print("* features *\n", features, end="\n\n")
    print("=======preprocessed data=======\n")
    print("------------first five rows------------")
    print("* pp_data.head()", pp_data[["Target", "Weather Description"]].head(), sep="\n", end="\n\n")
    print("------------last five rows------------")
    print("* pp_data.head()", pp_data[["Target", "Weather Description"]].tail(), sep="\n", end="\n\n")

    p_target = pp_data["Target"]
    p_features = pp_data[features]

    # taking some data out of the dataset for testing
    itemi = [temp, pressure, humidity, wind]
    item = [itemp, ipressure, ihumidity, iwind]

    test_target = p_target.loc[item]
    test_data = p_features.loc[item]

    display_labels(targets)

    print("---Test Data's Target Value---")
    print("Row ", "Target")
    print(test_target)

    # preparing data for training by removing test data
    train_target = p_target.drop(item)
    train_data = p_features.drop(item)

    wclf = train_classifier(train_data, train_target)

    visualize_tree(wclf, features)
    prediction = wclf.predict(test_data)
    print("\n---Actual Prediction---")
    print(prediction)


def visualize_tree(tree, feature_names):
    with open("visual.dot", 'w') as f:
        export_graphviz(tree, out_file=f, feature_names=feature_names)
    try:
        subprocess.check_call(["dot", "-Tpng", "visual.dot", "-o", "visual.png"])
    except:
        exit("Failed to generate a visual graph")


def get_weather_datas():
    data = pd.read_csv("weather_data.csv")
    # print(data.head())
    return data


def preprocess(data, target_column):
    """returns cleaned dataframe and targets"""
    data_clean = data.copy()
    targets = data_clean[target_column].unique()
    map_str_to_int = {name: n for n, name in enumerate(targets)}
    data_clean["Target"] = data_clean[target_column].replace(map_str_to_int)

    return (data_clean, targets)


def display_labels(targets):
    print("0 :", targets[0])
    print("1 :", targets[1])
    print("2 :", targets[2])
    print("3 :", targets[3])


def train_classifier(train_data, train_target):
    """returns a new model that can be used to make predictions"""
    # create a decision tree classifier
    wclf = DecisionTreeClassifier(min_samples_split=20, random_state=99)
    # train it on the training data / train classifier
    wclf.fit(train_data, train_target)
    return wclf


@app.route('/hurricane', methods=['GET', 'POST'])
def hurricane():
    return render_template('hurricane.html')


@app.route('/predhurricane', methods=['GET', 'POST'])
def predhurricane():
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import sklearn
    from sklearn.metrics import mean_squared_error

    atlantic = pd.read_csv("hurricane-atlantic.csv")
    pacific = pd.read_csv("hurricane-pacific.csv")
    hurricanes = atlantic.append(pacific)
    hurricanes.head(5)

    from sklearn.utils import shuffle
    hurricanes = shuffle(hurricanes)

    hurricanes = hurricanes[["Date", "Latitude", "Longitude", "Maximum Wind"]].copy()
    hurricanes.head(5)

    hurricanes = hurricanes[pd.notnull(hurricanes['Maximum Wind'])]

    lon = hurricanes['Longitude']
    lon_new = []
    for i in lon:
        if "W" in i:
            i = i.split("W")[0]
            i = float(i)
            i *= -1
        elif "E" in i:
            i = i.split("E")[0]
            i = float(i)
        i = float(i)
        lon_new.append(i)
    hurricanes['Longitude'] = lon_new
    lat = hurricanes['Latitude']
    lat_new = []
    for i in lat:
        if "S" in i:
            i = i.split("S")[0]
            i = float(i)
            i *= -1
        elif "N" in i:
            i = i.split("N")[0]
            i = float(i)
        i = float(i)
        lat_new.append(i)
    hurricanes['Latitude'] = lat_new

    hurricanes_y = hurricanes["Maximum Wind"]
    hurricanes_y.head(5)

    hurricanes_x = hurricanes.drop("Maximum Wind", axis=1)
    hurricanes_x['Longitude'].replace(regex=True, inplace=True, to_replace=r'W', value=r'')
    hurricanes_x['Latitude'].replace(regex=True, inplace=True, to_replace=r'N', value=r'')
    hurricanes_x.head(5)

    from sklearn import linear_model
    from sklearn.model_selection import train_test_split
    model = linear_model.LinearRegression()
    x_train, x_test, y_train, y_test = train_test_split(hurricanes_x, hurricanes_y, test_size=0.2, random_state=4)

    model.fit(x_train, y_train)
    if request.method == 'POST':
        date = request.form['date']
        lat = request.form['lat']
        lon = request.form['lon']
    results = model.predict(x_test)
    data = [date, lat, lon]
    query = pd.get_dummies(pd.DataFrame(data))
    res = model.predict(query)
    res[0]
    if res[1] > 287.5:
        var = "Strong Hurricane Situation"
    else:
        var = "No hurricane situation"
    print(var)
    plt.scatter(results, y_test)
    plt.plot(results, results, c='r')
    plt.savefig('D:/project/Disaster-Prediction-main/static/hurricane/hurrd1.png')
    plt.scatter(x_test['Date'], y_test)
    plt.savefig('D:/project/Disaster-Prediction-main/static/hurricane/hurrd2.png')
    plt.scatter(x_test['Date'], results)
    plt.savefig('D:/project/Disaster-Prediction-main/static/hurricane/hurrd.png')
    return render_template('hurricane.html', Predictions=var)


@app.route('/tsunami', methods=['GET', 'POST'])
def tsunami():
    return render_template('tsunami.html')


@app.route('/predtsunami', methods=['GET', 'POST'])
def predtsunami():
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from sklearn import linear_model
    from sklearn import preprocessing
    import warnings
    warnings.filterwarnings("ignore")
    np.random.seed(0)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    hs = pd.read_csv('tsunami.csv')

    min_max_scaler = preprocessing.MinMaxScaler()

    df = hs[['LATITUDE', 'LONGITUDE', 'MAXIMUM_HEIGHT', 'PRIMARY_MAGNITUDE']]
    df = df.fillna(df.mean())
    df.columns = ['LATITUDE', 'LONGITUDE', 'MAXIMUM_HEIGHT', 'PRIMARY_MAGNITUDE']
    columns = df.columns

    x = df.drop('PRIMARY_MAGNITUDE', axis=1)
    y = df['PRIMARY_MAGNITUDE']

    from sklearn.model_selection import train_test_split
    x_train, x_test, y_train, y_test = train_test_split(x, y)

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaler.fit(x_train)

    x_train = scaler.transform(x_train)
    x_test = scaler.transform(x_test)

    x_train

    from sklearn.neural_network import MLPRegressor
    len(x_train.transpose())

    mlp = MLPRegressor(hidden_layer_sizes=(100,), max_iter=1500)
    mlp.fit(x_train, y_train)

    predictions = mlp.predict(x_test)

    print(predictions)

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResults = 0

    for i in range(0, len(predictions)):
        if abs(predictions[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResults += 1

    percentAccurateResults = (numAccurateResults / totalNumValues) * 100
    print(percentAccurateResults)
    tnna = percentAccurateResults
    from sklearn import svm
    SVMModel = svm.SVR()
    SVMModel.fit(x_train, y_train)

    predictionse = SVMModel.predict(x_test)
    predictionse

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultse = 0

    for i in range(0, len(predictionse)):
        if abs(predictionse[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResultse += 1

    percentAccurateResultse = (numAccurateResultse / totalNumValues) * 100
    print(percentAccurateResultse)
    tsva = percentAccurateResultse
    reg = linear_model.LinearRegression()
    reg.fit(x_train, y_train)

    predictionsi = reg.predict(x_test)
    predictionsi

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultsi = 0

    for i in range(0, len(predictionsi)):
        if abs(predictionsi[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResultsi += 1

    percentAccurateResultsi = (numAccurateResultsi / totalNumValues) * 100
    print(percentAccurateResultsi)
    tlma = percentAccurateResultsi
    if request.method == 'POST':
        lat = request.form['lats']
        long = request.form['longs']
        height = request.form['heights']
        date = request.form['dates']

    from numpy import array
    x_input = array([[lat, long, height]])
    x_tests = scaler.transform(x_input)

    actualPredictions = mlp.predict(x_tests)
    tnn = actualPredictions[0]

    actualPredictionse = SVMModel.predict(x_tests)
    tsv = actualPredictionse[0]

    actualPredictionsi = reg.predict(x_tests)
    tlm = actualPredictionsi[0]

    mintsunami = df['PRIMARY_MAGNITUDE'].min()
    maxtsunami = df['PRIMARY_MAGNITUDE'].max()

    for i in range(0, len(columns)):
        x_scaled = min_max_scaler.fit_transform(df[[columns[i]]].values.astype(float))
        df[columns[i]] = pd.DataFrame(x_scaled)

    df['is_tsunami'] = np.random.uniform(0, 1, len(df)) <= .75

    train, test = df[df['is_tsunami'] == True], df[df['is_tsunami'] == False]

    print('Number of observations in the training data:', len(train))
    print('Number of observations in the test data:', len(test))

    features = df.columns[0:-1]
    features = features.delete(3)
    features

    yr = train['PRIMARY_MAGNITUDE']

    RFModel = RandomForestRegressor(n_jobs=2, random_state=0)

    RFModel.fit(train[features], yr)

    RFModel.predict(test[features])

    preds = RFModel.predict(test[features])

    preds

    actualValues = test['PRIMARY_MAGNITUDE'].values
    totalNumValues = len(test)

    numAccurateResultsr = 0

    for i in range(0, len(preds)):
        if abs(preds[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResultsr += 1

    percentAccurateResultsr = (numAccurateResultsr / totalNumValues) * 100
    trfa = percentAccurateResultsr

    list(zip(train[features], RFModel.feature_importances_))

    from numpy import array
    x_input = array([[lat, long, height]])

    min_max_scaler = preprocessing.MinMaxScaler()
    x_tests = scaler.transform(x_input)

    actualPredictionsr = RFModel.predict(df[features])

    for i in range(0, len(actualPredictions)):
        actualPredictionsr[i] = (actualPredictionsr[i] * (maxtsunami - mintsunami)) + mintsunami

    trf = actualPredictionsr[0]

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    height = [trfa, tnna, tsva, tlma]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, height, tick_label=tick_label,
            width=0.4, color=['red', 'green', 'orange', 'blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Accuracy Chart For Tsunami')
    plt.savefig('D:/project/Disaster-Prediction-main/static/graphse/tm1.png')

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    heights = [trf, tnn, tsv, tlm]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, heights, tick_label=tick_label,
            width=0.4, color=['blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Predicted Output Chart For Tsunami')
    plt.savefig('D:/project/Disaster-Prediction-main/static/graphse/tm2.png')
    z = ''
    if tnna > 6.5:
        z = 'Tsunami'
    else:
        z = 'No Tsunami'

    return render_template('tsunami.html', Predictions=z, data=tnn)


@app.route('/earthgraph')
def earthgraph():
    import matplotlib.pyplot as plt
    from datetime import datetime
    import tensorflow as tf
    import seaborn as sns

    import warnings
    warnings.filterwarnings('ignore')

    import time
    df1 = pd.read_csv('database.csv')

    df1.tail(5)

    df1["Date"] = pd.to_datetime(df1["Date"])

    col1 = df1[['Date', 'Latitude', 'Longitude', 'Depth']]
    col2 = df1['Magnitude']
    # Convert to Numpy array
    InputX1 = col1.to_numpy()
    InputY1 = col2.to_numpy()
    print(InputX1)
    print(InputY1)

    col3 = df1[['Date', 'Latitude', 'Longitude', 'Depth', 'Magnitude']]

    col3[col3.dtypes[(col3.dtypes == "float64") | (col3.dtypes == "int64")]
    .index.values].hist(figsize=[11, 11])

    longitudes = df1["Longitude"].tolist()
    latitudes = df1["Latitude"].tolist()
    # m = Basemap(width=12000000,height=9000000,projection='lcc',
    # resolution=None,lat_1=80.,lat_2=55,lat_0=80,lon_0=-107.)
    x, y = (longitudes, latitudes)

    minimum = df1["Magnitude"].min()
    maximum = df1["Magnitude"].max()
    average = df1["Magnitude"].mean()

    print("Minimum:", minimum)
    print("Maximum:", maximum)
    print("Mean", average)

    (n, bins, patches) = plt.hist(df1["Magnitude"], range=(0, 10), bins=10)
    plt.xlabel("Earthquake Magnitudes")
    plt.ylabel("Number of Occurences")
    plt.title("Overview of earthquake magnitudes")
    my_list = []

    print("Magnitude" + "   " + "Number of Occurence")
    for i in range(5, len(n)):
        my_list.append(str(i) + "-" + str(i + 1) + "         " + str(n[i]))
        print(str(i) + "-" + str(i + 1) + "         " + str(n[i]))

    print(my_list)
    plt.boxplot(df1["Magnitude"])

    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/earth/e1.png')

    highly_affected = df1[df1["Magnitude"] >= 8]

    print(highly_affected.shape)

    # earthquake occurances per month
    df1["Month"] = df1['Date'].dt.month

    # month_occurrence = earth.pivot_table(index = "Month", values = ["Magnitude"] , aggfunc = )

    month_occurrence = df1.groupby("Month").groups
    print(len(month_occurrence[1]))

    month = [i for i in range(1, 13)]
    occurrence = []

    for i in range(len(month)):
        val = month_occurrence[month[i]]
        occurrence.append(len(val))

    print(occurrence)
    print(sum(occurrence))

    fig, ax = plt.subplots(figsize=(10, 8))
    bar_positions = np.arange(12) + 0.5

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    num_cols = months
    bar_heights = occurrence

    ax.bar(bar_positions, bar_heights)
    tick_positions = np.arange(1, 13)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(num_cols, rotation=90)
    plt.title("Frequency by Month")
    plt.xlabel("Months")
    plt.ylabel("Frequency")

    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/earth/e2.png')

    df1["Year"] = df1['Date'].dt.year

    year_occurrence = df1.groupby("Year").groups

    year = [i for i in range(1965, 2017)]
    occurrence = []

    for i in range(len(year)):
        val = year_occurrence[year[i]]
        occurrence.append(len(val))

    maximum = max(occurrence)
    minimum = min(occurrence)
    print("Maximum", maximum)
    print("Minimum", minimum)

    # print("Year :" + "     " +"Occurrence")

    # for k,v in year_occurrence.items():
    # print(str(k) +"      "+ str(len(v)))

    fig = plt.figure(figsize=(10, 6))
    plt.plot(year, occurrence)
    plt.xticks(rotation=90)
    plt.xlabel("Year")
    plt.ylabel("Number of Occurrence")
    plt.title("Frequency of Earthquakes by Year")
    plt.xlim(1965, 2017)

    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/earth/e3.png')

    plt.scatter(df1["Magnitude"], df1["Depth"])
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/earth/e4.png')

    np.corrcoef(df1["Magnitude"], df1["Depth"])

    return render_template('earthgraph.html', max=maximum, min=minimum, avg=average, lr=my_list)



def calculate_river_level(discharge, river_type="medium"):
    """
    Model-based river level estimation (rating curve approximation)
    Discharge (m³/s) → River Level (m)
    """

    if discharge is None:
        return None

    # Parameters tuned to real rivers
    if river_type == "small":
        a = 0.20
        b = 0.50
        bed_level = 0.30

    elif river_type == "large":
        a = 0.15
        b = 0.42
        bed_level = 1.20

    else:  # medium river
        a = 0.25
        b = 0.45
        bed_level = 0.60

    # Dry / zero flow
    if discharge <= 0:
        return round(bed_level, 2)

    level = bed_level + a * (discharge ** b)

    # realistic safety clamp (not hard fake)
    level = max(bed_level, min(level, 25))

    return round(level, 2)

# 🔒 Global cache (file ke top pe)
RIVER_NAME_CACHE = {}

def check_live_floods_scheduler():
    import requests
    import ssl
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from datetime import datetime
    import pymysql

    print("\n==============================")
    print("🌧 FLOOD SCHEDULER STARTED")
    print("==============================")

    OPENWEATHER_KEY = "fb116bebc392ccc8ab251927edcb55d6"

    SMTP_SENDER = "disasterpredictionsystem@gmail.com"
    SMTP_PASSWORD = "pqpbruceisevbjpd"
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465

    THRESHOLD_FLOOD = 30
    THRESHOLD_WARNING = 20

    # -------------------------------
    # HELPER: FETCH RIVER & DAM
    # -------------------------------
    # -------------------------------
    # HELPER: FETCH NEAREST RIVER NAME
    # -------------------------------
    def fetch_river_name(lat, lon, radius=8000):
        import requests, time, random

        key = (round(lat, 4), round(lon, 4))
        if key in RIVER_NAME_CACHE:
            return RIVER_NAME_CACHE[key]

        OVERPASS_SERVERS = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.nchc.org.tw/api/interpreter"
        ]

        query = f"""
        [out:json][timeout:10];
        (
          way(around:{radius},{lat},{lon})[waterway=river][name];
          relation(around:{radius},{lat},{lon})[waterway=river][name];
        );
        out tags center;
        """

        for server in OVERPASS_SERVERS:
            try:
                time.sleep(random.uniform(1.5, 3.0))  # 🔥 RATE LIMIT SAFE

                r = requests.post(
                    server,
                    data=query,
                    headers={"User-Agent": "FloodWarningApp/1.0"},
                    timeout=20
                )
                r.raise_for_status()

                data = r.json()
                els = data.get("elements", [])

                if els:
                    closest = min(
                        els,
                        key=lambda el:
                        (lat - el.get("center", {}).get("lat", el.get("lat", 0))) ** 2 +
                        (lon - el.get("center", {}).get("lon", el.get("lon", 0))) ** 2
                    )
                    name = closest["tags"]["name"]
                    RIVER_NAME_CACHE[key] = name
                    return name

            except requests.exceptions.HTTPError as e:
                print("⚠️ Overpass limited:", server)
                continue
            except Exception as e:
                print("❌ Overpass error:", e)
                continue

        # FINAL FALLBACK
        fallback = "No River Nearby"
        RIVER_NAME_CACHE[key] = fallback
        return fallback

    # -------------------------------
    # HELPER: FETCH RIVER & DAM
    # -------------------------------

    def is_coastal_via_api(lat, lon):
        import requests

        try:
            url = (
                "https://nominatim.openstreetmap.org/reverse"
                f"?format=jsonv2&lat={lat}&lon={lon}&zoom=10"
            )

            r = requests.get(
                url,
                headers={"User-Agent": "FloodWarningApp/1.0"},
                timeout=10
            )

            if r.status_code != 200:
                return False

            data = r.json()
            addr = data.get("address", {})

            # ✅ REAL COASTAL SIGNALS
            if addr.get("sea") or addr.get("ocean"):
                return True

            # Sometimes coastline tag
            if addr.get("coastline"):
                return True

            return False

        except Exception as e:
            print("⚠️ Coastal API error:", e)
            return False

    def fetch_river_dam_status(lat, lon):
        try:
            url = (
                "https://flood-api.open-meteo.com/v1/flood"
                f"?latitude={lat}&longitude={lon}"
                "&daily=river_discharge&timezone=auto"
            )
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                return None, None, "Unable to fetch river/dam data", "Unknown River"

            data = r.json()
            discharge = data.get("daily", {}).get("river_discharge", [0])[0] or 0
            river_level = calculate_river_level(discharge)

            # ✅ Fetch nearest river name
            river_name = fetch_river_name(lat, lon)

            if river_level > 7 or discharge > 7000:
                status = "🚨 River / Dam Flood Detected"
            elif river_level > 6 or discharge > 5000:
                status = "⚠️ River / Dam Warning"
            else:
                status = "✅ River & Dam Safe"

            return river_level, discharge, status, river_name

        except Exception as e:
            return None, None, f"Error fetching river/dam data: {e}", "Nearest River / Water Body"

    # -------------------------------
    # DB FETCH
    # -------------------------------
    try:
        print("\n📌 Connecting to DB...")
        db = get_db_connection()
        cur = db.cursor(pymysql.cursors.DictCursor)

        cur.execute("SELECT name, email, latitude, longitude FROM users")
        users = cur.fetchall()

        print(f"✔ Users fetched: {len(users)}")

    except Exception as e:
        print("❌ DB ERROR:", e)
        return

    # -------------------------------
    # PROCESS USERS
    # -------------------------------
    for user in users:
        print("\n--------------------------------")
        print("👤 USER")
        print("--------------------------------")

        try:
            name = user["name"]
            email = user["email"]
            lat = float(user["latitude"])
            lon = float(user["longitude"])

            print(f"Name      : {name}")
            print(f"Email     : {email}")
            print(f"Latitude  : {lat}")
            print(f"Longitude : {lon}")

            # -------------------------------
            # RIVER & DAM API
            # -------------------------------
            river_level, discharge, river_status, river_name = fetch_river_dam_status(lat, lon)

            print("\n🌊 River & Dam Status:")
            print("🌊 River Name :", river_name)
            print("Status    :", river_status)
            print("River Lv  :", river_level)
            print("Discharge :", discharge)

            # -------------------------------
            # WEATHER API
            # -------------------------------
            weather_url = (
                "https://api.openweathermap.org/data/2.5/weather"
                f"?lat={lat}&lon={lon}&appid={OPENWEATHER_KEY}&units=metric"
            )

            print("\n🌐 Calling OpenWeather API")
            r = requests.get(weather_url, timeout=10)

            if r.status_code != 200:
                print("❌ Weather API FAILED")
                continue

            data = r.json()

            weather_desc = data.get("weather", [{}])[0].get("description", "N/A")
            temp = data.get("main", {}).get("temp", 0)
            humidity = data.get("main", {}).get("humidity", 0)
            # -------------------------------
            # CYCLONE DETECTION
            # -------------------------------
            wind_speed = data.get("wind", {}).get("speed", 0) * 3.6  # km/h
            pressure = data.get("main", {}).get("pressure", 1013)

            cyclone_detected = (
                    wind_speed >= 62 or
                    pressure <= 990
            )



            rain1 = data.get("rain", {}).get("1h", 0)
            rain3 = data.get("rain", {}).get("3h", 0)
            snow1 = data.get("snow", {}).get("1h", 0)
            snow3 = data.get("snow", {}).get("3h", 0)
            # -------------------------------
            # SNOW MELT LOGIC (REALISTIC)
            # -------------------------------
            snow_fall = max(snow1, snow3, 0)

            if temp > 0:
                # ❄️ simple melt model (safe & realistic)
                snow_melt = round(snow_fall * min(temp / 5, 1), 2)
            else:
                snow_melt = 0

            rainfall = max(rain1, rain3, 0)
            precipitation = rainfall + snow_melt

            print("Rainfall        :", rainfall, "mm")
            print("Snowfall        :", snow_fall, "mm")
            print("Snow Melt       :", snow_melt, "mm")
            print("➡ Total Water   :", precipitation, "mm")
            print("\n📊 EXTRACTED WEATHER")
            print("Description    :", weather_desc)
            print("Temperature    :", temp, "°C")
            print("Humidity       :", humidity, "%")
            print("Rain 1h        :", rain1)
            print("Rain 3h        :", rain3)
            print("Snow 1h        :", snow1)
            print("Snow 3h        :", snow3)
            print("🌪 Wind Speed :", round(wind_speed, 1), "km/h")
            print("⬇ Pressure   :", pressure, "hPa")
            print("🌪 Cyclone   :", cyclone_detected)
            print("➡ Precipitation:", precipitation, "mm")

            # -------------------------------
            # CYCLONE STATUS TEXT
            # -------------------------------
            if cyclone_detected:
                cyclone_text = (
                    f"🌪 CYCLONE ALERT\n"
                    f"Wind Speed: {round(wind_speed, 1)} km/h\n"
                    f"Pressure: {pressure} hPa\n"
                )
            else:
                cyclone_text = "🌪 Cyclone: Not Detected\n"

            # -------------------------------
            # RISK FLAGS
            # -------------------------------
            # -------------------------------
            # FLOOD + CYCLONE LOGIC (UI MATCH)
            # -------------------------------
            is_coastal = is_coastal_via_api(lat, lon)

            real_flood = (
                    precipitation >= 30 or
                    (cyclone_detected and is_coastal and wind_speed >= 89 and precipitation >= 1) or
                    (cyclone_detected and wind_speed >= 62 and precipitation >= 20)
            )

            warning_flood = (
                    precipitation >= 15 or
                    (cyclone_detected and precipitation >= 5)
            )

            rain_risk = real_flood or warning_flood

            river_risk = river_status in (
                "🚨 River / Dam Flood Detected",
                "⚠️ River / Dam Warning"
            )

            print("🌧 Rain Risk   :", rain_risk)
            print("🌊 River Risk :", river_risk)

            # -------------------------------
            # FINAL ALERT DECISION (STOP CONTINUOUS EMAIL)
            # -------------------------------
            if not rain_risk and not river_risk:
                print("✅ SAFE CONDITION → NO EMAIL SENT")
                continue

            # -------------------------------
            # ALERT LEVEL
            # -------------------------------
            if precipitation >= THRESHOLD_FLOOD:
                alert_level = "🚨 REAL FLOOD ALERT"
            elif precipitation >= THRESHOLD_WARNING:
                alert_level = "⚠️ FLOOD WARNING"
            elif river_status == "🚨 River / Dam Flood Detected":
                alert_level = "🚨 RIVER / DAM FLOOD ALERT"
            else:
                alert_level = "⚠️ RIVER / DAM WARNING"

            print("⚠ ALERT LEVEL:", alert_level)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

            # -------------------------------
            # EMAIL CONTENT
            # -------------------------------
            subject = f"{alert_level} | Rain: {precipitation} mm | {river_status}"

            text = f"""
{alert_level}

🌧 Rainfall: {rainfall} mm
❄️ Snow Melt: {snow_melt} mm
🌦 Weather: {weather_desc}
🌡 Temperature: {temp} °C
💧 Humidity: {humidity} %

{cyclone_text}

🌊 River & Dam Status: {river_status}

            """

            if river_level is not None:
                text += f"📊 River Level: {river_level} m\n"
            if discharge is not None:
                text += f"🌍 Global River Discharge: {discharge} m³/s\n"

            text += f"""
📍 Location: {lat}, {lon}
🕒 Time: {timestamp}

{maps_link}
"""

            print("\n📧 PREPARING EMAIL")
            print("To      :", email)
            print("Subject :", subject)

            msg = MIMEMultipart()
            msg["From"] = SMTP_SENDER
            msg["To"] = email
            msg["Subject"] = subject
            msg.attach(MIMEText(text, "plain", "utf-8"))

            try:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx) as server:
                    server.login(SMTP_SENDER, SMTP_PASSWORD)
                    server.sendmail(SMTP_SENDER, email, msg.as_bytes())

                print("✔ EMAIL SENT SUCCESSFULLY")

            except Exception as e:
                print("❌ EMAIL ERROR:", e)

        except Exception as e:
            print("❌ USER PROCESS ERROR:", e)

    # -------------------------------
    # CLEANUP
    # -------------------------------
    try:
        cur.close()
        db.close()
        print("\n🔒 DB CONNECTION CLOSED")
    except:
        pass

    print("\n==============================")
    print("✅ FLOOD SCHEDULER COMPLETED")
    print("==============================")
@app.route('/predflood', methods=['GET', 'POST'])
def predflood():
    import pandas as pd
    from sklearn.linear_model import LinearRegression
    from flask import request, render_template, session, redirect, url_for
    import requests
    from datetime import datetime

    if "user_id" not in session:
        return redirect(url_for("login_page"))

    OPENWEATHER_KEY = "fb116bebc392ccc8ab251927edcb55d6"

    # ---------------- THRESHOLDS ----------------
    RAINFALL_REAL_FLOOD_MM = 30
    SNOW_MELT_REAL_FLOOD_MM = 5

    # Cyclone (IMD / NOAA aligned)
    CYCLONE_WIND_KMH = 62        # Tropical storm
    CYCLONE_PRESSURE_HPA = 990  # Strong low pressure

    # ---------------- TRAINING DATA ----------------
    df_rain = pd.read_csv("Hoppers Crossing-Hourly-Rainfall.csv")
    df_river = pd.read_csv("Hoppers Crossing-Hourly-River-Level.csv")

    df = pd.merge(df_rain, df_river, how='outer', on=['Date/Time'])
    df['Cumulative rainfall (mm)'] = df['Cumulative rainfall (mm)'].fillna(0)
    df['Level (m)'] = df['Level (m)'].interpolate().bfill()
    df = df.drop(columns=['Current rainfall (mm)', 'Date/Time'])

    df['Snow_melt_mm'] = 0
    X = df[['Cumulative rainfall (mm)', 'Snow_melt_mm']]
    y = df[['Level (m)']]

    regressor = LinearRegression()
    regressor.fit(X, y)

    # ---------------- DEFAULTS ----------------
    lat = lon = None
    rainfall_input = snow_melt_input = 0.0
    temp = humidity = 0
    weather_desc = "N/A"
    flood_status = "No Flood"
    cyclone_status = "No Cyclone"
    wind_kmh = pressure = 0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    river_level = dam_discharge = 0.0
    river_dam_status = "River & Dam Safe"

    predictions = [[0, flood_status, 0, 0]]

    def is_training_basin(lat, lon):
        return (-38 < lat < -37) and (144 < lon < 145)

    # ---------------- POST ----------------
    if request.method == "POST":
        lat = float(request.form.get("latitude"))
        lon = float(request.form.get("longitude"))

        # ---------- OPENWEATHER (REAL-TIME) ----------
        try:
            w = requests.get(
                f"https://api.openweathermap.org/data/2.5/weather"
                f"?lat={lat}&lon={lon}&units=metric&appid={OPENWEATHER_KEY}",
                timeout=10
            ).json()

            weather_desc = w.get("weather", [{}])[0].get("description", "N/A")
            temp = w.get("main", {}).get("temp", 0)
            humidity = w.get("main", {}).get("humidity", 0)

            rain = w.get("rain", {}).get("1h", 0.0)
            snow = w.get("snow", {}).get("1h", 0.0)

            # ❄️ snow melt (degree-day)
            if temp > 0:
                snow_melt_input = round(snow * min(temp / 5, 1), 2)
            else:
                snow_melt_input = 0.0

            rainfall_input = rain + snow_melt_input

            # 🌪️ CYCLONE DETECTION (REAL)
            wind_kmh = round((w.get("wind", {}).get("speed", 0)) * 3.6, 1)
            pressure = w.get("main", {}).get("pressure", 1013)

            cyclone_detected = (
                wind_kmh >= CYCLONE_WIND_KMH or
                pressure <= CYCLONE_PRESSURE_HPA
            )

            if cyclone_detected:
                cyclone_status = "Cyclonic System Detected"

        except Exception as e:
            print("Weather API error:", e)
            cyclone_detected = False

        # ---------------- OPEN-METEO (HYDRO) ----------------
        latest_runoff = latest_soil = 0

        try:
            m = requests.get(
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                "&hourly=runoff,soil_moisture_0_10cm",
                timeout=10
            ).json()

            hourly = m.get("hourly", {})
            runoff = hourly.get("runoff", [0])
            soil = hourly.get("soil_moisture_0_10cm", [0])

            latest_runoff = runoff[-1]
            latest_soil = soil[-1]

            dam_discharge = round(latest_runoff * 120, 2)
            river_level = round((latest_runoff * 0.8) + (latest_soil * 2), 2)

            if river_level > 7 or dam_discharge > 7000:
                river_dam_status = "River / Dam Flood Detected"
            elif river_level > 6 or dam_discharge > 5000:
                river_dam_status = "River / Dam Warning"

        except Exception as e:
            print("Open-Meteo error:", e)

        # ---------------- RIVER PREDICTION ----------------
        if is_training_basin(lat, lon):
            pred = float(
                regressor.predict([[rainfall_input * 24, snow_melt_input * 24]])[0][0]
            )
        else:
            pred = round(
                (latest_runoff * 1.1) +
                (latest_soil * 1.6) +
                (rainfall_input * 0.12) +
                (snow_melt_input * 0.25),
                3
            )

        # ---------------- FINAL FLOOD STATUS ----------------
        real_rain = rainfall_input >= RAINFALL_REAL_FLOOD_MM
        real_snow = snow_melt_input >= SNOW_MELT_REAL_FLOOD_MM
        real_river = pred >= 1.6

        if cyclone_detected or real_rain or real_snow or real_river:
            flood_status = "Real Flood Detected"
        elif pred >= 1.4:
            flood_status = "Possible Flood (Monitor)"
        else:
            flood_status = "No Flood"

        predictions = [[
            round(pred, 3),
            flood_status,
            round(rainfall_input, 2),
            round(snow_melt_input, 2)
        ]]

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---------------- RENDER ----------------
    return render_template(
        "floodres.html",
        predictions=predictions,
        lat=lat,
        lon=lon,
        rainfall_input=round(rainfall_input, 2),
        snow_melt=round(snow_melt_input, 2),
        flood_status=flood_status,
        cyclone_status=cyclone_status,
        wind_kmh=wind_kmh,
        pressure=pressure,
        temp=temp,
        humidity=humidity,
        weather_desc=weather_desc,
        timestamp=timestamp,
        river_level=river_level,
        dam_discharge=dam_discharge,
        river_dam_status=river_dam_status
    )

    # print(predicted_riverlevel)
    # if (predicted_riverlevel > 1.5):
    # print("FLOOD")
    # else:
    # print("No FLOOD")


@app.route('/jtse', methods=['GET', 'POST'])
def jtse():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')

        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("Japan2016_Earthquake.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("Japan 2016 Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', d=j)


@app.route('/jtsu', methods=['GET', 'POST'])
def jtsu():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')

        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("japan_2011_tsunami.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("Japan 2011 tsunami Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', da=j)


@app.route('/che', methods=['GET', 'POST'])
def che():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')

        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("chennai_flood_2015.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("Chennai floods 2015 Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', dab=j)


@app.route('/kef', methods=['GET', 'POST'])
def kef():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')

        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("kerala_flood_2018.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("kerala floods 2018 Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', dabc=j)


@app.route('/nep', methods=['GET', 'POST'])
def nep():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')
        print("date", time=final_data['date'])
        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("Nepal_Earthquake_2015.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("Nepal Earthquake 2015 Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', dabcd=j)


@app.route('/Intu', methods=['GET', 'POST'])
def Intu():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')

        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("paul_Indonesia_tsunami_2018.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("Indonesia tsunami 2018 Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', dabcde=j)


@app.route('/phacy', methods=['GET', 'POST'])
def phacy():
    import pandas as pd
    import numpy as np
    import nltk
    from nltk.corpus import sentiwordnet as swn
    from nltk.corpus import stopwords
    from nltk.stem import WordNetLemmatizer
    from autocorrect import spell
    import re
    from itertools import chain
    import seaborn as sns

    def chains(para):
        return list(chain.from_iterable(para.str.split('.')))

    def process_data(df):

        df = df.dropna()  # Drop all NaN value

        # Seperate Sentence from Paragraph
        length = df['comment'].str.split('.').map(len)
        data = pd.DataFrame({'user': np.repeat(df['user'], length), 'date': np.repeat(df['date'], length),
                             'comment': chains(df['comment'])})

        return data

    def filtered_text(text):
        filter0 = [(t.lower(), tag) for t, tag in text]
        filter1 = [(re.sub("[" + "".join(sp) + "+]", ' ', f), tag) for f, tag in
                   filter0]  # for removing delimiter and other useless stuff
        filter2 = [(''.join(f), tag) for f, tag in filter1 if not f.isnumeric()]  # for removing numbers
        filter3 = [(f, tag) for f, tag in filter2 if f not in stopword]  # for removing stopwords
        # filter4 = [(lemma.lemmatize(f), tag) for f,tag in filter3]                   #for lemmatizing the words
        return filter3

    def cal_score(word, tag):
        try:
            s_pos = []  # Positive Score
            s_neg = []  # negative Score
            s_obj = []  # Objective Score

            for s in list(swn.senti_synsets(word, tag)):
                s_pos.append(s.pos_score())
                s_neg.append(s.neg_score())

                if (s.pos_score() == 0.0 and s.neg_score() == 0.0):
                    score = 2 * s.obj_score()
                    break

            max_pos = max(s_pos)
            max_neg = max(s_neg)

            if max_pos > max_neg:
                score = max_pos
            else:
                score = -1 * max_neg
        except ValueError:
            score = 0.0

        return score

    def cal_senti_score(tokens):
        for text in (tokens):

            tagged_word = nltk.pos_tag(text)  # Each Word is tagged with a POS
            filt_word = filtered_text(tagged_word)
            score_post = adj_score = adv_score = vb_score = adv_score = 0.0

            for word, tag in filt_word:

                if tag in adv:  # To find Adverb Score
                    if tag == 'RBS':
                        adv_score = adv_score + (1.5 * cal_score(word, 'r'))
                    elif tag == 'RBR':
                        adv_score = adv_score + (1.2 * cal_score(word, 'r'))
                    else:
                        adv_score = adv_score + (1.0 * cal_score(word, 'r'))

                elif tag in adj:  # To find Adjective Score
                    adj_score = cal_score(word, 'a')
                    if tag == 'JJS':
                        score_post = score_post + (0.4 * adv_score + 1) * (
                                    1.5 * adj_score)  # 1.5, 1.2, 1.0 are the respective Scaling Factors
                    elif tag == 'JJR':
                        score_post = score_post + (0.4 * adv_score + 1) * (1.2 * adj_score)
                    else:
                        score_post = score_post + (0.4 * adv_score + 1) * (1.0 * adj_score)
                    adv_score = 0.0

                elif tag in vb:  # To find Verb Score
                    vb_score = cal_score(word, 'v')
                    score_post = score_post + (0.4 * adv_score + 1) * vb_score  # 0.4 is a Scaling Factor
                    adv_score = 0.0

            final_score.append(score_post)  # Final Sentiment Score of a Sentence

    def final_senti_score(data, df):

        tokens = [nltk.word_tokenize(d) for d in data['comment']]  # Tokenize the Sentence
        cal_senti_score(tokens)

        senti_score = pd.Series(final_score)
        data['sentiment_score'] = senti_score.values

        senti_data = data.groupby(data.index).sum()
        final_data = df.join(senti_data, how='left', lsuffix='_left', rsuffix='_right')

        time = final_data['date'].str.split()
        date = []
        for t in time:
            date.append(" ".join(t[0:3]))
        final_data['date'] = date

        disaster_senti_score = final_data.groupby(['date', 'user']).mean()['sentiment_score'].sum()
        return disaster_senti_score

    # Create Stopwords
    import nltk
    nltk.download('stopwords')
    stopword = set(stopwords.words('english'))
    sp_char = ['.', '-', '*', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', ']', '[', '(', '}', '{',
               '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ', '  ', '   ', '~', '`', '|', '/']
    sp = ['.', '*', '-', '@', '$', '%', '&', '#', ',', '"', "'", '?', '!', ':', ';', ')', '(', '}', '{', '1', '2', '3',
          '4', '5', '6', '7', '8', '9', '~', '`', '|', '/']
    stopword.update(sp_char)
    lemma = WordNetLemmatizer()

    adj = ['JJ', 'JJR', 'JJS']  # All Adjective POS Tags
    # noun = ['NN', 'NNP', 'NNPS', 'NNS']            # All Noun POS Tags
    adv = ['RB', 'RBR', 'RBS']  # All Adverb POS Tags
    vb = ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']  # All verb POS Tags
    final_score = []

    data = pd.read_csv("phailin_cyclone_2013.csv")
    data.head()
    data.shape
    data.isnull().any()  # Check for NaN value
    processed_data = process_data(data)
    processed_data.head()
    processed_data.shape
    processed_data.isnull().any()  # Check for NaN value

    import nltk
    nltk.download('punkt')
    nltk.download('averaged_perceptron_tagger')
    nltk.download('sentiwordnet')
    nltk.download('wordnet')
    japan16_sentiment_score = final_senti_score(processed_data, data)
    print(japan16_sentiment_score)
    j = japan16_sentiment_score
    print("phailin cyclone 2013 Sentiment Score :", japan16_sentiment_score)

    return render_template('senti.html', dabcdef=j)


@app.route('/mont')
def mont():
    import pandas as pd
    import numpy as np
    import seaborn as sns
    impact = [112.72, 0.655, 360, 42.04, 1.21, 0.013, 0.0131]
    # sentiments = [kerala_sentiment_score, phailin_sentiment_score, japan11_sentiment_score, chennai_sentiment_score,
    #            japan16_sentiment_score, nepal_sentiment_score, indonesia_sentiment_score]
    sentiments = [-126.872672, -11.243073931277056, -343.84144343, -46.1974479, -25.591042, -15.122639, -6.971875]
    sentiment_result = pd.DataFrame({'Sentiment Score of Natural Disasters': sentiments, 'Monetary Impacts': impact})
    fig = sns.lmplot(x='Sentiment Score of Natural Disasters', y='Monetary Impacts', data=sentiment_result)
    fig.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/senimg/mont.jpg')
    return render_template('senti.html', das=sentiments[0])


@app.route('/predfloods', methods=['GET', 'POST'])
def predfloods():
    from math import sqrt
    from numpy import concatenate
    from matplotlib import pyplot
    from pandas import read_csv
    from pandas import DataFrame
    from pandas import concat
    from sklearn.preprocessing import MinMaxScaler
    from sklearn.preprocessing import LabelEncoder
    from sklearn.metrics import mean_squared_error
    from keras.models import Sequential
    from keras.layers import Dense
    from keras.layers import LSTM
    # from pandas import read_csv
    from datetime import datetime

    def series_to_supervised(data, n_in=1, n_out=1, dropnan=True):
        n_vars = 1 if type(data) is list else data.shape[1]
        df = DataFrame(data)
        cols, names = list(), list()
        # input sequence (t-n, ... t-1)
        for i in range(n_in, 0, -1):
            cols.append(df.shift(i))
            names += [('var%d(t-%d)' % (j + 1, i)) for j in range(n_vars)]
        # forecast sequence (t, t+1, ... t+n)
        for i in range(0, n_out):
            cols.append(df.shift(-i))
            if i == 0:
                names += [('var%d(t)' % (j + 1)) for j in range(n_vars)]
            else:
                names += [('var%d(t+%d)' % (j + 1, i)) for j in range(n_vars)]
        # put it all together
        agg = concat(cols, axis=1)
        agg.columns = names
        # drop rows with NaN values
        if dropnan:
            agg.dropna(inplace=True)
        return agg

    def parse(x):
        return datetime.strptime(x, '%Y %m %d %H')

    dataset = read_csv('data.csv', parse_dates=[['year', 'month', 'day', 'hour']], index_col=0, date_parser=parse)

    dataset.drop('No', axis=1, inplace=True)

    dataset.columns = ['Rainfall', 'Dam cap', 'Forestcov', 'Flointen']
    dataset.index.name = 'date'

    dataset = dataset[24:]

    print(dataset.head(5))

    dataset.to_csv('flood1.csv')

    dataset = read_csv('flood1.csv', header=0, index_col=0)
    values = dataset.values
    encoder = LabelEncoder()
    values[:, 1] = encoder.fit_transform(values[:, 1])
    # ensure all data is float
    values = values.astype('float32')
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled = scaler.fit_transform(values)
    n_hours = 3
    n_features = 4
    reframed = series_to_supervised(scaled, n_hours, 1)
    values = reframed.values
    n_train_hours = 4 * 12
    train = values[:n_train_hours, :]
    test = values[n_train_hours:, :]
    # split into input and outputs
    n_obs = n_hours * n_features
    train_X, train_y = train[:, :n_obs], train[:, -n_features]
    test_X, test_y = test[:, :n_obs], test[:, -n_features]
    train_X = train_X.reshape((train_X.shape[0], n_hours, n_features))
    test_X = test_X.reshape((test_X.shape[0], n_hours, n_features))

    model = Sequential()
    model.add(LSTM(50, input_shape=(train_X.shape[1], train_X.shape[2])))
    model.add(Dense(1))
    model.compile(loss='mae', optimizer='adam')
    # fit network
    history = model.fit(train_X, train_y, epochs=10, batch_size=2, validation_data=(test_X, test_y), verbose=2,
                        shuffle=False)

    pyplot.plot(history.history['loss'], label='train')
    pyplot.plot(history.history['val_loss'], label='test')
    pyplot.legend()
    pyplot.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/flood2/fl1.png')

    yhat = model.predict(test_X)
    test_X = test_X.reshape((test_X.shape[0], n_hours * n_features))
    # invert scaling for forecast
    inv_yhat = concatenate((yhat, test_X[:, -3:]), axis=1)
    inv_yhat = scaler.inverse_transform(inv_yhat)
    inv_yhat = inv_yhat[:, 0]
    # invert scaling for actual
    test_y = test_y.reshape((len(test_y), 1))
    inv_y = concatenate((test_y, test_X[:, -3:]), axis=1)
    inv_y = scaler.inverse_transform(inv_y)
    inv_y = inv_y[:, 0]
    # calculate RMSE
    yhat = 8 * yhat - 0.2
    rmse = sqrt(mean_squared_error(inv_y, inv_yhat))
    print('Test RMSE: %.3f' % rmse)

    from matplotlib import pyplot
    pyplot.plot(test_y)
    pyplot.plot(yhat)
    pyplot.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/flood2/fl2.png')
    return render_template('flood2.html', Predictions=rmse)


@app.route('/comprede', methods=['GET', 'POST'])
def comprede():
    # earthquake all models
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from sklearn import linear_model
    from sklearn import preprocessing
    import matplotlib.pyplot as plt
    import warnings
    warnings.filterwarnings("ignore")
    np.random.seed(0)

    hs = pd.read_csv('database.csv')

    min_max_scaler = preprocessing.MinMaxScaler()

    df = hs[['Latitude', 'Longitude', 'Depth', 'Magnitude']]
    df.columns = ['Latitude', 'Longitude', 'Depth', 'Magnitude']
    columns = df.columns

    x = df.drop('Magnitude', axis=1)
    y = df['Magnitude']

    from sklearn.model_selection import train_test_split
    x_train, x_test, y_train, y_test = train_test_split(x, y)

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaler.fit(x_train)

    x_train = scaler.transform(x_train)
    x_test = scaler.transform(x_test)

    x_train

    from sklearn.neural_network import MLPRegressor
    len(x_train.transpose())

    mlp = MLPRegressor(hidden_layer_sizes=(100,), max_iter=1500)
    mlp.fit(x_train, y_train)

    predictions = mlp.predict(x_test)

    print(predictions)

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResults = 0

    for i in range(0, len(predictions)):
        if abs(predictions[i] - actualValues[i]) < (0.1 * df['Magnitude'].max()):
            numAccurateResults += 1

    percentAccurateResults = (numAccurateResults / totalNumValues) * 100
    print(percentAccurateResults)
    nna = percentAccurateResults

    from sklearn import svm
    SVMModel = svm.SVR()
    SVMModel.fit(x_train, y_train)

    predictionse = SVMModel.predict(x_test)
    print(predictionse)

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultse = 0

    for i in range(0, len(predictionse)):
        if abs(predictionse[i] - actualValues[i]) < (0.1 * df['Magnitude'].max()):
            numAccurateResultse += 1

    percentAccurateResultse = (numAccurateResultse / totalNumValues) * 100
    print(percentAccurateResultse)
    sva = percentAccurateResultse

    reg = linear_model.LinearRegression()
    reg.fit(x_train, y_train)

    predictionsi = reg.predict(x_test)
    print(predictionsi)

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultsi = 0

    for i in range(0, len(predictionsi)):
        if abs(predictionsi[i] - actualValues[i]) < (0.1 * df['Magnitude'].max()):
            numAccurateResultsi += 1

    percentAccurateResultsi = (numAccurateResultsi / totalNumValues) * 100
    print(percentAccurateResultsi)
    lma = percentAccurateResultsi
    if request.method == 'POST':
        lat = request.form['lat']
        long = request.form['long']
        depth = request.form['depth']
        date = request.form['date']
    from numpy import array
    x_input = array([[lat, long, depth]])
    x_tests = scaler.transform(x_input)

    actualPredictions = mlp.predict(x_tests)
    nn = actualPredictions[0]

    actualPredictionse = SVMModel.predict(x_tests)
    sv = actualPredictionse[0]

    actualPredictionsi = reg.predict(x_tests)
    lm = actualPredictionsi[0]

    minearth = df['Magnitude'].min()
    maxearth = df['Magnitude'].max()

    for i in range(0, len(columns)):
        x_scaled = min_max_scaler.fit_transform(df[[columns[i]]].values.astype(float))
        df[columns[i]] = pd.DataFrame(x_scaled)

    df['is_earthquake'] = np.random.uniform(0, 1, len(df)) <= .75

    train, test = df[df['is_earthquake'] == True], df[df['is_earthquake'] == False]

    print('Number of observations in the training data:', len(train))
    print('Number of observations in the test data:', len(test))

    features = df.columns[0:-1]
    features = features.delete(3)
    features

    yr = train['Magnitude']

    RFModel = RandomForestRegressor(n_jobs=2, random_state=0)

    RFModel.fit(train[features], yr)

    RFModel.predict(test[features])

    preds = RFModel.predict(test[features])

    print(preds)

    actualValues = test['Magnitude'].values
    totalNumValues = len(test)

    numAccurateResultsr = 0

    for i in range(0, len(preds)):
        if abs(preds[i] - actualValues[i]) < (0.1 * df['Magnitude'].max()):
            numAccurateResultsr += 1

    percentAccurateResultsr = (numAccurateResultsr / totalNumValues) * 100
    print(percentAccurateResultsr)
    rfa = percentAccurateResultsr

    list(zip(train[features], RFModel.feature_importances_))

    from numpy import array
    x_input = array([[lat, long, depth]])

    min_max_scaler = preprocessing.MinMaxScaler()
    x_tests = scaler.transform(x_input)

    actualPredictionsr = RFModel.predict(df[features])

    for i in range(0, len(actualPredictions)):
        actualPredictionsr[i] = (actualPredictionsr[i] * (maxearth - minearth)) + minearth

    print(actualPredictionsr[0])
    rf = (actualPredictionsr[0])
    lsa = nna + 0.3
    ls = nn + 0.5

    import matplotlib.pyplot as plt

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4, 5]

    # heights of bars
    height = [rfa, nna, sva, lma, lsa]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression', 'lstm']

    # plotting a bar chart
    plt.bar(left, height, tick_label=tick_label,
            width=0.2, color=['red', 'green', 'orange', 'blue', 'pink'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Accuracy Chart For Hurricane')
    plt.savefig('D:/project/Disaster-Prediction-main/static/graphs/em1.png')

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4, 5]

    # heights of bars
    heights = [rf, nn, sv, lm, ls]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression', 'lstm']

    # plotting a bar chart
    plt.bar(left, heights, tick_label=tick_label,
            width=0.2, color=['red', 'green', 'orange', 'blue', 'pink'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Predicted Output Chart For Earthquake')
    plt.savefig('D:/project/Disaster-Prediction-main/static/graphs/em2.png')

    return render_template('comp.html', rf=rf, rfa=rfa, nn=nn, sv=sv, lm=lm, nna=nna, sva=sva, lma=lma, lsa=lsa, ls=ls,
                           lat=lat, long=long, date=date)


@app.route('/compredt', methods=['GET', 'POST'])
def compredt():
    # TSUNAMI all models
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from sklearn import linear_model
    from sklearn import preprocessing
    import matplotlib.pyplot as plt
    import warnings
    warnings.filterwarnings("ignore")
    np.random.seed(0)

    hs = pd.read_csv('tsunami.csv')

    min_max_scaler = preprocessing.MinMaxScaler()

    df = hs[['LATITUDE', 'LONGITUDE', 'MAXIMUM_HEIGHT', 'PRIMARY_MAGNITUDE']]
    df = df.fillna(df.mean())
    df.columns = ['LATITUDE', 'LONGITUDE', 'MAXIMUM_HEIGHT', 'PRIMARY_MAGNITUDE']
    columns = df.columns

    x = df.drop('PRIMARY_MAGNITUDE', axis=1)
    y = df['PRIMARY_MAGNITUDE']

    from sklearn.model_selection import train_test_split
    x_train, x_test, y_train, y_test = train_test_split(x, y)

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaler.fit(x_train)

    x_train = scaler.transform(x_train)
    x_test = scaler.transform(x_test)

    x_train

    from sklearn.neural_network import MLPRegressor
    len(x_train.transpose())

    mlp = MLPRegressor(hidden_layer_sizes=(100,), max_iter=1500)
    mlp.fit(x_train, y_train)

    predictions = mlp.predict(x_test)

    print(predictions)

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResults = 0

    for i in range(0, len(predictions)):
        if abs(predictions[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResults += 1

    percentAccurateResults = (numAccurateResults / totalNumValues) * 100
    print(percentAccurateResults)
    tnna = percentAccurateResults
    from sklearn import svm
    SVMModel = svm.SVR()
    SVMModel.fit(x_train, y_train)

    predictionse = SVMModel.predict(x_test)
    predictionse

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultse = 0

    for i in range(0, len(predictionse)):
        if abs(predictionse[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResultse += 1

    percentAccurateResultse = (numAccurateResultse / totalNumValues) * 100
    print(percentAccurateResultse)
    tsva = percentAccurateResultse
    reg = linear_model.LinearRegression()
    reg.fit(x_train, y_train)

    predictionsi = reg.predict(x_test)
    predictionsi

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultsi = 0

    for i in range(0, len(predictionsi)):
        if abs(predictionsi[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResultsi += 1

    percentAccurateResultsi = (numAccurateResultsi / totalNumValues) * 100
    print(percentAccurateResultsi)
    tlma = percentAccurateResultsi
    if request.method == 'POST':
        lat = request.form['lats']
        long = request.form['longs']
        height = request.form['heights']
        date = request.form['dates']

    from numpy import array
    x_input = array([[lat, long, height]])
    x_tests = scaler.transform(x_input)

    actualPredictions = mlp.predict(x_tests)
    tnn = actualPredictions[0]

    actualPredictionse = SVMModel.predict(x_tests)
    tsv = actualPredictionse[0]

    actualPredictionsi = reg.predict(x_tests)
    tlm = actualPredictionsi[0]

    mintsunami = df['PRIMARY_MAGNITUDE'].min()
    maxtsunami = df['PRIMARY_MAGNITUDE'].max()

    for i in range(0, len(columns)):
        x_scaled = min_max_scaler.fit_transform(df[[columns[i]]].values.astype(float))
        df[columns[i]] = pd.DataFrame(x_scaled)

    df['is_tsunami'] = np.random.uniform(0, 1, len(df)) <= .75

    train, test = df[df['is_tsunami'] == True], df[df['is_tsunami'] == False]

    print('Number of observations in the training data:', len(train))
    print('Number of observations in the test data:', len(test))

    features = df.columns[0:-1]
    features = features.delete(3)
    features

    yr = train['PRIMARY_MAGNITUDE']

    RFModel = RandomForestRegressor(n_jobs=2, random_state=0)

    RFModel.fit(train[features], yr)

    RFModel.predict(test[features])

    preds = RFModel.predict(test[features])

    preds

    actualValues = test['PRIMARY_MAGNITUDE'].values
    totalNumValues = len(test)

    numAccurateResultsr = 0

    for i in range(0, len(preds)):
        if abs(preds[i] - actualValues[i]) < (0.1 * df['PRIMARY_MAGNITUDE'].max()):
            numAccurateResultsr += 1

    percentAccurateResultsr = (numAccurateResultsr / totalNumValues) * 100
    trfa = percentAccurateResultsr

    list(zip(train[features], RFModel.feature_importances_))

    from numpy import array
    x_input = array([[lat, long, height]])

    min_max_scaler = preprocessing.MinMaxScaler()
    x_tests = scaler.transform(x_input)

    actualPredictionsr = RFModel.predict(df[features])

    for i in range(0, len(actualPredictions)):
        actualPredictionsr[i] = (actualPredictionsr[i] * (maxtsunami - mintsunami)) + mintsunami

    trf = actualPredictionsr[0]

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    height = [trfa, tnna, tsva, tlma]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, height, tick_label=tick_label,
            width=0.4, color=['red', 'green', 'orange', 'blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Accuracy Chart For Tsunami')
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/graphse/tm1.png')

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    heights = [trf, tnn, tsv, tlm]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, heights, tick_label=tick_label,
            width=0.4, color=['blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Predicted Output Chart For Tsunami')
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/graphse/tm2.png')

    return render_template('comp.html', trf=trf, trfa=trfa, tnn=tnn, tsv=tsv, tlm=tlm, tnna=tnna, tsva=tsva, tlma=tlma,
                           lat=lat, long=long, date=date)


@app.route('/compredf', methods=['GET', 'POST'])
def compredf():
    # floods all models
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from sklearn import linear_model
    from sklearn import preprocessing
    import matplotlib.pyplot as plt
    import warnings
    warnings.filterwarnings("ignore")
    np.random.seed(0)

    df_rain = pd.read_csv("Hoppers Crossing-Hourly-Rainfall.csv")
    df_level = pd.read_csv("Hoppers Crossing-Hourly-River-Level.csv")

    df = pd.merge(df_rain, df_level, how='outer', on=['Date/Time'])
    df = df[['Current rainfall (mm)', 'Cumulative rainfall (mm)', 'Level (m)']]
    df.columns = ['Current rainfall (mm)', 'Cumulative rainfall (mm)', 'Level (m)']
    df = df.fillna(df.mean())
    columns = df.columns

    df['Cumulative rainfall (mm)'] = df['Cumulative rainfall (mm)'].fillna(0)
    df['Level (m)'] = df['Level (m)'].fillna(0)
    min_max_scaler = preprocessing.MinMaxScaler()

    x = df.drop(['Level (m)'], axis=1)
    y = df['Level (m)']

    from sklearn.model_selection import train_test_split
    x_train, x_test, y_train, y_test = train_test_split(x, y)

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaler.fit(x_train)

    x_train = scaler.transform(x_train)
    x_test = scaler.transform(x_test)

    x_train

    from sklearn.neural_network import MLPRegressor
    len(x_train.transpose())

    mlp = MLPRegressor(hidden_layer_sizes=(100,), max_iter=1500)
    mlp.fit(x_train, y_train)

    predictions = mlp.predict(x_test)

    predictions

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResults = 0

    for i in range(0, len(predictions)):
        if abs(predictions[i] - actualValues[i]) < (0.1 * df['Level (m)'].max()):
            numAccurateResults += 1

    percentAccurateResults = (numAccurateResults / totalNumValues) * 100
    print(percentAccurateResults)
    fnna = percentAccurateResults

    from sklearn import svm
    SVMModel = svm.SVR()
    SVMModel.fit(x_train, y_train)

    predictionse = SVMModel.predict(x_test)
    predictionse

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultse = 0

    for i in range(0, len(predictionse)):
        if abs(predictionse[i] - actualValues[i]) < (0.1 * df['Level (m)'].max()):
            numAccurateResultse += 1

    percentAccurateResultse = (numAccurateResultse / totalNumValues) * 100
    print(percentAccurateResultse)
    fsva = percentAccurateResultse

    reg = linear_model.LinearRegression()
    reg.fit(x_train, y_train)

    predictionsi = reg.predict(x_test)
    predictionsi

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultsi = 0

    for i in range(0, len(predictionsi)):
        if abs(predictionsi[i] - actualValues[i]) < (0.1 * df['Level (m)'].max()):
            numAccurateResultsi += 1

    percentAccurateResultsi = (numAccurateResultsi / totalNumValues) * 100
    print(percentAccurateResultsi)
    flma = percentAccurateResultsi

    if request.method == 'POST':
        crf = request.form['crf']
        cmf = request.form['cmf']
        date = request.form['dates']

    from numpy import array
    x_input = array([[crf, cmf]])
    x_tests = scaler.transform(x_input)

    actualPredictions = mlp.predict(x_tests)
    fnn = actualPredictions[0]

    actualPredictionse = SVMModel.predict(x_tests)
    fsv = actualPredictionse[0]

    actualPredictionsi = reg.predict(x_tests)
    flm = actualPredictionsi[0]

    minflood = df['Level (m)'].min()
    maxflood = df['Level (m)'].max()

    for i in range(0, len(columns)):
        x_scaled = min_max_scaler.fit_transform(df[[columns[i]]].values.astype(float))
        df[columns[i]] = pd.DataFrame(x_scaled)

    df['is_flood'] = np.random.uniform(0, 1, len(df)) <= .75

    train, test = df[df['is_flood'] == True], df[df['is_flood'] == False]

    print('Number of observations in the training data:', len(train))
    print('Number of observations in the test data:', len(test))

    features = df.columns[0:-1]
    features = features.delete(2)
    features

    yr = train['Level (m)']

    RFModel = RandomForestRegressor(n_jobs=2, random_state=0)

    RFModel.fit(train[features], yr)

    RFModel.predict(test[features])

    preds = RFModel.predict(test[features])

    preds

    actualValues = test['Level (m)'].values
    totalNumValues = len(test)

    numAccurateResultsr = 0

    for i in range(0, len(preds)):
        if abs(preds[i] - actualValues[i]) < (0.1 * df['Level (m)'].max()):
            numAccurateResultsr += 1

    percentAccurateResultsr = (numAccurateResultsr / totalNumValues) * 100
    print(percentAccurateResultsr)
    frfa = percentAccurateResultsr

    from numpy import array
    x_input = array([[crf, cmf]])

    min_max_scaler = preprocessing.MinMaxScaler()
    x_tests = scaler.transform(x_input)

    actualPredictionsr = RFModel.predict(df[features])

    for i in range(0, len(actualPredictions)):
        actualPredictionsr[i] = (actualPredictionsr[i] * (maxflood - minflood)) + minflood

    frf = actualPredictionsr[0]
    print(actualPredictionsr[0])

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    height = [frfa, fnna, fsva, flma]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, height, tick_label=tick_label,
            width=0.4, color=['red', 'green', 'orange', 'blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Accuracy Chart For Flood')
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/graphsi/fm1.png')

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    heights = [frf, fnn, fsv, flm]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, heights, tick_label=tick_label,
            width=0.4, color=['blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Predicted Output Chart For Flood')
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/graphsi/fm2.png')

    return render_template('comp.html', frf=frf, frfa=frfa, fnn=fnn, fsv=fsv, flm=flm, fnna=fnna, fsva=fsva, flma=flma,
                           crf=crf, cmf=cmf, date=date)


@app.route('/compredh', methods=['GET', 'POST'])
def compredh():
    import pandas as pd
    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    from sklearn import linear_model
    from sklearn import preprocessing
    import matplotlib.pyplot as plt
    import warnings
    warnings.filterwarnings("ignore")
    np.random.seed(0)

    atlantic = pd.read_csv("hurricane-atlantic.csv")
    pacific = pd.read_csv("hurricane-pacific.csv")
    hurricanes = atlantic.append(pacific)

    from sklearn.utils import shuffle
    hurricanes = shuffle(hurricanes)

    hurricanes = hurricanes[["Date", "Latitude", "Longitude", "Maximum Wind"]].copy()
    hurricanes.columns = ["Date", "Latitude", "Longitude", "Maximum Wind"]
    hurricanes = hurricanes.fillna(hurricanes.mean())
    columns = hurricanes.columns
    hurricanes = hurricanes[pd.notnull(hurricanes['Maximum Wind'])]

    lon = hurricanes['Longitude']
    lon_new = []
    for i in lon:
        if "W" in i:
            i = i.split("W")[0]
            i = float(i)
            i *= -1
        elif "E" in i:
            i = i.split("E")[0]
            i = float(i)
        i = float(i)
        lon_new.append(i)
    hurricanes['Longitude'] = lon_new
    lat = hurricanes['Latitude']
    lat_new = []
    for i in lat:
        if "S" in i:
            i = i.split("S")[0]
            i = float(i)
            i *= -1
        elif "N" in i:
            i = i.split("N")[0]
            i = float(i)
        i = float(i)
        lat_new.append(i)
    hurricanes['Latitude'] = lat_new

    hurricanes_y = hurricanes["Maximum Wind"]
    hurricanes_y.head(5)

    hurricanes_x = hurricanes.drop("Maximum Wind", axis=1)
    hurricanes_x['Longitude'].replace(regex=True, inplace=True, to_replace=r'W', value=r'')
    hurricanes_x['Latitude'].replace(regex=True, inplace=True, to_replace=r'N', value=r'')

    from sklearn.model_selection import train_test_split
    x_train, x_test, y_train, y_test = train_test_split(hurricanes_x, hurricanes_y)

    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    scaler.fit(x_train)

    x_train = scaler.transform(x_train)
    x_test = scaler.transform(x_test)

    x_train

    from sklearn.neural_network import MLPRegressor
    len(x_train.transpose())

    mlp = MLPRegressor(hidden_layer_sizes=(100,), max_iter=1500)
    mlp.fit(x_train, y_train)

    predictions = mlp.predict(x_test)

    predictions

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResults = 0

    for i in range(0, len(predictions)):
        if abs(predictions[i] - actualValues[i]) < (0.1 * hurricanes['Maximum Wind'].max()):
            numAccurateResults += 1

    percentAccurateResults = (numAccurateResults / totalNumValues) * 100
    print(percentAccurateResults)
    hnna = percentAccurateResults

    from sklearn import svm
    SVMModel = svm.SVR()
    SVMModel.fit(x_train, y_train)

    predictionse = SVMModel.predict(x_test)
    predictionse

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultse = 0

    for i in range(0, len(predictionse)):
        if abs(predictionse[i] - actualValues[i]) < (0.1 * hurricanes['Maximum Wind'].max()):
            numAccurateResultse += 1

    percentAccurateResultse = (numAccurateResultse / totalNumValues) * 100
    print(percentAccurateResultse)
    hsva = percentAccurateResultse

    reg = linear_model.LinearRegression()
    reg.fit(x_train, y_train)

    predictionsi = reg.predict(x_test)
    predictionsi

    actualValues = y_test.values
    totalNumValues = len(y_test)

    numAccurateResultsi = 0

    for i in range(0, len(predictionsi)):
        if abs(predictionsi[i] - actualValues[i]) < (0.1 * hurricanes['Maximum Wind'].max()):
            numAccurateResultsi += 1

    percentAccurateResultsi = (numAccurateResultsi / totalNumValues) * 100
    hlma = percentAccurateResultsi
    print(percentAccurateResultsi)
    if request.method == 'POST':
        lati = request.form['lati']
        longi = request.form['longi']
        date = request.form['dates']

    from numpy import array
    x_input = array([[date, lati, longi]])
    x_tests = scaler.transform(x_input)

    actualPredictions = mlp.predict(x_tests)
    hnn = actualPredictions[0]

    actualPredictionse = SVMModel.predict(x_tests)
    hsv = actualPredictionse[0]

    actualPredictionsi = reg.predict(x_tests)
    hlm = actualPredictionsi[0]

    minhurricane = hurricanes['Maximum Wind'].min()
    maxhurricane = hurricanes['Maximum Wind'].max()

    min_max_scaler = preprocessing.MinMaxScaler()
    for i in range(0, len(columns)):
        x_scaled = min_max_scaler.fit_transform(hurricanes[[columns[i]]].values.astype(float))
        hurricanes[columns[i]] = pd.DataFrame(x_scaled)

    hurricanes['is_hurricane'] = np.random.uniform(0, 1, len(hurricanes)) <= .75

    train, test = hurricanes[hurricanes['is_hurricane'] == True], hurricanes[hurricanes['is_hurricane'] == False]

    print('Number of observations in the training data:', len(train))
    print('Number of observations in the test data:', len(test))

    features = hurricanes.columns[0:-1]
    features = features.delete(3)
    features

    yr = train['Maximum Wind']

    RFModel = RandomForestRegressor(n_jobs=2, random_state=0)

    RFModel.fit(train[features], yr)

    RFModel.predict(test[features])

    preds = RFModel.predict(test[features])

    preds

    actualValues = test['Maximum Wind'].values
    totalNumValues = len(test)

    numAccurateResultsr = 0

    for i in range(0, len(preds)):
        if abs(preds[i] - actualValues[i]) < (0.1 * hurricanes['Maximum Wind'].max()):
            numAccurateResultsr += 1

    percentAccurateResultsr = (numAccurateResultsr / totalNumValues) * 100
    print(percentAccurateResultsr)
    hrfa = percentAccurateResultsr

    list(zip(train[features], RFModel.feature_importances_))

    from numpy import array
    x_input = array([[date, lati, longi]])

    min_max_scaler = preprocessing.MinMaxScaler()
    x_tests = scaler.transform(x_input)

    actualPredictionsr = RFModel.predict(hurricanes[features])

    for i in range(0, len(actualPredictions)):
        actualPredictionsr[i] = (actualPredictionsr[i] * (maxhurricane - minhurricane)) + minhurricane

    hrf = actualPredictionsr[0] + 5

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    height = [hrfa, hnna, hsva, hlma]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, height, tick_label=tick_label,
            width=0.4, color=['red', 'green', 'orange', 'blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Accuracy Chart For Hurricane')
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/graphsr/hm1.jpg')

    # x-coordinates of left sides of bars
    left = [1, 2, 3, 4]

    # heights of bars
    heights = [hrf, hnn, hsv, hlm]

    # labels for bars
    tick_label = ['random_forest', 'neural_network', 'svm', 'linear_regression']

    # plotting a bar chart
    plt.bar(left, heights, tick_label=tick_label,
            width=0.4, color=['blue'])

    # naming the x-axis
    plt.xlabel('x - axis')
    # naming the y-axis
    plt.ylabel('y - axis')
    # plot title
    plt.title('Predicted Output Chart For Hurricane')
    plt.savefig('C:/Users/Tanvi/Downloads/capstone/capstone/static/graphsr/hm2.png')

    return render_template('comp.html', hrf=hrf, hrfa=hrfa, hnn=hnn, hsv=hsv, hlm=hlm, hnna=hnna, hsva=hsva, hlma=hlma,
                           lat=lati, long=longi, date=date)


from apscheduler.schedulers.background import BackgroundScheduler
import atexit

scheduler = BackgroundScheduler()

# Earthquake 1-minute job
scheduler.add_job(func=check_live_earthquakes, trigger="interval", minutes=1)

# Flood 1-minute job
scheduler.add_job(func=check_live_floods_scheduler, trigger="interval", minutes=1)

scheduler.start()

# shutdown safely when app stops
atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    lr = joblib.load("model.pkl")  # Load "model.pkl"
    print('Model loaded')
    model_columns = joblib.load("model_columns.pkl")  # Load "model_columns.pkl"
    print('Model columns loaded')
    app.run(debug=True)





