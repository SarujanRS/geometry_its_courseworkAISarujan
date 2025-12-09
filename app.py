from pathlib import Path
from flask import Flask, request, redirect, url_for, session, flash, g, render_template_string, send_from_directory
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from owlready2 import get_ontology
import sqlite3, json, random, math



APP_ROOT = Path(__file__).parent
DB_PATH = APP_ROOT / "its_geometry.db"
ONTO_PATH = APP_ROOT / "geometry_its.owl"  # OWL file (Protégé)

app = Flask(__name__)
app.secret_key = "dev"  # change in real deployment


# ============ ONTOLOGY (HINTS FROM Protégé) ============

try:
    onto = get_ontology(str(ONTO_PATH)).load()
    print("Ontology loaded from:", ONTO_PATH)
except Exception as e:
    onto = None
    print("Could not load ontology, using default hints. Error:", e)


def get_hint_text(name, default_text):
    """Read a Hint individual's hasText from the ontology if available.
    Tries a few fallbacks and returns default_text if not present or on error.
    """
    if not onto:
        return default_text
    try:
        # Search by IRI fragment and by name
        ind = onto.search_one(iri=f"*#{name}") or onto.search_one(iri=f"*/{name}")
        if ind:
            val = getattr(ind, "hasText", None)
            if val:
                return val[0] if isinstance(val, (list, tuple)) else val
        # Last resort: iterate through individuals
        for i in onto.individuals():
            if i.name == name:
                v = getattr(i, "hasText", None)
                if v:
                    return v[0] if isinstance(v, (list, tuple)) else v
    except Exception as e:
        app.logger.debug("Error reading hint from ontology: %s", e)
    return default_text


@app.route("/geometry_its")
@app.route("/geometry_its.owl")
def serve_ontology():
    try:
        return send_from_directory(APP_ROOT, "geometry_its.owl", mimetype="application/rdf+xml")
    except Exception as e:
        app.logger.error("Failed to serve geometry_its.owl: %s", e)
        return f"Could not load ontology: {e}", 500


# Load hint text constants from ontology (or provide defaults)
HINT_PERIM = get_hint_text(
    "Hint_PerimeterVsArea",
    "Perimeter is the distance around; area is the space inside (L × W).",
  )
HINT_TRI = get_hint_text(
    "Hint_TriangleHalf",
    "Triangle area = (base × height) ÷ 2.",
  )
HINT_UNITS = get_hint_text(
    "Hint_SquareUnits",
    "Area is measured in square units (cm², m²). Convert to same unit before calculating.",
  )
HINT_SQUARE = get_hint_text(
    "Hint_SquareUnits",
    "Area of square = side × side.",
  )
HINT_CIRCLE = get_hint_text(
    "Hint_CircleArea",
    "Area of a circle = π × r². Use r for radius.",
  )


# Stage metadata mapping (stage N -> name, class, default difficulty, topic)
STAGE_META = {
    1: {"name": "Shape basics", "class": "shape", "difficulty": "Beginner", "topic": "Shape"},
    2: {"name": "Rectangles", "class": "rectangle", "difficulty": "Beginner", "topic": "Rectangle"},
    3: {"name": "Squares", "class": "square", "difficulty": "Beginner", "topic": "Square"},
    4: {"name": "Triangles", "class": "triangle", "difficulty": "Beginner", "topic": "Triangle"},
    5: {"name": "Circles", "class": "circle", "difficulty": "Intermediate", "topic": "Circle"},
    6: {"name": "Ellipses", "class": "ellipse", "difficulty": "Intermediate", "topic": "Ellipse"},
    7: {"name": "Parallelograms", "class": "parallelogram", "difficulty": "Intermediate", "topic": "Parallelogram"},
    8: {"name": "Rhombi", "class": "rhombus", "difficulty": "Advanced", "topic": "Rhombus"},
    9: {"name": "Trapezia", "class": "trapezium", "difficulty": "Advanced", "topic": "Trapezium"},
    10: {"name": "Mixed review", "class": "shape", "difficulty": "Advanced", "topic": "Mixed"},
}



# ============ SQLITE HELPERS ============

def get_db():
    """
    One SQLite connection per request.
    Uses WAL and busy_timeout to reduce 'database is locked' errors.
    """
    if "db" not in g:
        db_uri = f"file:{DB_PATH.as_posix()}?cache=shared"
        g.db = sqlite3.connect(db_uri, uri=True, timeout=30, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL;")
        g.db.execute("PRAGMA synchronous=NORMAL;")
        g.db.execute("PRAGMA busy_timeout=30000;")
        g.db.execute("PRAGMA foreign_keys=ON;")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    dbconn = g.pop("db", None)
    if dbconn is not None:
        dbconn.close()


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            student_id TEXT,
            full_name TEXT
        );

        CREATE TABLE IF NOT EXISTS stage_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stage INTEGER NOT NULL,
            questions_json TEXT NOT NULL,
            answers_json TEXT,
            started_at TEXT,
            finished_at TEXT,
            score INTEGER DEFAULT 0,
            passed INTEGER DEFAULT 0,
            UNIQUE(user_id, stage),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS pre_assessment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            questions_json TEXT NOT NULL,
            answers_json TEXT,
            started_at TEXT,
            finished_at TEXT,
            score INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()


with app.app_context():
    init_db()


# ============ USER / SESSION HELPERS ============

def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@app.context_processor
def inject_helpers():
    return dict(current_user=current_user)


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ============ GEOMETRY / QUESTION HELPERS ============

def parse_answer_with_unit(raw: str):
    """
    Require area answers in cm².
    Accepts:
      24 cm², 24cm², 24 cm^2, 24cm2, 24 sq cm
    Returns (value_as_float, unit_present_bool)
    """
    s = (raw or "").strip().lower().replace(" ", "")
    unit_present = False

    if s.endswith("cm²") or s.endswith("cm^2") or s.endswith("cm2") or s.endswith("sqcm"):
        unit_present = True
        for suf in ("cm²", "cm^2", "cm2", "sqcm"):
            if s.endswith(suf):
                s = s[: -len(suf)]
                break

    num_str = "".join(ch for ch in s if (ch.isdigit() or ch in ".-"))
    try:
        val = float(num_str) if num_str else 0.0
    except ValueError:
        val = 0.0
    return val, unit_present


def maybe_units():
    if random.random() > 0.3:
        return "cm", "cm"
    return random.choice(["cm", "m"]), random.choice(["cm", "m"])


def convert_to_cm(v, unit):
    return v * 100 if unit == "m" else v


def gen_question(override_shape: str = None, difficulty: str = 'Beginner'):
    """Random geometry area question; supports override_shape to produce a question for a single shape.
    Currently implemented shapes: 'rectangle', 'triangle', 'circle'. Unsupported values default to rectangle/triangle random.
    Returns question dict with answer in cm² and `kind` used for hints.
    """
    if override_shape:
      shape = override_shape
    else:
      shape = random.choice(["rectangle", "triangle"])
    # size ranges chosen by difficulty
    size_ranges = {
      'Beginner': (2, 10),
      'Intermediate': (5, 20),
      'Advanced': (10, 50),
    }
    lo, hi = size_ranges.get(difficulty, (2, 40))

    if shape == "rectangle":
      L, W = random.randint(lo, hi), random.randint(lo, hi)
      uL, uW = maybe_units()
      prompt = (
        f"Find the area of a rectangle with length {L}{uL} and width {W}{uW}. "
        "Enter your answer with units (e.g., 24 cm²)."
      )
      area_cm2 = convert_to_cm(L, uL) * convert_to_cm(W, uW)
      kind = "rectangle"
    elif shape == "triangle":
      L, W = random.randint(lo, hi), random.randint(lo, hi)
      uL, uW = maybe_units()
      prompt = (
        f"Find the area of a triangle with base {L}{uL} and height {W}{uW}. "
        "Enter your answer with units (e.g., 24 cm²)."
      )
      area_cm2 = (convert_to_cm(L, uL) * convert_to_cm(W, uW)) / 2.0
      kind = "triangle"

    elif shape == "circle":
        r = random.randint(max(1, lo), max(1, hi))
        ur = random.choice(["cm", "m"])
        prompt = (
            f"Find the area of a circle with radius {r}{ur}. Enter your answer with units (e.g., 24 cm²)."
        )
        radius_cm = convert_to_cm(r, ur)
        area_cm2 = math.pi * (radius_cm ** 2)
        L = r
        W = None
        uL = ur
        uW = None
        kind = "circle"

    elif shape == "ellipse":
        a = random.randint(lo, hi)
        b = random.randint(lo, hi)
        ua, ub = maybe_units()
        prompt = (
          f"Find the area of an ellipse with semi-major axis {a}{ua} and semi-minor axis {b}{ub}. Enter your answer with units (e.g., 24 cm²)."
        )
        area_cm2 = math.pi * convert_to_cm(a, ua) * convert_to_cm(b, ub)
        L = a
        W = b
        uL = ua
        uW = ub
        kind = "ellipse"

    elif shape == "parallelogram":
        base = random.randint(lo, hi)
        height = random.randint(lo, hi)
        ub = random.choice(["cm", "m"]) if random.random() > 0.3 else "cm"
        prompt = (
          f"Find the area of a parallelogram with base {base}{ub} and height {height}{ub}. Enter your answer with units (e.g., 24 cm²)."
        )
        area_cm2 = convert_to_cm(base, ub) * convert_to_cm(height, ub)
        L = base
        W = height
        uL = ub
        uW = ub
        kind = "parallelogram"

    elif shape == "rhombus":
        d1 = random.randint(lo, hi)
        d2 = random.randint(lo, hi)
        ud1 = random.choice(["cm", "m"]) if random.random() > 0.3 else "cm"
        prompt = (
          f"Find the area of a rhombus with diagonals {d1}{ud1} and {d2}{ud1}. Enter your answer with units (e.g., 24 cm²)."
        )
        area_cm2 = (convert_to_cm(d1, ud1) * convert_to_cm(d2, ud1)) / 2.0
        L = d1
        W = d2
        uL = ud1
        uW = ud1
        kind = "rhombus"

    elif shape == "trapezium":
        a = random.randint(lo, hi)
        b = random.randint(lo, hi)
        h = random.randint(lo, hi)
        ua = random.choice(["cm", "m"]) if random.random() > 0.3 else "cm"
        prompt = (
          f"Find the area of a trapezium with bases {a}{ua}, {b}{ua} and height {h}{ua}. Enter your answer with units (e.g., 24 cm²)."
        )
        area_cm2 = ((convert_to_cm(a, ua) + convert_to_cm(b, ua)) * convert_to_cm(h, ua)) / 2.0
        L = a
        W = b
        uL = ua
        uW = ua
        kind = "trapezium"

    elif shape == "square":
        L = random.randint(lo, hi)
        uL = random.choice(["cm", "m"]) if random.random() > 0.3 else "cm"
        prompt = (
            f"Find the area of a square with side {L}{uL}. Enter your answer with units (e.g., 24 cm²)."
        )
        area_cm2 = convert_to_cm(L, uL) * convert_to_cm(L, uL)
        W = L
        uW = uL
        kind = "square"

    return dict(
        shape=shape,
        L=L,
        W=W,
        uL=uL,
        uW=uW,
        prompt=prompt,
        answer=area_cm2,
        kind=kind,
    )


def hint_for_kind(kind: str) -> str:
    if kind == "rectangle":
        return f"{HINT_PERIM} {HINT_UNITS}"
    elif kind == "triangle":
        return f"{HINT_TRI} {HINT_UNITS}"
    elif kind == "square":
        # Square area = side × side
        return f"Area of square = side × side. {HINT_UNITS}"
    elif kind == "circle":
        return f"Area of a circle = π × r². {HINT_UNITS}"
    else:
        # Default hint for other shapes
        return f"{HINT_UNITS}"


# ============ BASE HTML (BLUE / WHITE THEME) ============

BASE_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Geometry ITS</title>
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
    <style>
      :root {
        --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        --secondary-gradient: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        --success-gradient: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
      }
      
      * { transition: all 0.3s ease; }
      
      body { 
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        min-height: 100vh;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        color: #2d3748;
      }
      
      .navbar { 
        background: var(--primary-gradient);
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        padding: 1rem 0;
      }
      
      .navbar-brand { 
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: 1px;
        text-shadow: 0 2px 4px rgba(0,0,0,0.1);
      }
      
      .btn-sm {
        border-radius: 25px;
        font-weight: 600;
        padding: 0.5rem 1.2rem;
        transition: transform 0.2s, box-shadow 0.2s;
      }
      
      .btn-sm:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      }
      
      .btn-light { background: #fff; color: #667eea; }
      .btn-outline-light { border: 2px solid #fff; color: #fff; }
      .btn-outline-light:hover { background: rgba(255,255,255,0.2); }
      .btn-warning { background: #f5576c; border: none; color: white; }
      
      .card {
        border: none;
        border-radius: 20px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.1);
        backdrop-filter: blur(10px);
        background: rgba(255, 255, 255, 0.95);
        overflow: hidden;
      }
      
      .card-body {
        padding: 2rem;
      }
      
      .alert {
        border: none;
        border-radius: 15px;
        padding: 1.2rem 1.5rem;
        font-weight: 500;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
      }
      
      .alert-success {
        background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
        color: #155724;
      }
      
      .alert-danger {
        background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
        color: #721c24;
      }
      
      .alert-warning {
        background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
        color: #856404;
      }
      
      .alert-info {
        background: linear-gradient(135deg, #d1ecf1 0%, #bee5eb 100%);
        color: #0c5460;
      }
      
      .form-control, .form-control-lg {
        border: 2px solid #e0e4f0;
        border-radius: 12px;
        font-size: 1rem;
        padding: 0.75rem 1rem;
        background: #f8f9fa;
        transition: all 0.3s ease;
      }
      
      .form-control:focus, .form-control-lg:focus {
        border-color: #667eea;
        background: white;
        box-shadow: 0 0 0 0.2rem rgba(102, 126, 234, 0.25);
        outline: none;
      }
      
      .form-label {
        font-weight: 600;
        color: #2d3748;
        margin-bottom: 0.5rem;
        font-size: 0.95rem;
      }
      
      .btn-primary {
        background: var(--primary-gradient);
        border: none;
        border-radius: 12px;
        font-weight: 600;
        padding: 0.75rem 1.5rem;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
      }
      
      .btn-primary:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
      }
      
      .btn-primary:active {
        transform: translateY(0);
      }
      
      .btn-lg {
        padding: 1rem 2rem;
        font-size: 1.1rem;
        border-radius: 12px;
      }
      
      /* Stage card: fixed width range and consistent top-aligned layout */
      .card-stage { 
        border-radius: 16px;
        border: 2px solid #e0e4f0;
        cursor: pointer;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: space-between;
        min-height: 155px;
        padding: 1rem;
        text-align: center;
        box-sizing: border-box;
        width: 100%;
        max-width: 100%;
        z-index: 1;
      }python app.py

      
      .stage-ready { 
        background: linear-gradient(135deg, #f0f4ff 0%, #f5f0ff 100%);
        border-color: #667eea;
      }
      
      .stage-locked { 
        background: #f8f9fa;
        opacity: 0.6;
        cursor: not-allowed;
      }
      
      .stage-complete { 
        background: linear-gradient(135deg, #e8fff3 0%, #d4fffe 100%);
        border-color: #00f2fe;
      }
      
      .badge {
        border-radius: 20px;
        padding: 0.5rem 1rem;
        font-weight: 600;
        font-size: 0.85rem;
      }
      
      .badge.bg-info {
        background: var(--success-gradient) !important;
        color: white;
      }
      
      h4, h5 {
        color: #2d3748;
        font-weight: 700;
      }
      
      .lead {
        font-size: 1.1rem;
        line-height: 1.6;
        color: #4a5568;
      }
      
      .table {
        color: #2d3748;
      }
      
      .table thead th {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        font-weight: 600;
        padding: 1rem;
      }
      
      .container {
        max-width: 1000px;
      }

      /* Move overview/practice group slightly down to align visually with the stage grid */
      .left-overview { margin-top: 1.25rem; }
      /* Prevent heading and small meta from wrapping; show ellipsis if long */
      .card-stage .h5 { margin: 0; font-size: 1rem; line-height: 1.1; white-space: normal; overflow: visible; text-overflow: clip; word-break: normal; }
      .card-stage .small { display: block; margin-top: 0.2rem; white-space: normal; overflow: visible; text-overflow: clip; font-size: 0.9rem; }
      /* Push actions to the bottom of the card to keep titles aligned */
      .card-stage a.btn, .card-stage button.btn { margin-top: 0.75rem; }
      /* push the action to the bottom of the stage card for consistent title alignment */
      .card-stage a.btn, .card-stage button.btn { margin-top: auto; }
      /* Raise active/hovered card above neighbors to avoid reddish overlap appearing on hover */
      .card-stage:hover { z-index: 10; }

      .stage-meta { width: 100%; min-height: 3.2rem; }
      .stage-meta .h5 { text-align: center; }
      .card-stage a.btn, .card-stage button.btn { white-space: nowrap; margin-top: auto; }

      /* Stage grid layout */
      .stage-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 1.25rem;
        margin-bottom: 1rem;
        width: 100%;
      }

      /* Responsive adjustments */
      @media (min-width: 992px) {
        .stage-grid { grid-template-columns: repeat(3, 1fr); gap: 1.5rem; }
      }
      
      @media (max-width: 768px) {
        .card-stage { min-height: 130px; padding: 0.75rem; }
        .stage-grid { grid-template-columns: 1fr; gap: 1rem; }
        .left-overview { margin-top: 0; }
      }
    </style>
  </head>
  <body>
    <nav class="navbar navbar-expand-lg navbar-dark">
      <div class="container">
        <a class="navbar-brand fw-bold" href="{{ url_for('dashboard') }}">Geometry ITS</a>
        <div class="ms-auto">
          {% if current_user() %}
            <a class="btn btn-sm btn-light me-2" href="{{ url_for('study') }}">Study</a>
            <span class="badge bg-light text-dark me-2">Level: {{ session.get('preferred_level', 'Beginner') }}</span>
            <a class="btn btn-sm btn-outline-light me-2" href="{{ url_for('profile') }}">Profile</a>
            <a class="btn btn-sm btn-warning" href="{{ url_for('logout') }}">Logout</a>
          {% else %}
            <a class="btn btn-sm btn-light me-2" href="{{ url_for('login') }}">Login</a>
            <a class="btn btn-sm btn-outline-light" href="{{ url_for('register') }}">Register</a>
          {% endif %}
        </div>
      </div>
    </nav>
    <div class="container py-4">
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for category, message in messages %}
            <div class="alert alert-{{ category if category in ['success','danger','warning','info'] else 'primary' }} shadow-sm">
              {{ message }}
            </div>
          {% endfor %}
        {% endif %}
      {% endwith %}
      {{ content|safe }}
    </div>
  </body>
</html>
"""

def render_page(content_template, **context):
  inner = render_template_string(content_template, **context)
  html = render_template_string(BASE_HTML, content=inner)
  inject_css = """
  <style>
    .left-overview { margin-top: 0 !important; }
    .welcome-heading { white-space: nowrap !important; display: inline-block !important; }
    h3 { white-space: nowrap !important; overflow: visible !important; }
    .card-body { overflow: visible !important; min-height: auto !important; }
    .stage-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 1.25rem;
      margin-bottom: 1rem;
      width: 100%;
      overflow: visible !important;
    }
    .card-stage {
      min-height: 155px !important;
      display: flex !important;
      flex-direction: column !important;
      align-items: center;
      justify-content: space-between;
      width: 100% !important;
      max-width: 100% !important;
      padding: 1rem !important;
    }
    .card-stage a.btn, .card-stage button.btn { margin-top: auto !important; width: auto; }
    .card-stage .h5 { white-space: normal !important; overflow: visible !important; word-break: normal !important; overflow-wrap: break-word !important; font-size: 1.1rem !important; }
    .card-stage .small { font-size: 0.85rem !important; }
    @media (min-width: 992px) {
      .stage-grid { grid-template-columns: repeat(3, 1fr); gap: 1.5rem; }
    }
    @media (max-width: 768px) {
      .left-overview { margin-top: 0 !important; }
      .stage-grid { grid-template-columns: 1fr; gap: 1rem; }
      .card-stage { min-height: 130px !important; padding: 0.75rem !important; }
    }
  </style>
  """
  if "</head>" in html:
    html = html.replace("</head>", inject_css + "</head>")
  else:
    html = inject_css + html
  return html


# ============ AUTH (LOGIN / REGISTER) ============

LOGIN_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-md-6 col-lg-4">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Sign in</h4>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Username</label>
            <input class="form-control" name="username" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Password</label>
            <input type="password" class="form-control" name="password" required>
          </div>
          <button class="btn btn-primary w-100">Login</button>
        </form>
        <div class="mt-3 text-center">
          <a href="{{ url_for('register') }}">Create an account</a>
        </div>
      </div>
    </div>
  </div>
</div>
"""

REGISTER_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-md-7 col-lg-5">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Register</h4>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Full name</label>
            <input class="form-control" name="full_name" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Student ID</label>
            <input class="form-control" name="student_id">
          </div>
          <div class="mb-3">
            <label class="form-label">Username</label>
            <input class="form-control" name="username" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Password</label>
            <input type="password" class="form-control" name="password" required>
          </div>
          <button class="btn btn-primary w-100">Create account</button>
        </form>
        <div class="mt-3 text-center">
          <a href="{{ url_for('login') }}">Back to login</a>
        </div>
      </div>
    </div>
  </div>
</div>
"""

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]
        full_name = request.form.get("full_name", "").strip()
        student_id = request.form.get("student_id", "").strip()

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("register"))

        try:
            get_db().execute(
                "INSERT INTO users (username, password_hash, student_id, full_name) VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), student_id, full_name),
            )
            get_db().commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists.", "danger")

    return render_page(REGISTER_HTML)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]

        row = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if row and check_password_hash(row["password_hash"], password):
            session["uid"] = row["id"]
            flash(f"Welcome, {row['full_name'] or row['username']}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_page(LOGIN_HTML)


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# ============ DASHBOARD / STUDY ============

DASHBOARD_HTML = """
<div class="row g-4" style="margin-top: 0;">
  <div class="col-12 col-lg-4 left-overview">
    <div class="card shadow-sm mb-3">
      <div class="card-body">
        <h5>Overview</h5>
        <p class="mb-1"><strong>Student ID:</strong> {{ user.student_id or '-' }}</p>
        {% if pre %}
          <p class="mb-1"><strong>Pre-assessment:</strong>
             {{ 'Completed' if pre.finished_at else 'In progress' }} — Score: {{ pre.score or 0 }}/100
          </p>
          <a class="btn btn-outline-primary btn-sm" href="{{ url_for('pre_result') }}">View pre-assessment result</a>
        {% else %}
          <a class="btn btn-primary btn-sm" href="{{ url_for('pre_start') }}">Start pre-assessment</a>
        {% endif %}
      </div>
    </div>
    <div class="card shadow-sm">
      <div class="card-body">
        <h5>Practice & Study</h5>
        <p class="text-muted mb-2">Do practice questions or review the lesson before the stages.</p>
        <a class="btn btn-warning btn-sm me-2" href="{{ url_for('practice') }}">Practice</a>
        <a class="btn btn-outline-secondary btn-sm" href="{{ url_for('study') }}">Study lesson</a>
      </div>
    </div>
  </div>
  <div class="col-12 col-lg-7">
    <div class="card shadow-sm">
      <div class="card-body">
        <h5 class="mb-3">Stages 1–10</h5>
        <div class="stage-grid">
          {% for s in stages %}
            <div class="card card-stage text-center p-2
              {% if s.status == 'Completed' %}stage-complete{% elif s.status == 'Locked' %}stage-locked{% else %}stage-ready{% endif %}">
              <div class="stage-meta w-100">
                <div class="h5 mb-0">{{ s.name }}</div>
                <div class="small text-muted">{{ s.status }}</div>
                <div class="small text-muted">{{ s.difficulty }} - {{ s.topic }}</div>
              </div>
              {% if s.status == 'Ready' %}
                <a class="btn btn-sm btn-primary" href="{{ url_for('stage_start', stage=s.num) }}">Start</a>
              {% elif s.status == 'Completed' %}
                <a class="btn btn-sm btn-outline-primary" href="{{ url_for('stage_result', stage=s.num) }}">Results</a>
                <div class="small mt-1">{{ 'PASS' if s.passed else 'FAIL' }} · {{ s.score }}/100</div>
              {% else %}
                <button class="btn btn-sm btn-secondary" disabled>Locked</button>
              {% endif %}
            </div>
          {% endfor %}
        </div>
      </div>
    </div>
  </div>
</div>
"""

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()

    rows = conn.execute(
        "SELECT stage, score, passed, finished_at FROM stage_attempts WHERE user_id = ? ORDER BY stage",
        (user["id"],),
    ).fetchall()
    stage_map = {r["stage"]: r for r in rows}

    stages = []
    for s in range(1, 11):
        r = stage_map.get(s)
        status = "Ready"
        if s > 1 and not stage_map.get(s - 1):
            status = "Locked"
        if r and r["finished_at"]:
            status = "Completed"

        meta = STAGE_META.get(s, {})
        stages.append(
          dict(
            num=s,
            status=status,
            score=(r["score"] if r else None),
            passed=(r["passed"] if r else None),
            name=meta.get("name", f"S{s}"),
            difficulty=meta.get("difficulty", ""),
            topic=meta.get("topic", ""),
          )
        )

    pre = conn.execute(
        "SELECT score, finished_at FROM pre_assessment WHERE user_id = ?",
        (user["id"],),
    ).fetchone()

    return render_page(DASHBOARD_HTML, user=user, stages=stages, pre=pre)


STUDY_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-lg-8">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Study: Area of Rectangles & Triangles</h4>
        <p><strong>Rectangle area:</strong> length × width. Example: 6cm × 3cm = 18cm².</p>
        <p><strong>Triangle area:</strong> (base × height) ÷ 2. Example: base=10cm, height=4cm → (10×4)/2 = 20cm².</p>
        <p><strong>Units:</strong> Convert to the same unit before calculating. 2m = 200cm. Area answers are in cm².</p>
        <hr>
        <h5>Shapes & Example questions</h5>
        <p>The ITS supports the following shapes and their area calculations:</p>
        <ul>
          <li><a href="{{ url_for('study_shape', shape='shape') }}">Shape (generic)</a></li>
          <li><a href="{{ url_for('study_shape', shape='rectangle') }}">Rectangle</a></li>
          <li><a href="{{ url_for('study_shape', shape='square') }}">Square</a></li>
          <li><a href="{{ url_for('study_shape', shape='triangle') }}">Triangle</a></li>
          <li><a href="{{ url_for('study_shape', shape='circle') }}">Circle</a></li>
          <li><a href="{{ url_for('study_shape', shape='ellipse') }}">Ellipse</a></li>
          <li><a href="{{ url_for('study_shape', shape='polygon') }}">Polygon</a></li>
          <li><a href="{{ url_for('study_shape', shape='quadrilateral') }}">Quadrilateral</a></li>
          <li><a href="{{ url_for('study_shape', shape='parallelogram') }}">Parallelogram</a></li>
          <li><a href="{{ url_for('study_shape', shape='rhombus') }}">Rhombus</a></li>
          <li><a href="{{ url_for('study_shape', shape='trapezium') }}">Trapezium</a></li>
          <li><a href="{{ url_for('study_shape', shape='pentagon') }}">Pentagon</a></li>
          <li><a href="{{ url_for('study_shape', shape='hexagon') }}">Hexagon</a></li>
        </ul>
        <h5>Example questions</h5>
        <ul>
          <li>Rectangle: 8cm × 3cm → 24cm²</li>
          <li>Triangle: base 12cm, height 5cm → 30cm²</li>
          <li>Triangle: base 2m, height 30cm → (200×30)/2 = 3000cm²</li>
        </ul>
      </div>
    </div>
  </div>
</div>
"""

STAGE_START_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-md-6">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Start Stage {{ stage }}: {{ stage_meta.name }}</h4>
        <p>Stage difficulty (default: {{ default_level }}). Choose a level to start this stage:</p>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Select difficulty</label>
            <select class="form-control" name="level">
              <option value="Beginner" {% if default_level=='Beginner' %}selected{% endif %}>Beginner</option>
              <option value="Intermediate" {% if default_level=='Intermediate' %}selected{% endif %}>Intermediate</option>
              <option value="Advanced" {% if default_level=='Advanced' %}selected{% endif %}>Advanced</option>
            </select>
          </div>
          <button class="btn btn-primary">Start Stage</button>
        </form>
      </div>
    </div>
  </div>
</div>
"""

@app.route("/study")
@login_required
def study():
    return render_page(STUDY_HTML)


STUDY_SHAPE_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-md-8">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Study: {{ shape|title }}</h4>
        <p class="lead">{{ description }}</p>
        <p class="small text-info"><strong>Hint:</strong> {{ hint_text }}</p>
        <a class="btn btn-warning" href="{{ url_for('practice_shape', shape=shape) }}">Practice {{ shape|title }}</a>
        <a class="btn btn-outline-secondary" href="{{ url_for('study') }}">Back</a>
      </div>
    </div>
  </div>
</div>
"""


@app.route('/study/shape/<shape>')
@login_required
def study_shape(shape):
    # Provide a short description and a hint for the given shape
    desc_map = {
        'rectangle': 'Area = length × width',
        'square': 'Area = side × side',
        'triangle': 'Area = (base × height) ÷ 2',
        'circle': 'Area = π × r²',
        'ellipse': 'Area = π × a × b',
        'parallelogram': 'Area = base × height',
        'rhombus': 'Area = (diagonal1 × diagonal2) ÷ 2',
        'trapezium': 'Area = (a + b) × h ÷ 2',
        'pentagon': 'Area depends on regular/irregular: regular pentagon = 1/4 √(5(5+2√5)) × side²',
        'hexagon': 'Regular hexagon area = (3√3/2) × side²',
        'quadrilateral': 'Divide into triangles or use Bretschneider formula for cyclic/quadrilaterals',
        'polygon': 'Use triangulation or standard formula depending on polygon type',
    }
    hint_text = hint_for_kind(shape)
    return render_page(STUDY_SHAPE_HTML, shape=shape, description=desc_map.get(shape, 'Study this shape.'), hint_text=hint_text)


@app.route('/practice/shape/<shape>', methods=['GET', 'POST'])
@login_required
def practice_shape(shape):
    if request.method == 'POST':
        true_area = float(request.form['true_area'])
        raw = request.form['answer']
        val, unit_ok = parse_answer_with_unit(raw)

        if not unit_ok:
            flash('Please include units (e.g. 24 cm²).', 'warning')
            return redirect(url_for('practice_shape', shape=shape))

        if math.isclose(val, true_area, rel_tol=1e-6):
            flash('Correct! 🎉', 'success')
        else:
            flash(f'Not quite. Correct area is {true_area} cm².', 'danger')
        return redirect(url_for('practice_shape', shape=shape))

    level = request.args.get('level', session.get('preferred_level', 'Beginner'))
    q = gen_question(override_shape=shape, difficulty=level)
    hint_text = hint_for_kind(q['kind'])
    return render_page(PRACTICE_HTML, prompt=q['prompt'], true_area=q['answer'], hint_text=hint_text)


# ============ SHARED QUESTION / RESULT HTML ============

QUESTION_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-lg-8">
    <div class="card shadow-sm">
      <div class="card-body">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h5 class="mb-0">{{ title }}</h5>
          <span class="badge bg-info text-dark">Q {{ idx }}/{{ total }}</span>
        </div>
        <p class="lead">{{ prompt }}</p>
        <p class="text-muted">Write your answer with units, e.g. <strong>24 cm²</strong>.</p>
        <p class="small text-info"><strong>Hint:</strong> {{ hint_text }}</p>
        <form method="post" class="row g-2 align-items-center">
          <div class="col-12 col-md-8">
            <input class="form-control form-control-lg" name="answer" placeholder="e.g. 24 cm²" required>
          </div>
          <div class="col-6 col-md-4">
            <button class="btn btn-primary btn-lg w-100" type="submit">Submit</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
"""

RESULT_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-lg-10">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">{{ title }}</h4>
        <p><strong>Score:</strong> {{ score }}/{{ total }}</p>
        <div class="table-responsive">
          <table class="table table-sm align-middle">
            <thead>
              <tr>
                <th>#</th><th>Question</th><th>Your answer</th><th>Correct answer</th><th>Result</th>
              </tr>
            </thead>
            <tbody>
              {% for d in detail %}
              <tr>
                <td>{{ d.i }}</td>
                <td>{{ d.prompt }}</td>
                <td>{{ d.given if d.given is not none else '-' }} cm²</td>
                <td>{{ d.expected }} cm²</td>
                <td>
                  {% if d.correct %}
                    <span class="badge bg-success">Correct</span>
                  {% else %}
                    <span class="badge bg-danger">Wrong</span>
                  {% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        <a class="btn btn-primary" href="{{ back_url }}">Back to dashboard</a>
      </div>
    </div>
  </div>
</div>
"""


# ============ PRE-ASSESSMENT (10 Q, ONCE) ============

@app.route("/pre/start")
@login_required
def pre_start():
    user = current_user()
    conn = get_db()

    existing = conn.execute(
        "SELECT * FROM pre_assessment WHERE user_id = ?", (user["id"],)
    ).fetchone()

    if existing and existing["finished_at"]:
        flash("You already completed the pre-assessment.", "warning")
        return redirect(url_for("pre_result"))

    if not existing:
        questions = [gen_question() for _ in range(10)]
        conn.execute(
            "INSERT INTO pre_assessment (user_id, questions_json, started_at) "
            "VALUES (?, ?, datetime('now'))",
            (user["id"], json.dumps(questions)),
        )
        conn.commit()

    return redirect(url_for("pre_q", idx=1))


@app.route("/pre/q/<int:idx>", methods=["GET", "POST"])
@login_required
def pre_q(idx):
    user = current_user()
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM pre_assessment WHERE user_id = ?", (user["id"],)
    ).fetchone()

    if not row:
        return redirect(url_for("pre_start"))

    questions = json.loads(row["questions_json"])
    answers = json.loads(row["answers_json"]) if row["answers_json"] else {}

    if row["finished_at"]:
        return redirect(url_for("pre_result"))

    total = len(questions)
    q = questions[idx - 1]
    hint_text = hint_for_kind(q["kind"])

    if request.method == "POST":
        raw = request.form["answer"]
        val, unit_ok = parse_answer_with_unit(raw)

        if not unit_ok:
            flash("Please include units (e.g. 24 cm²).", "warning")
            return render_page(
                QUESTION_HTML,
                title="Pre-assessment",
                idx=idx,
                total=total,
                prompt=q["prompt"],
                hint_text=hint_text,
            )

        answers[str(idx)] = val
        conn.execute(
            "UPDATE pre_assessment SET answers_json = ? WHERE id = ?",
            (json.dumps(answers), row["id"]),
        )
        conn.commit()

        if idx >= total:
            score = 0
            for i, qq in enumerate(questions, start=1):
                given = answers.get(str(i), float("inf"))
                if math.isclose(given, qq["answer"], rel_tol=1e-6):
                    score += 10
            conn.execute(
                "UPDATE pre_assessment SET finished_at = datetime('now'), score = ? WHERE id = ?",
                (score, row["id"]),
            )
            conn.commit()
            flash(f"Pre-assessment finished. Score: {score}/100", "success")
            return redirect(url_for("dashboard"))

        return redirect(url_for("pre_q", idx=idx + 1))

    return render_page(
        QUESTION_HTML,
        title="Pre-assessment",
        idx=idx,
        total=total,
        prompt=q["prompt"],
        hint_text=hint_text,
    )


@app.route("/pre/result")
@login_required
def pre_result():
    user = current_user()
    row = get_db().execute(
        "SELECT * FROM pre_assessment WHERE user_id = ?", (user["id"],)
    ).fetchone()

    if not row or not row["finished_at"]:
        flash("Pre-assessment not completed yet.", "warning")
        return redirect(url_for("dashboard"))

    questions = json.loads(row["questions_json"])
    answers = json.loads(row["answers_json"]) if row["answers_json"] else {}
    detail = []

    for i, q in enumerate(questions, start=1):
        given = answers.get(str(i))
        correct = given is not None and math.isclose(
            given, q["answer"], rel_tol=1e-6
        )
        detail.append(
            dict(
                i=i,
                prompt=q["prompt"],
                given=given,
                expected=q["answer"],
                correct=correct,
            )
        )

    return render_page(
        RESULT_HTML,
        title="Pre-assessment result",
        score=row["score"],
        total=100,
        detail=detail,
        back_url=url_for("dashboard"),
    )


# ============ STAGES 1–10 (ONE ATTEMPT EACH) ============

@app.route("/stage/<int:stage>/start", methods=["GET","POST"])
@login_required
def stage_start(stage):
    user = current_user()

    if stage < 1 or stage > 10:
        flash("Invalid stage.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db()

    if stage > 1:
        prev = conn.execute(
            "SELECT finished_at FROM stage_attempts WHERE user_id = ? AND stage = ?",
            (user["id"], stage - 1),
        ).fetchone()
        if not (prev and prev["finished_at"]):
            flash(f"Stage {stage} is locked. Complete Stage {stage-1} first.", "warning")
            return redirect(url_for("dashboard"))

    row = conn.execute(
        "SELECT * FROM stage_attempts WHERE user_id = ? AND stage = ?",
        (user["id"], stage),
    ).fetchone()

    if row and row["finished_at"]:
        flash("This stage is already completed (single attempt only).", "warning")
        return redirect(url_for("stage_result", stage=stage))

    if request.method == 'GET' and not row:
      # Show selection page to pick difficulty before starting stage
      meta = STAGE_META.get(stage, {})
      default_level = session.get('preferred_level', meta.get('difficulty', 'Beginner'))
      return render_page(STAGE_START_HTML, stage=stage, stage_meta=meta, default_level=default_level)

    if request.method == 'POST' and not row:
        # If the stage has a specific class cap (e.g., rectangle, triangle, circle), use it
        meta = STAGE_META.get(stage, {})
        cls = meta.get("class")
        def _q():
          if cls in ("rectangle", "triangle", "circle", "square"):
            return gen_question(override_shape=cls, difficulty=level)
          return gen_question(difficulty=level)

        # Read the selected level from the form (fallback to default)
        level = request.form.get('level', session.get('preferred_level', meta.get('difficulty', 'Beginner')))
        # Save preferred level to session
        session['preferred_level'] = level
        questions = [_q() for _ in range(10)]
        conn.execute(
            "INSERT INTO stage_attempts (user_id, stage, questions_json, started_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (user["id"], stage, json.dumps(questions)),
        )
        conn.commit()

    return redirect(url_for("stage_q", stage=stage, idx=1))


@app.route("/stage/<int:stage>/q/<int:idx>", methods=["GET", "POST"])
@login_required
def stage_q(stage, idx):
    user = current_user()
    conn = get_db()

    row = conn.execute(
        "SELECT * FROM stage_attempts WHERE user_id = ? AND stage = ?",
        (user["id"], stage),
    ).fetchone()

    if not row:
        return redirect(url_for("stage_start", stage=stage))

    if row["finished_at"]:
        return redirect(url_for("stage_result", stage=stage))

    questions = json.loads(row["questions_json"])
    answers = json.loads(row["answers_json"]) if row["answers_json"] else {}
    total = len(questions)
    q = questions[idx - 1]
    hint_text = hint_for_kind(q["kind"])

    if request.method == "POST":
        raw = request.form["answer"]
        val, unit_ok = parse_answer_with_unit(raw)

        if not unit_ok:
            flash("Please include units (e.g. 24 cm²).", "warning")
            return render_page(
              QUESTION_HTML,
              title=f"Stage {stage}: {STAGE_META.get(stage, {}).get('name', '')}",
                idx=idx,
                total=total,
                prompt=q["prompt"],
                hint_text=hint_text,
            )

        answers[str(idx)] = val
        conn.execute(
            "UPDATE stage_attempts SET answers_json = ? WHERE id = ?",
            (json.dumps(answers), row["id"]),
        )
        conn.commit()

        if idx >= total:
            score = 0
            for i, qq in enumerate(questions, start=1):
                given = answers.get(str(i), float("inf"))
                if math.isclose(given, qq["answer"], rel_tol=1e-6):
                    score += 10
            passed = 1 if score >= 60 else 0
            conn.execute(
                "UPDATE stage_attempts SET finished_at = datetime('now'), score = ?, passed = ? WHERE id = ?",
                (score, passed, row["id"]),
            )
            conn.commit()
            flash(
                f"Stage {stage} finished. Score: {score}/100 — {'PASS' if passed else 'FAIL'}",
                "success" if passed else "danger",
            )
            return redirect(url_for("dashboard"))

        return redirect(url_for("stage_q", stage=stage, idx=idx + 1))

    return render_page(
      QUESTION_HTML,
      title=f"Stage {stage}: {STAGE_META.get(stage, {}).get('name', '')}",
        idx=idx,
        total=total,
        prompt=q["prompt"],
        hint_text=hint_text,
    )


@app.route("/stage/<int:stage>/result")
@login_required
def stage_result(stage):
    user = current_user()
    row = get_db().execute(
        "SELECT * FROM stage_attempts WHERE user_id = ? AND stage = ?",
        (user["id"], stage),
    ).fetchone()

    if not row or not row["finished_at"]:
        flash("Stage not completed yet.", "warning")
        return redirect(url_for("dashboard"))

    questions = json.loads(row["questions_json"])
    answers = json.loads(row["answers_json"]) if row["answers_json"] else {}
    detail = []

    for i, q in enumerate(questions, start=1):
        given = answers.get(str(i))
        correct = given is not None and math.isclose(
            given, q["answer"], rel_tol=1e-6
        )
        detail.append(
            dict(
                i=i,
                prompt=q["prompt"],
                given=given,
                expected=q["answer"],
                correct=correct,
            )
        )

    return render_page(
      RESULT_HTML,
      title=f"Stage {stage} result: {STAGE_META.get(stage, {}).get('name', '')}",
        score=row["score"],
        total=100,
        detail=detail,
        back_url=url_for("dashboard"),
    )


# ============ PRACTICE MODE ============

PRACTICE_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-lg-8">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Practice</h4>
        <p class="lead">{{ prompt }}</p>
        <p class="text-muted">Write your answer with units, e.g. <strong>24 cm²</strong>.</p>
        <p class="small text-info"><strong>Hint:</strong> {{ hint_text }}</p>
        <form method="post" class="row g-2 align-items-center">
          <input type="hidden" name="true_area" value="{{ true_area }}">
          <div class="col-12 col-md-8">
            <input class="form-control form-control-lg" name="answer" placeholder="e.g. 24 cm²" required>
          </div>
          <div class="col-6 col-md-4">
            <button class="btn btn-primary btn-lg w-100" type="submit">Check</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
"""

@app.route("/practice", methods=["GET", "POST"])
@login_required
def practice():
    if request.method == "POST":
        true_area = float(request.form["true_area"])
        raw = request.form["answer"]
        val, unit_ok = parse_answer_with_unit(raw)

        if not unit_ok:
            flash("Please include units (e.g. 24 cm²).", "warning")
            return redirect(url_for("practice"))

        if math.isclose(val, true_area, rel_tol=1e-6):
            flash("Correct! 🎉", "success")
        else:
            flash(f"Not quite. Correct area is {true_area} cm².", "danger")
        return redirect(url_for("practice"))

    q = gen_question()
    hint_text = hint_for_kind(q["kind"])
    return render_page(
        PRACTICE_HTML,
        prompt=q["prompt"],
        true_area=q["answer"],
        hint_text=hint_text,
    )


# ============ PROFILE PAGE ============

PROFILE_HTML = """
<div class="row justify-content-center">
  <div class="col-12 col-md-8">
    <div class="card shadow-sm">
      <div class="card-body">
        <h4 class="mb-3">Profile</h4>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Full name</label>
            <input class="form-control" name="full_name" value="{{ user.full_name or '' }}">
          </div>
          <div class="mb-3">
            <label class="form-label">Student ID</label>
            <input class="form-control" name="student_id" value="{{ user.student_id or '' }}">
          </div>
          <div class="mb-3">
            <label class="form-label">Preferred difficulty</label>
            <select name="preferred_level" class="form-control">
              <option value="Beginner" {% if session.get('preferred_level')=='Beginner' %}selected{% endif %}>Beginner</option>
              <option value="Intermediate" {% if session.get('preferred_level')=='Intermediate' %}selected{% endif %}>Intermediate</option>
              <option value="Advanced" {% if session.get('preferred_level')=='Advanced' %}selected{% endif %}>Advanced</option>
            </select>
          </div>
          <button class="btn btn-primary">Save</button>
        </form>
      </div>
    </div>
  </div>
</div>
"""

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        full_name = request.form.get("full_name", "")
        student_id = request.form.get("student_id", "")
        preferred_level = request.form.get("preferred_level")
        if preferred_level:
            session['preferred_level'] = preferred_level
        get_db().execute(
            "UPDATE users SET full_name = ?, student_id = ? WHERE id = ?",
            (full_name, student_id, user["id"]),
        )
        get_db().commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    return render_page(PROFILE_HTML, user=user)


# ============ DEV: RESET DB (OPTIONAL) ============

@app.route("/dev/reset_db")
def dev_reset_db():
    """Danger: wipes DB. Useful if you want to start clean."""
    try:
        close_db(None)
    except Exception:
        pass

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    with app.app_context():
        init_db()

    flash("Database reset.", "info")
    return redirect(url_for("login"))


# ============ MAIN ENTRY ============

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
