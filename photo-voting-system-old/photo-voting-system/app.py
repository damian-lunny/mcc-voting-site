from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response, send_file
import sqlite3
import hashlib
import os
import socket
import time
import io
from datetime import datetime
from functools import wraps
import qrcode
import subprocess

app = Flask(__name__)
DATABASE = 'voting.db'

app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))
DEFAULT_ADMIN_PASSWORD = 'ChangeThisAdminPass123!'
DEFAULT_ADMIN_HASH = hashlib.sha256(DEFAULT_ADMIN_PASSWORD.encode('utf-8')).hexdigest()
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', DEFAULT_ADMIN_HASH)

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            photo_number INTEGER NOT NULL,
            vote_position INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    try:
        cursor.execute('ALTER TABLE votes ADD COLUMN vote_position INTEGER DEFAULT 1')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS archived_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_label TEXT NOT NULL,
            category TEXT NOT NULL,
            photo_number INTEGER NOT NULL,
            vote_count INTEGER NOT NULL,
            archived_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    cursor.execute('SELECT COUNT(*) FROM settings')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('max_beginner', '15'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('max_intermediate', '12'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('max_advanced', '13'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('current_week_id', str(int(time.time()))))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('beginner_active', 'false'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('intermediate_active', 'false'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('advanced_active', 'false'))
        default_code_hash = hashlib.sha256('1234'.encode('utf-8')).hexdigest()
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('site_access_enabled', 'false'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('site_access_code_hash', default_code_hash))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('site_access_code_plain', '1234'))
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('admin_password_hash', ADMIN_PASSWORD_HASH))
    
    for category in ['beginner', 'intermediate', 'advanced']:
        cursor.execute('SELECT value FROM settings WHERE key = ?', (f'{category}_active',))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (f'{category}_active', 'false'))

    cursor.execute('SELECT value FROM settings WHERE key = ?', ('site_access_enabled',))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('site_access_enabled', 'false'))

    cursor.execute('SELECT value FROM settings WHERE key = ?', ('site_access_code_hash',))
    if not cursor.fetchone():
        default_code_hash = hashlib.sha256('1234'.encode('utf-8')).hexdigest()
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('site_access_code_hash', default_code_hash))
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('site_access_code_plain',))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('site_access_code_plain', '1234'))
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('admin_password_hash',))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('admin_password_hash', ADMIN_PASSWORD_HASH))
    
    conn.commit()
    conn.close()

def get_local_ip():
    """Get the local IP address of the server"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "localhost"

def admin_required(f):
    """Decorator for admin-only routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_setting_value(key, default=None):
    """Helper to fetch a setting value"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else default

def get_site_access_settings():
    """Fetch site access toggle and code hash"""
    enabled = get_setting_value('site_access_enabled', 'false') == 'true'
    code_hash = get_setting_value('site_access_code_hash', '')
    return enabled, code_hash

def get_site_access_plain_code():
    """Fetch plaintext site access code (admin use)"""
    return get_setting_value('site_access_code_plain', '')

def verify_admin_password(password: str) -> bool:
    """Verify provided admin password against stored hash"""
    if not password:
        return False
    stored_hash = get_setting_value('admin_password_hash', ADMIN_PASSWORD_HASH)
    provided_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return provided_hash == stored_hash

def update_admin_password(new_password: str):
    """Persist new admin password hash to settings"""
    new_hash = hashlib.sha256(new_password.encode('utf-8')).hexdigest()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (new_hash, 'admin_password_hash'))
    conn.commit()
    conn.close()

EXEMPT_ENDPOINTS = {
    'static',
    'admin_login',
    'admin_do_login',
    'admin_logout',
    'enter_access_code',
    'admin_qrcode',
    'admin_qrcode_image'
}

@app.before_request
def enforce_site_access():
    """Require site access code when enabled"""
    endpoint = request.endpoint
    if not endpoint:
        return
    if endpoint in EXEMPT_ENDPOINTS or endpoint.startswith('admin_'):
        return
    if request.path.startswith('/admin'):
        return

    enabled, code_hash = get_site_access_settings()
    if not enabled or not code_hash:
        return

    if session.get('admin_logged_in'):
        return

    if session.get('site_access_hash') == code_hash:
        return

    next_url = request.path
    if request.method == 'GET':
        return redirect(url_for('enter_access_code', next=next_url))
    else:
        return jsonify({'success': False, 'message': 'Access code required.'}), 403

@app.route('/')
def index():
    """Voting page (homepage)"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('max_beginner',))
    max_beginner = int(cursor.fetchone()[0])
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('max_intermediate',))
    max_intermediate = int(cursor.fetchone()[0])
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('max_advanced',))
    max_advanced = int(cursor.fetchone()[0])
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('beginner_active',))
    beginner_active = cursor.fetchone()[0] == 'true'
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('intermediate_active',))
    intermediate_active = cursor.fetchone()[0] == 'true'
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('advanced_active',))
    advanced_active = cursor.fetchone()[0] == 'true'
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('current_week_id',))
    week_id_row = cursor.fetchone()
    current_week_id = week_id_row[0] if week_id_row else str(int(time.time()))
    
    conn.close()
    
    is_admin = session.get('admin_logged_in', False)
    
    beginner_voted = not is_admin and request.cookies.get(f'voted_beginner_{current_week_id}') == 'true'
    intermediate_voted = not is_admin and request.cookies.get(f'voted_intermediate_{current_week_id}') == 'true'
    advanced_voted = not is_admin and request.cookies.get(f'voted_advanced_{current_week_id}') == 'true'
    
    status_snapshot = {
        'beginner_active': beginner_active,
        'intermediate_active': intermediate_active,
        'advanced_active': advanced_active,
        'beginner_voted': beginner_voted,
        'intermediate_voted': intermediate_voted,
        'advanced_voted': advanced_voted
    }

    return render_template('index.html', 
                         max_beginner=max_beginner,
                         max_intermediate=max_intermediate,
                         max_advanced=max_advanced,
                         beginner_active=beginner_active,
                         intermediate_active=intermediate_active,
                         advanced_active=advanced_active,
                         beginner_voted=beginner_voted,
                         intermediate_voted=intermediate_voted,
                         advanced_voted=advanced_voted,
                         current_week_id=current_week_id,
                         is_admin=is_admin,
                         status_snapshot=status_snapshot)

@app.route('/submit_votes', methods=['POST'])
def submit_votes():
    """Handle vote submission for a specific category"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('current_week_id',))
    week_id_row = cursor.fetchone()
    current_week_id = week_id_row[0] if week_id_row else str(int(time.time()))
    
    data = request.json
    category = data.get('category', '').lower()
    
    if category not in ['beginner', 'intermediate', 'advanced']:
        conn.close()
        return jsonify({'success': False, 'message': 'Invalid category'}), 400
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', (f'{category}_active',))
    is_active = cursor.fetchone()[0] == 'true'
    if not is_active:
        conn.close()
        return jsonify({'success': False, 'message': f'{category.capitalize()} category voting is not currently active.'}), 400
    
    is_admin = session.get('admin_logged_in', False)
    cookie_key = f'voted_{category}_{current_week_id}'
    
    if not is_admin and request.cookies.get(cookie_key) == 'true':
        conn.close()
        return jsonify({'success': False, 'message': f'You have already voted for the {category} category!'}), 400
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', (f'max_{category}',))
    max_photo = int(cursor.fetchone()[0])
    
    votes = []
    errors = []
    
    for vote_position in range(1, 6):  # vote_position: 1 to 5
        vote_key = f'vote_{vote_position}'
        photo_num = data.get(vote_key, '').strip()
        
        if photo_num:
            try:
                photo_num = int(photo_num)
                if photo_num < 1 or photo_num > max_photo:
                    errors.append(f'Vote {vote_position}: Photo #{photo_num} is invalid (max: {max_photo})')
                else:
                    votes.append((category, photo_num, vote_position))
            except ValueError:
                errors.append(f'Vote {vote_position}: Invalid number format')
    
    if errors:
        conn.close()
        return jsonify({'success': False, 'message': 'Validation errors', 'errors': errors}), 400
    
    for category, photo_num, vote_position in votes:
        cursor.execute('INSERT INTO votes (category, photo_number, vote_position) VALUES (?, ?, ?)', 
                      (category, photo_num, vote_position))
    
    conn.commit()
    conn.close()
    
    is_admin = session.get('admin_logged_in', False)
    cookie_key = f'voted_{category}_{current_week_id}'
    
    response = make_response(jsonify({'success': True, 'message': f'{category.capitalize()} votes submitted successfully!', 'category': category}))
    
    if not is_admin:
        response.set_cookie(cookie_key, 'true', max_age=86400*7, samesite='Lax', path='/')  # 7 days
    
    return response

@app.route('/tally')
def tally():
    """Live tally page"""
    return render_template('tally.html')

@app.route('/api/tally')
def api_tally():
    """API endpoint for tally data with weighted points"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT category, photo_number, vote_position
        FROM votes
        ORDER BY category, photo_number
    ''')
    
    results = {}
    """ point_values = {1: 3, 2: 2, 3: 1}  # Vote 1 = 3 points, Vote 2 = 2 points, Vote 3 = 1 point """
    point_values = {
        1: 5,  # First place
        2: 4,  # Second place
        3: 3,  # Third place
        4: 2,  # Fourth place
        5: 1   # Fifth place
    }


    for row in cursor.fetchall():
        category = row['category']
        photo_num = row['photo_number']
        vote_position = row['vote_position']
        points = point_values.get(vote_position, 0)
        
        if category not in results:
            results[category] = {}
        
        if photo_num not in results[category]:
            results[category][photo_num] = 0
        
        results[category][photo_num] += points
    
    formatted_results = {}
    for category, photos in results.items():
        formatted_results[category] = [
            {
                'photo_number': photo_num,
                'points': total_points
            }
            for photo_num, total_points in photos.items()
        ]
        formatted_results[category].sort(key=lambda x: x['points'], reverse=True)
    
    conn.close()
    return jsonify(formatted_results)

@app.route('/clear_votes', methods=['POST'])
def clear_votes():
    """Clear all votes (password protected)"""
    data = request.json
    password = data.get('password', '')
    
    if not verify_admin_password(password):
        return jsonify({'success': False, 'message': 'Incorrect password'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM votes')
    
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', ('false', 'beginner_active'))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', ('false', 'intermediate_active'))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', ('false', 'advanced_active'))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'All votes cleared and all categories closed for voting'})

@app.route('/archive')
def archive():
    """Results archive page"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT week_label, archived_date
        FROM archived_results
        ORDER BY archived_date DESC
    ''')
    
    weeks = [{'label': row['week_label'], 'date': row['archived_date']} 
             for row in cursor.fetchall()]
    
    conn.close()
    return render_template('archive.html', weeks=weeks)

@app.route('/api/archive/<week_label>')
def api_archive_week(week_label):
    """Get archived results for a specific week"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, category, photo_number, vote_count, photographer_name, photo_title
        FROM archived_results
        WHERE week_label = ?
        ORDER BY category, vote_count DESC, photo_number
    ''', (week_label,))
    
    results = {}
    for row in cursor.fetchall():
        id = row['id']
        category = row['category']
        photo_num = row['photo_number']
        photographer_name = row['photographer_name']
        photo_title = row['photo_title']
        points = row['vote_count']  # Stored as points in archive
    
        if category not in results:
            results[category] = []
        
        results[category].append({
            'id': row['id'],
            'photo_number': row['photo_number'],
            'points': row['vote_count'],
            'photographer_name': row['photographer_name'],
            'photo_title': row['photo_title']
        })
    
    conn.close()
    return jsonify(results)

@app.route('/admin')
def admin_login():
    """Admin login page"""
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_panel'))
    return render_template('admin_login.html')

@app.route('/admin/login', methods=['POST'])
def admin_do_login():
    """Handle admin login"""
    data = request.json
    password = data.get('password', '')
    
    if verify_admin_password(password):
        session['admin_logged_in'] = True
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': 'Incorrect password'}), 401

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/access', methods=['GET', 'POST'])
def enter_access_code():
    """Access code entry page"""
    enabled, code_hash = get_site_access_settings()
    error = None

    if not enabled:
        return redirect(url_for('index'))

    if request.method == 'POST':
        code = request.form.get('access_code', '').strip()
        if code:
            provided_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()
            if provided_hash == code_hash:
                session['site_access_hash'] = code_hash
                next_url = request.args.get('next') or url_for('index')
                return redirect(next_url)
        error = 'Incorrect access code. Please try again.'

    return render_template('access_code.html', error=error, enabled=enabled)

@app.route('/admin/qrcode')
@admin_required
def admin_qrcode():
    """Admin-only QR code page"""
    local_ip = get_local_ip()
    port = request.environ.get('SERVER_PORT', '8000')
    server_url = f"http://{local_ip}:{port}"
    
    return render_template('admin_qrcode.html', server_url=server_url)

@app.route('/admin/qrcode/image')
@admin_required
def admin_qrcode_image():
    """Generate QR code image"""
    local_ip = get_local_ip()
    port = request.environ.get('SERVER_PORT', '8000')
    server_url = f"http://{local_ip}:{port}"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(server_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png')

@app.route('/admin/panel')
@admin_required
def admin_panel():
    """Admin panel"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('max_beginner',))
    max_beginner = int(cursor.fetchone()[0])
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('max_intermediate',))
    max_intermediate = int(cursor.fetchone()[0])
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('max_advanced',))
    max_advanced = int(cursor.fetchone()[0])
    
    cursor.execute('SELECT COUNT(*) FROM votes')
    vote_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('beginner_active',))
    beginner_active = cursor.fetchone()[0] == 'true'
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('intermediate_active',))
    intermediate_active = cursor.fetchone()[0] == 'true'
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('advanced_active',))
    advanced_active = cursor.fetchone()[0] == 'true'
    
    cursor.execute('SELECT COUNT(*) FROM votes WHERE category = ?', ('beginner',))
    beginner_votes = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM votes WHERE category = ?', ('intermediate',))
    intermediate_votes = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM votes WHERE category = ?', ('advanced',))
    advanced_votes = cursor.fetchone()[0]
    
    cursor.execute('SELECT value FROM settings WHERE key = ?', ('site_access_enabled',))
    site_access_enabled = cursor.fetchone()[0] == 'true'

    cursor.execute('''
        SELECT DISTINCT week_label, MAX(archived_date) AS archived_date
        FROM archived_results
        GROUP BY week_label
        ORDER BY archived_date DESC
    ''')
    archived_weeks = [row['week_label'] for row in cursor.fetchall()]

    conn.close()
    
    local_ip = get_local_ip()
    port = request.environ.get('SERVER_PORT', '8000')
    server_url = f"http://{local_ip}:{port}"
    
    return render_template('admin_panel.html',
                         max_beginner=max_beginner,
                         max_intermediate=max_intermediate,
                         max_advanced=max_advanced,
                         vote_count=vote_count,
                         beginner_active=beginner_active,
                         intermediate_active=intermediate_active,
                         advanced_active=advanced_active,
                         beginner_votes=beginner_votes,
                         intermediate_votes=intermediate_votes,
                         advanced_votes=advanced_votes,
                         archived_weeks=archived_weeks,
                         site_access_enabled=site_access_enabled,
                         server_url=server_url)

@app.route('/admin/update_settings', methods=['POST'])
@admin_required
def update_settings():
    """Update max photo numbers"""
    data = request.json
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', 
                  (str(data['max_beginner']), 'max_beginner'))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', 
                  (str(data['max_intermediate']), 'max_intermediate'))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', 
                  (str(data['max_advanced']), 'max_advanced'))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Settings updated successfully'})

@app.route('/admin/toggle_category', methods=['POST'])
@admin_required
def toggle_category():
    """Start or stop voting for a specific category"""
    data = request.json
    category = data.get('category', '').lower()
    action = data.get('action', '')  # 'start' or 'stop'
    
    if category not in ['beginner', 'intermediate', 'advanced']:
        return jsonify({'success': False, 'message': 'Invalid category'}), 400
    
    if action not in ['start', 'stop']:
        return jsonify({'success': False, 'message': 'Invalid action'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    new_status = 'true' if action == 'start' else 'false'
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (new_status, f'{category}_active'))
    
    if action == 'start':
        import time
        new_week_id = str(int(time.time()))
        cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (new_week_id, 'current_week_id'))
        if cursor.rowcount == 0:
            cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('current_week_id', new_week_id))
    
    conn.commit()
    conn.close()
    
    action_text = 'started' if action == 'start' else 'stopped'
    return jsonify({'success': True, 'message': f'{category.capitalize()} category voting {action_text}. Cookies for this category have been reset.'})

@app.route('/admin/update_access_control', methods=['POST'])
@admin_required
def update_access_control():
    """Enable/disable site access or update the access code"""
    data = request.json or {}
    conn = get_db()
    cursor = conn.cursor()
    messages = []

    if 'enabled' in data:
        enabled_value = 'true' if data['enabled'] else 'false'
        cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (enabled_value, 'site_access_enabled'))
        messages.append(f"Site access protection {'enabled' if enabled_value == 'true' else 'disabled'}.")

    if data.get('code'):
        code = data.get('code').strip()
        if code:
            code_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()
            cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (code_hash, 'site_access_code_hash'))
            cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (code, 'site_access_code_plain'))
            messages.append('Access code updated successfully.')
        else:
            conn.close()
            return jsonify({'success': False, 'message': 'Access code cannot be empty.'}), 400

    if not messages:
        conn.close()
        return jsonify({'success': False, 'message': 'No changes submitted.'}), 400

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': ' '.join(messages)})

@app.route('/admin/reveal_access_code', methods=['POST'])
@admin_required
def reveal_access_code():
    """Reveal current access code after verifying admin password"""
    data = request.json or {}
    password = data.get('password', '')

    if not verify_admin_password(password):
        return jsonify({'success': False, 'message': 'Incorrect admin password.'}), 401

    code = get_site_access_plain_code()
    if not code:
        return jsonify({'success': False, 'message': 'Access code not set.'}), 400

    return jsonify({'success': True, 'code': code})

@app.route('/api/category_status')
def api_category_status():
    """Return current category status and vote state"""
    conn = get_db()
    cursor = conn.cursor()

    def fetch_bool(key):
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row and row['value'] == 'true'

    beginner_active = fetch_bool('beginner_active')
    intermediate_active = fetch_bool('intermediate_active')
    advanced_active = fetch_bool('advanced_active')

    cursor.execute('SELECT value FROM settings WHERE key = ?', ('current_week_id',))
    row = cursor.fetchone()
    current_week_id = row['value'] if row else str(int(time.time()))

    conn.close()

    is_admin = session.get('admin_logged_in', False)

    def voted(cat):
        if is_admin:
            return False
        return request.cookies.get(f'voted_{cat}_{current_week_id}') == 'true'

    data = {
        'beginner_active': beginner_active,
        'intermediate_active': intermediate_active,
        'advanced_active': advanced_active,
        'beginner_voted': voted('beginner'),
        'intermediate_voted': voted('intermediate'),
        'advanced_voted': voted('advanced')
    }
    return jsonify(data)

@app.route('/admin/update_admin_password', methods=['POST'])
@admin_required
def admin_update_password():
    """Allow admin to change their password"""
    data = request.json or {}
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not verify_admin_password(current_password):
        return jsonify({'success': False, 'message': 'Current password is incorrect.'}), 401

    if not new_password or len(new_password) < 8:
        return jsonify({'success': False, 'message': 'New password must be at least 8 characters.'}), 400

    update_admin_password(new_password)
    return jsonify({'success': True, 'message': 'Admin password updated successfully.'})

@app.route('/admin/delete_archive', methods=['POST'])
@admin_required
def delete_archive():
    """Delete archived results for a specific week"""
    data = request.json or {}
    week_label = data.get('week_label', '').strip()

    if not week_label:
        return jsonify({'success': False, 'message': 'Week label is required.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM archived_results WHERE week_label = ?', (week_label,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        return jsonify({'success': False, 'message': 'No archived entries found for that week.'}), 404

    return jsonify({'success': True, 'message': f'Archive "{week_label}" deleted.'})

@app.route('/admin/start_new_week', methods=['POST'])
@admin_required
def start_new_week():
    """Start a new week - archive current results and clear votes"""
    data = request.json
    week_label = data.get('week_label', '').strip()
    
    if not week_label:
        week_label = f"Week of {datetime.now().strftime('%b %d, %Y')}"
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT category, photo_number, vote_position
        FROM votes
    ''')
    
    """point_values = {1: 3, 2: 2, 3: 1}"""
    point_values = {
        1: 5,
        2: 4,
        3: 3,
        4: 2,
        5: 1
    }
    points_by_photo = {}
    
    for row in cursor.fetchall():
        category = row['category']
        photo_num = row['photo_number']
        vote_position = row['vote_position']
        points = point_values.get(vote_position, 0)
        
        key = (category, photo_num)
        if key not in points_by_photo:
            points_by_photo[key] = 0
        points_by_photo[key] += points
    
    for (category, photo_num), total_points in points_by_photo.items():
        cursor.execute('''
            INSERT INTO archived_results (week_label, category, photo_number, vote_count)
            VALUES (?, ?, ?, ?)
        ''', (week_label, category, photo_num, total_points))
    
    cursor.execute('DELETE FROM votes')
    
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', ('false', 'beginner_active'))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', ('false', 'intermediate_active'))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', ('false', 'advanced_active'))
    
    new_week_id = str(int(time.time()))
    cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (new_week_id, 'current_week_id'))
    if cursor.rowcount == 0:
        cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', ('current_week_id', new_week_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': f'New week started: {week_label}. All categories have been closed for voting.'})

@app.route('/admin/update_archive_entry', methods=['POST'])
@admin_required
def update_archive_entry():
    data = request.json or {}

    archive_id = data.get('id')
    photographer = data.get('photographer_name', '').strip()
    title = data.get('photo_title', '').strip()

    if not archive_id:
        return jsonify({'success': False, 'message': 'Archive entry ID is required'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE archived_results
        SET photographer_name = ?, photo_title = ?
        WHERE id = ?
    """, (photographer, title, archive_id))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Archive entry updated'})

@app.route('/admin/shutdown_pi', methods=['POST'])
@admin_required
def shutdown_pi():
    try:
        subprocess.Popen(['sudo', '/sbin/shutdown', '-h', 'now'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/admin/set_datetime', methods=['POST'])
@admin_required
def set_datetime():
    data = request.json or {}

    date = data.get('date')  # YYYY-MM-DD
    time = data.get('time')  # HH:MM

    if not date or not time:
        return jsonify({'success': False, 'message': 'Date and time are required'}), 400

    try:
        # Validate input
        dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")

        # Set system time
        subprocess.run(
            ['sudo', '/bin/date', '-s', dt.strftime('%Y-%m-%d %H:%M:00')],
            check=True
        )

        return jsonify({
            'success': True,
            'message': f'System time set to {dt.strftime("%Y-%m-%d %H:%M")}'
        })

    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date/time format'}), 400
    except subprocess.CalledProcessError as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
    init_db()
    local_ip = get_local_ip()
    port = 8000
    print(f"\n{'='*60}")
    print(f"Photo Voting System is starting...")
    print(f"Access the voting page at: http://{local_ip}:{port}")
    print(f"Admin panel at: http://{local_ip}:{port}/admin")
    print(f"{'='*60}\n")
    app.run(host='0.0.0.0', port=port, debug=True)
