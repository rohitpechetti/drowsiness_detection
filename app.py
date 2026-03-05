from flask import Flask, render_template, request, redirect, session, url_for, Response
import sqlite3
import random 
from functools import wraps
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from database import init_db
from camera import generate_frames

app = Flask(__name__)
app.secret_key = "supersecretkey"
# ---------------- OTP STORAGE ----------------
otp_store = {}

@app.route("/")
def home():
    return redirect("/login")

@app.route("/register")
def register_choice():
    return render_template("register_choice.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")
    return render_template("dashboard.html")

# ---------------- MAIL CONFIG ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'rohitpechetti2@gmail.com'
app.config['MAIL_PASSWORD'] = 'piibjbgkgoovzsxa'

mail = Mail(app)

# ---------------- INIT DATABASE ----------------
init_db()

# ---------------- CREATE SUPERADMIN ----------------
def create_superadmin():
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE role='superadmin'")
    if not c.fetchone():
        c.execute("""
        INSERT INTO users(username,email,password,role,is_approved)
        VALUES(?,?,?,?,?)
        """, (
            "superadmin",
            "rohitpechetti2@gmail.com",  # fixed spelling
            generate_password_hash("Rohit@456"),
            "superadmin",
            1   # Approved automatically
        ))
        conn.commit()

    conn.close()

# ---------------- CREATE ADMIN ----------------
def create_admin():
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        c.execute("""
        INSERT INTO users(username,email,password,role)
        VALUES(?,?,?,?)
        """, (
            "admin",
            "admin@gmail.com",
            generate_password_hash("Rohit@456"),
            "admin"
        ))
        conn.commit()

    conn.close()

# Call both
create_superadmin()
create_admin()

#------------------test mail--------------#
@app.route("/test_email")
def test_email():
    msg = Message(
        subject="Test Email",
        sender=app.config['MAIL_USERNAME'],
        recipients=["rohitpechetti2@gmail.com"]
    )
    msg.body = "Test working"
    mail.send(msg)
    return "Email Sent"

# ---------------- ROLE DECORATOR ----------------
def role_required(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user" not in session:
                return redirect("/login")
            if session["role"] != role:
                return "Access Denied"
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = sqlite3.connect("drowsiness.db")
        c = conn.cursor()

        # 🔹 Fetch user including approval status
        c.execute("SELECT username,password,role,is_approved FROM users WHERE username=?", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[1], password):

            # 🔹 Check approval
            if user[3] == 0:
                return "Waiting for SuperAdmin approval."

            session["user"] = user[0]
            session["role"] = user[2]

            if user[2] == "user":
                return redirect("/detect")

            elif user[2] == "admin":
                return redirect("/report")

            elif user[2] == "superadmin":
                return redirect("/superadmin")

        return "Invalid Credentials"

    return render_template("login.html")# ---------------- REGISTER ----------------
@app.route("/register/<role>", methods=["GET", "POST"])
def register(role):
    if role not in ["user", "admin"]:
        return "Invalid Role"

    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = generate_password_hash(request.form.get("password"))

        conn = sqlite3.connect("drowsiness.db")
        c = conn.cursor()

        try:
            c.execute("""
            INSERT INTO users(username,email,password,role,is_approved)
            VALUES(?,?,?,?,?)
            """, (username, email, password, role, 0))
            conn.commit()
        except:
            conn.close()
            return "User already exists"

        conn.close()

        # -------- SEND EMAIL TO SUPERADMIN --------
        msg = Message(
            subject="New Registration Request",
            sender=app.config['MAIL_USERNAME'],
            recipients=["rohitpechetti2@gmail.com"]   # superadmin email
        )

        msg.body = f"""
        New Registration Request

      Username: {username}
      Email: {email}
      Role Requested: {role}

      Please login and approve the account.
      """

        mail.send(msg)

        return "Registered successfully. Waiting for SuperAdmin approval."

    return render_template("register.html", role=role)

# ---------------- DETECTION (USER ONLY) ----------------
@app.route("/detect")
@role_required("user")
def detect():
    return render_template("detect.html")

@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(session["user"], mail, app),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
# ---------------- REPORT (ADMIN ONLY) ----------------
@app.route("/report")
@role_required("admin")
def report():
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()
    c.execute("SELECT * FROM logs ORDER BY id DESC")
    logs = c.fetchall()
    conn.close()
    return render_template("report.html", logs=logs)

# ---------------- SUPERADMIN PANEL ----------------
@app.route("/superadmin")
@role_required("superadmin")
def superadmin():
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    # 🔹 Pending users (not approved)
    c.execute("SELECT id,username,email,role,is_approved FROM users WHERE is_approved=0")
    pending = c.fetchall()

    # 🔹 All users
    c.execute("SELECT id,username,email,role,is_approved FROM users")
    users = c.fetchall()

    conn.close()

    return render_template("superadmin.html", users=users, pending=pending)

# ---------------- EMAIL ALERT FUNCTION ----------------
def send_alert_email(username, status):
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("SELECT email FROM users WHERE role IN ('admin','superadmin')")
    admins = c.fetchall()

    conn.close()

    recipients = [a[0] for a in admins]

    msg = Message(
        subject="Driver Alert",
        sender=app.config['MAIL_USERNAME'],
        recipients=recipients
    )
    msg.body = f"User: {username}\nAlert: {status}"

    mail.send(msg)

#-----------------approve route-----------#
@app.route("/approve/<int:user_id>")
@role_required("superadmin")
def approve_user(user_id):
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()
    c.execute("UPDATE users SET is_approved=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return redirect("/superadmin")

#---------------forgot password ------------#
@app.route("/forgot_password", methods=["GET","POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")

        otp = str(random.randint(100000,999999))
        otp_store[email] = otp

        msg = Message("OTP Reset",
                      sender=app.config['MAIL_USERNAME'],
                      recipients=[email])
        msg.body = f"Your OTP is {otp}"
        mail.send(msg)

        return redirect(url_for("verify_otp", email=email))

    return render_template("forgot_password.html")


#-----------------OTP Verification---------------#

@app.route("/verify_otp/<email>", methods=["GET","POST"])
def verify_otp(email):
    if request.method == "POST":
        entered = request.form.get("otp")
        new_password = request.form.get("password")

        if not new_password:
            return "Password cannot be empty"

        if otp_store.get(email) == entered:

            hashed_password = generate_password_hash(new_password)

            conn = sqlite3.connect("drowsiness.db")
            c = conn.cursor()
            c.execute("UPDATE users SET password=? WHERE email=?", (hashed_password,email))
            conn.commit()
            conn.close()

            otp_store.pop(email)
            return redirect("/login")

        else:
            return "Invalid OTP"

    return render_template("verify_otp.html", email=email)
#---------------delete------------------#
# ---------------- DELETE USER (SUPERADMIN ONLY) ----------------
@app.route("/delete_user/<int:user_id>")
@role_required("superadmin")
def delete_user(user_id):
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    # Prevent deleting superadmin
    c.execute("SELECT role FROM users WHERE id=?", (user_id,))
    user = c.fetchone()

    if user and user[0] == "superadmin":
        conn.close()
        return "Cannot delete SuperAdmin!"

    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    return redirect("/superadmin")

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
