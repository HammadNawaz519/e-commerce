
import os
import re
import json
import random
import string
import uuid
from datetime import datetime, timedelta
from html import escape
from functools import wraps

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
from flask_mail import Mail, Message
import mysql.connector
import requests as http_requests
from dotenv import load_dotenv
from prompts import ask_sage_with_context
from security import validate_user_input, validate_ai_output
from langchain_pipeline import ask_sage_langchain

load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "shopy-secret-key-change-me")

app.config['SESSION_PERMANENT'] = False
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

# ---------------- MAIL ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['MAIL_USE_TLS'] = True
mail = Mail(app)

# ---------------- DATABASE ----------------
def get_db():
    db_user = (os.getenv("APP_DB_USER") or os.getenv("DB_USER") or "").strip()
    db_password = os.getenv("APP_DB_PASSWORD")
    if db_password is None:
        db_password = os.getenv("DB_PASSWORD", "")
    if not db_user:
        raise RuntimeError("Database user is not configured. Set APP_DB_USER or DB_USER.")

    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=db_user,
        password=db_password,
        database=os.getenv("DB_NAME", "shopy"),
        autocommit=True
    )

def get_readonly_db():
    """Read-only DB connection for the AI pipeline (Phase 3 — Least Privilege).
    Uses READ_DB_USER / READ_DB_PASSWORD when configured."""
    ro_user = (os.getenv("READ_DB_USER") or os.getenv("DB_USER") or "").strip()
    ro_password = os.getenv("READ_DB_PASSWORD")
    if ro_password is None:
        ro_password = os.getenv("DB_PASSWORD", "")

    if not ro_user:
        raise RuntimeError("Read-only DB user is not configured. Set READ_DB_USER.")
    if ro_user.lower() == "root":
        raise RuntimeError("Read-only DB user cannot be root. Configure READ_DB_USER with least privilege.")

    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=ro_user,
        password=ro_password,
        database=os.getenv("DB_NAME", "shopy"),
        autocommit=True
    )

def ensure_db_exists():
    """Create the database if it doesn't exist yet."""
    try:
        db_name = os.getenv("DB_NAME", "shopy")
        admin_user = (os.getenv("APP_DB_USER") or os.getenv("DB_USER") or "").strip()
        admin_password = os.getenv("APP_DB_PASSWORD")
        if admin_password is None:
            admin_password = os.getenv("DB_PASSWORD", "")

        if not admin_user:
            raise RuntimeError("Database user is not configured. Set APP_DB_USER or DB_USER.")

        conn = mysql.connector.connect(
            host=os.getenv("DB_HOST", "localhost"),
            user=admin_user,
            password=admin_password,
            autocommit=True
        )
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.close()
        conn.close()
        app.logger.info(f'Database `{db_name}` is ready.')
    except Exception as e:
        app.logger.warning(f'ensure_db_exists error: {e}')

def qry(cursor, sql, params=None):
    """Safe execute helper."""
    cursor.execute(sql, params or [])


def ensure_column_exists(cursor, table_name, column_name, alter_sql):
    """Add missing columns safely for lightweight runtime migrations."""
    db_name = os.getenv('DB_NAME', 'shopy')
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (db_name, table_name, column_name)
    )
    if not cursor.fetchone():
        cursor.execute(alter_sql)


def get_default_address(user_id, db):
    cur = db.cursor(dictionary=True)
    cur.execute(
        """
        SELECT id, label, full_name, phone, address_line, city, province, postal_code, is_default
        FROM addresses
        WHERE user_id = %s AND is_default = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,)
    )
    address = cur.fetchone()
    if not address:
        cur.execute(
            """
            SELECT id, label, full_name, phone, address_line, city, province, postal_code, is_default
            FROM addresses
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,)
        )
        address = cur.fetchone()
    cur.close()
    return address


def save_default_address(user_id, payload, db):
    """Upsert one default address for a user."""
    line = (payload.get('address_line') or '').strip()
    if not line:
        return

    full_name = (payload.get('full_name') or '').strip()[:100]
    phone = (payload.get('phone') or '').strip()[:30]
    label = (payload.get('label') or 'Home').strip()[:50] or 'Home'
    city = (payload.get('city') or '').strip()[:100]
    province = (payload.get('province') or '').strip()[:100]
    postal_code = (payload.get('postal_code') or '').strip()[:20]

    cur = db.cursor(dictionary=True)
    cur.execute(
        "SELECT id FROM addresses WHERE user_id=%s AND is_default=1 ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    existing = cur.fetchone()
    cur.close()

    cur2 = db.cursor()
    if existing:
        cur2.execute(
            """
            UPDATE addresses
            SET label=%s, full_name=%s, phone=%s, address_line=%s,
                city=%s, province=%s, postal_code=%s, is_default=1
            WHERE id=%s AND user_id=%s
            """,
            (label, full_name, phone, line, city, province, postal_code, existing['id'], user_id)
        )
    else:
        cur2.execute("UPDATE addresses SET is_default=0 WHERE user_id=%s", (user_id,))
        cur2.execute(
            """
            INSERT INTO addresses (user_id, label, full_name, phone, address_line, city, province, postal_code, is_default)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)
            """,
            (user_id, label, full_name, phone, line, city, province, postal_code)
        )
    cur2.close()

# ---------------- DECORATORS ----------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def retailer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect('/login')
        if session.get('role') != 'retailer':
            return redirect('/shop')
        return f(*args, **kwargs)
    return decorated

def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return redirect('/login')
        if session.get('role') != 'customer':
            return redirect('/retailer/dashboard')
        return f(*args, **kwargs)
    return decorated

# ---------------- UPLOAD FOLDERS ----------------
UPLOAD_FOLDER_PRODUCTS = os.path.join('static', 'uploads', 'products')
os.makedirs(UPLOAD_FOLDER_PRODUCTS, exist_ok=True)
ALLOWED_IMAGE_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXT

# ---------------- MAIL HELPER ----------------
def send_otp(email):
    otp = ''.join(random.choices(string.digits, k=6))
    try:
        msg = Message("Your Shopy OTP",
                      sender=app.config['MAIL_USERNAME'],
                      recipients=[email])
        msg.body = f"Your OTP is {otp}. It expires in 10 minutes."
        mail.send(msg)
    except Exception as e:
        app.logger.warning(f"Mail send error: {e}")
    return otp

# ---------------- DATABASE INIT ----------------
def init_db():
    try:
        db = get_db()
        cur = db.cursor()

        # Users
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(80) NOT NULL,
                phone_number VARCHAR(30) DEFAULT NULL,
                email VARCHAR(120) NOT NULL UNIQUE,
                password VARCHAR(256) NOT NULL,
                verified TINYINT DEFAULT 0,
                role VARCHAR(20) DEFAULT 'customer',
                profile_pic TEXT DEFAULT NULL,
                bio TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_users_email (email),
                INDEX idx_users_phone (phone_number)
            ) ENGINE=InnoDB
        """)

        # Lightweight migrations for evolving schema
        try:
            ensure_column_exists(cur, 'users', 'role',
                                 "ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'customer'")
            ensure_column_exists(cur, 'users', 'profile_pic',
                                 "ALTER TABLE users ADD COLUMN profile_pic TEXT DEFAULT NULL")
            ensure_column_exists(cur, 'users', 'bio',
                                 "ALTER TABLE users ADD COLUMN bio TEXT DEFAULT NULL")
            ensure_column_exists(cur, 'orders', 'payment_method',
                                 "ALTER TABLE orders ADD COLUMN payment_method VARCHAR(30) DEFAULT 'cod'")
            ensure_column_exists(cur, 'orders', 'payment_status',
                                 "ALTER TABLE orders ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending'")
            ensure_column_exists(cur, 'orders', 'payment_ref',
                                 "ALTER TABLE orders ADD COLUMN payment_ref VARCHAR(64) DEFAULT NULL")
        except Exception:
            pass

        # Categories
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
        """)

        # Products
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                retailer_id INT NOT NULL,
                category_id INT DEFAULT NULL,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                price DECIMAL(10,2) NOT NULL,
                original_price DECIMAL(10,2) DEFAULT NULL,
                stock INT DEFAULT 0,
                image_url TEXT,
                is_active TINYINT DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_prod_retailer (retailer_id),
                INDEX idx_prod_category (category_id)
            ) ENGINE=InnoDB
        """)

        # Product images (gallery)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_images (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                image_url TEXT NOT NULL,
                is_primary TINYINT DEFAULT 0,
                INDEX idx_pi_product (product_id)
            ) ENGINE=InnoDB
        """)

        # Cart
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                product_id INT NOT NULL,
                quantity INT DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_cart (user_id, product_id),
                INDEX idx_cart_user (user_id)
            ) ENGINE=InnoDB
        """)

        # Wishlists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wishlists (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                product_id INT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_wishlist (user_id, product_id),
                INDEX idx_wish_user (user_id)
            ) ENGINE=InnoDB
        """)

        # Orders
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                customer_id INT NOT NULL,
                total_amount DECIMAL(10,2) NOT NULL,
                status VARCHAR(30) DEFAULT 'pending',
                shipping_name VARCHAR(100),
                shipping_address TEXT,
                shipping_phone VARCHAR(30),
                notes TEXT,
                payment_method VARCHAR(30) DEFAULT 'cod',
                payment_status VARCHAR(20) DEFAULT 'pending',
                payment_ref VARCHAR(64) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_order_customer (customer_id),
                INDEX idx_order_status (status),
                INDEX idx_order_payment_status (payment_status)
            ) ENGINE=InnoDB
        """)

        # Saved addresses
        cur.execute("""
            CREATE TABLE IF NOT EXISTS addresses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                label VARCHAR(50) DEFAULT 'Home',
                full_name VARCHAR(100),
                phone VARCHAR(30),
                address_line TEXT NOT NULL,
                city VARCHAR(100),
                province VARCHAR(100),
                postal_code VARCHAR(20),
                is_default TINYINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_addr_user (user_id),
                INDEX idx_addr_default (user_id, is_default)
            ) ENGINE=InnoDB
        """)

        # Order items
        cur.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                product_id INT,
                retailer_id INT,
                product_name VARCHAR(200),
                quantity INT NOT NULL,
                unit_price DECIMAL(10,2) NOT NULL,
                INDEX idx_oi_order (order_id),
                INDEX idx_oi_retailer (retailer_id)
            ) ENGINE=InnoDB
        """)

        # Reviews
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                user_id INT NOT NULL,
                rating INT NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_review (product_id, user_id),
                INDEX idx_rev_product (product_id)
            ) ENGINE=InnoDB
        """)

        # AI Chat History
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_chat_history (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                user_id     INT NOT NULL,
                role        VARCHAR(20) NOT NULL DEFAULT 'customer',
                sender      ENUM('user','bot') NOT NULL,
                message     TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ach_user (user_id),
                INDEX idx_ach_created (user_id, created_at)
            ) ENGINE=InnoDB
        """)

        # Backward compatibility for older ai_chat_history schemas.
        try:
            ensure_column_exists(
                cur,
                'ai_chat_history',
                'role',
                "ALTER TABLE ai_chat_history ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'customer' AFTER user_id"
            )
        except Exception:
            pass

        # Seed default categories
        cur.execute("SELECT COUNT(*) FROM categories")
        if cur.fetchone()[0] == 0:
            cats = [
                'Electronics', 'Fashion', 'Home & Garden',
                'Sports', 'Books', 'Beauty', 'Toys', 'Food',
                'Health', 'Automobiles'
            ]
            for name in cats:
                cur.execute("INSERT INTO categories (name) VALUES (%s)", (name,))

        cur.close()
        db.close()
        app.logger.info('Shopy init_db completed successfully')
    except Exception as e:
        app.logger.warning(f'init_db error: {e}')

ensure_db_exists()
init_db()

# ================================================================
# AUTH ROUTES
# ================================================================

@app.route('/')
def index():
    return render_template('landing.html')


@app.route('/landing')
def landing_page():
    return render_template('landing.html')


@app.route('/home')
@login_required
def customer_home():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) as cnt FROM orders WHERE customer_id=%s AND status NOT IN ('delivered','cancelled')", (uid,))
    active_orders = cur.fetchone()['cnt']

    cur.execute("SELECT COUNT(*) as cnt FROM wishlists WHERE user_id=%s", (uid,))
    wishlist_count = cur.fetchone()['cnt']

    cur.execute("""
        SELECT COALESCE(SUM(p.price * c.quantity), 0) as total, COALESCE(SUM(c.quantity), 0) as cnt
        FROM cart c JOIN products p ON p.id = c.product_id
        WHERE c.user_id=%s AND p.is_active=1
    """, (uid,))
    cart_row = cur.fetchone()
    cart_total = float(cart_row['total'])
    cart_count = int(cart_row['cnt'])

    cur.execute("SELECT COUNT(*) as cnt FROM reviews WHERE user_id=%s", (uid,))
    review_count = cur.fetchone()['cnt']

    cur.execute("""
        SELECT o.*, (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id = o.id) as item_count
        FROM orders o WHERE o.customer_id=%s ORDER BY o.created_at DESC LIMIT 4
    """, (uid,))
    recent_orders = cur.fetchall()
    for o in recent_orders:
        o['created_at'] = str(o.get('created_at', ''))

    cur.execute("""
        SELECT p.id, p.name, p.price, p.image_url, p.stock, u.username as retailer_name
        FROM wishlists w
        JOIN products p ON p.id = w.product_id
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE w.user_id=%s AND p.is_active=1
        ORDER BY w.added_at DESC LIMIT 4
    """, (uid,))
    wishlist_items = cur.fetchall()

    cur.execute("""
        SELECT p.id, p.name, p.price, p.image_url, u.username as retailer_name
        FROM products p LEFT JOIN users u ON u.id = p.retailer_id
        WHERE p.is_active=1 ORDER BY p.created_at DESC LIMIT 4
    """)
    recommendations = cur.fetchall()

    cur.close(); db.close()
    return render_template('customer_dashboard.html',
        username=session.get('username', 'User'),
        active_orders=active_orders,
        wishlist_count=wishlist_count,
        cart_total=cart_total,
        cart_count=cart_count,
        review_count=review_count,
        recent_orders=recent_orders,
        wishlist_items=wishlist_items,
        recommendations=recommendations
    )


@app.route('/assets/highres.glb')
def landing_model_asset():
    return send_from_directory(app.root_path, 'highres.glb', mimetype='model/gltf-binary')


@app.route('/assets/new-headphone.glb')
def landing_new_headphone_asset():
    return send_from_directory(app.root_path, 'New-Headphone.glb', mimetype='model/gltf-binary')


@app.route('/assets/textured.glb')
def landing_textured_headphone_asset():
    return send_from_directory(app.root_path, 'textured.glb', mimetype='model/gltf-binary')


@app.route('/assets/landing/<path:filename>')
def landing_visual_asset(filename):
    allowed = {'Beauty-Demo.webp', 'Earbuds-Demo.webp', 'Cycle-Demo.jpg'}
    if filename not in allowed:
        return '', 404
    return send_from_directory(app.root_path, filename)


@app.route('/api/contact', methods=['POST'])
def landing_contact():
    data = request.get_json(silent=True) or request.form

    first_name = (data.get('first_name') or data.get('firstName') or '').strip()
    last_name = (data.get('last_name') or data.get('lastName') or '').strip()
    name = (data.get('name') or '').strip()
    if not name:
        name = f'{first_name} {last_name}'.strip()

    email = (data.get('email') or '').strip()
    phone = (data.get('phone') or data.get('phone_number') or data.get('phoneNumber') or '').strip()
    message = (data.get('message') or '').strip()

    if not name or not email or not message:
        return jsonify({'success': False, 'message': 'Name, email, and message are required.'}), 400

    api_key = (os.getenv('RESEND_API_KEY') or '').strip()
    to_email = (os.getenv('RESEND_TO_EMAIL') or 'hammadnawaz519@gmail.com').strip()
    from_email = (os.getenv('RESEND_FROM_EMAIL') or 'onboarding@resend.dev').strip()

    if not api_key:
        return jsonify({'success': False, 'message': 'Email service is not configured yet.'}), 500

    submitted_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    safe_name = escape(name)
    safe_email = escape(email)
    safe_phone = escape(phone) if phone else 'Not provided'
    safe_message = escape(message).replace('\n', '<br>')

    payload = {
        'from': from_email,
        'to': [to_email],
        'subject': f'New Landing Contact: {name}',
        'reply_to': email,
        'text': (
            f'New contact message from landing page\\n\\n'
            f'Name: {name}\\n'
            f'Email: {email}\\n'
            f'Phone: {phone or "Not provided"}\\n'
            f'Submitted At: {submitted_at}\\n\\n'
            f'Message:\\n{message}'
        ),
        'html': (
            '<div style="background:#f4f6fb;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#1a1c23;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:700px;margin:0 auto;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;">'
            '<tr>'
            '<td style="padding:18px 22px;background:#1a1c23;color:#ffffff;">'
            '<p style="margin:0;font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.84;">Shopy</p>'
            '<h2 style="margin:6px 0 0 0;font-size:20px;line-height:1.3;">New Landing Contact Message</h2>'
            '</td>'
            '</tr>'
            '<tr>'
            '<td style="padding:20px 22px 10px 22px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
            '<tr><td style="padding:0 0 8px 0;font-size:12px;color:#6b758a;text-transform:uppercase;letter-spacing:.06em;">Name</td></tr>'
            f'<tr><td style="padding:0 0 14px 0;font-size:15px;font-weight:700;color:#1a1c23;">{safe_name}</td></tr>'
            '<tr><td style="padding:0 0 8px 0;font-size:12px;color:#6b758a;text-transform:uppercase;letter-spacing:.06em;">Email</td></tr>'
            f'<tr><td style="padding:0 0 14px 0;font-size:15px;color:#1a1c23;">{safe_email}</td></tr>'
            '<tr><td style="padding:0 0 8px 0;font-size:12px;color:#6b758a;text-transform:uppercase;letter-spacing:.06em;">Phone</td></tr>'
            f'<tr><td style="padding:0 0 14px 0;font-size:15px;color:#1a1c23;">{safe_phone}</td></tr>'
            '<tr><td style="padding:0 0 8px 0;font-size:12px;color:#6b758a;text-transform:uppercase;letter-spacing:.06em;">Submitted At</td></tr>'
            f'<tr><td style="padding:0 0 14px 0;font-size:14px;color:#1a1c23;">{submitted_at}</td></tr>'
            '<tr><td style="padding:0 0 8px 0;font-size:12px;color:#6b758a;text-transform:uppercase;letter-spacing:.06em;">Message</td></tr>'
            f'<tr><td style="padding:12px 14px;background:#f8f9fd;border:1px solid #e4e8f1;border-radius:10px;font-size:14px;line-height:1.6;color:#1a1c23;">{safe_message}</td></tr>'
            '</table>'
            '</td>'
            '</tr>'
            '<tr>'
            '<td style="padding:12px 22px 20px 22px;font-size:12px;color:#7a8397;">Reply directly to this email to respond to the sender.</td>'
            '</tr>'
            '</table>'
            '</div>'
        ),
    }

    def _send_email(mail_payload):
        return http_requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json=mail_payload,
            timeout=18,
        )

    try:
        resp = _send_email(payload)
    except Exception as e:
        app.logger.warning(f'Resend request error: {e}')
        return jsonify({'success': False, 'message': 'Could not send right now. Please try again.'}), 502

    if 200 <= resp.status_code < 300:
        return jsonify({'success': True, 'message': 'Message sent successfully.'})

    detail = ''
    try:
        detail = (resp.json() or {}).get('message') or ''
    except Exception:
        detail = resp.text[:180]

    # Resend sandbox accounts can only send to the account owner address.
    if resp.status_code == 403:
        match = re.search(r'own email address \(([^)]+)\)', detail or '', flags=re.IGNORECASE)
        if match:
            owner_email = match.group(1).strip()
            retry_payload = dict(payload)
            retry_payload['to'] = [owner_email]
            try:
                retry_resp = _send_email(retry_payload)
                if 200 <= retry_resp.status_code < 300:
                    return jsonify({'success': True, 'message': f'Message sent to {owner_email}.'})
            except Exception as retry_err:
                app.logger.warning(f'Resend retry error: {retry_err}')

    if detail:
        app.logger.warning(f'Resend API error: {detail}')
    return jsonify({'success': False, 'message': detail or 'Mail service rejected the request.'}), 502

@app.route('/signup')
def signup_page():
    """Dark-themed OnlyPipe-style signup page."""
    if session.get('user_id'):
        return redirect('/retailer/dashboard' if session.get('role') == 'retailer' else '/shop')
    return render_template('auth_dark.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect('/retailer/dashboard' if session.get('role') == 'retailer' else '/shop')

    session.clear()

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        raw_password = request.form.get('password', '')
        requested_role = request.form.get('role', 'customer')

        try:
            db = get_db()
            cur = db.cursor(dictionary=True)
            cur.execute("SELECT * FROM users WHERE email=%s", (email,))
            user = cur.fetchone()
            cur.close()
            db.close()
        except Exception as e:
            return redirect(f'/login?error=Database+error:+{str(e)[:60]}')

        if user and check_password_hash(user['password'], raw_password):
            user_role = user.get('role') or 'customer'
            if user_role != requested_role:
                return redirect(f'/login?error=This+account+is+registered+as+a+{user_role}.+Please+select+the+correct+role.')
            if not int(user.get('verified') or 0):
                return redirect('/login?error=Please+verify+your+email+first.')
            session['user_id'] = user['id']
            session['role'] = user_role
            session['username'] = user['username']
            return redirect('/retailer/dashboard' if user_role == 'retailer' else '/shop')

        return redirect('/login?error=Invalid+email+or+password')

    return render_template('auth.html')

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username', '').strip()
    phone    = request.form.get('phone', '').strip()  # optional
    email    = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    role     = request.form.get('role', 'customer')

    if role not in ('customer', 'retailer'):
        role = 'customer'
    if not all([username, email, password]):
        return redirect('/login?tab=register&error=Username,+email+and+password+are+required')

    try:
        db = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            cur.close(); db.close()
            return redirect('/login?tab=register&error=An+account+with+that+email+already+exists')
        cur.close(); db.close()
    except Exception as e:
        return redirect(f'/login?tab=register&error=Database+error:+{str(e)[:60]}')

    otp = send_otp(email)
    session['otp'] = otp
    session['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    session['reg_data'] = {
        'username': username,
        'phone': phone,
        'email': email,
        'password': generate_password_hash(password),
        'role': role
    }
    return redirect('/verify')

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'POST':
        if request.args.get('resend') == '1':
            reg_data = session.get('reg_data') or {}
            email = (reg_data.get('email') or '').strip()
            if not email:
                return jsonify({'success': False, 'message': 'Session expired. Please register again.'}), 400

            otp = send_otp(email)
            session['otp'] = otp
            session['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
            return jsonify({'success': True, 'message': 'Sent again. Type the new code.'})

        expiry_str = session.get('otp_expiry')
        if expiry_str and datetime.utcnow() > datetime.fromisoformat(expiry_str):
            session.pop('otp', None); session.pop('otp_expiry', None); session.pop('reg_data', None)
            return redirect('/login?tab=register&error=OTP+expired.+Please+register+again')

        entered = request.form.get('otp', '').strip()
        if entered == session.get('otp'):
            data = session.pop('reg_data', {})
            session.pop('otp', None); session.pop('otp_expiry', None)
            try:
                db = get_db()
                cur = db.cursor()
                cur.execute("""
                    INSERT INTO users (username, phone_number, email, password, verified, role)
                    VALUES (%s,%s,%s,%s,1,%s)
                """, (data['username'], data['phone'], data['email'], data['password'], data.get('role', 'customer')))
                cur.close(); db.close()
            except Exception as e:
                return redirect(f'/login?tab=register&error=Registration+failed:+{str(e)[:60]}')
            return redirect('/login')

        return redirect('/verify?error=Invalid+OTP.+Please+try+again')

    return render_template('verify.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ================================================================
# CUSTOMER ROUTES
# ================================================================

def _cart_count(uid, db):
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT SUM(quantity) as cnt FROM cart WHERE user_id=%s", (uid,))
    row = cur.fetchone()
    cur.close()
    return int(row['cnt'] or 0) if row else 0


@app.route('/profile')
@login_required
def profile_page():
    uid = session['user_id']
    role = session.get('role', 'customer')

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute(
        """
        SELECT id, username, email, phone_number, role, profile_pic, bio, created_at
        FROM users
        WHERE id = %s
        """,
        (uid,)
    )
    user = cur.fetchone() or {}
    default_address = get_default_address(uid, db)

    if role == 'retailer':
        cur.execute("SELECT COUNT(*) as cnt FROM products WHERE retailer_id=%s AND is_active=1", (uid,))
        product_count = int((cur.fetchone() or {}).get('cnt') or 0)

        cur.execute(
            """
            SELECT COUNT(DISTINCT o.id) as cnt
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            WHERE oi.retailer_id=%s
            """,
            (uid,),
        )
        order_count = int((cur.fetchone() or {}).get('cnt') or 0)

        cur.execute(
            """
            SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) as revenue
            FROM order_items oi
            JOIN orders o ON o.id=oi.order_id
            WHERE oi.retailer_id=%s AND o.status != 'cancelled'
            """,
            (uid,),
        )
        revenue = float((cur.fetchone() or {}).get('revenue') or 0)

        cur.execute("SELECT COUNT(*) as cnt FROM products WHERE retailer_id=%s AND is_active=1 AND stock <= 5", (uid,))
        low_stock_count = int((cur.fetchone() or {}).get('cnt') or 0)

        summary = {
            'product_count': product_count,
            'order_count': order_count,
            'revenue': revenue,
            'low_stock_count': low_stock_count,
        }
    else:
        cur.execute("SELECT COUNT(*) as cnt FROM orders WHERE customer_id=%s", (uid,))
        order_count = int((cur.fetchone() or {}).get('cnt') or 0)

        cur.execute("SELECT COUNT(*) as cnt FROM wishlists WHERE user_id=%s", (uid,))
        wishlist_count = int((cur.fetchone() or {}).get('cnt') or 0)

        cur.execute(
            "SELECT COALESCE(SUM(total_amount), 0) as spend FROM orders WHERE customer_id=%s AND status != 'cancelled'",
            (uid,),
        )
        total_spent = float((cur.fetchone() or {}).get('spend') or 0)

        summary = {
            'order_count': order_count,
            'wishlist_count': wishlist_count,
            'cart_count': _cart_count(uid, db),
            'total_spent': total_spent,
        }

    cur.close()
    db.close()

    if user.get('created_at'):
        user['created_at'] = str(user['created_at'])

    template_name = 'retailer_profile.html' if role == 'retailer' else 'profile.html'
    return render_template(
        template_name,
        user=user,
        role=role,
        summary=summary,
        default_address=default_address,
        username=session.get('username', ''),
    )


@app.route('/api/profile/update', methods=['POST'])
@login_required
def profile_update():
    data = request.get_json(silent=True) or request.form
    uid = session['user_id']

    username = (data.get('username') or '').strip()
    phone = (data.get('phone_number') or data.get('phone') or '').strip()
    bio = (data.get('bio') or '').strip()[:500]

    if not username:
        return jsonify({'error': 'Username is required'}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "UPDATE users SET username=%s, phone_number=%s, bio=%s WHERE id=%s",
        (username[:80], phone[:30], bio, uid),
    )
    cur.close()
    db.close()

    session['username'] = username[:80]
    return jsonify({'success': True, 'username': session['username']})


@app.route('/api/profile/address', methods=['POST'])
@login_required
def profile_save_address():
    data = request.get_json(silent=True) or request.form
    payload = {
        'label': data.get('label'),
        'full_name': data.get('full_name') or data.get('name'),
        'phone': data.get('phone'),
        'address_line': data.get('address_line') or data.get('address'),
        'city': data.get('city'),
        'province': data.get('province'),
        'postal_code': data.get('postal_code'),
    }
    if not (payload.get('address_line') or '').strip():
        return jsonify({'error': 'Address is required'}), 400

    db = get_db()
    save_default_address(session['user_id'], payload, db)
    db.close()
    return jsonify({'success': True})

@app.route('/shop')
@login_required
def shop():
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT * FROM categories ORDER BY name")
    categories = cur.fetchall()

    cat_filter = request.args.get('category', '')
    search     = request.args.get('q', '').strip()

    sql = """
        SELECT p.*, c.name as category_name,
               u.username as retailer_name,
               (SELECT COUNT(*) FROM reviews r WHERE r.product_id = p.id) as review_count,
               (SELECT AVG(r.rating)  FROM reviews r WHERE r.product_id = p.id) as avg_rating
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        LEFT JOIN users u      ON u.id = p.retailer_id
        WHERE p.is_active = 1
    """
    params = []
    if cat_filter:
        sql += " AND p.category_id = %s"; params.append(cat_filter)
    if search:
        sql += " AND (p.name LIKE %s OR p.description LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY p.created_at DESC LIMIT 60"

    cur.execute(sql, params)
    products = cur.fetchall()

    cur.execute("SELECT COUNT(*) as cnt FROM products WHERE is_active = 1")
    total_products = int((cur.fetchone() or {}).get('cnt') or 0)

    cur.execute("SELECT COUNT(DISTINCT retailer_id) as cnt FROM products WHERE is_active = 1")
    retailer_count = int((cur.fetchone() or {}).get('cnt') or 0)

    cur.execute("SELECT COUNT(*) as cnt FROM categories")
    category_count = int((cur.fetchone() or {}).get('cnt') or 0)

    cart_count = _cart_count(session['user_id'], db)
    cur.close(); db.close()

    return render_template('shop.html', categories=categories, products=products,
                           cart_count=cart_count, search=search,
                           cat_filter=cat_filter, username=session.get('username', ''),
                           total_products=total_products,
                           retailer_count=retailer_count,
                           category_count=category_count)

@app.route('/product/<int:product_id>')
@login_required
def product_detail(product_id):
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT p.*, c.name as category_name,
               u.username as retailer_name,
               (SELECT AVG(r.rating) FROM reviews r WHERE r.product_id = p.id) as avg_rating,
               (SELECT COUNT(*)      FROM reviews r WHERE r.product_id = p.id) as review_count
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        LEFT JOIN users u      ON u.id = p.retailer_id
        WHERE p.id = %s AND p.is_active = 1
    """, (product_id,))
    product = cur.fetchone()

    if not product:
        cur.close(); db.close()
        return redirect('/shop')

    cur.execute("""
        SELECT rv.*, u.username FROM reviews rv
        JOIN users u ON u.id = rv.user_id
        WHERE rv.product_id = %s ORDER BY rv.created_at DESC
    """, (product_id,))
    reviews = cur.fetchall()

    cur.execute("""
        SELECT * FROM products
        WHERE category_id = %s AND id != %s AND is_active = 1
        ORDER BY RAND() LIMIT 4
    """, (product.get('category_id'), product_id))
    related = cur.fetchall()

    cur.execute("SELECT id FROM wishlists WHERE user_id=%s AND product_id=%s",
                (session['user_id'], product_id))
    in_wishlist = cur.fetchone() is not None

    cart_count = _cart_count(session['user_id'], db)
    cur.close(); db.close()

    return render_template('product.html', product=product, reviews=reviews,
                           related=related, in_wishlist=in_wishlist,
                           cart_count=cart_count, username=session.get('username', ''))

@app.route('/cart')
@login_required
def cart_page():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT c.quantity, p.id as product_id, p.name, p.price, p.image_url, p.stock,
               u.username as retailer_name,
               (p.price * c.quantity) as subtotal
        FROM cart c
        JOIN products p ON p.id = c.product_id
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE c.user_id = %s AND p.is_active = 1
    """, (uid,))
    items = cur.fetchall()
    total      = sum(float(i['subtotal']) for i in items)
    cart_count = _cart_count(uid, db)

    cur.execute("""
        SELECT p.id, p.name, p.price, p.image_url, u.username as retailer_name
        FROM products p
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE p.is_active = 1
        ORDER BY p.created_at DESC
        LIMIT 6
    """)
    recommendations = cur.fetchall()

    cur.close(); db.close()
    return render_template('cart.html', items=items, total=total,
                           cart_count=cart_count, username=session.get('username', ''),
                           recommendations=recommendations)

@app.route('/checkout')
@login_required
def checkout_page():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT c.quantity, p.id as product_id, p.name, p.price, p.stock, p.image_url,
               u.username as retailer_name,
               (p.price * c.quantity) as subtotal
        FROM cart c
        JOIN products p ON p.id = c.product_id
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE c.user_id = %s AND p.is_active = 1
    """, (uid,))
    items = cur.fetchall()
    if not items:
        cur.close(); db.close()
        return redirect('/cart')

    cur.execute("SELECT username, phone_number, email FROM users WHERE id=%s", (uid,))
    profile = cur.fetchone() or {}
    default_address = get_default_address(uid, db) or {}

    total      = sum(float(i['subtotal']) for i in items)
    cart_count = sum(i['quantity'] for i in items)
    cur.close(); db.close()
    return render_template('checkout.html', items=items, total=total,
                           cart_count=cart_count, username=session.get('username', ''),
                           profile=profile, default_address=default_address)

@app.route('/orders')
@login_required
def orders_page():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT o.*,
               (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id = o.id) as item_count
        FROM orders o WHERE o.customer_id = %s ORDER BY o.created_at DESC
    """, (uid,))
    orders = cur.fetchall()
    for o in orders:
        o['created_at'] = str(o.get('created_at', ''))

    cur.execute("""
        SELECT p.id, p.name, p.price, p.image_url, u.username as retailer_name
        FROM products p
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE p.is_active = 1
        ORDER BY p.created_at DESC
        LIMIT 6
    """)
    recommendations = cur.fetchall()

    cart_count = _cart_count(uid, db)
    cur.close(); db.close()
    return render_template('orders.html', orders=orders, username=session.get('username', ''),
                           cart_count=cart_count, recommendations=recommendations)

@app.route('/api/orders/statuses')
@login_required
def customer_order_statuses():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, status FROM orders WHERE customer_id = %s ORDER BY id", (uid,))
    rows = cur.fetchall()
    cur.close(); db.close()
    return jsonify({'success': True, 'orders': rows})

@app.route('/wishlist')
@login_required
def wishlist_page():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT p.*, w.added_at, u.username as retailer_name
        FROM wishlists w
        JOIN products p ON p.id = w.product_id
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE w.user_id = %s AND p.is_active = 1 ORDER BY w.added_at DESC
    """, (uid,))
    items      = cur.fetchall()

    cur.execute("""
        SELECT p.id, p.name, p.price, p.image_url, u.username as retailer_name
        FROM products p
        LEFT JOIN users u ON u.id = p.retailer_id
        WHERE p.is_active = 1
          AND p.id NOT IN (SELECT product_id FROM wishlists WHERE user_id = %s)
        ORDER BY p.created_at DESC
        LIMIT 6
    """, (uid,))
    recommendations = cur.fetchall()

    cart_count = _cart_count(uid, db)
    cur.close(); db.close()
    return render_template('wishlist.html', items=items, cart_count=cart_count,
                           username=session.get('username', ''),
                           recommendations=recommendations)

# ================================================================
# CUSTOMER API
# ================================================================

@app.route('/api/cart/add', methods=['POST'])
@login_required
def cart_add():
    uid  = session['user_id']
    data = request.get_json() or {}
    product_id = data.get('product_id')
    qty = max(1, int(data.get('quantity', 1)))

    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id, stock FROM products WHERE id=%s AND is_active=1", (product_id,))
    product = cur.fetchone()
    if not product:
        cur.close(); db.close()
        return jsonify({'error': 'Product not found'}), 404

    cur.execute("SELECT id, quantity FROM cart WHERE user_id=%s AND product_id=%s", (uid, product_id))
    existing = cur.fetchone()

    cur2 = db.cursor()
    if existing:
        new_qty = min(existing['quantity'] + qty, product['stock'])
        cur2.execute("UPDATE cart SET quantity=%s WHERE id=%s", (new_qty, existing['id']))
    else:
        cur2.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (%s,%s,%s)",
                     (uid, product_id, min(qty, product['stock'])))
    cur2.close()

    cart_count = _cart_count(uid, db)
    cur.close(); db.close()
    return jsonify({'success': True, 'cart_count': cart_count})

@app.route('/api/cart/update', methods=['POST'])
@login_required
def cart_update():
    uid  = session['user_id']
    data = request.get_json() or {}
    product_id = data.get('product_id')
    qty = int(data.get('quantity', 1))

    db  = get_db()
    cur = db.cursor()
    if qty <= 0:
        cur.execute("DELETE FROM cart WHERE user_id=%s AND product_id=%s", (uid, product_id))
    else:
        cur.execute("UPDATE cart SET quantity=%s WHERE user_id=%s AND product_id=%s",
                    (qty, uid, product_id))
    cur.close()

    cur2 = db.cursor(dictionary=True)
    cur2.execute("""
        SELECT (p.price * c.quantity) as subtotal
        FROM cart c JOIN products p ON p.id = c.product_id
        WHERE c.user_id=%s AND p.id=%s
    """, (uid, product_id))
    item = cur2.fetchone()

    cur2.execute("""
        SELECT SUM(p.price * c.quantity) as total
        FROM cart c JOIN products p ON p.id = c.product_id WHERE c.user_id=%s
    """, (uid,))
    total_row = cur2.fetchone()
    cur2.close(); db.close()

    return jsonify({
        'success': True,
        'subtotal': float(item['subtotal']) if item else 0,
        'total':    float(total_row['total'] or 0) if total_row else 0,
        'cart_count': qty if qty > 0 else 0
    })

@app.route('/api/cart/remove', methods=['POST'])
@login_required
def cart_remove():
    uid  = session['user_id']
    data = request.get_json() or {}
    product_id = data.get('product_id')
    db  = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM cart WHERE user_id=%s AND product_id=%s", (uid, product_id))
    cur.close()

    cur2 = db.cursor(dictionary=True)
    cur2.execute("""
        SELECT SUM(p.price * c.quantity) as total, SUM(c.quantity) as cnt
        FROM cart c JOIN products p ON p.id = c.product_id WHERE c.user_id=%s
    """, (uid,))
    row = cur2.fetchone()
    cur2.close(); db.close()

    return jsonify({
        'success': True,
        'total':      float(row['total'] or 0) if row else 0,
        'cart_count': int(row['cnt'] or 0) if row else 0
    })

@app.route('/api/order/place', methods=['POST'])
@login_required
def place_order():
    uid  = session['user_id']
    data = request.get_json() or {}

    shipping_name = (data.get('name') or '').strip()[:100]
    shipping_phone = (data.get('phone') or '').strip()[:30]
    shipping_address = (data.get('address') or '').strip()[:1000]
    notes = (data.get('notes') or '').strip()[:1000]

    if not shipping_name or not shipping_phone or not shipping_address:
        return jsonify({'error': 'Name, phone, and address are required'}), 400

    payment_method = (data.get('payment_method') or 'cod').strip().lower()
    valid_payment_methods = {'cod', 'card', 'wallet', 'bank_transfer'}
    if payment_method not in valid_payment_methods:
        payment_method = 'cod'

    payment_status = 'pending' if payment_method == 'cod' else 'paid'
    payment_ref = None if payment_method == 'cod' else (
        f"PAY-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    )

    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT c.quantity, p.id as product_id, p.retailer_id,
               p.name, p.price, p.stock
        FROM cart c JOIN products p ON p.id = c.product_id
        WHERE c.user_id = %s AND p.is_active = 1
    """, (uid,))
    items = cur.fetchall()

    if not items:
        cur.close(); db.close()
        return jsonify({'error': 'Cart is empty'}), 400

    for item in items:
        if item['quantity'] > item['stock']:
            cur.close(); db.close()
            return jsonify({'error': f"{item['name']} has insufficient stock"}), 400

    total = sum(float(i['price']) * i['quantity'] for i in items)

    cur2 = db.cursor()
    cur2.execute("""
        INSERT INTO orders (
            customer_id, total_amount, status,
            shipping_name, shipping_address, shipping_phone, notes,
            payment_method, payment_status, payment_ref
        )
        VALUES (%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s)
    """, (uid, total, shipping_name, shipping_address, shipping_phone, notes,
          payment_method, payment_status, payment_ref))
    order_id = cur2.lastrowid

    for item in items:
        cur2.execute("""
            INSERT INTO order_items (order_id, product_id, retailer_id, product_name, quantity, unit_price)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (order_id, item['product_id'], item['retailer_id'], item['name'], item['quantity'], item['price']))
        cur2.execute("UPDATE products SET stock = stock - %s WHERE id=%s",
                     (item['quantity'], item['product_id']))

    save_default_address(uid, {
        'label': data.get('address_label') or 'Home',
        'full_name': shipping_name,
        'phone': shipping_phone,
        'address_line': shipping_address,
        'city': data.get('city') or '',
        'province': data.get('province') or '',
        'postal_code': data.get('postal_code') or ''
    }, db)

    cur2.execute("DELETE FROM cart WHERE user_id=%s", (uid,))
    cur2.close(); cur.close(); db.close()
    return jsonify({
        'success': True,
        'order_id': order_id,
        'payment_method': payment_method,
        'payment_status': payment_status,
        'payment_ref': payment_ref
    })

@app.route('/api/wishlist/toggle', methods=['POST'])
@login_required
def wishlist_toggle():
    uid  = session['user_id']
    data = request.get_json() or {}
    product_id = data.get('product_id')

    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id FROM wishlists WHERE user_id=%s AND product_id=%s", (uid, product_id))
    existing = cur.fetchone()
    cur.close()

    cur2 = db.cursor()
    if existing:
        cur2.execute("DELETE FROM wishlists WHERE user_id=%s AND product_id=%s", (uid, product_id))
        in_wishlist = False
    else:
        cur2.execute("INSERT INTO wishlists (user_id, product_id) VALUES (%s,%s)", (uid, product_id))
        in_wishlist = True
    cur2.close(); db.close()
    return jsonify({'success': True, 'in_wishlist': in_wishlist})

@app.route('/api/review/add', methods=['POST'])
@login_required
def add_review():
    uid  = session['user_id']
    data = request.get_json() or {}
    product_id = data.get('product_id')
    rating = int(data.get('rating', 5))
    comment = (data.get('comment') or '').strip()[:1000]

    if not 1 <= rating <= 5:
        return jsonify({'error': 'Invalid rating'}), 400

    db  = get_db()
    cur = db.cursor()
    try:
        cur.execute("""
            INSERT INTO reviews (product_id, user_id, rating, comment)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE rating=%s, comment=%s
        """, (product_id, uid, rating, comment, rating, comment))
    except Exception as e:
        cur.close(); db.close()
        return jsonify({'error': str(e)}), 500
    cur.close()

    cur2 = db.cursor(dictionary=True)
    cur2.execute("SELECT AVG(rating) as avg, COUNT(*) as cnt FROM reviews WHERE product_id=%s", (product_id,))
    stats = cur2.fetchone()
    cur2.execute("SELECT username FROM users WHERE id=%s", (uid,))
    u = cur2.fetchone()
    cur2.close(); db.close()

    return jsonify({
        'success': True,
        'avg_rating': round(float(stats['avg'] or 0), 1),
        'review_count': stats['cnt'],
        'username': u['username'] if u else '',
        'comment': comment,
        'rating': rating
    })

# ================================================================
# RETAILER ROUTES
# ================================================================

@app.route('/retailer/dashboard')
@retailer_required
def retailer_dashboard():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) as cnt FROM products WHERE retailer_id=%s AND is_active=1", (uid,))
    product_count = cur.fetchone()['cnt']

    cur.execute("""
        SELECT COUNT(DISTINCT o.id) as cnt FROM orders o
        JOIN order_items oi ON oi.order_id = o.id WHERE oi.retailer_id=%s
    """, (uid,))
    order_count = cur.fetchone()['cnt']

    cur.execute("""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) as revenue
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE oi.retailer_id=%s AND o.status != 'cancelled'
    """, (uid,))
    revenue = float(cur.fetchone()['revenue'] or 0)

    cur.execute("""
        SELECT COUNT(DISTINCT o.id) as cnt FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.retailer_id=%s AND o.status='pending'
    """, (uid,))
    pending_count = cur.fetchone()['cnt']

    cur.execute("""
        SELECT o.id, o.created_at, o.status, o.total_amount, o.shipping_name,
               GROUP_CONCAT(oi.product_name SEPARATOR ', ') as items_summary
        FROM orders o JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.retailer_id=%s
        GROUP BY o.id ORDER BY o.created_at DESC LIMIT 10
    """, (uid,))
    recent_orders = cur.fetchall()
    for o in recent_orders:
        o['created_at'] = str(o.get('created_at', ''))

    cur.execute("""
        SELECT p.id, p.name, p.price, p.stock,
               COALESCE(SUM(oi.quantity),0) as sold
        FROM products p LEFT JOIN order_items oi ON oi.product_id=p.id
        WHERE p.retailer_id=%s
        GROUP BY p.id ORDER BY sold DESC LIMIT 5
    """, (uid,))
    top_products = cur.fetchall()
    cur.close(); db.close()

    return render_template('retailer_dashboard.html',
                           product_count=product_count, order_count=order_count,
                           revenue=revenue, pending_count=pending_count,
                           recent_orders=recent_orders, top_products=top_products,
                           username=session.get('username', ''))

@app.route('/retailer/products')
@retailer_required
def retailer_products():
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT p.*, c.name as category_name,
               (SELECT COUNT(*) FROM order_items oi WHERE oi.product_id=p.id) as total_sold
        FROM products p LEFT JOIN categories c ON c.id=p.category_id
        WHERE p.retailer_id=%s ORDER BY p.created_at DESC
    """, (uid,))
    products = cur.fetchall()
    cur.execute("SELECT * FROM categories ORDER BY name")
    categories = cur.fetchall()
    cur.close(); db.close()
    return render_template('retailer_products.html', products=products,
                           categories=categories, username=session.get('username', ''))

@app.route('/retailer/orders')
@retailer_required
def retailer_orders():
    uid = session['user_id']
    status_filter = request.args.get('status', '')
    db  = get_db()
    cur = db.cursor(dictionary=True)
    sql = """
        SELECT o.id, o.created_at, o.status, o.total_amount,
               o.shipping_name, o.shipping_address, o.shipping_phone,
               GROUP_CONCAT(CONCAT(oi.product_name,' x',oi.quantity) SEPARATOR ', ') as items_summary,
               SUM(oi.quantity * oi.unit_price) as retailer_total
        FROM orders o JOIN order_items oi ON oi.order_id=o.id
        WHERE oi.retailer_id=%s
    """
    params = [uid]
    if status_filter:
        sql += " AND o.status=%s"; params.append(status_filter)
    sql += " GROUP BY o.id ORDER BY o.created_at DESC"
    cur.execute(sql, params)
    orders = cur.fetchall()
    for o in orders:
        o['created_at'] = str(o.get('created_at', ''))
    cur.close(); db.close()
    return render_template('retailer_orders.html', orders=orders,
                           status_filter=status_filter, username=session.get('username', ''))


@app.route('/retailer/assistant')
@retailer_required
def retailer_assistant():
    return render_template('retailer_assistant.html', username=session.get('username', ''))

# ================================================================
# RETAILER API
# ================================================================

@app.route('/api/retailer/product/add', methods=['POST'])
@retailer_required
def retailer_add_product():
    uid  = session['user_id']
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Product name is required'}), 400

    try:
        price          = float(request.form.get('price', 0) or 0)
        stock          = int(request.form.get('stock', 0) or 0)
        orig_raw       = request.form.get('original_price', '').strip()
        original_price = float(orig_raw) if orig_raw else None
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid price or stock value'}), 400

    description = request.form.get('description', '').strip()
    category_id = request.form.get('category_id') or None
    is_active   = int(request.form.get('is_active', 1))

    image_url = None
    if 'image' in request.files and request.files['image'].filename:
        f = request.files['image']
        if allowed_file(f.filename):
            ext      = f.filename.rsplit('.', 1)[1].lower()
            filename = f"prod_{uid}_{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(UPLOAD_FOLDER_PRODUCTS, filename))
            image_url = f"/static/uploads/products/{filename}"

    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO products (retailer_id, category_id, name, description, price,
                              original_price, stock, image_url, is_active)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (uid, category_id, name, description, price, original_price, stock, image_url, is_active))
    new_id = cur.lastrowid
    cur.close(); db.close()
    return jsonify({'success': True, 'product_id': new_id})

@app.route('/api/retailer/product/<int:product_id>/edit', methods=['POST'])
@retailer_required
def retailer_edit_product(product_id):
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM products WHERE id=%s AND retailer_id=%s", (product_id, uid))
    product = cur.fetchone()
    cur.close()
    if not product:
        db.close()
        return jsonify({'error': 'Not found'}), 404

    name        = request.form.get('name', product['name']).strip()
    description = request.form.get('description', product['description'] or '').strip()
    try:
        price       = float(request.form.get('price', product['price']))
        stock       = int(request.form.get('stock', product['stock']))
        orig_raw    = request.form.get('original_price', '').strip()
        orig_price  = float(orig_raw) if orig_raw else None
    except (ValueError, TypeError):
        db.close()
        return jsonify({'error': 'Invalid price or stock'}), 400

    category_id = request.form.get('category_id') or product['category_id']
    is_active   = int(request.form.get('is_active', product['is_active']))

    image_url = product['image_url']
    if 'image' in request.files and request.files['image'].filename:
        f = request.files['image']
        if allowed_file(f.filename):
            ext      = f.filename.rsplit('.', 1)[1].lower()
            filename = f"prod_{uid}_{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(UPLOAD_FOLDER_PRODUCTS, filename))
            image_url = f"/static/uploads/products/{filename}"

    cur2 = db.cursor()
    cur2.execute("""
        UPDATE products SET name=%s, description=%s, price=%s, original_price=%s,
               stock=%s, category_id=%s, is_active=%s, image_url=%s
        WHERE id=%s AND retailer_id=%s
    """, (name, description, price, orig_price, stock, category_id, is_active, image_url, product_id, uid))
    cur2.close(); db.close()
    return jsonify({'success': True})

@app.route('/api/retailer/product/<int:product_id>/delete', methods=['POST'])
@retailer_required
def retailer_delete_product(product_id):
    uid = session['user_id']
    db  = get_db()
    cur = db.cursor()
    cur.execute("UPDATE products SET is_active=0 WHERE id=%s AND retailer_id=%s", (product_id, uid))
    cur.close(); db.close()
    return jsonify({'success': True})

@app.route('/api/retailer/order/<int:order_id>/status', methods=['POST'])
@retailer_required
def retailer_update_order_status(order_id):
    uid       = session['user_id']
    data      = request.get_json() or {}
    new_status = data.get('status', '')
    valid      = ('pending', 'processing', 'shipped', 'delivered', 'cancelled')
    if new_status not in valid:
        return jsonify({'error': 'Invalid status'}), 400

    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT id FROM order_items WHERE order_id=%s AND retailer_id=%s LIMIT 1",
                (order_id, uid))
    if not cur.fetchone():
        cur.close(); db.close()
        return jsonify({'error': 'Unauthorized'}), 403
    cur.close()

    cur2 = db.cursor()
    cur2.execute("UPDATE orders SET status=%s WHERE id=%s", (new_status, order_id))
    db.commit()
    cur2.close(); db.close()
    return jsonify({'success': True, 'status': new_status})

@app.route('/api/retailer/stock/update', methods=['POST'])
@retailer_required
def retailer_update_stock():
    uid  = session['user_id']
    data = request.get_json() or {}
    product_id = data.get('product_id')
    stock      = int(data.get('stock', 0))
    db  = get_db()
    cur = db.cursor()
    cur.execute("UPDATE products SET stock=%s WHERE id=%s AND retailer_id=%s",
                (stock, product_id, uid))
    cur.close(); db.close()
    return jsonify({'success': True})

@app.route('/api/products')
def api_products():
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT p.id, p.name, p.price, p.image_url, p.stock, c.name as category
        FROM products p LEFT JOIN categories c ON c.id=p.category_id
        WHERE p.is_active=1 ORDER BY p.created_at DESC LIMIT 50
    """)
    products = cur.fetchall()
    cur.close(); db.close()
    return jsonify(products)

@app.route('/api/categories')
def api_categories():
    db  = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM categories ORDER BY name")
    cats = cur.fetchall()
    cur.close(); db.close()
    return jsonify(cats)

# ================================================================
# AI CHAT ROUTE
# ================================================================

def _encode_structured_bot_message(query_ran: str, db_output: str, answer: str) -> str:
    payload = {
        '_sage_structured': True,
        'query_ran': query_ran,
        'db_output': db_output,
        'answer': answer,
    }
    return json.dumps(payload, ensure_ascii=True)


def _decode_structured_bot_message(raw_message):
    if not raw_message:
        return raw_message

    try:
        payload = json.loads(raw_message)
    except (TypeError, json.JSONDecodeError):
        return raw_message

    if isinstance(payload, dict) and payload.get('_sage_structured'):
        return {
            'query_ran': payload.get('query_ran', ''),
            'db_output': payload.get('db_output', ''),
            'answer': payload.get('answer', ''),
        }
    return raw_message


def _insert_ai_chat_row(cur, user_id: int, role: str, sender: str, message: str):
    """
    Insert one AI history row.
    Fallback to legacy schema (without role column) when needed.
    """
    normalized_role = (role or 'customer').strip().lower()[:20] or 'customer'
    normalized_sender = 'user' if str(sender).lower() == 'user' else 'bot'
    payload = str(message or '')

    try:
        cur.execute(
            "INSERT INTO ai_chat_history (user_id, role, sender, message) VALUES (%s,%s,%s,%s)",
            (user_id, normalized_role, normalized_sender, payload)
        )
    except mysql.connector.Error as e:
        # Backward compatibility for old ai_chat_history tables missing `role`.
        if getattr(e, 'errno', None) == 1054 and 'role' in str(e).lower():
            cur.execute(
                "INSERT INTO ai_chat_history (user_id, sender, message) VALUES (%s,%s,%s)",
                (user_id, normalized_sender, payload)
            )
        else:
            raise

@app.route('/api/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    data     = request.get_json() or {}
    question = (data.get('message') or '').strip()
    if not question:
        return jsonify({'error': 'No message provided'}), 400

    # ── LAYER 2: Keyword Blocklist — scan input BEFORE it reaches the LLM ──
    input_safe, input_msg = validate_user_input(question)
    if not input_safe:
        return jsonify({
            'reply': input_msg,
            'query_ran': f'User Question: {question}\nStatus: BLOCKED by Layer 2 — Keyword Blocklist',
            'db_output': 'No database query was executed. Input was rejected by security filter.'
        })

    role    = session.get('role', 'customer')
    user_id = session.get('user_id')

    answer = ''
    query_ran = ''
    db_output = ''
    save_error = ''
    db = None

    try:
        # ── Phase 2: True text-to-SQL via LangChain + OpenRouter ──────────────
        # ask_sage_langchain generates real SQL using the LLM, executes it
        # against the read-only database, then synthesizes a plain-English answer.
        # It returns {'query_ran', 'db_output', 'answer'} — same keys as before.
        payload = ask_sage_langchain(question, role, user_id)
        answer    = (payload.get('answer') or '').strip() or 'No response.'
        query_ran = payload.get('query_ran') or f'User Question: {question}'
        db_output = payload.get('db_output') or 'No database output available.'

        # ── LAYER 4: Output Validation — check AI response BEFORE displaying ──
        output_safe, sanitized = validate_ai_output(answer)
        if not output_safe:
            answer = sanitized

        # Persisting chat history requires INSERT permission.
        db = get_db()
    except Exception as e:
        answer = f"Something went wrong: {str(e)[:100]}"
        query_ran = f'User Question: {question}'
        db_output = 'No database output available due to an internal error.'
    finally:
        if db:
            try:
                # Persist both sides of the conversation; do not fail the reply if save fails.
                cur = db.cursor()
                _insert_ai_chat_row(cur, user_id, role, 'user', question)
                _insert_ai_chat_row(cur, user_id, role, 'bot', _encode_structured_bot_message(query_ran, db_output, answer))
                cur.close()
            except Exception as save_exc:
                save_error = str(save_exc)[:180]
                app.logger.warning(f'ai_chat history save failed for user {user_id}: {save_exc}')

            try:
                db.close()
            except Exception:
                pass

    response = {'reply': answer, 'query_ran': query_ran, 'db_output': db_output}
    if save_error:
        response['save_error'] = save_error
    return jsonify(response)


@app.route('/api/ai/history')
@login_required
def ai_history():
    """Return last 40 messages for the current user, oldest first."""
    user_id = session.get('user_id')
    try:
        db  = get_db()
        cur = db.cursor(dictionary=True)
        cur.execute("""
            SELECT sender, message, created_at
            FROM ai_chat_history
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 40
        """, (user_id,))
        rows = cur.fetchall()
        cur.close(); db.close()
        # Reverse so oldest is first
        rows.reverse()
        # Convert datetime to string
        for r in rows:
            if r.get('sender') == 'bot':
                r['message'] = _decode_structured_bot_message(r.get('message'))
            r['created_at'] = r['created_at'].strftime('%d %b %H:%M') if r['created_at'] else ''
        return jsonify({'history': rows})
    except Exception as e:
        return jsonify({'history': [], 'error': str(e)})

# ================================================================
# RUN
# ================================================================
if __name__ == '__main__':
    port       = int(os.environ.get("PORT", 5000))
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
