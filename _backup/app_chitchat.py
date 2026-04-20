
import os
import random
import string
import base64
import uuid
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_mail import Mail, Message
import mysql.connector
from dotenv import load_dotenv

load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# Session settings
# ----------------
# By default Flask uses a signed cookie for session storage.  We
# do *not* mark the session as permanent, which means the cookie is
# discarded when the browser closes.  This prevents the situation
# where somebody opens the app, logs in, copies a URL and sends it to
# a friend who then magically has a valid login â€“ the friend has no
# session cookie, so our @login_required guards will redirect them to
# /login.

app.config['SESSION_PERMANENT'] = False
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB upload limit
# you can also customise the lifetime in case you want automatic
# expiration while the browser is still open:
# from datetime import timedelta
# app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

# ---------------- SOCKET.IO INITIALIZATION ----------------
# SINGLE, CLEAN INITIALIZATION - avoids duplicate re-init issues
_cors_origins = os.getenv("CORS_ORIGINS", "*")
if _cors_origins != "*":
    _cors_origins = [o.strip() for o in _cors_origins.split(",")]

socketio = SocketIO(
    app,
    cors_allowed_origins=_cors_origins,
    manage_session=False,
    max_http_buffer_size=10000000
)

# Master dictionary to keep track of everyone
# now maps user_id -> set of active socket session ids.  a single
# logical user may have multiple sockets (chat window + call popup,
# mobile + desktop, etc).  we only consider the user "offline" when
# the set becomes empty.
online_users = {}

# track currently in-progress calls by room so we can record them when
# they end.  key is the WebRTC room string used throughout the app.
ongoing_calls = {}

# ---------------- DATABASE HELPER ----------------
####################################################################
# FUNCTION: get_db  +  compatibility layer for MySQL / PostgreSQL
####################################################################
def get_db():
    db_url = os.getenv("DATABASE_URL")
    if db_url and db_url.startswith("postgres"):
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        return conn
        
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "chitchat"),
        autocommit=True
    )

# ---------------- MAIL ----------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['MAIL_USE_TLS'] = True
mail = Mail(app)

# ---------------- HELPERS ----------------
####################################################################
# FUNCTION: send_otp
####################################################################
def send_otp(email):
    otp = ''.join(random.choices(string.digits, k=6))
    msg = Message("Your VeloApp OTP",
                  sender=app.config['MAIL_USERNAME'],
                  recipients=[email])
    msg.body = f"Your OTP is {otp}"
    mail.send(msg)
    return otp

####################################################################
# FUNCTION: get_room_name
####################################################################
def get_room_name(user1, user2):
    return f"chat_{min(user1, user2)}_{max(user1, user2)}"

# ---------------- DATABASE INIT ----------------
def init_db():
    """Create missing tables and columns on startup."""
    try:
        db_url = os.getenv("DATABASE_URL")
        if db_url and db_url.startswith("postgres"):
            # Postgres schemas should be initialized with database.postgres.sql
            return
            
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='messages' AND COLUMN_NAME='deleted_for_everyone'",
                (os.getenv('DB_NAME'),))
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE messages ADD COLUMN deleted_for_everyone TINYINT DEFAULT 0")
        except Exception:
            pass
        try:
            cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='users' AND COLUMN_NAME='bio'",
                (os.getenv('DB_NAME'),))
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE users ADD COLUMN bio TEXT")
        except Exception:
            pass
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_deletions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                user_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_del (message_id, user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                user_id INT NOT NULL,
                emoji VARCHAR(10) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_react (message_id, user_id, emoji)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                type VARCHAR(20) NOT NULL,
                from_user_id INT,
                reference_id INT,
                content TEXT,
                is_read TINYINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_notif_user (user_id),
                INDEX idx_notif_unread (user_id, is_read)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS statuses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                media_url TEXT NOT NULL,
                media_type VARCHAR(10) NOT NULL,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME,
                INDEX idx_status_user (user_id),
                INDEX idx_status_expires (expires_at)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS status_views (
                id INT AUTO_INCREMENT PRIMARY KEY,
                status_id INT NOT NULL,
                user_id INT NOT NULL,
                viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_status_view (status_id, user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reels (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                video_url TEXT NOT NULL,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_reel_user (user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reel_likes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                reel_id INT NOT NULL,
                user_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_reel_like (reel_id, user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                audio_url TEXT NOT NULL,
                title VARCHAR(200),
                artist VARCHAR(200),
                cover_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_song_user (user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS song_likes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                song_id INT NOT NULL,
                user_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_song_like (song_id, user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reel_comments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                reel_id INT NOT NULL,
                user_id INT NOT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_rc_reel (reel_id)
            )
        """)
        # Add bio column if it doesn't exist yet
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT NULL")
        except Exception:
            pass
        # Add reply columns to messages
        for col, defn in [('reply_to_id', 'INT DEFAULT NULL'), ('reply_preview', 'TEXT DEFAULT NULL')]:
            try:
                cursor.execute(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='messages' AND COLUMN_NAME=%s",
                    (os.getenv('DB_NAME'), col))
                if not cursor.fetchone():
                    cursor.execute(f"ALTER TABLE messages ADD COLUMN {col}")
            except Exception:
                pass
        # Follows table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS follows (
                id INT AUTO_INCREMENT PRIMARY KEY,
                follower_id INT UNSIGNED NOT NULL,
                following_id INT UNSIGNED NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_follow (follower_id, following_id),
                INDEX idx_follower (follower_id),
                INDEX idx_following (following_id)
            ) ENGINE=InnoDB
        """)
        # Blocks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                blocker_id INT UNSIGNED NOT NULL,
                blocked_id INT UNSIGNED NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_block (blocker_id, blocked_id),
                INDEX idx_blocker (blocker_id),
                INDEX idx_blocked (blocked_id)
            ) ENGINE=InnoDB
        """)
        # Posts table (permanent image/video posts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                media_url TEXT NOT NULL,
                media_type VARCHAR(10) NOT NULL,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_post_user (user_id)
            ) ENGINE=InnoDB
        """)
        # Post likes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_likes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                post_id INT NOT NULL,
                user_id INT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_post_like (post_id, user_id),
                INDEX idx_pl_post (post_id)
            ) ENGINE=InnoDB
        """)
        # Post comments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_comments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                post_id INT NOT NULL,
                user_id INT NOT NULL,
                comment TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_pc_post (post_id)
            ) ENGINE=InnoDB
        """)
        db.commit()
        cursor.close()
        db.close()
        app.logger.info('init_db completed successfully')
    except Exception as e:
        app.logger.warning(f'init_db error: {e}')

init_db()

# ---------------- GOOGLE VERIFICATION ----------------
####################################################################
# FUNCTION: google_verify
####################################################################
@app.route('/google<verification_id>.html')
def google_verify(verification_id):
    filename = f"google{verification_id}.html"
    return send_from_directory('.', filename)

# ---------------- ROUTES ----------------
####################################################################
# FUNCTION: index
####################################################################
@app.route('/')
def index():
    return redirect('/login')


####################################################################
# FUNCTION: call_page
# Serves the call UI template at /call.html (expects query params)
####################################################################
@app.route('/call.html')
def call_page():
    if not session.get('user_id'):
        return redirect('/login')
    return render_template('call.html')

####################################################################
# FUNCTION: call_alias
# Optional alias for convenience
####################################################################
@app.route('/call')
def call_alias():
    if not session.get('user_id'):
        return redirect('/login')
    return render_template('call.html')



####################################################################
# FUNCTION: login
####################################################################
@app.route('/login', methods=['GET','POST'])

def login():
    # If already logged in, go straight to chat
    if session.get('user_id'):
        return redirect('/chat')

    # Clear any leftover session data so old sessions don't bleed in
    session.clear()

    if request.method == 'POST' and 'phone' in request.form:
        db = get_db()
        cursor = db.cursor(dictionary=True)

        phone = request.form.get('phone')
        raw_password = request.form.get('password', '')

        cursor.execute("SELECT * FROM users WHERE phone_number=%s", (phone,))
        user = cursor.fetchone()

        if user and check_password_hash(user['password'], raw_password):
            cursor.close()
            db.close()
            session['user_id'] = user['id']
            return redirect('/chat')

        cursor.close()
        db.close()
        return redirect('/login?error=Invalid+phone+number+or+password')

    return render_template('auth.html')

####################################################################
# FUNCTION: register
####################################################################
@app.route('/register', methods=['POST'])

def register():
    db = get_db()
    cursor = db.cursor(dictionary=True)

    username = request.form.get('username')
    phone = request.form.get('phone')
    email = request.form.get('email')
    password = request.form.get('password')

    cursor.execute("SELECT * FROM users WHERE email=%s OR phone_number=%s",
                   (email, phone))
    if cursor.fetchone():
        cursor.close()
        db.close()
        return redirect('/login?tab=register&error=An+account+with+that+email+or+phone+already+exists')

    otp = send_otp(email)
    session['otp'] = otp
    session['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    session['reg_data'] = {
        'username': username,
        'phone': phone,
        'email': email,
        'password': generate_password_hash(password)
    }

    cursor.close()
    db.close()
    return redirect('/verify')

#-----------------PROFILE-----------------------
UPLOAD_FOLDER_PFP = os.path.join('static', 'uploads', 'profile_pics')
UPLOAD_FOLDER_STATUS = os.path.join('static', 'uploads', 'statuses')
UPLOAD_FOLDER_REELS = os.path.join('static', 'uploads', 'reels')
UPLOAD_FOLDER_SONGS = os.path.join('static', 'uploads', 'songs')
UPLOAD_FOLDER_COVERS = os.path.join('static', 'uploads', 'song_covers')
UPLOAD_FOLDER_POSTS = os.path.join('static', 'uploads', 'images')
os.makedirs(UPLOAD_FOLDER_PFP, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_STATUS, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_REELS, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_SONGS, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_COVERS, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_POSTS, exist_ok=True)

ALLOWED_IMAGE_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
ALLOWED_VIDEO_EXT = {'mp4', 'webm', 'mov'}
ALLOWED_AUDIO_EXT = {'mp3', 'wav', 'ogg', 'aac', 'm4a', 'webm'}

def allowed_file(filename, allowed_exts):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_exts

####################################################################
# FUNCTION: update_profile
####################################################################
@app.route('/update_profile', methods=['POST'])
def update_profile():
    if not session.get('user_id'):
        return redirect('/login')

    if 'profile_pic' not in request.files:
        return redirect('/profile')

    file = request.files['profile_pic']
    if file.filename == '':
        return redirect('/profile')

    if file:
        ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
        parts = file.filename.rsplit('.', 1)
        if len(parts) != 2 or parts[1].lower() not in ALLOWED_EXTENSIONS:
            return redirect('/profile')
        ext = parts[1].lower()
        filename = f"pfp_{session['user_id']}_{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER_PFP, filename)
        file.save(filepath)
        db_path = f"profile_pics/{filename}"
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute("UPDATE users SET profile_pic = %s WHERE id = %s", (db_path, session['user_id']))
        db.commit()
        cursor.close()
        db.close()

    return redirect('/profile')

####################################################################
# FUNCTION: profile
####################################################################
@app.route('/profile')
def profile():
    if not session.get('user_id'):
        return redirect('/login')

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (session['user_id'],))
    user = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as cnt FROM reels WHERE user_id = %s", (session['user_id'],))
    reel_count = cursor.fetchone()['cnt']
    cursor.execute("SELECT COUNT(*) as cnt FROM posts WHERE user_id = %s", (session['user_id'],))
    post_count = cursor.fetchone()['cnt']
    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE following_id = %s", (session['user_id'],))
    follower_count = cursor.fetchone()['cnt']
    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE follower_id = %s", (session['user_id'],))
    following_count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()

    return render_template('profile.html',
                           username=user['username'],
                           email=user['email'],
                           phone=user['phone_number'],
                           profile_pic=user['profile_pic'],
                           bio=(user.get('bio') or ''),
                           user_id=user['id'],
                           reel_count=reel_count,
                           post_count=post_count,
                           follower_count=follower_count,
                           following_count=following_count)

####################################################################
# FUNCTION: logout
####################################################################
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

####################################################################
# FUNCTION: user_profile (view-only, for viewing other users)
####################################################################
@app.route('/user_profile/<int:user_id>')
def user_profile(user_id):
    my_id = session.get('user_id')
    if not my_id:
        return redirect('/login')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT id, username, profile_pic, bio FROM users WHERE id = %s", (user_id,))
    target_user = cursor.fetchone()
    if not target_user:
        cursor.close()
        db.close()
        return redirect('/chat')

    cursor.execute("SELECT COUNT(*) as cnt FROM reels WHERE user_id = %s", (user_id,))
    reel_count = cursor.fetchone()['cnt']

    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE following_id = %s", (user_id,))
    follower_count = cursor.fetchone()['cnt']

    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE follower_id = %s", (user_id,))
    following_count = cursor.fetchone()['cnt']

    cursor.execute("SELECT id FROM follows WHERE follower_id = %s AND following_id = %s", (my_id, user_id))
    is_following = cursor.fetchone() is not None

    cursor.execute("SELECT id FROM blocks WHERE blocker_id = %s AND blocked_id = %s", (my_id, user_id))
    is_blocked = cursor.fetchone() is not None

    cursor.close()
    db.close()

    return render_template('user_profile.html',
                           target_user=target_user,
                           reel_count=reel_count,
                           follower_count=follower_count,
                           following_count=following_count,
                           is_following=is_following,
                           is_blocked=is_blocked)

####################################################################
# FUNCTION: follow
####################################################################
@app.route('/api/follow', methods=['POST'])
def api_follow():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    target_id = data.get('user_id')
    if not target_id or int(target_id) == my_id:
        return jsonify({'error': 'Invalid'}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute("INSERT INTO follows (follower_id, following_id) VALUES (%s, %s)", (my_id, int(target_id)))
        db.commit()
    except Exception:
        pass
    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE following_id = %s", (int(target_id),))
    fc = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'success': True, 'follower_count': fc})

####################################################################
# FUNCTION: unfollow
####################################################################
@app.route('/api/unfollow', methods=['POST'])
def api_unfollow():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    target_id = data.get('user_id')
    if not target_id or int(target_id) == my_id:
        return jsonify({'error': 'Invalid'}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (my_id, int(target_id)))
    db.commit()
    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE following_id = %s", (int(target_id),))
    fc = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'success': True, 'follower_count': fc})

####################################################################
# FUNCTION: my followers / following lists
####################################################################
@app.route('/api/my_followers')
def api_my_followers():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.username, u.profile_pic,
               (SELECT COUNT(*) FROM follows WHERE follower_id = %s AND following_id = u.id) as i_follow_them
        FROM follows f
        JOIN users u ON u.id = f.follower_id
        WHERE f.following_id = %s
    """, (my_id, my_id))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(rows)

@app.route('/api/my_following')
def api_my_following():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.username, u.profile_pic
        FROM follows f
        JOIN users u ON u.id = f.following_id
        WHERE f.follower_id = %s
    """, (my_id,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(rows)

@app.route('/api/remove_follower', methods=['POST'])
def api_remove_follower():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    target_id = data.get('user_id')
    if not target_id or int(target_id) == my_id:
        return jsonify({'error': 'Invalid'}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (int(target_id), my_id))
    db.commit()
    cursor.execute("SELECT COUNT(*) as cnt FROM follows WHERE following_id = %s", (my_id,))
    fc = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'success': True, 'follower_count': fc})

@app.route('/api/user/<int:uid>/followers')
def api_user_followers(uid):
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.username, u.profile_pic
        FROM follows f
        JOIN users u ON u.id = f.follower_id
        WHERE f.following_id = %s
    """, (uid,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(rows)

@app.route('/api/user/<int:uid>/following')
def api_user_following(uid):
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.username, u.profile_pic
        FROM follows f
        JOIN users u ON u.id = f.following_id
        WHERE f.follower_id = %s
    """, (uid,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(rows)

####################################################################
# FUNCTION: block
####################################################################
@app.route('/api/block', methods=['POST'])
def api_block():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    target_id = data.get('user_id')
    if not target_id or int(target_id) == my_id:
        return jsonify({'error': 'Invalid'}), 400
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s, %s)", (my_id, int(target_id)))
        db.commit()
    except Exception:
        pass
    # Also remove any follow relationship
    cursor.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (my_id, int(target_id)))
    cursor.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (int(target_id), my_id))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'success': True})

####################################################################
# FUNCTION: AI chat (OpenRouter)
####################################################################
@app.route('/api/ai', methods=['POST'])
def api_ai():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Empty message'}), 400
    try:
        reply = _generate_ai_reply_text(message, my_id)
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

####################################################################
# FUNCTION: unblock
####################################################################
@app.route('/api/unblock', methods=['POST'])
def api_unblock():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    target_id = data.get('user_id')
    if not target_id or int(target_id) == my_id:
        return jsonify({'error': 'Invalid'}), 400
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM blocks WHERE blocker_id = %s AND blocked_id = %s", (my_id, int(target_id)))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'success': True})

####################################################################
# FUNCTION: remove_profile_pic
####################################################################
@app.route('/remove_profile_pic')
def remove_profile_pic():
    if not session.get('user_id'):
        return redirect('/login')

    uid = session['user_id']
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT profile_pic FROM users WHERE id = %s", (uid,))
    user = cursor.fetchone()

    if user and user['profile_pic']:
        file_path = os.path.join(app.root_path, 'static', 'uploads', user['profile_pic'])
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file: {e}")

    cursor.execute("UPDATE users SET profile_pic = NULL WHERE id = %s", (uid,))
    db.commit()
    
    cursor.close()
    db.close()

    return redirect('/profile')

####################################################################
# FUNCTION: update_profile_info
####################################################################
@app.route('/update_profile_info', methods=['POST'])
def update_profile_info():
    if not session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    username = data.get('username', '').strip()
    bio = data.get('bio', '').strip()
    if not username or len(username) > 50:
        return jsonify({'error': 'Invalid username'}), 400
    if len(bio) > 200:
        return jsonify({'error': 'Bio too long'}), 400
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE users SET username=%s, bio=%s WHERE id=%s",
                   (username, bio, session['user_id']))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'success': True})

####################################################################
# FUNCTION: call_history
####################################################################
@app.route('/api/call_history')
def call_history():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT m.id, m.sender_id, m.receiver_id, m.message, m.timestamp,
               u.username, u.profile_pic
        FROM messages m
        JOIN users u ON u.id = IF(m.sender_id=%s, m.receiver_id, m.sender_id)
        WHERE m.type = 'call' AND (m.sender_id=%s OR m.receiver_id=%s)
        ORDER BY m.timestamp DESC
        LIMIT 50
    """, (uid, uid, uid))
    calls = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(calls)

####################################################################
# FUNCTION: get_notifications
####################################################################
@app.route('/api/notifications')
def get_notifications():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT n.*, u.username, u.profile_pic
        FROM notifications n
        LEFT JOIN users u ON u.id = n.from_user_id
        WHERE n.user_id = %s
        ORDER BY n.created_at DESC
        LIMIT 50
    """, (uid,))
    notifs = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(notifs)

####################################################################
# FUNCTION: mark_notifications_read
####################################################################
@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (uid,))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'success': True})

####################################################################
# FUNCTION: unread_notification_count
####################################################################
@app.route('/api/notifications/unread_count')
def unread_notification_count():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id=%s AND is_read=0", (uid,))
    result = cursor.fetchone()
    cursor.close()
    db.close()
    return jsonify({'count': result['count']})

####################################################################
# FUNCTION: get_user_info
####################################################################
@app.route('/api/user/<int:user_id>')
def get_user_info(user_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, username, profile_pic FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    cursor.close()
    db.close()
    if not user:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(user)

# ---- STATUS ROUTES ----

@app.route('/upload_status', methods=['POST'])
def upload_status():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    if 'media' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['media']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    caption = request.form.get('caption', '').strip()[:200]
    if not allowed_file(file.filename, ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT):
        return jsonify({'error': 'Invalid file type'}), 400
    ext = file.filename.rsplit('.', 1)[1].lower()
    media_type = 'image' if ext in ALLOWED_IMAGE_EXT else 'video'
    filename = f"status_{uid}_{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER_STATUS, filename))
    media_url = f"/static/uploads/statuses/{filename}"
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO statuses (user_id, media_url, media_type, caption, expires_at) "
        "VALUES (%s, %s, %s, %s, DATE_ADD(NOW(), INTERVAL 24 HOUR))",
        (uid, media_url, media_type, caption))
    cursor.close()
    db.close()
    return jsonify({'success': True})

@app.route('/api/statuses')
def get_statuses():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.*, u.username, u.profile_pic,
               (SELECT COUNT(*) FROM status_views sv WHERE sv.status_id = s.id) as view_count,
               (SELECT COUNT(*) FROM status_views sv WHERE sv.status_id = s.id AND sv.user_id = %s) as viewed_by_me
        FROM statuses s
        JOIN users u ON u.id = s.user_id
        WHERE s.expires_at > NOW()
          AND (s.user_id = %s
               OR s.user_id IN (SELECT following_id FROM follows WHERE follower_id = %s)
               OR s.user_id IN (SELECT follower_id FROM follows WHERE following_id = %s))
        ORDER BY s.user_id, s.created_at ASC
    """, (uid, uid, uid, uid))
    statuses = cursor.fetchall()
    cursor.close()
    db.close()
    for s in statuses:
        for key in ('created_at', 'expires_at'):
            if s.get(key):
                s[key] = str(s[key])
    grouped = {}
    for s in statuses:
        k = s['user_id']
        if k not in grouped:
            grouped[k] = {'user_id': k, 'username': s['username'],
                          'profile_pic': s['profile_pic'], 'statuses': []}
        grouped[k]['statuses'].append(s)
    result = list(grouped.values())
    result.sort(key=lambda x: (0 if x['user_id'] == uid else 1))
    return jsonify(result)

@app.route('/api/status/<int:status_id>/view', methods=['POST'])
def view_status(status_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT IGNORE INTO status_views (status_id, user_id) VALUES (%s, %s)", (status_id, uid))
    cursor.close()
    db.close()
    return jsonify({'success': True})

@app.route('/api/status/<int:status_id>', methods=['DELETE'])
def delete_status(status_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM statuses WHERE id=%s AND user_id=%s", (status_id, uid))
    status = cursor.fetchone()
    if not status:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404
    fpath = os.path.join(app.root_path, status['media_url'].lstrip('/'))
    if os.path.exists(fpath):
        try:
            os.remove(fpath)
        except Exception:
            pass
    cur2 = db.cursor()
    cur2.execute("DELETE FROM status_views WHERE status_id=%s", (status_id,))
    cur2.execute("DELETE FROM statuses WHERE id=%s", (status_id,))
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'success': True})

# ---- POSTS ROUTES ----

@app.route('/upload_post', methods=['POST'])
def upload_post():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    if 'media' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['media']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename, ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT):
        return jsonify({'error': 'Invalid file type'}), 400
    caption = request.form.get('caption', '').strip()[:200]
    ext = file.filename.rsplit('.', 1)[1].lower()
    media_type = 'image' if ext in ALLOWED_IMAGE_EXT else 'video'
    filename = f"post_{uid}_{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER_POSTS, filename))
    media_url = f"/static/uploads/images/{filename}"
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO posts (user_id, media_url, media_type, caption) VALUES (%s, %s, %s, %s)",
                   (uid, media_url, media_type, caption))
    cursor.close()
    db.close()
    return jsonify({'success': True})

@app.route('/api/my_posts')
def get_my_posts():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM posts WHERE user_id = %s ORDER BY created_at DESC", (uid,))
    posts = cursor.fetchall()
    for p in posts:
        if p.get('created_at'):
            p['created_at'] = str(p['created_at'])
    cursor.close()
    db.close()
    return jsonify(posts)

@app.route('/api/posts')
def get_all_posts():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.*, u.username, u.profile_pic
        FROM posts p JOIN users u ON u.id = p.user_id
        ORDER BY p.created_at DESC LIMIT 50
    """,)
    posts = cursor.fetchall()
    for p in posts:
        if p.get('created_at'):
            p['created_at'] = str(p['created_at'])
    cursor.close()
    db.close()
    return jsonify(posts)

@app.route('/api/post/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM posts WHERE id=%s AND user_id=%s", (post_id, uid))
    post = cursor.fetchone()
    if not post:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404
    fpath = os.path.join(app.root_path, post['media_url'].lstrip('/'))
    if os.path.exists(fpath):
        try:
            os.remove(fpath)
        except Exception:
            pass
    cur2 = db.cursor()
    cur2.execute("DELETE FROM posts WHERE id=%s", (post_id,))
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'success': True})

# ---- POST INTERACTIONS ----

@app.route('/api/post/<int:post_id>/like', methods=['POST'])
def like_post(post_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM post_likes WHERE post_id=%s AND user_id=%s", (post_id, uid))
    existing = cursor.fetchone()
    cur2 = db.cursor()
    if existing:
        cur2.execute("DELETE FROM post_likes WHERE post_id=%s AND user_id=%s", (post_id, uid))
        liked = False
    else:
        cur2.execute("INSERT INTO post_likes (post_id, user_id) VALUES (%s, %s)", (post_id, uid))
        liked = True
    cursor.execute("SELECT COUNT(*) as cnt FROM post_likes WHERE post_id=%s", (post_id,))
    count = cursor.fetchone()['cnt']
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'liked': liked, 'count': count})

@app.route('/api/post/<int:post_id>/comments')
def get_post_comments(post_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT pc.*, u.username, u.profile_pic
        FROM post_comments pc
        JOIN users u ON u.id = pc.user_id
        WHERE pc.post_id = %s
        ORDER BY pc.created_at ASC
    """, (post_id,))
    comments = cursor.fetchall()
    for c in comments:
        if c.get('created_at'):
            c['created_at'] = str(c['created_at'])
    cursor.close()
    db.close()
    return jsonify(comments)

@app.route('/api/post/<int:post_id>/comment', methods=['POST'])
def add_post_comment(post_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    comment = (data.get('comment') or '').strip()[:500]
    if not comment:
        return jsonify({'error': 'Empty comment'}), 400
    db = get_db()
    cur2 = db.cursor()
    cur2.execute("INSERT INTO post_comments (post_id, user_id, comment) VALUES (%s,%s,%s)",
                 (post_id, uid, comment))
    cur2.close()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT username, profile_pic FROM users WHERE id=%s", (uid,))
    u = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as cnt FROM post_comments WHERE post_id=%s", (post_id,))
    cnt = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'success': True, 'username': u['username'], 'profile_pic': u['profile_pic'], 'comment': comment, 'count': cnt})

# ---- REELS ROUTES ----

@app.route('/upload_reel', methods=['POST'])
def upload_reel():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    if 'video' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['video']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename, ALLOWED_VIDEO_EXT):
        return jsonify({'error': 'Use MP4, WebM, or MOV'}), 400
    caption = request.form.get('caption', '').strip()[:200]
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"reel_{uid}_{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER_REELS, filename))
    video_url = f"/static/uploads/reels/{filename}"
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO reels (user_id, video_url, caption) VALUES (%s, %s, %s)",
                   (uid, video_url, caption))
    cursor.close()
    db.close()
    return jsonify({'success': True})

@app.route('/api/reels')
def get_reels():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT r.*, u.username, u.profile_pic,
               (SELECT COUNT(*) FROM reel_likes rl WHERE rl.reel_id = r.id) as like_count,
               (SELECT COUNT(*) FROM reel_likes rl WHERE rl.reel_id = r.id AND rl.user_id = %s) as liked_by_me,
               (SELECT COUNT(*) FROM reel_comments rc WHERE rc.reel_id = r.id) as comment_count
        FROM reels r JOIN users u ON u.id = r.user_id
        ORDER BY r.created_at DESC LIMIT 50
    """, (uid,))
    reels = cursor.fetchall()
    for r in reels:
        if r.get('created_at'):
            r['created_at'] = str(r['created_at'])
    cursor.close()
    db.close()
    return jsonify(reels)

@app.route('/api/reel/<int:reel_id>/like', methods=['POST'])
def like_reel(reel_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM reel_likes WHERE reel_id=%s AND user_id=%s", (reel_id, uid))
    existed = cursor.fetchone()
    cur2 = db.cursor()
    if existed:
        cur2.execute("DELETE FROM reel_likes WHERE reel_id=%s AND user_id=%s", (reel_id, uid))
    else:
        cur2.execute("INSERT INTO reel_likes (reel_id, user_id) VALUES (%s, %s)", (reel_id, uid))
    cursor.execute("SELECT COUNT(*) as cnt FROM reel_likes WHERE reel_id=%s", (reel_id,))
    count = cursor.fetchone()['cnt']
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'liked': not bool(existed), 'count': count})

@app.route('/api/reel/<int:reel_id>', methods=['DELETE'])
def delete_reel(reel_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM reels WHERE id=%s AND user_id=%s", (reel_id, uid))
    reel = cursor.fetchone()
    if not reel:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404
    fpath = os.path.join(app.root_path, reel['video_url'].lstrip('/'))
    if os.path.exists(fpath):
        try:
            os.remove(fpath)
        except Exception:
            pass
    cur2 = db.cursor()
    cur2.execute("DELETE FROM reel_likes WHERE reel_id=%s", (reel_id,))
    cur2.execute("DELETE FROM reel_comments WHERE reel_id=%s", (reel_id,))
    cur2.execute("DELETE FROM reels WHERE id=%s", (reel_id,))
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'success': True})

@app.route('/api/reel/<int:reel_id>/comments', methods=['GET'])
def get_reel_comments(reel_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT rc.id, rc.user_id, rc.comment, rc.created_at, u.username, u.profile_pic
        FROM reel_comments rc
        JOIN users u ON rc.user_id = u.id
        WHERE rc.reel_id = %s
        ORDER BY rc.created_at ASC LIMIT 200
    """, (reel_id,))
    comments = cursor.fetchall()
    for c in comments:
        c['created_at'] = str(c['created_at'])
    cursor.close()
    db.close()
    return jsonify(comments)

@app.route('/api/reel/<int:reel_id>/comments', methods=['POST'])
@app.route('/api/reel/<int:reel_id>/comment', methods=['POST'])
def post_reel_comment(reel_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(force=True)
    comment = (data.get('comment') or '').strip()[:500]
    if not comment:
        return jsonify({'error': 'Empty comment'}), 400
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM reels WHERE id=%s", (reel_id,))
    if not cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({'error': 'Reel not found'}), 404
    cur2 = db.cursor()
    cur2.execute("INSERT INTO reel_comments (reel_id, user_id, comment) VALUES (%s,%s,%s)",
                 (reel_id, uid, comment))
    cur2.close()
    cursor.execute("SELECT username, profile_pic FROM users WHERE id=%s", (uid,))
    me = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as cnt FROM reel_comments WHERE reel_id=%s", (reel_id,))
    cc = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'success': True, 'username': me['username'], 'profile_pic': me.get('profile_pic',''), 'count': cc})

# ---- SONGS ROUTES ----

@app.route('/upload_song', methods=['POST'])
def upload_song():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    if 'audio' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['audio']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename, ALLOWED_AUDIO_EXT):
        return jsonify({'error': 'Invalid audio file type'}), 400
    title = request.form.get('title', '').strip()[:200] or file.filename.rsplit('.', 1)[0][:200]
    artist = request.form.get('artist', '').strip()[:200]
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"song_{uid}_{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER_SONGS, filename))
    audio_url = f"/static/uploads/songs/{filename}"
    cover_url = None
    if 'cover' in request.files and request.files['cover'].filename:
        cover = request.files['cover']
        if allowed_file(cover.filename, ALLOWED_IMAGE_EXT):
            cext = cover.filename.rsplit('.', 1)[1].lower()
            cname = f"cover_{uid}_{uuid.uuid4().hex}.{cext}"
            cover.save(os.path.join(UPLOAD_FOLDER_COVERS, cname))
            cover_url = f"/static/uploads/song_covers/{cname}"
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO songs (user_id, audio_url, title, artist, cover_url) VALUES (%s,%s,%s,%s,%s)",
                   (uid, audio_url, title, artist, cover_url))
    cursor.close()
    db.close()
    return jsonify({'success': True})

@app.route('/api/songs')
def get_songs():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.*, u.username, u.profile_pic,
               (SELECT COUNT(*) FROM song_likes sl WHERE sl.song_id = s.id) as like_count,
               (SELECT COUNT(*) FROM song_likes sl WHERE sl.song_id = s.id AND sl.user_id = %s) as liked_by_me
        FROM songs s JOIN users u ON u.id = s.user_id
        ORDER BY s.created_at DESC LIMIT 100
    """, (uid,))
    songs = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(songs)

@app.route('/api/song/<int:song_id>/like', methods=['POST'])
def like_song(song_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id FROM song_likes WHERE song_id=%s AND user_id=%s", (song_id, uid))
    existed = cursor.fetchone()
    cur2 = db.cursor()
    if existed:
        cur2.execute("DELETE FROM song_likes WHERE song_id=%s AND user_id=%s", (song_id, uid))
    else:
        cur2.execute("INSERT INTO song_likes (song_id, user_id) VALUES (%s, %s)", (song_id, uid))
    cursor.execute("SELECT COUNT(*) as cnt FROM song_likes WHERE song_id=%s", (song_id,))
    count = cursor.fetchone()['cnt']
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'liked': not bool(existed), 'count': count})

@app.route('/api/song/<int:song_id>', methods=['DELETE'])
def delete_song(song_id):
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM songs WHERE id=%s AND user_id=%s", (song_id, uid))
    song = cursor.fetchone()
    if not song:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404
    for url_field in ['audio_url', 'cover_url']:
        if song.get(url_field):
            fpath = os.path.join(app.root_path, song[url_field].lstrip('/'))
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
    cur2 = db.cursor()
    cur2.execute("DELETE FROM song_likes WHERE song_id=%s", (song_id,))
    cur2.execute("DELETE FROM songs WHERE id=%s", (song_id,))
    cur2.close()
    cursor.close()
    db.close()
    return jsonify({'success': True})

####################################################################
# FUNCTION: verify
####################################################################
@app.route('/verify', methods=['GET','POST'])
def verify():
    if request.method == 'POST':
        expiry_str = session.get('otp_expiry')
        if expiry_str and datetime.utcnow() > datetime.fromisoformat(expiry_str):
            session.pop('otp', None)
            session.pop('otp_expiry', None)
            session.pop('reg_data', None)
            return redirect('/login?tab=register&error=OTP+expired.+Please+register+again')

        if request.form['otp'] == session.get('otp'):
            db = get_db()
            cursor = db.cursor()

            data = session.pop('reg_data')
            session.pop('otp', None)
            session.pop('otp_expiry', None)

            cursor.execute("""
                INSERT INTO users (username, phone_number, email, password, verified)
                VALUES (%s,%s,%s,%s,1)
            """, (data['username'], data['phone'],
                  data['email'], data['password']))

            cursor.close()
            db.close()
            return redirect('/login')

        return redirect('/verify?error=Invalid+OTP.+Please+try+again')

    return render_template('verify.html')

####################################################################
# FUNCTION: chat
####################################################################
@app.route('/chat')
def chat():
    if not session.get('user_id'):
        return redirect('/login')
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, username, profile_pic FROM users WHERE id = %s", (session['user_id'],))
    user = cursor.fetchone()
    cursor.close()
    db.close()
    if not user:
        session.clear()
        return redirect('/login')
    return render_template('chat.html',
                           my_id=user['id'],
                           my_username=user['username'],
                           my_profile_pic=user.get('profile_pic', ''))

####################################################################
# FUNCTION: search_users
####################################################################
@app.route('/search_users')
def search_users():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401
    query = request.args.get('q', '')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # Exclude users who have blocked me or whom I have blocked
    cursor.execute("""
        SELECT id, username, profile_pic
        FROM users
        WHERE username LIKE %s
        AND id != %s
        AND id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = %s)
        AND id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = %s)
        LIMIT 20
    """, (query + "%", my_id, my_id, my_id))

    users = cursor.fetchall()
    cursor.close()
    db.close()

    return jsonify(users)

####################################################################
# FUNCTION: recent_chats
####################################################################
@app.route('/recent_chats')
def recent_chats():
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT u.id, u.username, u.profile_pic, m.message, m.type AS msg_type, m.timestamp
        FROM messages m
        JOIN users u
          ON u.id = IF(m.sender_id=%s, m.receiver_id, m.sender_id)
        LEFT JOIN message_deletions md ON md.message_id = m.id AND md.user_id = %s
        WHERE (m.sender_id=%s OR m.receiver_id=%s) AND md.id IS NULL
        ORDER BY m.timestamp DESC
    """, (my_id, my_id, my_id, my_id))

    rows = cursor.fetchall()
    seen = set()
    recent = []
    for row in rows:
        if row['id'] not in seen:
            seen.add(row['id'])
            if row.get('msg_type') == 'ai':
                row['message'] = 'Message by AI'
            recent.append(row)

    cursor.close()
    db.close()
    return jsonify(recent)

####################################################################
# FUNCTION: get_messages
####################################################################
@app.route('/messages/<int:other_id>')
def get_messages(other_id):
    my_id = session.get('user_id')
    if not my_id:
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT m.* FROM messages m
        LEFT JOIN message_deletions md ON md.message_id = m.id AND md.user_id = %s
        WHERE ((m.sender_id=%s AND m.receiver_id=%s)
            OR (m.sender_id=%s AND m.receiver_id=%s))
            AND md.id IS NULL
        ORDER BY m.timestamp
    """, (my_id, my_id, other_id, other_id, my_id))

    messages = cursor.fetchall()
    msg_ids = [m['id'] for m in messages]

    reactions_map = {}
    if msg_ids:
        ph = ','.join(['%s'] * len(msg_ids))
        cursor.execute(f"""
            SELECT message_id, emoji, GROUP_CONCAT(user_id) as user_ids, COUNT(*) as cnt
            FROM message_reactions
            WHERE message_id IN ({ph})
            GROUP BY message_id, emoji
        """, tuple(msg_ids))
        for r in cursor.fetchall():
            mid = r['message_id']
            if mid not in reactions_map:
                reactions_map[mid] = []
            reactions_map[mid].append({
                'emoji': r['emoji'],
                'count': r['cnt'],
                'user_ids': [int(x) for x in str(r['user_ids']).split(',')]
            })

    for msg in messages:
        msg['reactions'] = reactions_map.get(msg['id'], [])
        if msg.get('deleted_for_everyone'):
            msg['message'] = 'This message was deleted'
            msg['original_type'] = msg.get('type')
            msg['type'] = 'deleted'

    cursor.close()
    db.close()
    return jsonify(messages)

# -------- SOCKET HANDLERS --------

####################################################################
# FUNCTION: on_connect
####################################################################
@socketio.on('connect')
def on_connect():
    uid = session.get('user_id')
    if not uid:
        return

    join_room(str(uid))

    first_socket = False
    # add this socket id to the user's set
    if uid in online_users:
        online_users[uid].add(request.sid)
    else:
        online_users[uid] = {request.sid}
        first_socket = True

    # only broadcast "user became online" when the first connection appears
    if first_socket:
        emit('user_status', {'user_id': uid, 'online': True}, broadcast=True)
    # always send the fresh list to the connecting client so their UI updates
    emit('online_users_list', list(online_users.keys()), room=str(uid))

####################################################################
# FUNCTION: on_disconnect
####################################################################
@socketio.on('disconnect')
def on_disconnect():
    uid = session.get('user_id')
    if not uid or uid not in online_users:
        return

    sids = online_users[uid]
    sids.discard(request.sid)
    if not sids:
        # no remaining sockets for this user
        del online_users[uid]
        emit('user_status', {'user_id': uid, 'online': False}, broadcast=True)

####################################################################
# FUNCTION: handle_user_online
####################################################################
@socketio.on('user_online')
def handle_user_online(data):
    # this event is triggered manually by the client when they come back
    # from a locked screen or similar.  treat it almost exactly like
    # connect: add the socket id and broadcast if this is the first one.
    uid = session.get('user_id')
    if not uid:
        return

    first_socket = False
    if uid in online_users:
        online_users[uid].add(request.sid)
    else:
        online_users[uid] = {request.sid}
        first_socket = True

    if first_socket:
        emit('user_status', {'user_id': uid, 'online': True}, broadcast=True)
    emit('online_users_list', list(online_users.keys()), broadcast=True)

####################################################################
# FUNCTION: handle_join
####################################################################
@socketio.on('join')
def handle_join(data):
    room = data.get('room', '')
    uid = session.get('user_id')
    if not uid or not room:
        return
    # For chat rooms, ensure the requesting user is a participant
    if room.startswith('chat_'):
        parts = room.split('_')
        if len(parts) != 3:
            return
        try:
            if uid not in {int(parts[1]), int(parts[2])}:
                return
        except ValueError:
            return
    join_room(room)

####################################################################
# FUNCTION: handle_incoming_call
####################################################################
@socketio.on('incoming_call_notification')
def handle_incoming_call(data):
    """Relay a call notification to the intended recipient only.

    A few things to watch out for that were causing "everyone hears the
    call" reports:

    * if the `callee` value is missing or has the wrong type the ``room``
      argument becomes something unexpected; ``emit`` then falls back to a
      broadcast.  (``room=None`` == broadcast in some configurations).
    * users who open multiple tabs share the same session id and therefore
      join the same private room; all of those sockets will legitimately
      receive the event.  This can look like "all users" when you are
      testing with several tabs of the same account.

    To make the routing bulletâ€‘proof we now look up the target socket id
    from ``online_users`` and emit directly to that socket, and log what
    we're doing for easier debugging.
    """
    callee = data.get('callee')
    if callee is None:
        app.logger.debug('incoming_call_notification missing callee: %r', data)
        return

    try:
        callee_id = int(callee)
    except (TypeError, ValueError):
        app.logger.warning('invalid callee id received: %r', callee)
        return

    # Store call metadata so we can log it when the call ends
    room = data.get('room')
    if room:
        try:
            caller_id = int(data.get('caller'))
        except (TypeError, ValueError):
            caller_id = None
        ongoing_calls[room] = {
            'caller': caller_id,
            'callee': callee_id,
            'type': data.get('type', 'audio'),
            'start': datetime.utcnow()
        }
        app.logger.info(f'[INCOMING_CALL] stored: room={room}, caller={caller_id}, callee={callee_id}, type={data.get("type")}')

    sid_set = online_users.get(callee_id)
    if sid_set:
        app.logger.debug('relaying call from %s to %s (sids=%s)',
                         data.get('caller'), callee_id, sid_set)
        # either send via room (simpler) or to each sid individually
        # using the room means Socket.IO takes care of multiple sockets.
        emit('incoming_call_notification', data, room=str(callee_id))
    else:
        app.logger.debug('callee %s is not online, dropping notification', callee_id)

####################################################################
# FUNCTION: handle_join_call_room
# Only allow the two participants to join; verify via ongoing_calls
####################################################################
@socketio.on('join_call_room')
def handle_join_call_room(data):
    room = data.get('room')
    user_id = session.get('user_id')
    
    if not room or not user_id:
        app.logger.warning('join_call_room: missing room or user_id')
        return
    
    # Verify this user is part of the call
    call_rec = ongoing_calls.get(room)
    if call_rec:
        is_caller = call_rec.get('caller') == user_id
        is_callee = call_rec.get('callee') == user_id
        if is_caller or is_callee:
            join_room(room)
            app.logger.info(f'join_call_room: {user_id} joined {room}')
            emit('peer_ready', {'userId': user_id}, room=room, include_self=False)
        else:
            app.logger.warning(f'join_call_room: user {user_id} not in call {room}')
    else:
        app.logger.warning(f'join_call_room: no call record for {room}')

####################################################################
# FUNCTION: handle_mark_as_read
####################################################################
@socketio.on('mark_as_read')
def handle_mark_as_read(data):
    sender_id = data.get('sender_id')
    receiver_id = data.get('receiver_id')
    
    if not sender_id or not receiver_id:
        return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        UPDATE messages 
        SET is_seen = 1 
        WHERE sender_id = %s AND receiver_id = %s AND is_seen = 0
    """, (sender_id, receiver_id))
    db.commit()
    cursor.close()
    db.close()

    emit('messages_read', {'reader_id': receiver_id}, room=str(sender_id))

####################################################################
# FUNCTION: handle_delete_message
####################################################################
@socketio.on('delete_message')
def handle_delete_message(data):
    uid = session.get('user_id')
    if not uid:
        return
    msg_id = data.get('message_id')
    delete_for = data.get('delete_for', 'me')
    room = data.get('room')
    if not msg_id:
        return
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM messages WHERE id=%s", (msg_id,))
    msg = cursor.fetchone()
    if not msg or (msg['sender_id'] != uid and msg['receiver_id'] != uid):
        cursor.close()
        db.close()
        return
    if delete_for == 'everyone' and msg['sender_id'] == uid:
        cur2 = db.cursor()
        cur2.execute("UPDATE messages SET deleted_for_everyone = 1 WHERE id = %s", (msg_id,))
        db.commit()
        cur2.close()
        if room:
            emit('message_deleted', {'message_id': msg_id, 'delete_for': 'everyone'}, room=room)
    else:
        cur2 = db.cursor()
        cur2.execute("INSERT IGNORE INTO message_deletions (message_id, user_id) VALUES (%s, %s)", (msg_id, uid))
        db.commit()
        cur2.close()
        emit('message_deleted', {'message_id': msg_id, 'delete_for': 'me'}, room=str(uid))
    cursor.close()
    db.close()

####################################################################
# FUNCTION: handle_react_message
####################################################################
@socketio.on('react_message')
def handle_react_message(data):
    uid = session.get('user_id')
    if not uid:
        return
    msg_id = data.get('message_id')
    emoji = data.get('emoji', '')
    room = data.get('room')
    if not msg_id or not emoji or len(emoji) > 10:
        return
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute(
        "SELECT id FROM message_reactions WHERE message_id=%s AND user_id=%s AND emoji=%s",
        (msg_id, uid, emoji)
    )
    existed = cursor.fetchone()
    cur2 = db.cursor()
    if existed:
        cur2.execute(
            "DELETE FROM message_reactions WHERE message_id=%s AND user_id=%s AND emoji=%s",
            (msg_id, uid, emoji)
        )
    else:
        cur2.execute(
            "INSERT INTO message_reactions (message_id, user_id, emoji) VALUES (%s, %s, %s)",
            (msg_id, uid, emoji)
        )
        cursor.execute("SELECT sender_id FROM messages WHERE id=%s", (msg_id,))
        msg_row = cursor.fetchone()
        if msg_row and msg_row['sender_id'] != uid:
            cur2.execute(
                "INSERT INTO notifications (user_id, type, from_user_id, reference_id, content) VALUES (%s, 'reaction', %s, %s, %s)",
                (msg_row['sender_id'], uid, msg_id, emoji)
            )
    db.commit()
    cursor.execute(f"""
        SELECT emoji, GROUP_CONCAT(user_id) as user_ids, COUNT(*) as cnt
        FROM message_reactions WHERE message_id=%s GROUP BY emoji
    """, (msg_id,))
    reactions = []
    for r in cursor.fetchall():
        reactions.append({
            'emoji': r['emoji'],
            'count': r['cnt'],
            'user_ids': [int(x) for x in str(r['user_ids']).split(',')]
        })
    cursor.close()
    cur2.close()
    db.close()
    if room:
        emit('message_reacted', {
            'message_id': msg_id,
            'reactions': reactions,
            'user_id': uid,
            'emoji': emoji,
            'action': 'removed' if existed else 'added'
        }, room=room)

####################################################################
# HELPER: AI db + emit utilities
####################################################################
def _row_to_dict(cursor, row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    cols = [d[0] for d in (cursor.description or [])]
    if not cols:
        return {}
    return dict(zip(cols, row))


def _db_fetchone_dict(db, query, params=()):
    cur = db.cursor()
    cur.execute(query, params)
    row = _row_to_dict(cur, cur.fetchone())
    cur.close()
    return row


def _db_fetchall_dict(db, query, params=()):
    cur = db.cursor()
    cur.execute(query, params)
    rows = [_row_to_dict(cur, r) for r in (cur.fetchall() or [])]
    cur.close()
    return [r for r in rows if r is not None]


def _db_fetch_scalar(db, query, params=()):
    cur = db.cursor()
    cur.execute(query, params)
    row = cur.fetchone()
    cur.close()
    if not row or row[0] is None:
        return 0
    return int(row[0])


def _format_db_value(value):
    if value is None:
        return 'null'
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat(sep=' ', timespec='seconds')
        except TypeError:
            try:
                return value.isoformat()
            except Exception:
                pass
    return str(value)


def _parse_db_context_kv(db_context):
    """Parse simple key/value lines from DATABASE QUERY RESULT text."""
    parsed = {}
    if not db_context:
        return parsed

    for raw in db_context.splitlines():
        line = raw.strip()
        if not line or line == '[DATABASE QUERY RESULT]' or line.startswith('- '):
            continue
        if ': ' in line:
            key, value = line.split(': ', 1)
        elif line.endswith(':'):
            key, value = line[:-1], ''
        elif ':' in line:
            key, value = line.split(':', 1)
        else:
            continue
        parsed[key.strip()] = value.strip()
    return parsed


def _db_rows_are_none(db_context, section_name):
    """Return True when section like followers_rows contains only '- none'."""
    lines = [ln.strip() for ln in (db_context or '').splitlines()]
    marker = f"{section_name}_rows:"
    for i, line in enumerate(lines):
        if line != marker:
            continue
        j = i + 1
        while j < len(lines) and not lines[j]:
            j += 1
        if j < len(lines) and lines[j] == '- none':
            return True
    return False


def _build_db_no_data_reply(db_context):
    """Build deterministic 'no data' reply when DB result has no requested data."""
    if not db_context or '[DATABASE QUERY RESULT]' not in db_context:
        return None

    parsed = _parse_db_context_kv(db_context)
    scope = parsed.get('scope', 'user').lower()
    target_user_id = parsed.get('target_user_id', 'unknown')

    if parsed.get('user_found', '').lower() == 'false':
        return f"No, I could not find user id {target_user_id} in the database."

    requested_raw = parsed.get('requested_items', '')
    requested = [x.strip() for x in requested_raw.split(',') if x.strip()]
    if not requested:
        return None

    count_key_map = {
        'users': 'users_count',
        'follows': 'follows_count',
        'followers': 'followers_count',
        'following': 'following_count',
        'posts': 'posts_count',
        'reels': 'reels_count',
        'songs': 'songs_count',
        'statuses': 'statuses_count',
        'messages': 'messages_count',
    }
    labels = {
        'users': 'users',
        'follows': 'follow records',
        'followers': 'followers',
        'following': 'following users',
        'posts': 'posts',
        'reels': 'reels',
        'songs': 'songs',
        'statuses': 'statuses',
        'messages': 'messages',
        'username': 'username',
        'email': 'email',
        'phone_number': 'phone number',
        'bio': 'bio',
        'verified': 'verified status',
        'id': 'id',
        'created_at': 'created time',
        'updated_at': 'updated time',
    }

    empty_items = []
    for item in requested:
        if item in count_key_map:
            cnt_val = parsed.get(count_key_map[item])
            if cnt_val is not None:
                try:
                    if int(cnt_val) == 0:
                        empty_items.append(item)
                        continue
                except Exception:
                    pass
            if _db_rows_are_none(db_context, item):
                empty_items.append(item)
            continue

        val = parsed.get(item)
        if val is None or val.lower() in {'', 'null', 'none'}:
            empty_items.append(item)

    if not empty_items:
        return None

    # If the only requested item has no data, return a focused "no".
    if len(requested) == 1 and requested[0] in empty_items:
        label = labels.get(requested[0], requested[0].replace('_', ' '))
        if scope == 'global':
            return f"No, I could not find {label} in the database."
        return f"No, I could not find {label} in the database for user id {target_user_id}."

    # If all requested items are empty, return a global no-data reply.
    if len(empty_items) == len(requested):
        if scope == 'global':
            return "No, I could not find the requested data in the database."
        return f"No, I could not find the requested data in the database for user id {target_user_id}."

    return None


def _get_user_record_reply(question, requester_id):
    """Return structured DB context for user/global data questions, or None."""
    import re as _re

    q = (question or '').strip()
    if not q:
        return None
    q_lower = q.lower()

    def _has_any(*parts):
        return any(p in q_lower for p in parts)

    explicit_user_match = _re.search(r'\buser\s*(?:id\s*)?(\d+)\b', q_lower)
    explicit_user_id = int(explicit_user_match.group(1)) if explicit_user_match else None

    asks_all = _has_any(
        'all record', 'all records', 'full record', 'complete record',
        'complete data', 'all data', 'everything'
    )
    asks_count = _has_any('how many', 'count', 'number of')
    asks_list = _has_any('list', 'show', 'latest', 'recent', 'who', 'which', 'name', 'names')

    global_mode = (
        explicit_user_id is None
        and _has_any(
            'all users', 'every user', 'all records', 'all data', 'everything',
            'whole database', 'entire database', 'full database', 'database records', 'database data'
        )
        and not _re.search(r'\bmy\b', q_lower)
        and not _re.search(r'\bwho am i\b', q_lower)
    )

    target_user_id = None
    if not global_mode:
        if explicit_user_id is not None:
            target_user_id = explicit_user_id
        elif _re.search(r'\bwho am i\b', q_lower):
            target_user_id = int(requester_id)
        elif _re.search(r'\b(my|me)\b', q_lower):
            target_user_id = int(requester_id)

        if not target_user_id:
            # If user asks for account data but omits an id, default to their own account.
            if _has_any(
                'name', 'username', 'email', 'mail', 'phone', 'number', 'mobile',
                'bio', 'verified', 'follower', 'following', 'post', 'reel',
                'song', 'music', 'status', 'message', 'chat history', 'conversation',
                'record', 'records', 'profile', 'data', 'detail', 'details', 'info', 'information'
            ):
                target_user_id = int(requester_id)
            else:
                return None

    requested = set()
    if global_mode:
        if asks_all or _has_any('all users', 'every user', 'all records', 'all data', 'everything'):
            requested.update({'users', 'follows', 'posts', 'reels', 'songs', 'statuses', 'messages'})
        if _has_any('user', 'users', 'name', 'username', 'email', 'mail', 'phone', 'number', 'mobile', 'profile'):
            requested.add('users')
        if _has_any('follow', 'follower', 'following'):
            requested.add('follows')
        if _has_any('post'):
            requested.add('posts')
        if _has_any('reel'):
            requested.add('reels')
        if _has_any('song', 'music'):
            requested.add('songs')
        if _has_any('status'):
            requested.add('statuses')
        if _has_any('message', 'chat history', 'conversation', 'inbox'):
            requested.add('messages')
        if not requested:
            requested.add('users')
    else:
        if asks_all:
            requested.update({
                'id', 'username', 'email', 'phone_number', 'bio', 'verified',
                'created_at', 'updated_at', 'followers', 'following',
                'posts', 'reels', 'songs', 'statuses', 'messages'
            })

        if _has_any('profile', 'detail', 'details', 'record', 'records', 'data', 'info', 'information'):
            requested.update({'id', 'username', 'bio', 'verified', 'created_at', 'updated_at'})
        if _has_any('name', 'username'):
            requested.add('username')
        if _has_any('email', 'mail'):
            requested.add('email')
        if _has_any('phone', 'number', 'mobile'):
            requested.add('phone_number')
        if _has_any('bio'):
            requested.add('bio')
        if _has_any('verified'):
            requested.add('verified')
        if _has_any('follower'):
            requested.add('followers')
        if _has_any('following'):
            requested.add('following')
        if _has_any('post'):
            requested.add('posts')
        if _has_any('reel'):
            requested.add('reels')
        if _has_any('song', 'music'):
            requested.add('songs')
        if _has_any('status'):
            requested.add('statuses')
        if _has_any('message', 'chat history', 'conversation', 'inbox'):
            requested.add('messages')

        # If a user target is present but no explicit field is requested, default to name.
        if not requested:
            requested.add('username')

    db = get_db()
    try:
        def _safe_count(sql, params=()):
            try:
                return _db_fetch_scalar(db, sql, params)
            except Exception:
                return None

        def _safe_rows(sql, params=()):
            try:
                return _db_fetchall_dict(db, sql, params)
            except Exception:
                return []

        def _preview(value, max_len=100):
            txt = (value or '').replace('\n', ' ').strip()
            if len(txt) > max_len:
                return txt[:max_len - 3] + '...'
            return txt or 'null'

        if global_mode:
            lines = [
                '[DATABASE QUERY RESULT]',
                f'question: {q}',
                'scope: global',
                f"requested_items: {', '.join(sorted(requested))}",
                'database_found: true'
            ]
            global_limit = 100 if asks_all else 25

            if 'users' in requested:
                users_count = _safe_count("SELECT COUNT(*) FROM users")
                if users_count is not None:
                    lines.append(f"users_count: {users_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT id, username, email, phone_number, verified, created_at
                        FROM users
                        ORDER BY id ASC
                        LIMIT %s
                        """,
                        (global_limit,)
                    )
                    lines.append('users_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- id={r.get('id')} username={r.get('username')} email={_preview(r.get('email'), 80)} "
                                f"phone={_preview(r.get('phone_number'), 40)} verified={r.get('verified')} "
                                f"created_at={_format_db_value(r.get('created_at'))}"
                            )
                    else:
                        lines.append('- none')

            if 'follows' in requested:
                follows_count = _safe_count("SELECT COUNT(*) FROM follows")
                if follows_count is not None:
                    lines.append(f"follows_count: {follows_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT follower_id, following_id, created_at
                        FROM follows
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (global_limit,)
                    )
                    lines.append('follows_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- follower_id={r.get('follower_id')} following_id={r.get('following_id')} "
                                f"created_at={_format_db_value(r.get('created_at'))}"
                            )
                    else:
                        lines.append('- none')

            if 'posts' in requested:
                posts_count = _safe_count("SELECT COUNT(*) FROM posts")
                if posts_count is not None:
                    lines.append(f"posts_count: {posts_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT id, user_id, media_type, caption, created_at
                        FROM posts
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (global_limit,)
                    )
                    lines.append('posts_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- id={r.get('id')} user_id={r.get('user_id')} type={r.get('media_type')} "
                                f"time={_format_db_value(r.get('created_at'))} caption={_preview(r.get('caption'))}"
                            )
                    else:
                        lines.append('- none')

            if 'reels' in requested:
                reels_count = _safe_count("SELECT COUNT(*) FROM reels")
                if reels_count is not None:
                    lines.append(f"reels_count: {reels_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT id, user_id, caption, created_at
                        FROM reels
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (global_limit,)
                    )
                    lines.append('reels_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- id={r.get('id')} user_id={r.get('user_id')} "
                                f"time={_format_db_value(r.get('created_at'))} caption={_preview(r.get('caption'))}"
                            )
                    else:
                        lines.append('- none')

            if 'songs' in requested:
                songs_count = _safe_count("SELECT COUNT(*) FROM songs")
                if songs_count is not None:
                    lines.append(f"songs_count: {songs_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT id, user_id, title, artist, created_at
                        FROM songs
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (global_limit,)
                    )
                    lines.append('songs_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- id={r.get('id')} user_id={r.get('user_id')} "
                                f"title={_preview(r.get('title'), 60)} artist={_preview(r.get('artist'), 60)} "
                                f"time={_format_db_value(r.get('created_at'))}"
                            )
                    else:
                        lines.append('- none')

            if 'statuses' in requested:
                statuses_count = _safe_count("SELECT COUNT(*) FROM statuses")
                if statuses_count is not None:
                    lines.append(f"statuses_count: {statuses_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT id, user_id, media_type, caption, created_at, expires_at
                        FROM statuses
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (global_limit,)
                    )
                    lines.append('statuses_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- id={r.get('id')} user_id={r.get('user_id')} type={r.get('media_type')} "
                                f"time={_format_db_value(r.get('created_at'))} "
                                f"expires={_format_db_value(r.get('expires_at'))} caption={_preview(r.get('caption'))}"
                            )
                    else:
                        lines.append('- none')

            if 'messages' in requested:
                messages_count = _safe_count("SELECT COUNT(*) FROM messages")
                if messages_count is not None:
                    lines.append(f"messages_count: {messages_count}")
                if asks_all or asks_list or not asks_count:
                    rows = _safe_rows(
                        """
                        SELECT id, sender_id, receiver_id, type, message, timestamp
                        FROM messages
                        ORDER BY timestamp DESC
                        LIMIT %s
                        """,
                        (40 if asks_all else 20,)
                    )
                    lines.append('messages_rows:')
                    if rows:
                        for r in rows:
                            lines.append(
                                f"- id={r.get('id')} sender={r.get('sender_id')} receiver={r.get('receiver_id')} "
                                f"type={r.get('type')} time={_format_db_value(r.get('timestamp'))} "
                                f"message={_preview(r.get('message'), 120)}"
                            )
                    else:
                        lines.append('- none')

            return '\n'.join(lines)

        user = _db_fetchone_dict(
            db,
            """
            SELECT id, username, phone_number, email, profile_pic, bio, verified, created_at, updated_at
            FROM users
            WHERE id = %s
            """,
            (target_user_id,)
        )
        if not user:
            return (
                "[DATABASE QUERY RESULT]\n"
                f"question: {q}\n"
                "scope: user\n"
                f"target_user_id: {target_user_id}\n"
                "user_found: false\n"
                "error: no user row for that id"
            )

        lines = [
            '[DATABASE QUERY RESULT]',
            f'question: {q}',
            'scope: user',
            f'target_user_id: {target_user_id}',
            f"requested_items: {', '.join(sorted(requested))}",
            'user_found: true'
        ]

        field_map = {
            'id': 'id',
            'username': 'username',
            'email': 'email',
            'phone_number': 'phone_number',
            'bio': 'bio',
            'verified': 'verified',
            'created_at': 'created_at',
            'updated_at': 'updated_at',
        }
        for key, col in field_map.items():
            if key in requested:
                lines.append(f"{key}: {_format_db_value(user.get(col))}")

        followers_needs_list = 'followers' in requested and (asks_all or asks_list or not asks_count)
        following_needs_list = 'following' in requested and (asks_all or asks_list or not asks_count)
        posts_needs_list = 'posts' in requested and (asks_all or asks_list or not asks_count)
        reels_needs_list = 'reels' in requested and (asks_all or asks_list or not asks_count)
        songs_needs_list = 'songs' in requested and (asks_all or asks_list or not asks_count)
        statuses_needs_list = 'statuses' in requested and (asks_all or asks_list or not asks_count)
        messages_needs_list = 'messages' in requested and (asks_all or asks_list or not asks_count)

        if 'followers' in requested:
            followers_count = _safe_count("SELECT COUNT(*) FROM follows WHERE following_id = %s", (target_user_id,))
            if followers_count is not None:
                lines.append(f"followers_count: {followers_count}")
            if followers_needs_list:
                rows = _safe_rows(
                    """
                    SELECT u.id, u.username
                    FROM follows f
                    JOIN users u ON u.id = f.follower_id
                    WHERE f.following_id = %s
                    ORDER BY f.created_at DESC
                    LIMIT %s
                    """,
                    (target_user_id, 50 if asks_all else 20)
                )
                lines.append('followers_rows:')
                if rows:
                    for r in rows:
                        lines.append(f"- id={r.get('id')} username={r.get('username')}")
                else:
                    lines.append('- none')

        if 'following' in requested:
            following_count = _safe_count("SELECT COUNT(*) FROM follows WHERE follower_id = %s", (target_user_id,))
            if following_count is not None:
                lines.append(f"following_count: {following_count}")
            if following_needs_list:
                rows = _safe_rows(
                    """
                    SELECT u.id, u.username
                    FROM follows f
                    JOIN users u ON u.id = f.following_id
                    WHERE f.follower_id = %s
                    ORDER BY f.created_at DESC
                    LIMIT %s
                    """,
                    (target_user_id, 50 if asks_all else 20)
                )
                lines.append('following_rows:')
                if rows:
                    for r in rows:
                        lines.append(f"- id={r.get('id')} username={r.get('username')}")
                else:
                    lines.append('- none')

        if 'posts' in requested:
            posts_count = _safe_count("SELECT COUNT(*) FROM posts WHERE user_id = %s", (target_user_id,))
            if posts_count is not None:
                lines.append(f"posts_count: {posts_count}")
            if posts_needs_list:
                rows = _safe_rows(
                    """
                    SELECT id, media_type, caption, created_at
                    FROM posts
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (target_user_id, 25 if asks_all else 10)
                )
                lines.append('posts_rows:')
                if rows:
                    for r in rows:
                        lines.append(
                            f"- id={r.get('id')} type={r.get('media_type')} "
                            f"time={_format_db_value(r.get('created_at'))} caption={_preview(r.get('caption'))}"
                        )
                else:
                    lines.append('- none')

        if 'reels' in requested:
            reels_count = _safe_count("SELECT COUNT(*) FROM reels WHERE user_id = %s", (target_user_id,))
            if reels_count is not None:
                lines.append(f"reels_count: {reels_count}")
            if reels_needs_list:
                rows = _safe_rows(
                    """
                    SELECT id, caption, created_at
                    FROM reels
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (target_user_id, 25 if asks_all else 10)
                )
                lines.append('reels_rows:')
                if rows:
                    for r in rows:
                        lines.append(
                            f"- id={r.get('id')} time={_format_db_value(r.get('created_at'))} "
                            f"caption={_preview(r.get('caption'))}"
                        )
                else:
                    lines.append('- none')

        if 'songs' in requested:
            songs_count = _safe_count("SELECT COUNT(*) FROM songs WHERE user_id = %s", (target_user_id,))
            if songs_count is not None:
                lines.append(f"songs_count: {songs_count}")
            if songs_needs_list:
                rows = _safe_rows(
                    """
                    SELECT id, title, artist, created_at
                    FROM songs
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (target_user_id, 25 if asks_all else 10)
                )
                lines.append('songs_rows:')
                if rows:
                    for r in rows:
                        lines.append(
                            f"- id={r.get('id')} title={_preview(r.get('title'), 60)} "
                            f"artist={_preview(r.get('artist'), 60)} time={_format_db_value(r.get('created_at'))}"
                        )
                else:
                    lines.append('- none')

        if 'statuses' in requested:
            statuses_count = _safe_count("SELECT COUNT(*) FROM statuses WHERE user_id = %s", (target_user_id,))
            if statuses_count is not None:
                lines.append(f"statuses_count: {statuses_count}")
            if statuses_needs_list:
                rows = _safe_rows(
                    """
                    SELECT id, media_type, caption, created_at, expires_at
                    FROM statuses
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (target_user_id, 25 if asks_all else 10)
                )
                lines.append('statuses_rows:')
                if rows:
                    for r in rows:
                        lines.append(
                            f"- id={r.get('id')} type={r.get('media_type')} "
                            f"time={_format_db_value(r.get('created_at'))} expires={_format_db_value(r.get('expires_at'))} "
                            f"caption={_preview(r.get('caption'))}"
                        )
                else:
                    lines.append('- none')

        if 'messages' in requested:
            total_messages = _safe_count(
                "SELECT COUNT(*) FROM messages WHERE sender_id = %s OR receiver_id = %s",
                (target_user_id, target_user_id)
            )
            if total_messages is not None:
                lines.append(f"messages_count: {total_messages}")
            if messages_needs_list:
                rows = _safe_rows(
                    """
                    SELECT id, sender_id, receiver_id, type, message, timestamp
                    FROM messages
                    WHERE sender_id = %s OR receiver_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                    """,
                    (target_user_id, target_user_id, 40 if asks_all else 15)
                )
                lines.append('messages_rows:')
                if rows:
                    for r in rows:
                        lines.append(
                            f"- id={r.get('id')} sender={r.get('sender_id')} receiver={r.get('receiver_id')} "
                            f"type={r.get('type')} time={_format_db_value(r.get('timestamp'))} "
                            f"message={_preview(r.get('message'), 120)}"
                        )
                else:
                    lines.append('- none')

        return '\n'.join(lines)
    except Exception as e:
        print(f'DB AI lookup error: {e}')
        return 'I tried to read the database record, but an internal error occurred while querying.'
    finally:
        try:
            db.close()
        except Exception:
            pass


AI_DB_ALLOWED_TABLES = {
    'users',
    'messages',
    'message_reactions',
    'message_deletions',
    'notifications',
    'statuses',
    'status_views',
    'reels',
    'reel_likes',
    'reel_comments',
    'songs',
    'song_likes',
    'follows',
    'blocks',
    'posts',
}


AI_DB_SCHEMA_OVERVIEW = """
users(id, username, phone_number, email, password, profile_pic, bio, verified, created_at, updated_at)
messages(id, sender_id, receiver_id, message, type, is_seen, deleted_for_everyone, timestamp)
message_reactions(id, message_id, user_id, emoji, created_at)
message_deletions(id, message_id, user_id, created_at)
notifications(id, user_id, type, from_user_id, reference_id, content, is_read, created_at)
statuses(id, user_id, media_url, media_type, caption, created_at, expires_at)
status_views(id, status_id, user_id, viewed_at)
reels(id, user_id, video_url, caption, created_at)
reel_likes(id, reel_id, user_id, created_at)
reel_comments(id, reel_id, user_id, comment, created_at)
songs(id, user_id, audio_url, title, artist, cover_url, created_at)
song_likes(id, song_id, user_id, created_at)
follows(id, follower_id, following_id, created_at)
blocks(id, blocker_id, blocked_id, created_at)
posts(id, user_id, media_url, media_type, caption, created_at)
""".strip()


def _ai_db_max_rows():
    try:
        return max(1, min(int(os.getenv('AI_DB_MAX_ROWS', '50')), 200))
    except Exception:
        return 50


def _looks_like_db_question(question):
    import re as _re

    q = (question or '').lower().strip()
    if not q:
        return False

    if _re.search(r'\buser\s*(?:id\s*)?\d+\b', q):
        return True

    db_keywords = (
        'database', 'db', 'table', 'record', 'records', 'row', 'rows',
        'user', 'users', 'profile', 'email', 'phone', 'bio', 'verified',
        'message', 'messages', 'post', 'posts', 'reel', 'reels', 'song', 'songs',
        'status', 'statuses', 'follow', 'followers', 'following', 'notification',
        'notifications', 'block', 'blocked', 'count', 'how many', 'list', 'show'
    )
    return any(k in q for k in db_keywords)


def _extract_json_object(text):
    import json as _json
    import re as _re

    payload = (text or '').strip()
    if not payload:
        return None

    fence = _re.search(r'```(?:json)?\s*(\{.*\})\s*```', payload, _re.IGNORECASE | _re.DOTALL)
    if fence:
        payload = fence.group(1).strip()

    try:
        obj = _json.loads(payload)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    start = payload.find('{')
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(payload)):
        ch = payload[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                snippet = payload[start:i + 1]
                try:
                    obj = _json.loads(snippet)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    return None
    return None


def _normalize_sql_params(params):
    if params is None:
        return []
    if isinstance(params, tuple):
        params = list(params)
    if not isinstance(params, list):
        return None

    normalized = []
    for value in params:
        if value is None:
            normalized.append(None)
        elif isinstance(value, bool):
            normalized.append(int(value))
        elif isinstance(value, (int, float, str)):
            normalized.append(value)
        else:
            return None
    return normalized


def _extract_sql_table_names(sql):
    import re as _re

    names = set()
    for m in _re.finditer(r'\b(?:from|join)\s+([`\"]?[a-zA-Z_][\w$]*(?:\.[`\"]?[a-zA-Z_][\w$]*)?)', sql, _re.IGNORECASE):
        token = m.group(1).replace('`', '').replace('"', '')
        table_name = token.split('.')[-1].lower().strip()
        if table_name:
            names.add(table_name)
    return names


def _validate_readonly_sql(sql):
    import re as _re

    if not isinstance(sql, str):
        return None, 'missing sql text'

    clean_sql = sql.strip().rstrip(';').strip()
    if not clean_sql:
        return None, 'empty sql text'

    if ';' in clean_sql:
        return None, 'multiple statements are not allowed'
    if '--' in clean_sql or '/*' in clean_sql or '*/' in clean_sql or '#' in clean_sql:
        return None, 'sql comments are not allowed'
    if not _re.match(r'^\s*select\b', clean_sql, _re.IGNORECASE):
        return None, 'only SELECT queries are allowed'

    forbidden = r'\b(insert|update|delete|drop|alter|create|truncate|replace|grant|revoke|call|do|set|use|show|describe|explain|copy|merge)\b'
    if _re.search(forbidden, clean_sql, _re.IGNORECASE):
        return None, 'write/admin sql keywords are blocked'

    if _re.search(r'\b(information_schema|pg_catalog|mysql|sys)\b', clean_sql, _re.IGNORECASE):
        return None, 'system schemas are blocked'

    if _re.search(r'\b(for\s+update|into\s+outfile|load_file|dumpfile|benchmark|sleep)\b', clean_sql, _re.IGNORECASE):
        return None, 'unsafe sql pattern detected'

    table_names = _extract_sql_table_names(clean_sql)
    unknown = sorted(t for t in table_names if t not in AI_DB_ALLOWED_TABLES)
    if unknown:
        return None, f"unknown table(s): {', '.join(unknown)}"

    return clean_sql, None


def _call_openrouter(messages, max_tokens=600, temperature=None):
    import requests as _req

    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        return None

    payload = {
        'model': 'google/gemma-3-4b-it:free',
        'messages': messages,
        'max_tokens': max_tokens,
    }
    if temperature is not None:
        payload['temperature'] = temperature

    try:
        resp = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        choice = resp.json()['choices'][0]['message']
        return (choice.get('content') or choice.get('reasoning') or '').strip() or None
    except Exception as e:
        print(f'OpenRouter error: {e}')
        return None


def _plan_db_query_with_ai(question, requester_id):
    prompt = (
        "You are a strict SQL planner for a chat app database.\\n"
        "Return only one JSON object with keys: should_query, sql, params, reason.\\n"
        "Rules:\\n"
        "- should_query must be true only if the user asked for data that exists in this schema.\\n"
        "- SQL must be one read-only SELECT statement only.\\n"
        "- Use only listed tables and columns.\\n"
        "- Use %s placeholders, and pass values through params array.\\n"
        "- If question refers to 'my/me', use requester_id.\\n"
        "- Add LIMIT 50 or less.\\n"
        "- Do not add markdown, comments, or extra text.\\n\\n"
        f"requester_id: {int(requester_id)}\\n\\n"
        "schema:\\n"
        f"{AI_DB_SCHEMA_OVERVIEW}\\n\\n"
        f"question: {question}"
    )

    raw = _call_openrouter([{'role': 'user', 'content': prompt}], max_tokens=420, temperature=0)
    if not raw:
        return None

    plan = _extract_json_object(raw)
    if not isinstance(plan, dict):
        return None

    should_query = plan.get('should_query', False)
    if isinstance(should_query, str):
        should_query = should_query.strip().lower() in {'true', '1', 'yes'}
    else:
        should_query = bool(should_query)

    sql = (plan.get('sql') or '').strip()
    params = plan.get('params')
    reason = (plan.get('reason') or '').strip()

    return {
        'should_query': should_query,
        'sql': sql,
        'params': params,
        'reason': reason,
    }


def _serialize_rows_for_prompt(rows):
    serializable = []
    for row in rows:
        out = {}
        for key, value in row.items():
            if value is None:
                out[key] = None
            elif isinstance(value, (str, int, float, bool)):
                out[key] = value
            else:
                out[key] = _format_db_value(value)
        serializable.append(out)
    return serializable


def _query_rows_with_limit(sql, params, max_rows):
    import re as _re

    db = get_db()
    cur = None
    try:
        final_sql = sql.strip().rstrip(';')
        if not _re.search(r'\blimit\s+\d+\b', final_sql, _re.IGNORECASE):
            final_sql = f"{final_sql} LIMIT {max_rows + 1}"

        cur = db.cursor()
        cur.execute(final_sql, tuple(params))
        fetched = cur.fetchmany(max_rows + 1)
        rows = [_row_to_dict(cur, r) for r in (fetched or [])]
        rows = [r for r in rows if r is not None]

        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        return rows, truncated, None
    except Exception as e:
        return None, False, str(e)
    finally:
        try:
            if cur is not None:
                cur.close()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass


def _format_query_result_text(rows, truncated):
    if not rows:
        return 'No data found.'

    preview_rows = rows[:10]
    lines = [f"I found {len(rows)} row(s)."]
    for idx, row in enumerate(preview_rows, start=1):
        parts = [f"{k}: {_format_db_value(v)}" for k, v in row.items()]
        lines.append(f"{idx}. " + ', '.join(parts))

    if truncated:
        lines.append(f"Showing first {len(preview_rows)} row(s); more rows exist.")
    elif len(rows) > len(preview_rows):
        lines.append(f"... and {len(rows) - len(preview_rows)} more row(s).")

    return '\n'.join(lines)


def _answer_database_question(question, requester_id):
    if not _looks_like_db_question(question):
        return None

    plan = _plan_db_query_with_ai(question, requester_id)
    if plan and plan.get('should_query'):
        raw_sql = (plan.get('sql') or '').replace('?', '%s')
        clean_sql, validation_error = _validate_readonly_sql(raw_sql)
        if clean_sql:
            params = _normalize_sql_params(plan.get('params'))
            if params is None:
                return 'I could not run that request because the generated SQL parameters were invalid.'

            placeholder_count = clean_sql.count('%s')
            if placeholder_count != len(params):
                return 'I could not run that request safely because SQL placeholders and parameters did not match.'

            rows, truncated, query_error = _query_rows_with_limit(clean_sql, params, _ai_db_max_rows())
            if query_error:
                print(f'DB SQL execution error: {query_error}')
                return 'I could not run that database query due to an internal SQL error.'

            if not rows:
                return 'No data found.'

            # Let the model convert raw rows into a natural reply while preserving facts.
            serializable_rows = _serialize_rows_for_prompt(rows)
            import json as _json
            format_prompt = (
                "Answer the user question using only the SQL result rows below.\\n"
                "Rules:\\n"
                "- Do not invent facts.\\n"
                "- If a value is null, explicitly say null.\\n"
                "- Keep the reply concise and clear.\\n"
                "- If multiple rows exist, summarize and include key identifiers.\\n\\n"
                f"Question: {question}\\n"
                f"Rows: {_json.dumps(serializable_rows, ensure_ascii=True)}"
            )
            model_reply = _call_openrouter([{'role': 'user', 'content': format_prompt}], max_tokens=420, temperature=0.2)
            if model_reply:
                if truncated:
                    return model_reply + "\\n\\nNote: showing a limited result set."
                return model_reply

            return _format_query_result_text(rows, truncated)

        print(f'DB SQL safety rejection: {validation_error}')
        return f"I could not run that request safely ({validation_error})."

    # Legacy deterministic fallback for environments without OpenRouter or invalid planner output.
    db_context = _get_user_record_reply(question, requester_id)
    if db_context:
        if '[DATABASE QUERY RESULT]' not in db_context:
            return db_context

        no_data_reply = _build_db_no_data_reply(db_context)
        if no_data_reply:
            return no_data_reply

        from prompts import build_messages
        db_rules = (
            "[DATABASE ANSWERING RULES]\\n"
            "- Use only facts from DATABASE QUERY RESULT.\\n"
            "- Answer only what the user asked.\\n"
            "- If data is missing or null, explicitly say No data found and do not guess.\\n"
            "- If user_found is false, clearly say that no user was found."
        )
        ai_reply = _call_openrouter(build_messages(question, None, db_context + "\\n\\n" + db_rules), max_tokens=450)
        if ai_reply:
            return ai_reply
        return db_context

    return None


def _answer_general_question(question):
    from prompts import build_messages, get_weather_context, get_search_context

    weather_ctx = get_weather_context(question)
    search_ctx = get_search_context(question)
    return _call_openrouter(build_messages(question, weather_ctx, search_ctx), max_tokens=600)


def _generate_ai_reply_text(question, requester_id):
    from prompts import get_puff_local_reply

    q = (question or '').strip()
    if not q:
        return 'Please send a question.'

    local_reply = get_puff_local_reply(q)
    if local_reply:
        return local_reply

    db_reply = _answer_database_question(q, requester_id)
    if db_reply:
        return db_reply

    ai_reply = _answer_general_question(q)
    if ai_reply:
        return ai_reply

    return 'My apologies, Sir - I seem to be momentarily indisposed. Do try again shortly.'


def _save_and_emit_ai_reply(sender, receiver, room, reply_text):
    """Persist AI reply as message type='ai' and emit to chat room."""
    ai_msg_id = None
    try:
        db2 = get_db()
        cur2 = db2.cursor()
        cur2.execute(
            "INSERT INTO messages (sender_id, receiver_id, message, type) VALUES (%s, %s, %s, 'ai')",
            (receiver, sender, reply_text)
        )
        ai_msg_id = cur2.lastrowid
        cur2.close()
        db2.close()
    except Exception as e:
        print(f'AI db save error: {e}')

    payload = {
        'sender': receiver,
        'sender_id': receiver,
        'receiver': sender,
        'receiver_id': sender,
        'message': reply_text,
        'type': 'ai',
        'timestamp': None,
        'is_seen': 0
    }
    if ai_msg_id is not None:
        payload['id'] = ai_msg_id

    socketio.emit('receive_message', payload, room=room)
    socketio.emit('update_recents', room=str(sender))
    socketio.emit('update_recents', room=str(receiver))


####################################################################
# HELPER: _do_ai - runs in a background task to avoid blocking gevent
####################################################################
def _do_ai(question, sender, receiver, room):
    """Generate AI reply text and emit it as a persisted chat message."""
    ai_reply = _generate_ai_reply_text(question, sender)
    _save_and_emit_ai_reply(sender, receiver, room, ai_reply)

####################################################################
# FUNCTION: handle_message
####################################################################
@socketio.on('send_message')
def handle_message(data):
    sender = session.get('user_id')
    if not sender:
        return
    receiver = int(data['receiver'])
    data['sender'] = sender  # enforce server-side sender identity
    msg_type = data.get('type', 'text')
    content = data['message']

    if msg_type in ['voice', 'image', 'video']:
        try:
            type_config = {
                'voice': {'folder': 'voice', 'ext': 'webm'},
                'image': {'folder': 'images', 'ext': 'png'},
                'video': {'folder': 'videos', 'ext': 'mp4'}
            }
            
            config = type_config.get(msg_type)
            subfolder = config['folder']
            extension = config['ext']

            upload_dir = os.path.join(app.root_path, 'static', 'uploads', subfolder)
            os.makedirs(upload_dir, exist_ok=True)

            header, encoded = content.split(",", 1)
            file_binary = base64.b64decode(encoded)
            
            filename = f"{msg_type}_{uuid.uuid4().hex}.{extension}"
            filepath = os.path.join(upload_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(file_binary)
            
            content = f"/static/uploads/{subfolder}/{filename}"
            data['message'] = content 
            
        except Exception as e:
            print(f"Error processing {msg_type}: {e}")
            return

    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO messages (sender_id, receiver_id, message, type)
        VALUES (%s, %s, %s, %s)
    """, (sender, receiver, content, msg_type))
    msg_id = cursor.lastrowid
    db.commit()
    cursor.close()
    db.close()

    data['id'] = msg_id
    room = get_room_name(sender, receiver)
    emit('receive_message', data, room=room)
    emit('update_recents', room=str(sender))
    emit('update_recents', room=str(receiver))

    # ---- AI: if message starts with @, run reply in background task ----
    import re as _re
    text_content = content if isinstance(content, str) else ''
    if msg_type == 'text' and _re.match(r'^\s*@', text_content):
        # Support both "@ai question" and "@question" formats.
        question = _re.sub(r'^\s*@ai(?:\s+|$)', '', text_content, flags=_re.IGNORECASE)
        question = _re.sub(r'^\s*@\s*', '', question).strip()
        if question:
            socketio.start_background_task(_do_ai, question, sender, receiver, room)

# ================= WEBRTC CALLING SIGNALING =================
####################################################################
# FUNCTION: handle_call_offer
# Receives 'call_offer' from a peer and relays it to the room (other participants)
####################################################################
@socketio.on('call_offer')
def handle_call_offer(data):
    room = data.get('room')
    if room:
        emit('call_offer', data, room=room, include_self=False)

####################################################################
# FUNCTION: handle_call_answer
# Receives 'call_answer' from callee and relays it to the room (caller)
####################################################################
@socketio.on('call_answer')
def handle_call_answer(data):
    room = data.get('room')
    if room:
        emit('call_answer', data, room=room, include_self=False)

####################################################################
# FUNCTION: handle_ice_candidate
# Receives ICE candidates and forwards to the other peer(s) in the room
####################################################################
@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    room = data.get('room')
    if room:
        emit('ice_candidate', data, room=room, include_self=False)

####################################################################
# FUNCTION: handle_call_ended
# Notifies participants in the room that the call ended and cleans up
####################################################################
@socketio.on('call_ended')
def handle_call_ended(data):
    room = data.get('room')
    app.logger.info(f'[CALL_ENDED] room={room}, ongoing_calls_keys={list(ongoing_calls.keys())}')
    
    if room:
        # log call in database if we previously recorded a start time
        rec = ongoing_calls.pop(room, None)
        app.logger.info(f'[CALL_ENDED] rec={rec}')
        
        if rec and rec.get('caller') and rec.get('callee'):
            end = datetime.utcnow()
            duration = int((end - rec['start']).total_seconds())
            
            # Determine call message based on connection status
            was_connected = data.get('was_connected', False)
            if was_connected:
                # Call was accepted and completed - include duration
                # Format duration as "1m 30s" or "45s"
                if duration >= 60:
                    mins = duration // 60
                    secs = duration % 60
                    duration_str = f"{mins}m {secs}s" if secs > 0 else f"{mins}m"
                else:
                    duration_str = f"{duration}s"
                msg_text = f"{rec['type'].capitalize()} Call - {duration_str}"
            else:
                # Call was not accepted
                msg_text = "Call not accepted"
            
            app.logger.info(f'[CALL_ENDED] inserting: caller={rec["caller"]}, callee={rec["callee"]}, duration={duration}, was_connected={was_connected}, msg={msg_text}')
            
            try:
                db = get_db()
                cursor = db.cursor()
                cursor.execute("""
                    INSERT INTO messages (sender_id, receiver_id, message, type)
                    VALUES (%s, %s, %s, %s)
                """, (rec['caller'], rec['callee'], msg_text, 'call'))
                cursor.close()
                db.close()
                app.logger.info(f'[CALL_ENDED] database insert successful for call: {msg_text}')
            except Exception as e:
                app.logger.error(f'[CALL_ENDED] database error: {e}', exc_info=True)
                return
            
            # notify the participants in their chat rooms so they see the call record
            chat_room = get_room_name(rec['caller'], rec['callee'])
            msg_data = {
                'sender': rec['caller'],
                'receiver': rec['callee'],
                'message': msg_text,
                'type': 'call',
                'sender_id': rec['caller'],
                'receiver_id': rec['callee']
            }
            app.logger.info(f'[CALL_ENDED] emitting to chat_room={chat_room}')
            emit('receive_message', msg_data, room=chat_room)
            emit('update_recents', room=str(rec['caller']))
            emit('update_recents', room=str(rec['callee']))
        else:
            app.logger.warning(f'[CALL_ENDED] no valid call record found')

        emit('call_ended', data, room=room)
        try:
            leave_room(room)
        except Exception:
            pass

# -------- RUN --------
if __name__ == '__main__':
    configured_port = os.environ.get("PORT")
    port = int(configured_port or 5000)
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    host = os.getenv("HOST", "0.0.0.0")

    def _is_address_in_use(exc: OSError) -> bool:
        if getattr(exc, "winerror", None) == 10048:
            return True
        if getattr(exc, "errno", None) in (48, 98):
            return True
        msg = str(exc).lower()
        return "address already in use" in msg or "only one usage" in msg

    max_attempts = 20 if configured_port is None else 1
    for _ in range(max_attempts):
        print(f"* Open: http://127.0.0.1:{port}  (or http://localhost:{port})", flush=True)
        try:
            socketio.run(
                app,
                host=host,
                port=port,
                debug=debug_mode,
                allow_unsafe_werkzeug=debug_mode,
            )
            break
        except KeyboardInterrupt:
            print("\n* Server stopped.", flush=True)
            break
        except OSError as e:
            # Only auto-shift local default ports; explicit PORT values should fail loudly.
            if configured_port is None and _is_address_in_use(e):
                print(f"* Port {port} is busy. Retrying on {port + 1}...", flush=True)
                port += 1
                continue
            raise
