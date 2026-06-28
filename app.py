from datetime import datetime, timedelta, timezone
import os
import re
import random
import smtplib
import time
from email.mime.text import MIMEText
from dotenv import load_dotenv

import certifi
from flask import Flask, request, jsonify, render_template, session, redirect, send_from_directory
from flask_cors import CORS
import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
import pymongo
from pymongo import MongoClient
from flask_pymongo import PyMongo

load_dotenv()

app = Flask(__name__)
CORS(app)

# ======================================================================
# PRODUCTION CONFIGURATION
# ======================================================================
mongo_uri = "mongodb+srv://orbit_admin:mhva7Pfw1xPskUdL@cluster0.lzkwe54.mongodb.net/?retryWrites=true&w=majority"

app.config["MONGO_URI"] = mongo_uri + "&authSource=admin"

# 1. Patch the connection initializer
original_init = MongoClient.__init__
def patched_init(self, *args, **kwargs):
    args = (mongo_uri,) + args[1:]
    kwargs.pop('host', None)
    kwargs.pop('port', None)
    original_init(self, *args, **kwargs)
MongoClient.__init__ = patched_init

# 2. FORCE CASE MATCH PATCH: Safely redirects everything to 'Orbit'
original_getitem = MongoClient.__getitem__
original_getattr = MongoClient.__getattr__

def patched_getitem(self, name):
    return original_getitem(self, 'Orbit')

def patched_getattr(self, name):
    # If it's a structural pymongo attribute, let it through normally
    if name.startswith('_') or name in ['nodes', 'address', 'codec_options', 'read_preference', 'write_concern', 'read_concern']:
        return original_getattr(self, name)
    return original_getitem(self, 'Orbit')

MongoClient.__getitem__ = patched_getitem
MongoClient.__getattr__ = patched_getattr
MongoClient.get_default_database = lambda self, default=None: self['Orbit']

# Initialize connections
client = MongoClient(mongo_uri)
db = client['Orbit']
mongo = PyMongo(app)

print("Global infrastructure initialization complete. All databases forced to 'Orbit'.")
# ======================================================================

# YOUR ROUTES / CODE GO BELOW THIS LINE

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Load environment variables from .env file
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=os.path.join(BASE_DIR, "static"))
app.secret_key = os.getenv("SECRET_KEY", "orbit_secret_key_12345")
CORS(app, supports_credentials=True)
# Keep browser sessions alive during tab switching/refresh testing.
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

@app.before_request
def keep_orbit_session_alive():
    session.permanent = True


def render_page(filename, **context):
    """Render from /templates first. If the file is beside app.py, serve it too."""
    template_file = os.path.join(TEMPLATE_DIR, filename)
    root_file = os.path.join(BASE_DIR, filename)
    if os.path.exists(template_file):
        return render_template(filename, **context)
    if os.path.exists(root_file):
        return send_from_directory(BASE_DIR, filename)
    return f"{filename} not found. Put {filename} inside the templates folder or beside app.py.", 404


@app.errorhandler(Exception)
def orbit_json_error(error):
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return jsonify(success=False, error=error.description), error.code
    print("❌ Flask error:", error)
    return jsonify(success=False, error=str(error)), 500

MONGO_URI = "mongodb://localhost:27017/"

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()
    print("✅ MongoDB Connected")
except Exception as e:
    print("❌ MongoDB connection failed:", e)
    exit()

# IMPORTANT: use SAME DB everywhere
db = client["orbit"]   # <-- change to "orbit" (not orbit_db)

users_collection = db["users"]
hosts_collection = db["hosts"]
events_collection = db["events"]
notifications_collection = db["notifications"]
otp_collection = db["otps"]
registrations_collection = db["registrations"]
payments_collection = db["payments"]
refunds_collection = db["refunds"]
settlements_collection = db["settlements"]
reports_collection = db["reports"]
reviews_collection = db["reviews"]
admins_collection = db["admins"]
settings_collection = db["settings"]

PLATFORM_USER_FEE = 5
PLATFORM_ORGANISER_FEE = 5
ADMIN_UPI_ID = os.getenv("ORBIT_ADMIN_UPI_ID", "orbitadmin@upi")
ADMIN_QR_IMAGE = os.getenv("ORBIT_ADMIN_QR_IMAGE", "")
OTP_DEBUG_MODE = os.getenv("OTP_DEBUG", "false").lower() in ("1", "true", "yes")

SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


def now():
    return datetime.now()


def clean(v):
    return str(v or "").strip()


def clean_lower(v):
    return clean(v).lower()


def get_orbit_payment_settings():
    defaults = {
        "upi_id": clean(os.getenv("ORBIT_ADMIN_UPI_ID", "orbitadmin@upi")),
        "qr_image": clean(os.getenv("ORBIT_ADMIN_QR_IMAGE", "")),
    }
    try:
        doc = settings_collection.find_one({"key": "orbit_payment"})
        if not doc:
            return defaults
        return {
            "upi_id": clean(doc.get("upi_id") or defaults["upi_id"]),
            "qr_image": clean(doc.get("qr_image") or defaults["qr_image"]),
        }
    except Exception:
        return defaults


def sync_orbit_payment_settings():
    global ADMIN_UPI_ID, ADMIN_QR_IMAGE
    settings = get_orbit_payment_settings()
    ADMIN_UPI_ID = settings["upi_id"]
    ADMIN_QR_IMAGE = settings["qr_image"]
    return settings


def update_orbit_payment_settings(upi_id, qr_image):
    settings_collection.update_one(
        {"key": "orbit_payment"},
        {"$set": {"upi_id": clean(upi_id), "qr_image": clean(qr_image), "updated_at": now()}},
        upsert=True,
    )
    return sync_orbit_payment_settings()


sync_orbit_payment_settings()


def set_active_user_session(email):
    """Log in/refresh the user part without deleting host/admin tab sessions."""
    email = clean_lower(email)
    if email:
        session["email"] = email
        session["user_email"] = email
        session["role"] = "user"
        session["user_type"] = "user"
        session["active_role"] = "user"


def set_active_host_session(email):
    """Log in/refresh the organiser part without deleting user/admin tab sessions."""
    email = clean_lower(email)
    if email:
        session["email"] = email
        session["host_email"] = email
        session["role"] = "host"
        session["user_type"] = "host"
        session["active_role"] = "host"


def set_active_admin_session(admin):
    """Log in/refresh admin without deleting user/organiser tab sessions."""
    admin = admin or {}
    session["role"] = "admin"
    session["user_type"] = "admin"
    session["active_role"] = "admin"
    session["admin_email"] = clean_lower(admin.get("email", "admin@orbit.com"))
    session["admin_name"] = admin.get("name", "Orbit Admin")
    session["admin_username"] = admin.get("username", "admin")


def clear_user_session_only():
    for key in ["user_email", "profile_welcome_mode"]:
        session.pop(key, None)
    if clean_lower(session.get("active_role") or session.get("role")) == "user":
        session.pop("role", None)
        session.pop("user_type", None)
        session.pop("active_role", None)
        session.pop("email", None)


def clear_host_session_only():
    for key in ["host_email"]:
        session.pop(key, None)
    if clean_lower(session.get("active_role") or session.get("role")) in ["host", "organiser", "organizer"]:
        session.pop("role", None)
        session.pop("user_type", None)
        session.pop("active_role", None)
        session.pop("email", None)


def clear_admin_session_only():
    for key in ["admin_email", "admin_name", "admin_username"]:
        session.pop(key, None)
    if clean_lower(session.get("active_role") or session.get("role")) == "admin":
        session.pop("role", None)
        session.pop("user_type", None)
        session.pop("active_role", None)
        session.pop("email", None)


def get_data():
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    return data or {}


def valid_email(email):
    email = clean_lower(email)
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", email):
        return False

    blocked = [
        "example.com",
        "test.com",
        "fake.com",
        "mailinator.com",
        "tempmail.com",
        "10minutemail.com",
        "yopmail.com",
        "guerrillamail.com",
    ]

    domain = email.split("@")[-1]
    return domain not in blocked


def strong_password(password):
    return len(password or "") >= 8


def email_query(email):
    return {"email": {"$regex": f"^{re.escape(clean_lower(email))}$", "$options": "i"}}


def find_user(email):
    email = clean_lower(email)
    if not email:
        return None
    return users_collection.find_one(email_query(email))


def find_host(email):
    email = clean_lower(email)
    if not email:
        return None
    return hosts_collection.find_one(email_query(email))


def event_owner_email(event):
    """Return the canonical organiser email saved on an event row."""
    event = event or {}
    return clean_lower(
        event.get("organiser_email")
        or event.get("organizer_email")
        or event.get("host_email")
        or event.get("owner_email")
    )


def host_deleted_response(email=""):
    """Clear stale sessions when a host was removed from MongoDB/Compass."""
    try:
        if not email or clean_lower(session.get("host_email")) == clean_lower(email):
            clear_host_session_only()
    except Exception:
        pass
    try:
        purge_orphan_admin_data()
    except Exception:
        pass
    return jsonify(
        success=False,
        deleted=True,
        error="Organiser account no longer exists. Please sign up again.",
        redirect="/hsign.html",
    ), 401


def require_existing_host(email=None):
    email = clean_lower(email or session.get("host_email"))
    if not email:
        return None, (jsonify(success=False, error="Host not logged in", redirect="/hlog.html"), 401)
    host = find_host(email)
    if not host:
        return None, host_deleted_response(email)
    return host, None


def normalize_list(value):
    if isinstance(value, list):
        return [clean(x) for x in value if clean(x)]
    if isinstance(value, str):
        return [clean(x) for x in value.split(",") if clean(x)]
    return []


def safe_dt(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def public_user(user):
    return {
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": "user",
        "profile": user.get("profile", {}),
        "onboarding_complete": user.get("onboarding_complete", False),
        "saved_events": user.get("saved_events", []),
        "interested_events": user.get("interested_events", []),
        "registered_events": user.get("registered_events", []),
        "city": user.get("city", "") or (user.get("profile", {}) or {}).get("city", ""),
    }


def public_host(host):
    host = host or {}
    return {
        "name": host.get("name", ""),
        "email": host.get("email", ""),
        "phone": host.get("phone", ""),
        "organization": host.get("organization", ""),
        "account_holder_name": host.get("account_holder_name", ""),
        "upi_id": host.get("upi_id", ""),
        "upi_qr_image": host.get("upi_qr_image", ""),
        "role": "host",
    }


def create_notification(email, title, message, recipient_type="user", extra=None):
    email = clean_lower(email)
    if not email:
        return None

    doc = {
        "notification_id": f"NOT{10001 + notifications_collection.count_documents({})}",
        "email": email,
        "recipient_id": email,
        "recipient_type": recipient_type,
        "title": title,
        "message": message,
        "status": "Unread",
        "unread": True,
        "created_at": now(),
    }
    if isinstance(extra, dict):
        doc.update(extra)

    # Avoid duplicate dropdown items when the same backend/admin action is
    # clicked twice or when the frontend echoes the same action after refresh.
    dedupe_query = {
        "email": email,
        "recipient_type": recipient_type,
        "title": title,
        "message": message,
    }
    for key in ["type", "related_event_id", "registration_id", "payment_id", "refund_id", "action_id", "client_action_id"]:
        if doc.get(key):
            dedupe_query[key] = doc.get(key)
    existing = notifications_collection.find_one(dedupe_query, {"_id": 1})
    if existing:
        return str(existing.get("_id"))

    result = notifications_collection.insert_one(doc)
    return str(result.inserted_id)


def make_event_id():
    return f"EVT{1001 + events_collection.count_documents({})}"


def make_reg_id():
    return f"REG{10001 + registrations_collection.count_documents({})}"


def money_fields(event_price, tickets):
    event_amount = max(0, int(float(event_price or 0))) * max(1, int(float(tickets or 1)))
    user_fee = PLATFORM_USER_FEE if event_amount > 0 else 0
    organiser_fee = PLATFORM_ORGANISER_FEE if event_amount > 0 else 0
    amount_paid = event_amount + user_fee
    admin_revenue = user_fee + organiser_fee
    organiser_payout = max(event_amount - organiser_fee, 0)
    return {
        "event_amount": event_amount,
        "event_price": int(float(event_price or 0)),
        "user_service_fee": user_fee,
        "organizer_commission": organiser_fee,
        "organiser_commission": organiser_fee,
        "amount_paid": amount_paid,
        "amount": amount_paid,
        "admin_revenue": admin_revenue,
        "organizer_payout": organiser_payout,
        "organiser_payout": organiser_payout,
    }


def event_completion_label(event):
    if event_is_completed(event):
        return "Completed"
    status = clean(event.get("status") or event.get("approval_status") or "Upcoming")
    return status or "Upcoming"


def enrich_organiser_registration_row(row, event=None):
    """Add organiser-facing payout fields to one registration row.

    Users pay Orbit first. Organisers only see their earned payout, whether it is
    still pending from Orbit, or whether Orbit has settled it after completion.
    """
    row = dict(row or {})
    event = event or {}

    try:
        tickets = max(1, int(float(row.get("tickets") or 1)))
    except Exception:
        tickets = 1

    try:
        event_price = int(float(row.get("event_price") or event.get("price") or 0))
    except Exception:
        event_price = 0

    event_amount = row.get("event_amount")
    if event_amount in [None, ""]:
        event_amount = event_price * tickets
    try:
        event_amount = int(float(event_amount or 0))
    except Exception:
        event_amount = 0

    amount_paid = row.get("amount_paid") or row.get("amount") or 0
    try:
        amount_paid = int(float(amount_paid or 0))
    except Exception:
        amount_paid = 0

    organiser_fee = row.get("organiser_commission", row.get("organizer_commission"))
    if organiser_fee in [None, ""]:
        organiser_fee = PLATFORM_ORGANISER_FEE if event_amount > 0 else 0
    try:
        organiser_fee = int(float(organiser_fee or 0))
    except Exception:
        organiser_fee = 0

    organiser_payout = row.get("organiser_payout", row.get("organizer_payout"))
    if organiser_payout in [None, ""]:
        organiser_payout = max(event_amount - organiser_fee, 0)
    try:
        organiser_payout = int(float(organiser_payout or 0))
    except Exception:
        organiser_payout = 0

    status_text = clean_lower(row.get("status") or row.get("registration_status") or row.get("payment_status"))
    cancelled = any(word in status_text for word in ["cancel", "refund"])
    settled = "settled" in clean_lower(row.get("settlement_status") or row.get("payment_status"))

    if amount_paid <= 0 or organiser_payout <= 0:
        payout_status = "Free Event"
        pending_from_orbit = 0
        paid_by_orbit = 0
    elif cancelled:
        payout_status = "Refund / Cancelled"
        pending_from_orbit = 0
        paid_by_orbit = 0
    elif settled:
        payout_status = "Paid by Orbit"
        pending_from_orbit = 0
        paid_by_orbit = organiser_payout
    else:
        payout_status = "Pending from Orbit"
        pending_from_orbit = organiser_payout
        paid_by_orbit = 0

    row.update({
        "event_price": event_price,
        "event_amount": event_amount,
        "amount_paid": amount_paid,
        "amount": amount_paid,
        "organiser_commission": organiser_fee,
        "organizer_commission": organiser_fee,
        "organiser_payout": organiser_payout,
        "organizer_payout": organiser_payout,
        "organiser_earning": organiser_payout,
        "payout_status": payout_status,
        "pending_from_orbit": pending_from_orbit,
        "paid_by_orbit": paid_by_orbit,
        "event_completion_status": event_completion_label(event),
    })
    return row


def organiser_payout_summary(attendees):
    rows = [enrich_organiser_registration_row(a) for a in attendees]
    paid_rows = [r for r in rows if int(float(r.get("amount_paid") or 0)) > 0 and clean_lower(r.get("payout_status")) not in ["refund / cancelled"]]
    return {
        "orbit_collected": sum(int(float(r.get("amount_paid") or 0)) for r in paid_rows),
        "organiser_total_earning": sum(int(float(r.get("organiser_payout") or 0)) for r in paid_rows),
        "pending_from_orbit": sum(int(float(r.get("pending_from_orbit") or 0)) for r in paid_rows),
        "paid_by_orbit": sum(int(float(r.get("paid_by_orbit") or 0)) for r in paid_rows),
    }


def serialize_doc(doc):
    if not doc:
        return doc
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    for key, val in list(out.items()):
        if isinstance(val, datetime):
            out[key] = safe_dt(val)
    return out


def clean_mongo_update(data):
    """Return a copy that is safe to use inside MongoDB $set updates.

    MongoDB's _id field is immutable. PyMongo also mutates inserted dictionaries
    by adding _id, so any full document copied into $set must be cleaned first.
    """
    if not isinstance(data, dict):
        return data
    cleaned = {}
    for key, value in data.items():
        if key == "_id":
            continue
        if isinstance(value, dict):
            cleaned[key] = clean_mongo_update(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_mongo_update(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value
    return cleaned


def active_registration_query(event_id=None, user_email=None):
    query = {
        "$and": [
            {"status": {"$not": {"$regex": "^(cancelled|cancelled_by_user|cancelled_by_organiser|cancelled_by_organizer|refunded|rejected|inactive|deleted)$", "$options": "i"}}},
            {"registration_status": {"$not": {"$regex": "^(cancelled|cancelled_by_user|cancelled_by_organiser|cancelled_by_organizer|refunded|rejected|inactive|deleted)$", "$options": "i"}}},
        ]
    }
    if event_id:
        query["$and"].append({"event_id": event_id})
    if user_email:
        query["$and"].append({"user_email": clean_lower(user_email)})
    return query


def active_ticket_count(event_id):
    total = 0
    for reg in registrations_collection.find(active_registration_query(event_id), {"tickets": 1}):
        try:
            total += max(1, int(float(reg.get("tickets") or 1)))
        except Exception:
            total += 1
    return total


def sync_event_registration_counts(event_id):
    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return {"registered_count": 0, "available_slots": 0, "slots_left": 0}
    total_slots = int(float(event.get("total_slots") or 0))
    registered_count = active_ticket_count(event_id)
    available_slots = max(total_slots - registered_count, 0)
    events_collection.update_one(
        {"event_id": event_id},
        {"$set": {
            "registered_count": registered_count,
            "available_slots": available_slots,
            "slots_left": available_slots,
            "updated_at": now(),
        }},
    )
    return {"registered_count": registered_count, "available_slots": available_slots, "slots_left": available_slots}

def repair_active_registrations(event_id=None, user_email=None):
    """Keep active registration counters synced.

    Orbit allows one active booking per user per event. Cancelled bookings stay
    in history, but they do not count toward active registrations or slots.
    """
    query = active_registration_query(event_id, user_email)
    rows = list(registrations_collection.find(query, {"event_id": 1}))
    touched_event_ids = {clean(r.get("event_id")) for r in rows if clean(r.get("event_id"))}
    if event_id:
        touched_event_ids.add(event_id)
    for eid in touched_event_ids:
        sync_event_registration_counts(eid)



def registration_sort_key_for_repair(reg):
    value = reg.get("registered_at") or reg.get("created_at") or datetime.min
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return datetime.min


def repair_duplicate_active_registrations(event_id=None, user_email=None):
    """Keep only one ACTIVE registration per user per event.

    Old duplicate rows are not deleted; they are marked duplicate_inactive so
    profile, organiser and ADP do not count/display them as real active bookings.
    This prevents one real registration from appearing twice in registrations
    and payments.
    """
    query = active_registration_query(event_id=event_id, user_email=user_email)
    rows = list(registrations_collection.find(query))
    groups = {}
    for row in rows:
        key = (clean_lower(row.get("user_email") or row.get("email")), clean(row.get("event_id")))
        if not key[0] or not key[1]:
            continue
        groups.setdefault(key, []).append(row)

    touched = set()
    for (_email, eid), items in groups.items():
        if len(items) <= 1:
            continue
        items.sort(key=registration_sort_key_for_repair, reverse=True)
        keep = items[0]
        keep_id = clean(keep.get("registration_id"))
        for dup in items[1:]:
            reg_id = clean(dup.get("registration_id"))
            if not reg_id:
                continue
            update = {
                "status": "duplicate_inactive",
                "registration_status": "duplicate_inactive",
                "payment_status": "Duplicate inactive",
                "settlement_status": "Duplicate inactive",
                "duplicate_of": keep_id,
                "updated_at": now(),
            }
            registrations_collection.update_one({"registration_id": reg_id}, {"$set": update})
            payments_collection.update_one({"registration_id": reg_id}, {"$set": update})
            touched.add(eid)
    if event_id:
        touched.add(event_id)
    for eid in touched:
        sync_event_registration_counts(eid)
    return len(touched)

def ensure_registered_events_from_active_regs(email):
    """Rebuild one user's active registered_events list from active registration rows."""
    email = clean_lower(email)
    if not email:
        return []
    repair_duplicate_active_registrations(user_email=email)
    repair_active_registrations(user_email=email)
    active_event_ids = []
    for reg in registrations_collection.find(active_registration_query(user_email=email), {"event_id": 1}):
        eid = clean(reg.get("event_id"))
        if eid and eid not in active_event_ids:
            active_event_ids.append(eid)
    users_collection.update_one(email_query(email), {"$set": {"registered_events": active_event_ids, "updated_at": now()}})
    return active_event_ids



def registration_event_rows_for_user(email, user=None, active_only=True):
    """Return one dashboard/event-history row per registration booking.

    Active results contain only one booking per event for a user. Cancelled rows
    remain available in full history when active_only=False.
    """
    email = clean_lower(email)
    if not email:
        return []

    if active_only:
        query = active_registration_query(user_email=email)
    else:
        query = {"user_email": email}

    regs = list(registrations_collection.find(query).sort([("registered_at", -1), ("created_at", -1)]))
    event_ids = [clean(r.get("event_id")) for r in regs if clean(r.get("event_id"))]
    event_map = {e.get("event_id"): e for e in events_collection.find({"event_id": {"$in": event_ids}})} if event_ids else {}

    rows = []
    for index, reg in enumerate(regs, start=1):
        event_id = clean(reg.get("event_id"))
        base_event = event_map.get(event_id, {})
        event_row = event_with_match(base_event, user) if base_event else format_event({
            "event_id": event_id,
            "title": reg.get("event_title", "Event"),
            "event_date": reg.get("event_date", ""),
            "start_date": reg.get("event_date", ""),
            "organiser_email": reg.get("organiser_email", ""),
            "organiser_name": reg.get("organiser_name", ""),
            "price": reg.get("event_price") or reg.get("event_amount") or 0,
            "status": "active",
            "approval_status": "approved",
        })

        registration_id = clean(reg.get("registration_id")) or f"REGROW{index}"
        tickets = 1
        try:
            tickets = max(1, int(float(reg.get("tickets") or 1)))
        except Exception:
            tickets = 1

        event_row.update({
            "registration_id": registration_id,
            "booking_id": registration_id,
            "event_instance_id": f"{event_id}::{registration_id}",
            "registration_status": reg.get("registration_status") or reg.get("status") or "registered",
            "status": reg.get("status") or reg.get("registration_status") or event_row.get("status", "registered"),
            "tickets": tickets,
            "booking_tickets": tickets,
            "amount": reg.get("amount") or reg.get("amount_paid") or 0,
            "amount_paid": reg.get("amount_paid") or reg.get("amount") or 0,
            "payment_status": reg.get("payment_status") or event_row.get("payment_status", ""),
            "refund_status": reg.get("refund_status", ""),
            "refund_requested": bool(reg.get("refund_requested", False)),
            "cancellation_reason": reg.get("cancellation_reason") or reg.get("cancel_reason") or "",
            "registered_at": safe_dt(reg.get("registered_at") or reg.get("created_at")),
            "created_at": safe_dt(reg.get("created_at")),
            "cancelled_at": safe_dt(reg.get("cancelled_at")),
        })
        rows.append(event_row)
    return rows


def registration_growth_series(email):
    """Cumulative count for ACTIVE booking rows only.

    Cancelled registration rows stay in Event History, but they should not appear
    in the dashboard growth chart, calendar, or active registered cards.
    Repeated active bookings for the same event are counted separately.
    """
    rows = list(registrations_collection.find(
        active_registration_query(user_email=clean_lower(email)),
        {"registered_at": 1, "created_at": 1}
    ).sort([("registered_at", 1), ("created_at", 1)]))
    return list(range(1, len(rows) + 1))

def cleanup_orphaned_registrations():
    """Remove registrations where the user no longer exists (data isolation fix).
    
    This prevents new users from seeing cancellation history from previous accounts
    that used the same email. Runs on startup to clean up any stale test/smoke data.
    """
    try:
        # Get all unique user emails from registrations
        all_regs = list(registrations_collection.find({}, {"user_email": 1}))
        user_emails = set()
        for reg in all_regs:
            email = clean_lower(reg.get("user_email") or "")
            if email:
                user_emails.add(email)
        
        # For each email, check if user exists; if not, delete their registrations
        deleted_count = 0
        for email in user_emails:
            if not find_user(email):
                result = registrations_collection.delete_many({"user_email": email})
                deleted_count += result.deleted_count
        
        if deleted_count > 0:
            print(f"🧹 Cleaned up {deleted_count} orphaned registrations from {len(user_emails)} emails")
    except Exception as e:
        print(f"⚠️ Orphaned registration cleanup skipped: {e}")

def ensure_orbit_indexes():
    """Create useful indexes without blocking the app if old duplicate data exists."""
    try:
        registrations_collection.create_index([("event_id", 1), ("user_email", 1)])
        registrations_collection.create_index([("user_email", 1), ("status", 1)])
    except Exception as e:
        print("⚠️ Registration index setup skipped:", e)
    # App-level logic blocks duplicate active registrations. A unique partial
    # index is intentionally not forced here because old local databases may
    # already contain historical duplicate rows.



try:
    cleanup_orphaned_registrations()
    ensure_orbit_indexes()
except Exception as e:
    print("⚠️ Orbit initialization failed:", e)


def format_event(e):
    e = dict(e)
    e.pop("_id", None)

    e.setdefault("event_id", "")
    e.setdefault("title", "")
    e.setdefault("description", "")
    e.setdefault("category", "")
    e.setdefault("location", "")
    e.setdefault("exact_address", "")
    e.setdefault("venue_address", "")
    e.setdefault("address", "")
    if not e.get("exact_address"):
        e["exact_address"] = e.get("venue_address") or e.get("address") or ""
    e.setdefault("event_date", "")
    e.setdefault("event_time", "")
    e.setdefault("start_date", e.get("event_date", ""))
    e.setdefault("end_date", e.get("event_date", ""))
    e.setdefault("start_time", e.get("event_time", ""))
    e.setdefault("end_time", e.get("event_time", ""))
    if not e.get("event_date"):
        e["event_date"] = e.get("start_date", "")
    if not e.get("event_time"):
        e["event_time"] = e.get("start_time", "")
    e.setdefault("price", 0)
    e.setdefault("total_slots", 0)
    e.setdefault("registered_count", 0)
    # Missing status should be safe by default. Do not auto-treat unknown events as Approved.
    e["status"] = clean(e.get("status") or e.get("approval_status") or "Pending")
    e["approval_status"] = clean(e.get("approval_status") or e.get("status") or "Pending")
    e.setdefault("payment_status", "Free" if int(float(e.get("price") or 0)) == 0 else "On Hold")
    e.setdefault("saved_count", 0)
    e.setdefault("interested_count", 0)
    e.setdefault("interest_tags", [])
    e.setdefault("focus_area", [])
    e.setdefault("images", [])
    e.setdefault("upi_id", "")
    e.setdefault("qr_image", "")
    # Keep organiser identity compatible across older/newer files.
    # Some frontend versions stored organizer_email/host_email, while the
    # latest organiser/admin pages filter by organiser_email.
    if not e.get("organiser_email"):
        e["organiser_email"] = clean_lower(e.get("organizer_email") or e.get("host_email") or e.get("email"))
    if not e.get("organizer_email"):
        e["organizer_email"] = e.get("organiser_email", "")
    if not e.get("host_email"):
        e["host_email"] = e.get("organiser_email", "")
    if not e.get("organiser_name"):
        e["organiser_name"] = e.get("organizer_name") or e.get("host_name") or ""
    if not e.get("organizer_name"):
        e["organizer_name"] = e.get("organiser_name", "")
    e.setdefault("organiser_name", "")
    e.setdefault("organiser_email", "")
    e.setdefault("organization", "")
    e.setdefault("contact_email", e.get("organiser_email", ""))
    e.setdefault("contact_phone", "")
    e.setdefault("benefits", "")
    e.setdefault("inclusions", e.get("benefits", ""))
    e.setdefault("event_photos", [])
    e.setdefault("banner_image", "")
    e.setdefault("poster_image", "")
    e.setdefault("reference_photos", [])
    e.setdefault("payment_mode", "")
    e.setdefault("payment_note", "")
    e.setdefault("age_groups", [])
    e.setdefault("personality_tags", [])
    e.setdefault("meeting_people_tags", [])
    e.setdefault("improvement_areas", [])
    e.setdefault("challenge_types", [])
    e.setdefault("professional_types", [])
    e.setdefault("cancel_reason", "")
    e.setdefault("refund_all_requested", False)
    e.setdefault("cancelled_at", "")
    e.setdefault("available_slots", 0)

    try:
        e["price"] = int(float(e.get("price") or 0))
    except Exception:
        e["price"] = 0

    try:
        e["total_slots"] = int(float(e.get("total_slots") or 0))
    except Exception:
        e["total_slots"] = 0

    try:
        e["registered_count"] = int(float(e.get("registered_count") or 0))
    except Exception:
        e["registered_count"] = 0

    # Recalculate from ACTIVE registrations only.
    # Cancelled/rejected/refunded attempts must never keep increasing people-going or slots.
    try:
        e["registered_count"] = active_ticket_count(e.get("event_id"))
    except Exception:
        pass

    e["interest_tags"] = normalize_list(e.get("interest_tags"))
    e["focus_area"] = normalize_list(e.get("focus_area"))
    e["images"] = normalize_list(e.get("images"))
    e["reference_photos"] = normalize_list(e.get("reference_photos"))
    e["event_photos"] = normalize_list(e.get("event_photos"))
    if e["event_photos"] and not e["reference_photos"]:
        e["reference_photos"] = e["event_photos"]
    if not e["event_photos"] and e["reference_photos"]:
        e["event_photos"] = e["reference_photos"]
    if not e["reference_photos"] and e.get("poster_image"):
        e["reference_photos"] = [e.get("poster_image")]
        e["event_photos"] = e["reference_photos"]
    e["age_groups"] = normalize_list(e.get("age_groups"))
    e["personality_tags"] = normalize_list(e.get("personality_tags"))
    e["meeting_people_tags"] = normalize_list(e.get("meeting_people_tags"))
    e["improvement_areas"] = normalize_list(e.get("improvement_areas"))
    e["challenge_types"] = normalize_list(e.get("challenge_types"))
    e["professional_types"] = normalize_list(e.get("professional_types"))
    if not e.get("banner_image") and e["images"]:
        e["banner_image"] = e["images"][0]
    if not e.get("poster_image") and e["reference_photos"]:
        e["poster_image"] = e["reference_photos"][0]
    if not e.get("poster_image") and len(e["images"]) > 1:
        e["poster_image"] = e["images"][1]
    e["slots_left"] = max(e["total_slots"] - e["registered_count"], 0)
    e["available_slots"] = e["slots_left"]
    # Canonical owner repair: some older rows accidentally saved public
    # contact_email as organiser_email. host_email is the dashboard owner.
    if e.get("host_email"):
        owner_email = clean_lower(e.get("host_email"))
        e["organiser_email"] = owner_email
        e["organizer_email"] = owner_email
    if not e.get("contact_email"):
        e["contact_email"] = e.get("organiser_email", "")
    if not e.get("inclusions"):
        e["inclusions"] = e.get("benefits", "")
    e["created_at"] = safe_dt(e.get("created_at"))
    e["updated_at"] = safe_dt(e.get("updated_at"))
    e["cancelled_at"] = safe_dt(e.get("cancelled_at"))

    # Include admin QR image for paid events
    if int(e.get("price") or 0) > 0:
        admin_settings = get_orbit_payment_settings()
        if admin_settings.get("qr_image"):
            e["admin_qr_image"] = admin_settings.get("qr_image")

    return e


def _matchable_words(value):
    """Normalize a single value/list into comparable words."""
    return {clean_lower(x) for x in normalize_list(value) if clean_lower(x)}


def _loose_contains(left, right):
    left = clean_lower(left)
    right = clean_lower(right)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _any_loose_match(user_values, event_values):
    for u in user_values:
        for e in event_values:
            if _loose_contains(u, e):
                return True
    return False


def _all_selected(values):
    return any(v in {"all", "all types", "everyone", "anyone"} for v in values)


def calculate_match_details(profile, event):
    """Calculate event match percentage based on user profile and event attributes.
    
    Compares:
    - User: Interests, Improvement Areas, Goals
    - Event: Categories, Tags, Audience Preferences
    
    Returns match_percent 0-100%. Events 50-100% are shown to users.
    Never shows 0% matches - displays as "No Match" and hides from results.
    """
    profile = profile or {}
    event = format_event(event)

    # Extract user profile data
    user_interests = _matchable_words(profile.get("interests"))
    user_improvement = _matchable_words(profile.get("improvement"))
    user_goals = _matchable_words(profile.get("goals") or profile.get("preferences") or "")
    
    # Extract event data - categories and tags
    event_category = _matchable_words(event.get("category"))
    event_tags = _matchable_words(event.get("interest_tags"))
    event_focus_areas = _matchable_words(event.get("focus_area")) | _matchable_words(event.get("improvement_areas"))
    event_audience = _matchable_words(event.get("audience_preferences") or "")
    
    # Additional user preferences for richer matching
    user_age = clean_lower(profile.get("age_group") or profile.get("ageGroup"))
    event_ages = _matchable_words(event.get("age_groups"))
    
    user_city = clean_lower(profile.get("city") or profile.get("location"))
    event_city = clean_lower(event.get("location"))
    
    # Calculate component match percentages
    match_scores = []
    reasons = []
    
    # 1. Interest/Category matching (primary - 40 points max)
    all_event_categories = event_category | event_tags
    if user_interests and all_event_categories:
        if _any_loose_match(user_interests, all_event_categories):
            match_scores.append(40)
            reasons.append("interest")
        else:
            match_scores.append(0)
    else:
        match_scores.append(15)  # Partial credit if one side is empty
    
    # 2. Improvement/Goal alignment (secondary - 35 points max)
    if user_improvement or user_goals:
        event_goals = event_focus_areas | event_audience
        combined_user_goals = user_improvement | user_goals
        if combined_user_goals and event_goals:
            if _any_loose_match(combined_user_goals, event_goals):
                match_scores.append(35)
                reasons.append("goal")
            else:
                match_scores.append(0)
        else:
            match_scores.append(12)  # Partial credit
    else:
        match_scores.append(10)  # Some baseline if user hasn't set goals
    
    # 3. Location matching (10 points)
    if user_city and event_city:
        if user_city == event_city:
            match_scores.append(10)
            reasons.append("location")
        else:
            match_scores.append(0)
    else:
        match_scores.append(5)  # Partial credit
    
    # 4. Age group matching (10 points)
    if event_ages:
        if _all_selected(event_ages) or (user_age and _any_loose_match({user_age}, event_ages)):
            match_scores.append(10)
            reasons.append("age")
        else:
            match_scores.append(0)
    else:
        match_scores.append(5)  # Partial credit
    
    # 5. Additional demographic matching (5 points)
    user_personality = clean_lower(profile.get("personality"))
    event_personalities = _matchable_words(event.get("personality_tags"))
    if event_personalities:
        if _all_selected(event_personalities) or (user_personality and _any_loose_match({user_personality}, event_personalities)):
            match_scores.append(5)
            reasons.append("personality")
        else:
            match_scores.append(0)
    else:
        match_scores.append(2)  # Small baseline
    
    # Calculate final percentage
    total_possible = 100
    total_score = sum(match_scores)
    match_percent = int((total_score / total_possible) * 100)
    match_percent = max(0, min(100, match_percent))
    
    # Determine recommendation and badge
    is_recommended = match_percent >= 50
    
    if match_percent >= 90:
        match_label = "Perfect Match"
        badge = "perfect"
    elif match_percent >= 80:
        match_label = "Great Match"
        badge = "great"
    elif match_percent >= 70:
        match_label = "Good Match"
        badge = "good"
    elif match_percent >= 50:
        match_label = "Fair Match"
        badge = "fair"
    else:
        match_label = "No Match"
        badge = "no_match"
    
    return {
        "match_percent": match_percent,
        "is_recommended": is_recommended,
        "match_reasons": reasons,
        "match_label": match_label,
        "match_badge": badge,
    }


def calculate_match(profile, event):
    return calculate_match_details(profile, event).get("match_percent", 0)


def event_with_match(event, user=None):
    e = format_event(event)
    profile = user.get("profile", {}) if user else {}
    details = calculate_match_details(profile, e)
    e.update(details)
    return e


def personalized_events_for_user(user):
    """Return every approved upcoming event for Explore, sorted by match.

    Earlier this function HID events when they did not match the user's setup at
    70%+. That made newly approved events look missing in profile.html. Explore
    must show approved upcoming events; personalization should rank them, not
    delete them. Suggestions can still prefer high-match events separately.
    """
    events = [
        event_with_match(e, user)
        for e in events_collection.find(user_visible_event_query())
        if event_is_user_visible(e)
    ]
    events.sort(key=lambda x: (x.get("is_recommended", False), x.get("match_percent", 0), x.get("created_at") or ""), reverse=True)
    return events


def user_visible_event_query():
    """Only organiser-created, approved/active events should be considered for user Explore.

    Be tolerant of older rows that saved owner email as host_email/organizer_email.
    """
    return {
        "$and": [
            {"$or": [
                {"status": {"$regex": "^(approved|active|published|live)$", "$options": "i"}},
                {"approval_status": {"$regex": "^approved$", "$options": "i"}},
            ]},
            {"status": {"$not": {"$regex": "^(cancelled|completed|rejected|inactive|deleted)$", "$options": "i"}}},
            {"approval_status": {"$not": {"$regex": "^(cancelled|completed|rejected|pending|deleted)$", "$options": "i"}}},
            {"$or": [
                {"organiser_email": {"$exists": True, "$ne": ""}},
                {"organizer_email": {"$exists": True, "$ne": ""}},
                {"host_email": {"$exists": True, "$ne": ""}},
                {"owner_email": {"$exists": True, "$ne": ""}},
            ]},
        ]
    }


def event_is_user_visible(event):
    event = event or {}
    owner_email = event_owner_email(event)
    if not owner_email or not find_host(owner_email):
        return False
    status = clean_lower(event.get("status") or "")
    approval = clean_lower(event.get("approval_status") or "")
    blocked = {"cancelled", "completed", "rejected", "inactive", "past"}
    if status in blocked or approval in blocked or approval == "pending":
        return False
    if not (status in {"approved", "active", "published", "live"} or approval == "approved"):
        return False
    if not clean(event.get("organiser_email") or event.get("organizer_email") or event.get("host_email")):
        return False
    title = clean_lower(event.get("title") or event.get("event_title"))
    if title in {"sample event", "fake event", "demo event"} or title.startswith("sample ") or title.startswith("fake "):
        return False
    return not event_is_completed(event)


def event_accepts_registration(event):
    event = event or {}
    return event_is_user_visible(event) and format_event(event).get("slots_left", 0) > 0




def parse_event_datetime(event):
    """Return the best end datetime for an event, or None if date/time cannot be parsed."""
    event = event or {}
    date_value = clean(event.get("end_date") or event.get("event_date") or event.get("start_date") or event.get("date"))
    time_value = clean(event.get("end_time") or event.get("event_time") or event.get("start_time") or event.get("time"))
    if not date_value:
        return None

    if date_value:
        if "to" in date_value.lower():
            date_parts = [p.strip() for p in re.split(r"\s*(?:to)\s*", date_value, maxsplit=1) if p.strip()]
            date_value = date_parts[-1] if len(date_parts) > 1 else date_value
        elif re.search(r"\s+[-–—]\s+", date_value):
            date_parts = [p.strip() for p in re.split(r"\s*[-–—]\s*", date_value) if p.strip()]
            date_value = date_parts[-1] if len(date_parts) > 1 else date_value
    if time_value:
        if "to" in time_value.lower() or re.search(r"\s+[-–—]\s+", time_value):
            time_parts = [p.strip() for p in re.split(r"\s*(?:[-–—]|to)\s*", time_value) if p.strip()]
            time_value = time_parts[-1] if len(time_parts) > 1 else time_value

    date_formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"]
    time_formats = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"]

    parsed_date = None
    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(date_value, fmt).date()
            break
        except Exception:
            pass

    if parsed_date is None:
        try:
            parsed_date = datetime.fromisoformat(date_value.replace("Z", "+00:00")).date()
        except Exception:
            return None

    parsed_time = None
    if time_value:
        compact_time = time_value.strip().upper().replace(".", "")
        for fmt in time_formats:
            try:
                parsed_time = datetime.strptime(compact_time, fmt).time()
                break
            except Exception:
                pass
    if parsed_time is None:
        parsed_time = datetime.max.time().replace(microsecond=0)

    return datetime.combine(parsed_date, parsed_time)


def event_is_completed(event):
    """True when the event's end date/time has passed or it is explicitly completed."""
    status = clean_lower((event or {}).get("status") or (event or {}).get("approval_status") or (event or {}).get("completion_status"))
    if status == "completed":
        return True
    if status in ["cancelled", "rejected", "pending"]:
        return False
    completed_at = (event or {}).get("completed_at")
    if completed_at:
        try:
            completed_dt = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
            if completed_dt.tzinfo is not None:
                completed_dt = completed_dt.astimezone().replace(tzinfo=None)
            if completed_dt <= now():
                return True
        except Exception:
            pass
    end_dt = parse_event_datetime(event)
    return bool(end_dt and end_dt < now())


def review_summary_for_event(event_id):
    reviews = list(reviews_collection.find({"event_id": event_id}))
    total = len(reviews)
    rating_sum = sum(int(float(r.get("rating") or 0)) for r in reviews)
    average = round(rating_sum / total, 2) if total else 0
    return {"event_id": event_id, "total_ratings": total, "average_rating": average}


def auto_ban_organiser_for_bad_reviews(organiser_email, threshold=50):
    organiser_email = clean_lower(organiser_email)
    if not organiser_email:
        return False
    bad_reviews = reviews_collection.count_documents({
        "organiser_email": organiser_email,
        "rating": {"$in": [1, "1", 1.0, "1.0"]},
    })
    if bad_reviews < threshold:
        return False
    update = {
        "banned": True,
        "blocked": True,
        "account_status": "Banned",
        "status": "Banned",
        "updated_at": now(),
    }
    hosts_collection.update_one(email_query(organiser_email), {"$set": update})
    return True


def mark_completed_events():
    """Mark past approved events completed and notify users/organisers once."""
    candidates = events_collection.find({"completion_notified": {"$ne": True}})

    for event in candidates:
        try:
            if clean_lower(event.get("status") or event.get("approval_status")) in ["cancelled", "rejected", "pending"]:
                continue
            if not event_is_completed(event):
                continue
            event_id = event.get("event_id")
            title = event.get("title", "your event")
            events_collection.update_one({"event_id": event_id}, {"$set": {
                "status": "Completed",
                "approval_status": event.get("approval_status", "Approved"),
                "completed_at": now(),
                "completion_notified": True,
                "updated_at": now(),
            }})

            regs = registrations_collection.find({
                "event_id": event_id,
                "status": {"$nin": ["cancelled_by_user", "cancelled_by_organiser", "refunded"]},
            })
            for reg in regs:
                user_email = clean_lower(reg.get("user_email"))
                if user_email:
                    create_notification(
                        user_email,
                        "How was your event?",
                        f"{title} is completed. Please rate your experience from 1 to 5.",
                        "user",
                        {"type": "rating_request", "action_type": "rate_event", "related_event_id": event_id},
                    )

            create_notification(
                event.get("organiser_email"),
                "Event completed",
                f"{title} is completed. Ratings will appear in your reviews section.",
                "host",
                {"type": "event_completed", "related_event_id": event_id},
            )
        except Exception as e:
            print("Completion check failed:", e)

@app.route("/")
@app.route("/index.html")
def index():
    return render_page("index.html")


@app.route("/host")
@app.route("/host.html")
def host():
    return render_page("host.html")


@app.route("/signup.html")
def signup_page():
    return render_page("signup.html")


@app.route("/login.html")
def login_page():
    return render_page("login.html")


@app.route("/setup.html")
@app.route("/setup")
@app.route("/setup.up")
def setup_page():
    return render_page("setup.html")


@app.route("/profile.html")
def profile_page():
    # IMPORTANT ROLE-SEPARATION FIX:
    # Flask has only one browser session cookie. During testing you often keep
    # organiser.html open in one tab and login to profile.html in another tab.
    # If this route redirects based on the shared cookie, the organiser tab gets
    # forced into profile.html. Therefore page routes must only render pages;
    # API routes below verify the correct account data.
    return render_page("profile.html")


@app.route("/organiser.html")
@app.route("/organizer.html")
def organiser_page():
    # Same reason as profile_page(): do not redirect organiser.html just because
    # the current shared Flask session was changed by profile.html. The organiser
    # frontend keeps its own saved host email and calls organiser-data/<email>.
    return render_page("organiser.html")


@app.route("/hsign.html", methods=["GET", "POST"])
def hsign_html_page_or_post():
    if request.method == "POST":
        return host_signup()
    return render_page("hsign.html")


@app.route("/hlog.html", methods=["GET", "POST"])
def hlog_page_or_post():
    if request.method == "POST":
        return host_login()
    return render_page("hlog.html")


@app.route("/forgot.html")
def forgot_page():
    return render_page("forgot.html")


@app.route("/hfog.html")
def hfog_page():
    return render_page("hfog.html")


@app.route("/signup", methods=["POST"])
@app.route("/user-signup", methods=["POST"])
@app.route("/api/signup", methods=["POST"])
def signup():
    data = get_data()

    name = clean(data.get("name") or data.get("fullName") or data.get("fullname"))
    email = clean_lower(data.get("email"))
    password = clean(data.get("password"))
    confirm = clean(data.get("confirmPassword") or data.get("confirm_password") or password)

    if not name:
        return jsonify(success=False, error="Name is required"), 400

    if not valid_email(email):
        return jsonify(success=False, error="Enter a valid email address"), 400

    if not strong_password(password):
        return jsonify(success=False, error="Password must be at least 8 characters"), 400

    if password != confirm:
        return jsonify(success=False, error="Passwords do not match"), 400

    if find_user(email):
        return jsonify(success=False, error="User already exists. Please login."), 400

    if find_host(email):
        return jsonify(success=False, error="This email is already used as host."), 400

    # IMPORTANT: Clean up any old registrations/payments/refunds from previous accounts
    # This prevents new users from seeing old cancellation history and data from other users.
    # When an email is reused, the new account gets a fresh start with no old data.
    try:
        registrations_collection.delete_many({"$or": [{"user_email": email}, {"email": email}]})
        payments_collection.delete_many({"$or": [{"user_email": email}, {"email": email}]})
        refunds_collection.delete_many({"$or": [{"user_email": email}, {"email": email}]})
        reviews_collection.delete_many({"$or": [{"user_email": email}, {"email": email}, {"reviewer_email": email}]})
        notifications_collection.delete_many({"$or": [{"email": email}, {"user_email": email}]})
    except Exception as e:
        print(f"⚠️ Signup cleanup warning (non-fatal): {e}")

    user_doc = {
        "name": name,
        "email": email,
        "password": generate_password_hash(password),
        "role": "user",
        "profile": {},
        "saved_events": [],
        "interested_events": [],
        "registered_events": [],
        "onboarding_complete": False,
        "created_at": now(),
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }

    users_collection.insert_one(user_doc)

    set_active_user_session(email)
    # One-time welcome mode for profile.html. This survives setup completion and
    # does not depend on browser localStorage/query strings.
    session["profile_welcome_mode"] = "signup"

    create_notification(email, "Welcome to Orbit", "Complete setup to unlock personalized events.")

    return jsonify(
        success=True,
        message="Signup successful",
        user=public_user(user_doc),
        email=email,
        redirect=f"/setup.html?email={email}&mode=signup",
        mode="signup",
        welcome_text=f"Welcome, {name}",
    ), 201


@app.route("/login", methods=["POST"])
@app.route("/user-login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = get_data()

    email = clean_lower(data.get("email"))
    password = clean(data.get("password"))

    user = find_user(email)

    if not user:
        return jsonify(success=False, error="User not found. Please sign up first."), 404

    if user.get("banned") or user.get("is_banned"):
        return jsonify(success=False, error="Your account has been banned. Please contact support."), 403

    if not check_password_hash(user.get("password", ""), password):
        return jsonify(success=False, error="Invalid password"), 401

    set_active_user_session(user["email"])
    session["profile_welcome_mode"] = "login"

    return jsonify(
        success=True,
        message="Login successful",
        user=public_user(user),
        redirect=f"/profile.html?email={email}&mode=login" if user.get("onboarding_complete") else f"/setup.html?email={email}&mode=login",
        mode="login",
        welcome_text=f"Welcome back, {user.get('name', 'User')}",
    )


@app.route("/logout", methods=["GET", "POST"])
def logout():
    clear_user_session_only()
    return jsonify(success=True, redirect="/login.html")


@app.route("/api/me")
def me():
    role = clean_lower(session.get("role") or session.get("user_type"))
    email = clean_lower(session.get("email") or session.get("user_email") or session.get("host_email"))

    if not role or not email:
        return jsonify(success=False, logged_in=False, error="Not logged in"), 401

    if role == "user":
        user_email = clean_lower(session.get("user_email") or email)
        user = find_user(user_email)
        if not user:
            clear_user_session_only()
            return jsonify(success=False, logged_in=False, error="User account no longer exists", redirect="/login.html"), 401
        if user.get("banned") or user.get("is_banned"):
            clear_user_session_only()
            return jsonify(success=False, logged_in=False, error="Your account has been banned", redirect="/login.html"), 403
        return jsonify(
            success=True,
            logged_in=True,
            role="user",
            user_type="user",
            email=user_email,
            account=public_user(user),
            user=public_user(user),
            welcome_mode=session.get("profile_welcome_mode", "login"),
        )

    if role == "host":
        host_email = clean_lower(session.get("host_email") or email)
        host, error_response = require_existing_host(host_email)
        if error_response:
            return error_response
        return jsonify(
            success=True,
            logged_in=True,
            role="host",
            user_type="host",
            email=host_email,
            account=public_host(host),
            host=public_host(host),
        )

    return jsonify(success=False, logged_in=False, error="Invalid session role"), 401


@app.route("/api/host/me")
@app.route("/api/organiser/me")
@app.route("/api/organizer/me")
def host_me():
    """Return organiser identity by explicit organiser email.

    This endpoint intentionally does not trust the shared Flask role cookie,
    because the same browser may also be logged in as a user in profile.html.
    It lets organiser.html restore its own host tab using the saved host email.
    """
    email = clean_lower(
        request.args.get("host_email")
        or request.args.get("organiser_email")
        or request.args.get("organizer_email")
        or request.headers.get("X-Orbit-Host-Email")
        or session.get("host_email")
    )
    if not email:
        return jsonify(success=False, logged_in=False, error="Host email missing"), 401
    host = find_host(email)
    if not host:
        return jsonify(success=False, logged_in=False, error="Host account no longer exists", redirect="/hsign.html"), 401
    if host.get("banned") or host.get("is_banned"):
        return jsonify(success=False, logged_in=False, error="Your account has been banned", redirect="/hsign.html"), 403
    return jsonify(
        success=True,
        logged_in=True,
        role="host",
        user_type="host",
        email=email,
        account=public_host(host),
        host=public_host(host),
    )

@app.route("/host-signup", methods=["POST"])
@app.route("/hsign", methods=["POST"])
@app.route("/api/host/signup", methods=["POST"])
def host_signup():
    data = get_data()

    name = clean(data.get("name") or data.get("hostName") or data.get("organiserName") or data.get("organizerName") or data.get("fullName") or data.get("username"))
    email = clean_lower(data.get("email") or data.get("hostEmail") or data.get("organiser_email") or data.get("organizer_email"))
    phone = clean(data.get("phone") or data.get("phoneNumber") or data.get("mobile") or data.get("contact"))
    organization = clean(data.get("organization") or data.get("organisation") or data.get("org") or data.get("orgName") or data.get("company"))
    password = clean(data.get("password") or data.get("hostPassword") or data.get("new_password"))
    confirm = clean(data.get("confirmPassword") or data.get("confirm_password") or data.get("confirm") or password)

    if not name:
        return jsonify(success=False, error="Full name is required"), 400
    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$", email):
        return jsonify(success=False, error="Enter a valid email address"), 400
    if not password:
        return jsonify(success=False, error="Password is required"), 400
    if len(password) < 6:
        return jsonify(success=False, error="Password must be at least 6 characters"), 400
    if password != confirm:
        return jsonify(success=False, error="Passwords do not match"), 400

    phone_digits = re.sub(r"\D", "", phone)
    if phone_digits.startswith("91") and len(phone_digits) == 12:
        phone_digits = phone_digits[2:]
    phone = phone_digits or phone

    # Keep user and host accounts separate. The same email cannot be reused across roles.
    if find_user(email):
        return jsonify(success=False, error="This email is already registered as a user. Please use a different email for organiser signup."), 400

    existing_host = find_host(email)
    if existing_host:
        return jsonify(success=False, error="Host account already exists. Please login."), 400

    # IMPORTANT: Clean up any old events, registrations, and related data from previous accounts
    # This prevents new hosts from seeing old event data and registrations from other organisers.
    # When an email is reused, the new account gets a fresh start with no old data.
    try:
        event_ids = [clean(e.get("event_id")) for e in events_collection.find({"$or": [{"organiser_email": email}, {"host_email": email}, {"organizer_email": email}, {"owner_email": email}]}, {"event_id": 1})]
        if event_ids:
            events_collection.delete_many({"$or": [{"organiser_email": email}, {"host_email": email}, {"organizer_email": email}, {"owner_email": email}, {"event_id": {"$in": event_ids}}]})
            registrations_collection.delete_many({"event_id": {"$in": event_ids}})
            payments_collection.delete_many({"event_id": {"$in": event_ids}})
            refunds_collection.delete_many({"event_id": {"$in": event_ids}})
            settlements_collection.delete_many({"event_id": {"$in": event_ids}})
            reviews_collection.delete_many({"event_id": {"$in": event_ids}})
        notifications_collection.delete_many({"$or": [{"email": email}, {"organiser_email": email}, {"host_email": email}]})
    except Exception as e:
        print(f"⚠️ Host signup cleanup warning (non-fatal): {e}")

    host_doc = {
        "name": name,
        "email": email,
        "phone": phone,
        "organization": organization,
        "password": generate_password_hash(password),
        "role": "host",
        "created_at": now(),
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }

    hosts_collection.insert_one(host_doc)
    set_active_host_session(email)

    try:
        create_notification(email, "Welcome organiser", "Your organiser dashboard is ready.")
    except Exception:
        pass

    return jsonify(
        success=True,
        message="Host signup successful",
        host=public_host(host_doc),
        mode="signup",
        welcome_text=f"Welcome {name}",
        redirect="/organiser.html?mode=signup",
    ), 201


@app.route("/host-signup/", methods=["POST"])
@app.route("/hsign/", methods=["POST"])
@app.route("/api/host/signup/", methods=["POST"])
def host_signup_slash():
    return host_signup()

@app.route("/host-login", methods=["POST"])
@app.route("/hlog", methods=["POST"])
@app.route("/api/host/login", methods=["POST"])
def host_login():
    data = get_data()

    email = clean_lower(data.get("email"))
    password = clean(data.get("password"))

    host = find_host(email)

    if not host:
        return jsonify(success=False, error="Host not found. Please sign up first."), 404

    if host.get("banned") or host.get("is_banned"):
        return jsonify(success=False, error="Your account has been banned. Please contact support."), 403

    if not check_password_hash(host.get("password", ""), password):
        return jsonify(success=False, error="Invalid password"), 401

    set_active_host_session(host["email"])

    return jsonify(
        success=True,
        message="Host login successful",
        host=public_host(host),
        mode="login",
        welcome_text=f"Welcome back {host.get('name', 'Organiser')}",
        redirect="/organiser.html?mode=login",
    )



@app.route("/api/host/update-profile", methods=["POST", "PUT"])
@app.route("/update-host-profile", methods=["POST", "PUT"])
def update_host_profile():
    data = get_data()
    old_email = clean_lower(data.get("old_email") or data.get("email") or session.get("host_email"))
    if not old_email:
        return jsonify(success=False, error="Host not logged in"), 401
    host = find_host(old_email)
    if not host:
        return jsonify(success=False, error="Host not found"), 404

    name = clean(data.get("name") or host.get("name", ""))
    organization = clean(data.get("organization") or data.get("organisation") or host.get("organization", ""))
    phone = clean(data.get("phone") or host.get("phone", ""))
    account_holder_name = clean(data.get("account_holder_name") or data.get("account_name") or host.get("account_holder_name", ""))
    upi_id = clean(data.get("upi_id") or data.get("upi") or host.get("upi_id", ""))
    upi_qr_image = clean(data.get("upi_qr_image") or data.get("qr_image") or host.get("upi_qr_image", ""))
    new_email = clean_lower(data.get("new_email") or data.get("email") or old_email)

    if not name:
        return jsonify(success=False, error="Name is required"), 400
    if not valid_email(new_email):
        return jsonify(success=False, error="Enter a valid email address"), 400
    if new_email != old_email and (find_host(new_email) or find_user(new_email)):
        return jsonify(success=False, error="This email is already used"), 400

    hosts_collection.update_one(email_query(old_email), {"$set": {
        "name": name,
        "email": new_email,
        "organization": organization,
        "phone": phone,
        "account_holder_name": account_holder_name,
        "upi_id": upi_id,
        "upi_qr_image": upi_qr_image,
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }})

    events_collection.update_many({"organiser_email": old_email}, {"$set": {
        "organiser_email": new_email,
        "contact_email": new_email,
        "organiser_name": name,
        "organization": organization,
        "contact_phone": phone,
        "account_holder_name": account_holder_name,
        "upi_id": upi_id,
        "upi_qr_image": upi_qr_image,
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }})

    session["host_email"] = new_email
    updated = find_host(new_email)
    return jsonify(success=True, message="Organiser profile updated", host=public_host(updated))

@app.route("/save-setup", methods=["POST"])
@app.route("/setup", methods=["POST"])
@app.route("/api/setup", methods=["POST"])
def save_setup():
    data = get_data()

    email = clean_lower(
        data.get("email")
        or request.args.get("email")
        or session.get("user_email")
    )

    profile_data = data.get("profile") if isinstance(data.get("profile"), dict) else data

    if not email:
        return jsonify(success=False, error="No email found. Please signup again."), 400

    user = find_user(email)

    if not user:
        signup_name = clean(data.get("name") or data.get("fullName") or data.get("fullname"))
        users_collection.insert_one({
            "name": signup_name,
            "email": email,
            "password": "",
            "role": "user",
            "profile": {},
            "saved_events": [],
            "interested_events": [],
            "registered_events": [],
            "onboarding_complete": False,
            "created_at": now(),
            "updated_at": now()
        })

    city = clean(profile_data.get("city") or profile_data.get("location"))

    profile = {
        "city": city,
        "location": city,
        "personality": clean(profile_data.get("personality")),
        "interests": normalize_list(profile_data.get("interests")),
        "age_group": clean(profile_data.get("age_group") or profile_data.get("ageGroup")),
        "meeting_people": clean(profile_data.get("meeting_people") or profile_data.get("meeting_new_people")),
        "meeting_new_people": clean(profile_data.get("meeting_people") or profile_data.get("meeting_new_people")),
        "improvement": normalize_list(profile_data.get("improvement")),
        "challenges": normalize_list(profile_data.get("challenges")),
        "blocker": clean(profile_data.get("blocker") or profile_data.get("holds_back")),
        "holds_back": clean(profile_data.get("blocker") or profile_data.get("holds_back")),
        "profession": clean(profile_data.get("profession") or profile_data.get("professional_type")),
        "professional_type": clean(profile_data.get("profession") or profile_data.get("professional_type")),
        "updated_at": now()
    }

    users_collection.update_one(
        email_query(email),
        {"$set": {
            "profile": profile,
            "onboarding_complete": True,
            "updated_at": now()
        }},
        upsert=True
    )

    set_active_user_session(email)

    profile["updated_at"] = safe_dt(profile["updated_at"])

    setup_mode = clean_lower(data.get("mode") or request.args.get("mode") or session.get("profile_welcome_mode") or "signup")
    profile_mode = "login" if setup_mode == "login" else "signup"
    session["profile_welcome_mode"] = profile_mode
    return jsonify(success=True, profile=profile, redirect=f"/profile.html?email={email}&mode={profile_mode}")

@app.route("/profile-data/<email>", methods=["GET"])
def profile_data(email):
    mark_completed_events()
    requested_email = clean_lower(email)
    # Use the explicit email from profile.html first. This prevents another open
    # admin/organiser tab from forcing the user dashboard back to login.
    session_email = clean_lower(session.get("user_email"))
    email = requested_email or session_email
    if not email:
        return jsonify(success=False, error="Please login as user.", redirect="/login.html"), 401
    if session_email and requested_email and requested_email != session_email:
        return jsonify(success=False, error="This user session cannot open another account.", redirect="/login.html"), 403
    repair_duplicate_active_registrations(user_email=email)
    user = find_user(email)

    if not user:
        return jsonify(success=False, error="User not found. Please sign up first."), 404

    explore_events = personalized_events_for_user(user)

    saved_ids = user.get("saved_events", [])
    registered_ids = ensure_registered_events_from_active_regs(email)

    saved_events = [
        event_with_match(e, user)
        for e in events_collection.find({"event_id": {"$in": saved_ids}})
    ]

    # One row per active booking. Do not dedupe by event_id.
    registered_events = registration_event_rows_for_user(email, user, active_only=True)
    registration_history = registration_event_rows_for_user(email, user, active_only=False)
    active_ticket_total = sum(int(e.get("tickets") or 1) for e in registered_events)

    # If user hasn't added their personal UPI/QR details, notify them to update.
    # Create this BEFORE fetching notifications so it's included in the response
    try:
        profile = user.get("profile") or {}
        missing_upi = not clean(profile.get("upi_id"))
        missing_qr = not clean(profile.get("upi_qr_image") or profile.get("upi_qr") or profile.get("user_qr_image"))
        if missing_upi or missing_qr:
            title = "Add payment details"
            message = "Please add your UPI ID and QR code in Profile Settings."
            # create_notification is idempotent via dedupe query.
            try:
                create_notification(email, title, message, recipient_type="user")
            except Exception:
                pass
    except Exception:
        pass

    notes = list(
        notifications_collection.find({"email": email}, {"_id": 0}).sort("created_at", -1)
    )

    for note in notes:
        note["created_at"] = safe_dt(note.get("created_at"))

    welcome_mode = clean_lower(session.pop("profile_welcome_mode", None) or request.args.get("mode") or "login")
    if welcome_mode not in ["signup", "login"]:
        welcome_mode = "login"

    orbit_payment = get_orbit_payment_settings()
    return jsonify({
        "success": True,
        "welcome_mode": welcome_mode,
        "welcome_prefix": "Welcome" if welcome_mode == "signup" else "Welcome back",
        "user": public_user(user),
        "events": {
            "explore": explore_events,
            "saved": saved_events,
            "registrations": registered_events,
            "registration_history": registration_history,
        },
        "stats": {
            "recommended_count": len(explore_events),
            "saved_count": len(saved_ids),
            "registered_count": len(registered_events),
            "active_registration_count": len(registered_events),
            "active_ticket_count": active_ticket_total,
            "growth_series": registration_growth_series(email),
        },
        "notifications": notes,
        "orbit_payment": {
            "upi_id": orbit_payment.get("upi_id", ""),
            "qr_image": orbit_payment.get("qr_image", ""),
        },
    })


@app.route("/api/weekly-curated/<email>", methods=["GET"])
@app.route("/weekly-curated/<email>", methods=["GET"])
def weekly_curated_events(email):
    """Return weekly curated events for a user based on their preferences."""
    mark_completed_events()
    requested_email = clean_lower(email)
    session_email = clean_lower(session.get("user_email"))
    email = requested_email or session_email
    
    if not email:
        return jsonify(success=False, error="Please login"), 401
    
    if session_email and requested_email and requested_email != session_email:
        return jsonify(success=False, error="Unauthorized access"), 403
    
    user = find_user(email)
    if not user:
        return jsonify(success=False, error="User not found"), 404
    
    # Get personalized events sorted by match score
    explore_events = personalized_events_for_user(user)
    
    # Prefer 90%+ matches for weekly curated picks, fallback to best available if needed
    weekly_curated = [e for e in explore_events if (e.get('match_percent') or 0) >= 90]
    if not weekly_curated:
        weekly_curated = [e for e in explore_events if (e.get('match_percent') or 0) >= 80]
    weekly_curated = weekly_curated[:6] if weekly_curated else explore_events[:6]
    
    return jsonify({
        "success": True,
        "events": weekly_curated,
        "count": len(weekly_curated),
        "message": "Weekly curated events for this week"
    })


@app.route("/update-profile", methods=["PUT", "POST"])
def update_profile():
    data = get_data()

    email = clean_lower(data.get("email") or session.get("user_email"))

    if not email:
        return jsonify(success=False, error="No email found"), 400

    user = find_user(email)

    if not user:
        return jsonify(success=False, error="User not found. Please sign up first."), 404

    name = clean(data.get("name") or data.get("fullName") or user.get("name"))

    old_profile = user.get("profile", {})

    profile = {
        **old_profile,
        "city": clean(data.get("city") or data.get("location") or old_profile.get("city")),
        "location": clean(data.get("location") or data.get("city") or old_profile.get("location")),
        "age_group": clean(data.get("age_group") or data.get("ageGroup") or old_profile.get("age_group")),
        "personality": clean(data.get("personality") or old_profile.get("personality")),
        "meeting_people": clean(
            data.get("meeting_people")
            or data.get("meeting_new_people")
            or data.get("meetingNewPeople")
            or old_profile.get("meeting_people")
            or old_profile.get("meeting_new_people")
        ),
        "meeting_new_people": clean(
            data.get("meeting_people")
            or data.get("meeting_new_people")
            or data.get("meetingNewPeople")
            or old_profile.get("meeting_people")
            or old_profile.get("meeting_new_people")
        ),
        "interests": normalize_list(data.get("interests") or old_profile.get("interests")),
        "improvement": normalize_list(data.get("improvement") or old_profile.get("improvement")),
        "goals": normalize_list(data.get("goals") or data.get("preferences") or old_profile.get("goals") or old_profile.get("preferences")),
        "preferences": normalize_list(data.get("goals") or data.get("preferences") or old_profile.get("goals") or old_profile.get("preferences")),
        "blocker": clean(
            data.get("blocker")
            or data.get("holds_back")
            or data.get("holdsBack")
            or old_profile.get("blocker")
            or old_profile.get("holds_back")
        ),
        "holds_back": clean(
            data.get("blocker")
            or data.get("holds_back")
            or data.get("holdsBack")
            or old_profile.get("blocker")
            or old_profile.get("holds_back")
        ),
        "profession": clean(data.get("profession") or data.get("professional_type") or old_profile.get("profession") or old_profile.get("professional_type")),
        "professional_type": clean(data.get("profession") or data.get("professional_type") or old_profile.get("profession") or old_profile.get("professional_type")),
        # Allow users to save their personal payment details
        "upi_id": clean(data.get("upi_id") or old_profile.get("upi_id") or old_profile.get("user_upi_id") or old_profile.get("upi")),
        "upi_qr_image": clean(data.get("upi_qr_image") or old_profile.get("upi_qr_image") or old_profile.get("user_qr_image") or old_profile.get("upi_qr")),
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }

    users_collection.update_one(
        email_query(email),
        {
            "$set": {
                "name": name,
                "profile": profile,
                "onboarding_complete": True,
                "updated_at": now(),
            }
        },
    )

    updated_user = find_user(email)

    return jsonify({
        "success": True,
        "message": "Profile updated",
        "user": public_user(updated_user),
    })


@app.route("/api/events")
@app.route("/events")
def get_events():
    mark_completed_events()
    email = clean_lower(request.args.get("email") or session.get("user_email"))
    user = find_user(email)

    location = clean_lower(request.args.get("location"))
    category = clean_lower(request.args.get("category"))

    events = personalized_events_for_user(user) if user else [
        event_with_match(e, None)
        for e in events_collection.find(user_visible_event_query())
        if event_is_user_visible(e)
    ]

    if location and location != "all":
        events = [e for e in events if clean_lower(e.get("location")) == location]

    if category and category != "all":
        events = [e for e in events if clean_lower(e.get("category")) == category or category in [clean_lower(x) for x in normalize_list(e.get("interest_tags"))]]

    events.sort(key=lambda x: x.get("match_percent", 0), reverse=True)

    return jsonify(success=True, events=events)


@app.route("/api/events/<event_id>")
def get_event(event_id):
    email = clean_lower(request.args.get("email") or session.get("user_email"))
    user = find_user(email)

    event = events_collection.find_one({"event_id": event_id})

    if not event:
        return jsonify(success=False, error="Event not found"), 404

    return jsonify(success=True, event=event_with_match(event, user))


@app.route("/api/recommendations")
def recommendations():
    email = clean_lower(request.args.get("email") or session.get("user_email"))
    user = find_user(email)

    if not user:
        return jsonify(success=False, error="User not logged in"), 401

    all_events = personalized_events_for_user(user)
    matched = [e for e in all_events if e.get("is_recommended") and e.get("match_percent", 0) >= 70]
    events = matched if matched else all_events

    return jsonify(success=True, events=events[:12])


@app.route("/create-event", methods=["POST"])
@app.route("/create-organiser-event", methods=["POST"])
@app.route("/api/host/events", methods=["POST"])
def create_event():
    data = get_data()

    host_email = clean_lower(data.get("host_email") or data.get("owner_email") or session.get("host_email") or data.get("organiser_email") or data.get("organizer_email"))
    host, error_response = require_existing_host(host_email)
    if error_response:
        return error_response
    host_email = clean_lower(host.get("email") or host_email)

    title = clean(data.get("title") or data.get("event_title"))
    description = clean(data.get("description"))

    if not title:
        return jsonify(success=False, error="Event title is required"), 400

    if not description:
        return jsonify(success=False, error="Event description is required"), 400

    try:
        price = int(float(data.get("price") or 0))
    except Exception:
        price = 0

    try:
        total_slots = int(float(data.get("total_slots") or data.get("slots") or data.get("available_slots") or 0))
    except Exception:
        total_slots = 0

    if total_slots <= 0:
        return jsonify(success=False, error="Total slots must be at least 1"), 400

    interest_tags = normalize_list(data.get("interest_tags") or data.get("interests"))
    improvement_areas = normalize_list(data.get("improvement_areas") or data.get("focus_area"))

    images = normalize_list(data.get("images"))
    banner_image = clean(data.get("banner_image"))
    reference_photos = normalize_list(data.get("reference_photos") or data.get("event_photos") or data.get("photos"))
    poster_image = reference_photos[0] if reference_photos else clean(data.get("poster_image"))
    if banner_image and banner_image not in images:
        images.insert(0, banner_image)
    for photo in reference_photos:
        if photo and photo not in images:
            images.append(photo)
    if poster_image and poster_image not in images:
        images.append(poster_image)

    event_doc = {
        "event_id": clean(data.get("event_id")) or make_event_id(),
        "title": title,
        "description": description,
        "category": data.get("category", "") or (interest_tags[:1] or [""])[0],
        "location": data.get("location", ""),
        "exact_address": data.get("exact_address") or data.get("venue_address") or data.get("address", ""),
        "venue_address": data.get("venue_address") or data.get("exact_address") or data.get("address", ""),
        "address": data.get("address") or data.get("exact_address") or data.get("venue_address", ""),
        "start_date": data.get("start_date") or data.get("event_date") or data.get("date", ""),
        "end_date": data.get("end_date") or data.get("event_date") or data.get("start_date") or data.get("date", ""),
        "start_time": data.get("start_time") or data.get("event_time") or data.get("time", ""),
        "end_time": data.get("end_time") or data.get("event_time") or data.get("start_time") or data.get("time", ""),
        "event_date": data.get("event_date") or data.get("start_date") or data.get("date", ""),
        "event_time": data.get("event_time") or data.get("start_time") or data.get("time", ""),
        "price": price,
        "total_slots": total_slots,
        "available_slots": total_slots,
        "registered_count": 0,
        # New organiser-created events must NOT go live automatically.
        # Ignore client-provided Approved/status values on create; only admin approval can change these.
        "status": "Pending",
        "approval_status": "Pending",
        "payment_status": "Free Pending Approval" if price == 0 else "Admin Hold Pending Approval",
        "payment_receiver": "orbit" if price > 0 else "none",
        "payment_route": "orbit_hold" if price > 0 else "free",
        "admin_upi_id": ADMIN_UPI_ID if price > 0 else "",
        "admin_qr_image": ADMIN_QR_IMAGE if price > 0 else "",
        "settlement_status": "Pending Approval" if price > 0 else "Free Event",
        "cancel_reason": "",
        "refund_all_requested": False,
        "saved_count": 0,
        "interested_count": 0,
        "interest_tags": interest_tags,
        "focus_area": improvement_areas,
        "age_groups": normalize_list(data.get("age_groups")),
        "personality_tags": normalize_list(data.get("personality_tags")),
        "meeting_people_tags": normalize_list(data.get("meeting_people_tags")),
        "improvement_areas": improvement_areas,
        "challenge_types": normalize_list(data.get("challenge_types")),
        "professional_types": normalize_list(data.get("professional_types") or data.get("profession_types") or data.get("professions")),
        "images": images,
        "banner_image": banner_image,
        "poster_image": poster_image,
        "reference_photos": reference_photos,
        "upi_id": clean(data.get("upi_id") or data.get("upi") or host.get("upi_id")),
        "account_holder_name": clean(data.get("account_holder_name") or data.get("account_name") or host.get("account_holder_name")),
        "upi_qr_image": clean(data.get("upi_qr_image") or host.get("upi_qr_image")),
        "payment_mode": "Free Event" if price == 0 else "Orbit Secure Settlement",
        "payment_note": "Payments are securely handled by Orbit. Eligible settlements will be processed after event completion." if price > 0 else "Free event",
        "qr_image": clean(data.get("qr_image") or data.get("qr")),
        "organiser_name": clean(data.get("organiser_name")) or host.get("name", "Organiser"),
        # IMPORTANT: organiser_email is the owner email used for dashboard/admin filtering.
        # Do not replace it with contact_email, because contact_email may be a public support email.
        "organiser_email": clean_lower(host.get("email") or host_email),
        "organizer_email": clean_lower(host.get("email") or host_email),
        "host_email": clean_lower(host.get("email") or host_email),
        "contact_email": clean_lower(data.get("contact_email") or host.get("email") or host_email),
        "contact_phone": clean(data.get("contact_phone") or data.get("organiser_phone") or data.get("phone")),
        "organization": clean(data.get("organization") or data.get("organisation")) or host.get("organization", ""),
        "benefits": clean(data.get("benefits") or data.get("inclusions")),
        "inclusions": clean(data.get("benefits") or data.get("inclusions")),
        "event_photos": reference_photos,
        "created_at": now(),
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }

    events_collection.insert_one(event_doc)

    return jsonify(success=True, message="Event created successfully", event=format_event(event_doc)), 201

@app.route("/update-event", methods=["POST", "PUT"])
@app.route("/api/events/<event_id>/update", methods=["POST", "PUT"])
def update_event(event_id=None):
    data = get_data()

    event_id = event_id or clean(data.get("event_id"))
    host_email = clean_lower(data.get("host_email") or data.get("owner_email") or session.get("host_email") or data.get("organiser_email") or data.get("organizer_email"))

    if not event_id:
        return jsonify(success=False, error="Event ID is required"), 400

    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return jsonify(success=False, error="Event not found"), 404

    event_owner = clean_lower(event.get("organiser_email") or event.get("organizer_email") or event.get("host_email"))
    if host_email and event_owner and event_owner != host_email:
        return jsonify(success=False, error="You can edit only your event"), 403

    # Editing must not change commitment fields already shown to users.
    # Locked fields: date, time, city/location, venue/address, audience,
    # and matching tags. Organisers can safely update title, price, slots,
    # description, benefits, banner/photos, and contact details.
    try:
        price = int(float(data.get("price") if data.get("price") not in [None, ""] else event.get("price") or 0))
    except Exception:
        price = int(float(event.get("price") or 0)) if str(event.get("price") or "0").replace(".", "", 1).isdigit() else 0

    registered_count = active_ticket_count(event_id)

    try:
        requested_slots = int(float(data.get("total_slots") if data.get("total_slots") not in [None, ""] else data.get("slots") if data.get("slots") not in [None, ""] else event.get("total_slots") or 0))
    except Exception:
        requested_slots = int(float(event.get("total_slots") or event.get("slots") or 0)) if str(event.get("total_slots") or event.get("slots") or "0").replace(".", "", 1).isdigit() else 0

    total_slots = max(requested_slots, registered_count, 1)
    interest_tags = normalize_list(event.get("interest_tags") or event.get("interests"))
    improvement_areas = normalize_list(event.get("improvement_areas") or event.get("focus_area"))

    update_doc = {
        "title": clean(data.get("title") or data.get("event_title")),
        "description": clean(data.get("description")),
        "category": event.get("category", "") or (interest_tags[:1] or [""])[0],
        "location": event.get("location", ""),
        "exact_address": event.get("exact_address") or event.get("venue_address") or event.get("address", ""),
        "venue_address": event.get("venue_address") or event.get("exact_address") or event.get("address", ""),
        "address": event.get("address") or event.get("exact_address") or event.get("venue_address", ""),
        "start_date": event.get("start_date") or event.get("event_date") or event.get("date", ""),
        "end_date": event.get("end_date") or event.get("event_date") or event.get("start_date") or event.get("date", ""),
        "start_time": event.get("start_time") or event.get("event_time") or event.get("time", ""),
        "end_time": event.get("end_time") or event.get("event_time") or event.get("start_time") or event.get("time", ""),
        "event_date": event.get("event_date") or event.get("start_date") or event.get("date", ""),
        "event_time": event.get("event_time") or event.get("start_time") or event.get("time", ""),
        "price": price,
        "total_slots": total_slots,
        "available_slots": max(total_slots - registered_count, 0),
        "interest_tags": interest_tags,
        "focus_area": improvement_areas,
        "age_groups": normalize_list(event.get("age_groups")),
        "personality_tags": normalize_list(event.get("personality_tags")),
        "meeting_people_tags": normalize_list(event.get("meeting_people_tags")),
        "improvement_areas": improvement_areas,
        "challenge_types": normalize_list(event.get("challenge_types")),
        "professional_types": normalize_list(event.get("professional_types") or event.get("profession_types") or event.get("professions")),
        "upi_id": clean(data.get("upi_id") or data.get("upi")) or event.get("upi_id", ""),
        "account_holder_name": clean(data.get("account_holder_name") or data.get("account_name")) or event.get("account_holder_name", ""),
        "upi_qr_image": clean(data.get("upi_qr_image")) or event.get("upi_qr_image", ""),
        "payment_mode": "Free Event" if price == 0 else "Orbit Secure Settlement",
        "payment_note": "Payments are securely handled by Orbit. Eligible settlements will be processed after event completion." if price > 0 else "Free event",
        "banner_image": clean(data.get("banner_image")) or event.get("banner_image", ""),
        "reference_photos": normalize_list(data.get("reference_photos") or data.get("event_photos") or data.get("photos")) or event.get("reference_photos", []),
        "event_photos": normalize_list(data.get("reference_photos") or data.get("event_photos") or data.get("photos")) or event.get("event_photos", event.get("reference_photos", [])),
        "organiser_name": clean(data.get("organiser_name")) or event.get("organiser_name", ""),
        "organization": clean(data.get("organization") or data.get("organisation")) or event.get("organization", ""),
        "contact_email": clean_lower(data.get("contact_email") or data.get("organiser_email")) or event.get("contact_email", event.get("organiser_email", "")),
        "contact_phone": clean(data.get("contact_phone") or data.get("organiser_phone") or data.get("phone")) or event.get("contact_phone", ""),
        "benefits": clean(data.get("benefits") or data.get("inclusions")) or event.get("benefits", ""),
        "inclusions": clean(data.get("benefits") or data.get("inclusions")) or event.get("inclusions", event.get("benefits", "")),
        "poster_image": (normalize_list(data.get("reference_photos")) or [clean(data.get("poster_image")) or event.get("poster_image", "")])[0],
        "qr_image": clean(data.get("qr_image") or data.get("qr")) or event.get("qr_image", ""),
        "edit_lock_note": "Date, time, venue and audience fields are locked after posting. Price and slots can be updated.",
        "updated_at": now(),
    }

    if not update_doc["title"]:
        return jsonify(success=False, error="Event title is required"), 400
    if not update_doc["description"]:
        return jsonify(success=False, error="Event description is required"), 400

    events_collection.update_one({"event_id": event_id}, {"$set": update_doc})
    updated = events_collection.find_one({"event_id": event_id})
    return jsonify(success=True, message="Event updated successfully", event=format_event(updated))

@app.route("/api/host/events", methods=["GET"])
def host_events():
    host_email = clean_lower(request.args.get("host_email") or request.args.get("organiser_email") or request.args.get("organizer_email") or session.get("host_email"))

    host, error_response = require_existing_host(host_email)
    if error_response:
        return error_response
    host_email = clean_lower(host.get("email") or host_email)

    owner_query = {"$or": [
        {"organiser_email": host_email},
        {"organizer_email": host_email},
        {"host_email": host_email},
    ]}
    # Repair older events that had host_email but missing organiser_email.
    events_collection.update_many({"host_email": host_email, "$or": [{"organiser_email": {"$exists": False}}, {"organiser_email": ""}]}, {"$set": {"organiser_email": host_email, "organizer_email": host_email, "updated_at": now()}})

    events = [format_event(e) for e in events_collection.find(owner_query).sort("created_at", -1)]

    return jsonify(success=True, events=events)


@app.route("/api/events/<event_id>/save", methods=["POST"])
@app.route("/save-event", methods=["POST"])
def save_event(event_id=None):
    data = get_data()

    event_id = event_id or clean(data.get("event_id"))
    email = clean_lower(data.get("email") or session.get("user_email"))

    user = find_user(email)
    event = events_collection.find_one({"event_id": event_id})

    if not user:
        return jsonify(success=False, error="User not found"), 404

    if not event:
        return jsonify(success=False, error="Event not found"), 404

    if event_id in user.get("saved_events", []):
        users_collection.update_one(email_query(email), {"$pull": {"saved_events": event_id}})
        events_collection.update_one(
            {"event_id": event_id, "saved_count": {"$gt": 0}},
            {"$inc": {"saved_count": -1}},
        )
        return jsonify(success=True, saved=False, message="Event unsaved")

    users_collection.update_one(email_query(email), {"$addToSet": {"saved_events": event_id}})
    events_collection.update_one({"event_id": event_id}, {"$inc": {"saved_count": 1}})

    return jsonify(success=True, saved=True, message="Event saved")


@app.route("/api/events/<event_id>/interested", methods=["POST"])
@app.route("/interested-event", methods=["POST"])
def interested_event(event_id=None):
    data = get_data()

    event_id = event_id or clean(data.get("event_id"))
    email = clean_lower(data.get("email") or session.get("user_email"))

    user = find_user(email)
    event = events_collection.find_one({"event_id": event_id})

    if not user:
        return jsonify(success=False, error="User not found"), 404

    if not event:
        return jsonify(success=False, error="Event not found"), 404

    if event_id in user.get("interested_events", []):
        users_collection.update_one(email_query(email), {"$pull": {"interested_events": event_id}})
        events_collection.update_one(
            {"event_id": event_id, "interested_count": {"$gt": 0}},
            {"$inc": {"interested_count": -1}},
        )
        return jsonify(success=True, interested=False, message="Interest removed")

    users_collection.update_one(email_query(email), {"$addToSet": {"interested_events": event_id}})
    events_collection.update_one({"event_id": event_id}, {"$inc": {"interested_count": 1}})

    return jsonify(success=True, interested=True, message="Marked interested")


@app.route("/api/events/<event_id>/register", methods=["POST"])
@app.route("/register-event", methods=["POST"])
def register_event(event_id=None):
    data = get_data()

    event_id = event_id or clean(data.get("event_id"))
    email = clean_lower(data.get("email") or session.get("user_email"))

    user = find_user(email)
    event = events_collection.find_one({"event_id": event_id})

    if not user:
        return jsonify(success=False, error="User not found"), 404

    if not event:
        return jsonify(success=False, error="Event not found"), 404

    repair_duplicate_active_registrations(event_id=event_id, user_email=email)
    event = format_event(event)

    if not event_accepts_registration(event):
        status = clean_lower(event.get("status") or event.get("approval_status"))
        if status == "cancelled":
            return jsonify(success=False, error="Event cancelled"), 400
        if status == "completed" or event_is_completed(event):
            return jsonify(success=False, error="Registration closed. Event completed."), 400
        return jsonify(success=False, error="Registration is open only for active upcoming approved events."), 400

    existing_active = registrations_collection.find_one(active_registration_query(event_id=event_id, user_email=email))
    if existing_active:
        return jsonify(
            success=False,
            error="You are already registered for this event. Cancel your current booking before registering again.",
            already_registered=True,
            registration_id=existing_active.get("registration_id"),
            event_counts=sync_event_registration_counts(event_id),
        ), 400

    sync_event_registration_counts(event_id)
    event = format_event(events_collection.find_one({"event_id": event_id}) or event)

    try:
        tickets = int(float(data.get("tickets") or 1))
    except Exception:
        tickets = 1

    tickets = max(1, min(tickets, 10))

    if event["slots_left"] < tickets:
        return jsonify(success=False, error="Not enough slots left"), 400

    fees = money_fields(event["price"], tickets)
    amount = fees["amount_paid"]
    is_paid_event = fees["event_amount"] > 0

    reg = {
        "registration_id": make_reg_id(),
        "event_id": event_id,
        "event_title": event.get("title"),
        "event_date": event.get("event_date") or event.get("start_date"),
        "organiser_email": event.get("organiser_email") or event.get("organizer_email") or event.get("host_email"),
        "organizer_email": event.get("organiser_email") or event.get("organizer_email") or event.get("host_email"),
        "host_email": event.get("organiser_email") or event.get("organizer_email") or event.get("host_email"),
        "organiser_name": event.get("organiser_name"),
        "organizer_name": event.get("organiser_name"),
        "organization": event.get("organization", ""),
        "user_email": user["email"],
        "user_name": user.get("name", ""),
        "tickets": tickets,
        **fees,
        "payment_method": "FREE" if not is_paid_event else "UPI to Orbit",
        "payment_receiver": "orbit" if is_paid_event else "none",
        "payment_route": "orbit_hold" if is_paid_event else "free",
        "admin_upi_id": ADMIN_UPI_ID if is_paid_event else "",
        "admin_qr_image": ADMIN_QR_IMAGE if is_paid_event else "",
        "payment_status": "free" if not is_paid_event else "Paid to Orbit - On Hold",
        "settlement_status": "Free Event" if not is_paid_event else "Held by Orbit",
        "registration_status": "Registered",
        "status": "registered",
        "upi_reference": clean(data.get("upi_reference") or data.get("transaction_id")),
        "user_upi_id": clean(data.get("user_upi_id") or data.get("upi_id") or data.get("user_upi")),
        "paid_at": now() if is_paid_event else "",
        "registered_at": now(),
        "created_at": now(),
    }

    try:
        registrations_collection.insert_one(reg)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

    if is_paid_event:
        payment_update = clean_mongo_update({**reg, "payment_id": f"PAY{10001 + payments_collection.count_documents({})}", "created_at": now()})
        payments_collection.update_one(
            {"registration_id": reg["registration_id"]},
            {"$set": payment_update},
            upsert=True
        )

    users_collection.update_one(email_query(email), {"$addToSet": {"registered_events": event_id}})
    sync_event_registration_counts(event_id)
    ensure_registered_events_from_active_regs(email)

    client_action_id = clean(data.get("client_action_id") or data.get("action_id"))
    create_notification(email, "Registration successful", f"You registered for {event.get('title')}." + (f" Payment received: ₹{amount}." if is_paid_event else ""), "user", {"type": "registration_success", "related_event_id": event_id, "registration_id": reg["registration_id"], "tickets": tickets, "client_action_id": client_action_id, "action_id": client_action_id})
    create_notification(
        event.get("organiser_email"),
        "Registration update",
        f"A new registration was received for {event.get('title')}. View your daily event summary for total registered users.",
        "organiser",
        {"type": "registration_summary", "related_event_id": event_id},
    )
    # Admin notifications were removed from adp.html by requirement.
    # Registration/payment records are still written to MongoDB for ADP reports.

    latest_counts = sync_event_registration_counts(event_id)
    reg.pop("_id", None)
    reg["registered_at"] = safe_dt(reg["registered_at"])

    return jsonify(success=True, message="Registration successful", registration=reg, event_counts=latest_counts)


@app.route("/api/user/saved")
def user_saved():
    email = clean_lower(request.args.get("email") or session.get("user_email"))
    user = find_user(email)

    if not user:
        return jsonify(success=False, events=[])

    ids = user.get("saved_events", [])
    events = [event_with_match(e, user) for e in events_collection.find({"event_id": {"$in": ids}})]

    return jsonify(success=True, events=events)


@app.route("/api/user/registered")
def user_registered():
    email = clean_lower(request.args.get("email") or session.get("user_email"))
    user = find_user(email)

    if not user:
        return jsonify(success=False, events=[])

    ensure_registered_events_from_active_regs(email)
    events = registration_event_rows_for_user(email, user, active_only=True)

    return jsonify(success=True, events=events, count=len(events), growth_series=registration_growth_series(email))


@app.route("/api/host/stats")
def host_stats():
    host_email = clean_lower(request.args.get("host_email") or session.get("host_email"))

    host, error_response = require_existing_host(host_email)
    if error_response:
        return error_response
    host_email = clean_lower(host.get("email") or host_email)

    event_owner_query = {"$or": [{"organiser_email": host_email}, {"organizer_email": host_email}, {"host_email": host_email}]}
    events = list(events_collection.find(event_owner_query))
    ids = [e.get("event_id") for e in events]
    regs = list(registrations_collection.find({"event_id": {"$in": ids}}))

    return jsonify(success=True, stats={
        "total_events": len(events),
        "total_registrations": len(regs),
        "total_revenue": sum(int(r.get("amount", 0)) for r in regs),
        "total_slots": sum(int(e.get("total_slots", 0)) for e in events),
    })


@app.route("/api/host/attendees")
def host_attendees():
    host_email = clean_lower(
        request.args.get("host_email")
        or request.args.get("organiser_email")
        or request.args.get("organizer_email")
        or request.args.get("email")
        or session.get("host_email")
    )

    host, error_response = require_existing_host(host_email)
    if error_response:
        return error_response
    host_email = clean_lower(host.get("email") or host_email)

    event_owner_query = {"$or": [{"organiser_email": host_email}, {"organizer_email": host_email}, {"host_email": host_email}]}
    events = list(events_collection.find(event_owner_query))
    ids = [e.get("event_id") for e in events]
    event_map = {e.get("event_id"): format_event(e) for e in events}
    attendees = list(registrations_collection.find(
        {"event_id": {"$in": ids}, "status": {"$ne": "duplicate_inactive"}, "registration_status": {"$ne": "duplicate_inactive"}},
        {"_id": 0}
    ))

    enriched_attendees = []
    for a in attendees:
        a["registered_at"] = safe_dt(a.get("registered_at"))
        a["created_at"] = safe_dt(a.get("created_at"))
        a["cancelled_at"] = safe_dt(a.get("cancelled_at"))
        a["settled_at"] = safe_dt(a.get("settled_at"))
        # Keep cancellation/refund fields explicit for organiser.html. Cancelled rows
        # must remain visible in Registrations instead of disappearing.
        if "cancel" in clean_lower(a.get("status") or a.get("registration_status") or a.get("booking_status")):
            a["status"] = a.get("status") or "cancelled_by_user"
            a["registration_status"] = a.get("registration_status") or "cancelled_by_user"
            a["booking_status"] = a.get("booking_status") or "cancelled_by_user"
        enriched_attendees.append(enrich_organiser_registration_row(a, event_map.get(a.get("event_id"), {})))

    def attendee_sort_value(row):
        return str(row.get("cancelled_at") or row.get("registered_at") or row.get("created_at") or "")

    enriched_attendees = sorted(enriched_attendees, key=attendee_sort_value, reverse=True)
    return jsonify(success=True, attendees=enriched_attendees, registrations=enriched_attendees, payout_summary=organiser_payout_summary(enriched_attendees))


@app.route("/organiser-data/<email>", methods=["GET"])
@app.route("/organizer-data/<email>", methods=["GET"])
def organiser_data(email):
    mark_completed_events()
    # IMPORTANT ROLE-SEPARATION FIX:
    # organiser.html passes its saved organiser email in the URL. Do not reject
    # this request just because profile.html changed the shared Flask cookie to
    # a user session. Validate the requested email against the hosts collection.
    requested_email = clean_lower(
        email
        or request.args.get("host_email")
        or request.args.get("organiser_email")
        or request.args.get("organizer_email")
        or request.headers.get("X-Orbit-Host-Email")
        or session.get("host_email")
    )
    if not requested_email:
        return jsonify(success=False, error="Please login as organiser.", redirect="/hlog.html"), 401

    host = find_host(requested_email)
    if not host:
        return host_deleted_response(requested_email)
    email = clean_lower(host.get("email") or requested_email)

    event_owner_query = {"$or": [{"organiser_email": email}, {"organizer_email": email}, {"host_email": email}]}
    events = [format_event(e) for e in events_collection.find(event_owner_query).sort("created_at", -1)]
    ids = [e.get("event_id") for e in events]
    attendees = list(registrations_collection.find({"event_id": {"$in": ids}}, {"_id": 0}).sort("registered_at", -1))

    event_city = {e.get("event_id"): e.get("location", "") for e in events}
    event_map = {e.get("event_id"): e for e in events}
    enriched_attendees = []
    for a in attendees:
        a["registered_at"] = safe_dt(a.get("registered_at"))
        a["settled_at"] = safe_dt(a.get("settled_at"))
        a["location"] = a.get("location") or event_city.get(a.get("event_id"), "")
        enriched_attendees.append(enrich_organiser_registration_row(a, event_map.get(a.get("event_id"), {})))

    attendees = enriched_attendees
    payout_summary = organiser_payout_summary(attendees)
    pending_payments = sum(1 for a in attendees if clean_lower(a.get("payment_status")) in ["pending", ""])

    return jsonify(
        success=True,
        organiser=public_host(host),
        events=events,
        attendees=attendees,
        payout_summary=payout_summary,
        stats={
            "total_events": len(events),
            "total_registrations": len(attendees),
            "total_revenue": payout_summary.get("paid_by_orbit", 0),
            "orbit_collected": payout_summary.get("orbit_collected", 0),
            "organiser_total_earning": payout_summary.get("organiser_total_earning", 0),
            "pending_from_orbit": payout_summary.get("pending_from_orbit", 0),
            "paid_by_orbit": payout_summary.get("paid_by_orbit", 0),
            "total_slots": sum(int(e.get("total_slots", 0) or 0) for e in events),
            "pending_payments": pending_payments,
        },
    )

@app.route("/api/notifications")
def notifications():
    email = clean_lower(request.args.get("email") or session.get("user_email") or session.get("host_email"))

    notes = list(notifications_collection.find({"email": email}, {"_id": 0}).sort("created_at", -1))

    for n in notes:
        n["created_at"] = safe_dt(n.get("created_at"))

    return jsonify(success=True, notifications=notes)


@app.route("/notifications/<email>", methods=["GET"])
def notifications_by_email(email):
    email = clean_lower(email)

    notes = list(notifications_collection.find({"email": email}, {"_id": 0}).sort("created_at", -1))

    for n in notes:
        n["created_at"] = safe_dt(n.get("created_at"))

    return jsonify(success=True, notifications=notes)


@app.route("/api/notifications/read", methods=["POST"])
def read_notifications():
    data = get_data()
    email = clean_lower(data.get("email") or session.get("user_email") or session.get("host_email"))

    notifications_collection.update_many({"email": email}, {"$set": {"unread": False, "status": "Read", "read_at": now()}})

    return jsonify(success=True)


@app.route("/cancel-registration", methods=["POST", "PUT"])
@app.route("/api/user/cancel-registration", methods=["POST", "PUT"])
def cancel_registration():
    data = get_data()
    email = clean_lower(data.get("email") or session.get("user_email"))
    event_id = clean(data.get("event_id"))
    reason = clean(data.get("reason") or data.get("cancel_reason") or data.get("cancellation_reason"))
    refund_requested = bool(data.get("refund_requested", True))
    if not email or not event_id:
        return jsonify(success=False, error="User email and event ID are required"), 400
    if not reason:
        return jsonify(success=False, error="Cancellation reason is required"), 400
    user = find_user(email)
    event = events_collection.find_one({"event_id": event_id})
    if not user or not event:
        return jsonify(success=False, error="Registration details not found"), 404
    registration_id = clean(data.get("registration_id") or data.get("booking_id"))
    reg_query = active_registration_query(event_id, email)
    if registration_id:
        reg_query["registration_id"] = registration_id
        active_regs = list(registrations_collection.find(reg_query).sort([("registered_at", -1), ("created_at", -1)]))
    else:
        # No specific booking selected in profile.html, so cancel this user's full
        # active registration for the event, including any extra tickets/bookings
        # they added later.
        active_regs = list(registrations_collection.find(reg_query).sort([("registered_at", -1), ("created_at", -1)]))
    if not active_regs:
        return jsonify(success=False, error="No active registration found for this event."), 400
    reg = active_regs[0]
    cancelled_at = now()

    amount_paid = 0
    event_amount = 0
    cancelled_tickets = 0
    for active_reg in active_regs:
        amount_paid += int(float(active_reg.get("amount_paid") or active_reg.get("amount") or 0))
        event_amount += int(float(active_reg.get("event_amount") or event.get("price") or 0))
        try:
            cancelled_tickets += max(1, int(float(active_reg.get("tickets") or 1)))
        except Exception:
            cancelled_tickets += 1
    if amount_paid <= 0:
        amount_paid = int(float(data.get("amount") or 0))

    refundable_amount = event_amount if refund_requested and amount_paid > 0 else 0
    update = {
        "status": "cancelled_by_user",
        "registration_status": "cancelled_by_user",
        "booking_status": "cancelled_by_user",
        "cancellation_status": "cancelled_by_user",
        "cancellation_reason": reason,
        "cancel_reason": reason,
        "cancelled_at": cancelled_at,
        "refund_requested": bool(refund_requested and amount_paid > 0),
        "refund_status": "Pending" if refund_requested and amount_paid > 0 else "Not required",
        "payment_status": "Refund requested" if refund_requested and amount_paid > 0 else "Cancelled",
        "settlement_status": "Refund requested" if refund_requested and amount_paid > 0 else "Cancelled",
        "refundable_amount": refundable_amount,
        "updated_at": cancelled_at,
    }
    cancelled_registration_ids = [r.get("registration_id") for r in active_regs if r.get("registration_id")]
    if registration_id:
        registrations_collection.update_one({"registration_id": registration_id}, {"$set": clean_mongo_update(update)})
        payments_collection.update_many({"registration_id": registration_id}, {"$set": clean_mongo_update(update)})
    else:
        registration_update_query = {"registration_id": {"$in": cancelled_registration_ids}} if cancelled_registration_ids else reg_query
        registrations_collection.update_many(
            registration_update_query,
            {"$set": clean_mongo_update(update)}
        )
        if cancelled_registration_ids:
            payments_collection.update_many(
                {"registration_id": {"$in": cancelled_registration_ids}},
                {"$set": clean_mongo_update(update)}
            )
    sync_event_registration_counts(event_id)
    ensure_registered_events_from_active_regs(email)
    refund_doc = None
    if refund_requested and amount_paid > 0:
        refund_doc = {
            "refund_id": f"REF{10001 + refunds_collection.count_documents({})}",
            "registration_id": (reg or {}).get("registration_id"),
            "registration_ids": [r.get("registration_id") for r in active_regs if r.get("registration_id")],
            "event_id": event_id,
            "event_title": event.get("title"),
            "user_email": email,
            "user_name": user.get("name", ""),
            "amount": refundable_amount,
            "cancelled_tickets": cancelled_tickets,
            "platform_fee_kept": PLATFORM_USER_FEE,
            "reason": reason,
            "refund_status": "Pending",
            "status": "Pending",
            "requested_at": cancelled_at,
            "requested_by": "user",
        }
        refunds_collection.insert_one(refund_doc)
    client_action_id = clean(data.get("client_action_id") or data.get("action_id"))
    create_notification(
        email,
        "Registration cancelled",
        f"Your registration for {event.get('title', 'the event')} was cancelled.",
        "user",
        {
            "type": "registration_cancelled",
            "related_event_id": event_id,
            "registration_id": reg.get("registration_id"),
            "registration_ids": [r.get("registration_id") for r in active_regs if r.get("registration_id")],
            "client_action_id": client_action_id,
            "action_id": client_action_id,
        },
    )
    # Do not send individual organiser cancellation notifications.
    # Organiser page shows only daily registration summaries plus allowed admin/event/payment updates.
    if refund_doc:
        # Refund is saved in refunds_collection for ADP; no admin notification row.
        pass
    latest_counts = sync_event_registration_counts(event_id)
    if refund_doc:
        refund_doc.pop("_id", None)
    return jsonify(success=True, message="Registration cancelled" + (" and refund request sent to Orbit." if refund_doc else "."), refund=refund_doc, cancelled_registration_id=reg.get("registration_id"), cancelled_registration_ids=[r.get("registration_id") for r in active_regs if r.get("registration_id")], cancelled_tickets=cancelled_tickets, event_counts=latest_counts)


@app.route("/api/events/<event_id>/rate", methods=["POST"])
@app.route("/rate-event", methods=["POST"])
@app.route("/api/submit-review", methods=["POST"])
@app.route("/submit-review", methods=["POST"])
def rate_event(event_id=None):
    data = get_data()
    event_id = event_id or clean(data.get("event_id"))
    email = clean_lower(data.get("email") or data.get("user_email") or session.get("user_email"))
    try:
        rating = int(float(data.get("rating")))
    except Exception:
        return jsonify(success=False, error="Rating must be from 1 to 5"), 400
    if rating < 1 or rating > 5:
        return jsonify(success=False, error="Rating must be from 1 to 5"), 400
    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return jsonify(success=False, error="Event not found"), 404
    reg = registrations_collection.find_one({"event_id": event_id, "user_email": email})
    if not reg:
        return jsonify(success=False, error="Only registered users can rate this event"), 403
    if not event_is_completed(event):
        return jsonify(success=False, error="You can rate only after the event is completed"), 400
    user_doc = find_user(email) or {}
    existing_review = reviews_collection.find_one({"event_id": event_id, "user_email": email})
    if existing_review:
        return jsonify(
            success=False,
            error="You have already rated this event. Ratings can be submitted only once.",
            already_rated=True,
            rating=existing_review.get("rating"),
        ), 409

    comment = clean(data.get("comment") or data.get("review") or data.get("feedback"))
    organiser_email = clean_lower(event.get("organiser_email") or event.get("organizer_email") or event.get("host_email") or event.get("contact_email"))
    organiser_name = event.get("organiser_name") or event.get("organizer_name") or event.get("host_name") or "Organiser"
    review_doc = {
        "review_id": f"REV{10001 + reviews_collection.count_documents({})}",
        "event_id": event_id,
        "event_title": event.get("title"),
        "organiser_email": organiser_email,
        "organizer_email": organiser_email,
        "host_email": organiser_email,
        "organiser_name": organiser_name,
        "user_email": email,
        "user_name": clean(data.get("user_name") or data.get("name") or user_doc.get("name") or "User"),
        "rating": rating,
        "comment": comment,
        "review": comment,
        "updated_at": now(),
    }
    review_doc["created_at"] = now()
    reviews_collection.insert_one(clean_mongo_update(review_doc))
    summary = review_summary_for_event(event_id)
    events_collection.update_one({"event_id": event_id}, {"$set": {"average_rating": summary["average_rating"], "total_ratings": summary["total_ratings"]}})
    auto_ban_organiser_for_bad_reviews(organiser_email)
    create_notification(organiser_email, "New rating received", f"{event.get('title', 'Your event')} received a {rating}/5 rating.", "host", {"type": "rating", "related_event_id": event_id})
    create_notification("admin@orbit.com", "New event review", f"{event.get('title', 'Your event')} received a {rating}/5 review.", "admin", {"type": "rating", "related_event_id": event_id})
    return jsonify(success=True, message="Rating saved", summary=summary)


@app.route("/api/host/reviews")
def host_reviews():
    host_email = clean_lower(request.args.get("host_email") or session.get("host_email"))
    host, error_response = require_existing_host(host_email)
    if error_response:
        return error_response
    host_email = clean_lower(host.get("email") or host_email)

    reviews = [serialize_doc(r) for r in reviews_collection.find({
        "$or": [
            {"organiser_email": host_email},
            {"organizer_email": host_email},
            {"host_email": host_email}
        ]
    }).sort("created_at", -1)]
    by_event = {}
    total_rating_sum = 0
    total_reviews = 0
    five_star_reviews = 0

    for r in reviews:
        try:
            rating = int(float(r.get("rating") or 0))
        except Exception:
            rating = 0
        if rating <= 0:
            continue
        total_reviews += 1
        total_rating_sum += rating
        if rating == 5:
            five_star_reviews += 1

        event_key = r.get("event_id") or r.get("event_title") or "unknown"
        item = by_event.setdefault(event_key, {
            "event_id": r.get("event_id"),
            "event_title": r.get("event_title") or "Event",
            "total_ratings": 0,
            "rating_sum": 0,
            "average_rating": 0,
        })
        item["total_ratings"] += 1
        item["rating_sum"] += rating
        item["average_rating"] = round(item["rating_sum"] / item["total_ratings"], 2)

    event_summaries = sorted(by_event.values(), key=lambda item: (item.get("average_rating", 0), item.get("total_ratings", 0)), reverse=True)
    overall_summary = {
        "total_reviews": total_reviews,
        "average_rating": round(total_rating_sum / total_reviews, 2) if total_reviews else 0,
        "reviewed_events": len(event_summaries),
        "five_star_reviews": five_star_reviews,
    }
    return jsonify(
        success=True,
        reviews=reviews,
        overall_summary=overall_summary,
        event_summaries=event_summaries,
        summaries=event_summaries,
    )


def generate_otp():
    return str(random.randint(100000, 999999))


def send_mail(to_email, otp):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_EMAIL and SMTP_PASSWORD are not set. Use your Gmail and Gmail App Password.")

    msg = MIMEText(
        f"Hi,\n\nYour Orbit password reset OTP is {otp}.\n\nThis OTP expires in 10 minutes. Do not share it with anyone.\n\n- Orbit",
        "plain",
        "utf-8"
    )
    msg["Subject"] = "Orbit Password Reset OTP"
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())


@app.route("/send-otp", methods=["POST"])
@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    data = get_data()

    email = clean_lower(data.get("email"))
    role = clean_lower(data.get("role") or "user")

    if role not in ["user", "host"]:
        role = "user"

    # Basic validation: email must be provided and syntactically valid.
    if not email:
        return jsonify(success=False, error="Email is required"), 400
    if not valid_email(email):
        return jsonify(success=False, error="Enter a valid email address"), 400

    # Validate that the account exists before sending OTP
    try:
        account = find_host(email) if role == "host" else find_user(email)
    except Exception:
        account = None

    if not account:
        error_msg = f"No {'organizer/host' if role == 'host' else 'user'} account found with this email"
        return jsonify(success=False, error=error_msg), 404

    otp = generate_otp()

    otp_collection.delete_many({"email": email, "role": role})
    otp_collection.insert_one({
        "email": email,
        "role": role,
        "otp": otp,
        "verified": False,
        "expires_at": now() + timedelta(minutes=10),
        "created_at": now(),
    })

    debug_mode = OTP_DEBUG_MODE or not SMTP_EMAIL or not SMTP_PASSWORD

    try:
        if not SMTP_EMAIL or not SMTP_PASSWORD:
            raise RuntimeError("SMTP_EMAIL and SMTP_PASSWORD are not configured")
        send_mail(email, otp)
        return jsonify(success=True, message="OTP sent successfully to your email")
    except Exception as e:
        print("❌ OTP email sending failed:", e)
        if debug_mode:
            return jsonify(success=True, message="OTP generated in debug mode", debug_otp=otp)
        otp_collection.delete_many({"email": email, "role": role})
        return jsonify(
            success=False,
            error="OTP email could not be sent. Check Flask terminal and Gmail App Password settings.",
            details=str(e),
        ), 500


@app.route("/verify-otp", methods=["POST"])
@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data = get_data()

    email = clean_lower(data.get("email"))
    role = clean_lower(data.get("role") or "user")
    otp = clean(data.get("otp"))

    record = otp_collection.find_one({"email": email, "role": role, "otp": otp})

    if not record:
        return jsonify(success=False, error="Invalid OTP"), 400

    if record.get("expires_at") < now():
        return jsonify(success=False, error="OTP expired"), 400

    otp_collection.update_one({"_id": record["_id"]}, {"$set": {"verified": True}})

    return jsonify(success=True, message="OTP verified")


@app.route("/reset-password", methods=["POST"])
@app.route("/api/reset-password", methods=["POST"])
def reset_password():
    data = get_data()

    email = clean_lower(data.get("email"))
    role = clean_lower(data.get("role") or "user")
    otp = clean(data.get("otp"))
    password = clean(data.get("password") or data.get("new_password"))

    if not otp:
        return jsonify(success=False, error="OTP is required"), 400

    if not strong_password(password):
        return jsonify(success=False, error="Password must be at least 8 characters"), 400

    record = otp_collection.find_one({"email": email, "role": role, "otp": otp, "verified": True})

    if not record:
        return jsonify(success=False, error="Invalid OTP or OTP verification required"), 400

    if record.get("expires_at") < now():
        return jsonify(success=False, error="OTP expired"), 400

    collection = hosts_collection if role == "host" else users_collection

    result = collection.update_one(
        email_query(email),
        {"$set": {"password": generate_password_hash(password), "updated_at": now()}},
    )

    if result.matched_count == 0:
        return jsonify(success=False, error="Account not found"), 404

    otp_collection.delete_many({"email": email, "role": role})

    return jsonify(
        success=True,
        message="Password reset successful",
        redirect="/hlog.html" if role == "host" else "/login.html",
    )



@app.route("/send-reset-otp", methods=["POST"])
def send_reset_otp():
    return send_otp()


@app.route("/verify-reset-otp", methods=["POST"])
def verify_reset_otp():
    return verify_otp()


@app.route("/reset-password-with-otp", methods=["POST"])
def reset_password_with_otp():
    return reset_password()



def delete_many_or_safe(collection, query):
    try:
        return collection.delete_many(query).deleted_count
    except Exception as exc:
        print("Cascade delete warning:", exc)
        return 0

def purge_orphan_admin_data():
    """Remove only records that truly belong to deleted accounts.

    IMPORTANT FIX:
    The old purge query used {"email": {"$nin": users}}.
    In MongoDB, $nin also matches documents where the field is missing, so ADP
    could delete valid registrations/payments because those rows normally store
    user_email, not email. This is why admin showed 0 registrations/payments
    after opening the dashboard.
    """
    # Safety guard: allow disabling purge via environment variable during testing.
    # By default, purging is DISABLED to avoid accidental deletion of valid test data.
    if os.environ.get("ORBIT_DISABLE_PURGE", "1") == "1":
        return

    user_emails = {
        clean_lower(u.get("email"))
        for u in users_collection.find({}, {"email": 1})
        if clean_lower(u.get("email"))
    }
    host_emails = {
        clean_lower(h.get("email"))
        for h in hosts_collection.find({}, {"email": 1})
        if clean_lower(h.get("email"))
    }
    event_ids = {
        clean(e.get("event_id"))
        for e in events_collection.find({}, {"event_id": 1})
        if clean(e.get("event_id"))
    }

    def field_exists_not_in(field, allowed_values):
        return {field: {"$exists": True, "$ne": "", "$nin": list(allowed_values)}}

    # Remove events whose organiser account was deleted, but only when an
    # organiser email is actually present.
    deleted_event_ids = []
    for ev in events_collection.find({}, {"event_id": 1, "organiser_email": 1, "host_email": 1, "organizer_email": 1, "owner_email": 1}):
        organiser_email = clean_lower(ev.get("organiser_email") or ev.get("host_email") or ev.get("organizer_email") or ev.get("owner_email"))
        if organiser_email and organiser_email not in host_emails:
            eid = clean(ev.get("event_id"))
            if eid:
                deleted_event_ids.append(eid)

    if deleted_event_ids:
        events_collection.delete_many({"event_id": {"$in": deleted_event_ids}})
        event_ids.difference_update(deleted_event_ids)

    # Never use plain {"field": {"$nin": ...}} here because it matches missing
    # fields. Only purge when the field exists and is invalid.
    user_or_event_orphan = {"$or": [
        field_exists_not_in("user_email", user_emails),
        field_exists_not_in("email", user_emails),
        field_exists_not_in("event_id", event_ids),
    ]}
    delete_many_or_safe(registrations_collection, user_or_event_orphan)
    delete_many_or_safe(payments_collection, user_or_event_orphan)
    delete_many_or_safe(refunds_collection, user_or_event_orphan)

    review_orphan = {"$or": [
        field_exists_not_in("user_email", user_emails),
        field_exists_not_in("email", user_emails),
        field_exists_not_in("organiser_email", host_emails),
        field_exists_not_in("host_email", host_emails),
        field_exists_not_in("event_id", event_ids),
    ]}
    delete_many_or_safe(reviews_collection, review_orphan)

    settlement_orphan = {"$or": [
        field_exists_not_in("organiser_email", host_emails),
        field_exists_not_in("host_email", host_emails),
        field_exists_not_in("event_id", event_ids),
    ]}
    delete_many_or_safe(settlements_collection, settlement_orphan)
    delete_many_or_safe(reports_collection, settlement_orphan)

    allowed_notification_emails = user_emails | host_emails
    notification_orphan = {"$or": [
        {"recipient_type": "admin"},
        {"email": "admin@orbit.com"},
        {"recipient_id": "admin@orbit.com"},
        {"recipient_email": "admin@orbit.com"},
        field_exists_not_in("email", allowed_notification_emails),
        field_exists_not_in("recipient_id", allowed_notification_emails),
        field_exists_not_in("recipient_email", allowed_notification_emails),
    ]}
    delete_many_or_safe(notifications_collection, notification_orphan)

def hard_delete_user_data(email):
    """COMPLETE DATA DELETION: Remove ALL traces of a user account from all collections.
    
    This is called when a user deletes their account. EVERY piece of data associated
    with this email must be removed from the database so the account cannot be recreated
    with old data appearing. This is a permanent, irreversible operation.
    """
    email = clean_lower(email)
    if not email:
        return {}
    
    counts = {}
    
    # Get all registration and event references first
    regs = list(registrations_collection.find({"$or": [{"user_email": email}, {"email": email}]}, {"registration_id": 1, "event_id": 1}))
    reg_ids = [clean(r.get("registration_id")) for r in regs if clean(r.get("registration_id"))]
    event_ids = [clean(r.get("event_id")) for r in regs if clean(r.get("event_id"))]
    
    # DELETE FROM REGISTRATIONS - comprehensive match on all possible email fields
    counts["registrations"] = registrations_collection.delete_many({
        "$or": [
            {"user_email": email},
            {"email": email},
            {"user_email_lower": email},
        ]
    }).deleted_count
    
    # DELETE FROM PAYMENTS - all payment records tied to this email
    counts["payments"] = payments_collection.delete_many({
        "$or": [
            {"user_email": email},
            {"email": email},
            {"user_email_lower": email},
            {"registration_id": {"$in": reg_ids}},
        ]
    }).deleted_count
    
    # DELETE FROM REFUNDS - all refund requests
    counts["refunds"] = refunds_collection.delete_many({
        "$or": [
            {"user_email": email},
            {"email": email},
            {"user_email_lower": email},
            {"registration_id": {"$in": reg_ids}},
        ]
    }).deleted_count
    
    # DELETE FROM REVIEWS - user as reviewer or review subject
    counts["reviews"] = reviews_collection.delete_many({
        "$or": [
            {"user_email": email},
            {"email": email},
            {"user_email_lower": email},
            {"reviewer_email": email},
            {"reviewer_email_lower": email},
        ]
    }).deleted_count
    
    # DELETE FROM NOTIFICATIONS - all user notifications
    counts["notifications"] = notifications_collection.delete_many({
        "$or": [
            {"email": email},
            {"recipient_id": email},
            {"user_email": email},
            {"recipient_email": email},
            {"email_lower": email},
            {"user_email_lower": email},
        ]
    }).deleted_count
    
    # DELETE FROM OTP/REGISTRATION - temporary auth codes
    counts["otps"] = otp_collection.delete_many({
        "$or": [
            {"email": email},
            {"email_lower": email},
            {"user_email": email},
        ]
    }).deleted_count
    
    # DELETE FROM REGISTRATIONS TEMP - cleanup registrations table
    counts["registrations_temp"] = registrations_collection.delete_many({
        "registration_id": {"$in": reg_ids}
    }).deleted_count
    
    # DELETE THE USER ACCOUNT ITSELF
    counts["user"] = users_collection.delete_one(email_query(email)).deleted_count
    
    # CLEANUP REVERSE REFERENCES - remove this email from other users' saved/interested/registered lists
    users_collection.update_many(
        {},
        {"$pull": {
            "saved_events": {"$in": event_ids},
            "interested_events": {"$in": event_ids},
            "registered_events": {"$in": event_ids}
        }}
    )
    
    # Sync updated event registration counts
    for eid in set(event_ids):
        if eid:
            try:
                sync_event_registration_counts(eid)
            except Exception:
                pass
    
    return counts

def hard_delete_host_data(email):
    """COMPLETE DATA DELETION: Remove ALL traces of a host/organizer account.
    
    This is called when a host/organizer deletes their account. EVERY piece of data
    associated with this email must be removed from the database including:
    - All events created by this organizer
    - All registrations for those events
    - All payments/refunds/settlements related to those events
    - All reviews and reports
    - All notifications
    - The host account record itself
    
    This is a permanent, irreversible operation.
    """
    email = clean_lower(email)
    if not email:
        return {}
    
    counts = {}
    
    # Get all events hosted by this organizer
    event_ids = [clean(e.get("event_id")) for e in events_collection.find({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"organizer_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
        ]
    }, {"event_id": 1}) if clean(e.get("event_id"))]
    
    # Get all registrations for those events
    reg_ids = [clean(r.get("registration_id")) for r in registrations_collection.find({
        "event_id": {"$in": event_ids}
    }, {"registration_id": 1}) if clean(r.get("registration_id"))]
    
    # DELETE EVENTS - all events created by this organizer
    counts["events"] = events_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"organizer_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
        ]
    }).deleted_count
    
    # DELETE REGISTRATIONS - all registrations for those events
    counts["registrations"] = registrations_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
        ]
    }).deleted_count
    
    # DELETE PAYMENTS - all payments related to those events
    counts["payments"] = payments_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
            {"registration_id": {"$in": reg_ids}},
        ]
    }).deleted_count
    
    # DELETE REFUNDS - all refunds related to those events
    counts["refunds"] = refunds_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
            {"registration_id": {"$in": reg_ids}},
        ]
    }).deleted_count
    
    # DELETE SETTLEMENTS - all payment settlements
    counts["settlements"] = settlements_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
        ]
    }).deleted_count
    
    # DELETE REVIEWS - all reviews for events or by organizer
    counts["reviews"] = reviews_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
        ]
    }).deleted_count
    
    # DELETE REPORTS - all reports for events
    counts["reports"] = reports_collection.delete_many({
        "$or": [
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"event_id": {"$in": event_ids}},
        ]
    }).deleted_count
    
    # DELETE NOTIFICATIONS - all notifications for this organizer
    counts["notifications"] = notifications_collection.delete_many({
        "$or": [
            {"email": email},
            {"recipient_id": email},
            {"organiser_email": email},
            {"host_email": email},
            {"owner_email": email},
            {"organiser_email_lower": email},
            {"host_email_lower": email},
            {"recipient_email": email},
            {"related_event_id": {"$in": event_ids}},
        ]
    }).deleted_count
    
    # DELETE OTP/AUTH - temporary auth codes
    counts["otps"] = otp_collection.delete_many({
        "$or": [
            {"email": email},
            {"email_lower": email},
            {"host_email": email},
        ]
    }).deleted_count
    
    # DELETE THE HOST ACCOUNT ITSELF
    counts["host"] = hosts_collection.delete_one(email_query(email)).deleted_count
    
    # CLEANUP REVERSE REFERENCES - remove saved events from users' lists
    if event_ids:
        users_collection.update_many(
            {},
            {"$pull": {
                "saved_events": {"$in": event_ids},
                "interested_events": {"$in": event_ids},
                "registered_events": {"$in": event_ids}
            }}
        )
    
    return counts


@app.route("/api/user/delete-account", methods=["POST", "DELETE"])
@app.route("/delete-user-account", methods=["POST", "DELETE"])
def delete_user_account():
    data = get_data()
    email = clean_lower(data.get("email") or session.get("user_email"))
    if not email:
        return jsonify(success=False, error="User not logged in"), 401
    user = find_user(email)
    if not user:
        clear_user_session_only()
        purge_orphan_admin_data()
        return jsonify(success=True, message="User account already deleted", redirect="/signup.html")
    deleted = hard_delete_user_data(email)
    purge_orphan_admin_data()
    clear_user_session_only()
    return jsonify(success=True, message="User account and all related Orbit data deleted permanently", deleted=deleted, redirect="/signup.html")


@app.route("/delete-account", methods=["POST", "DELETE"])
def delete_account_router():
    data = get_data()
    role = clean_lower(data.get("role") or data.get("user_type") or session.get("role"))
    if role in ["host", "organiser", "organizer"]:
        return delete_host_account()
    if role == "user":
        return delete_user_account()
    # If role is not supplied, infer from the supplied email.
    email = clean_lower(data.get("email") or data.get("host_email") or data.get("organiser_email") or data.get("user_email"))
    if find_host(email):
        return delete_host_account()
    if find_user(email):
        return delete_user_account()
    clear_user_session_only(); clear_host_session_only()
    return jsonify(success=True, message="Account already deleted", redirect="/login.html")


@app.route("/api/host/delete-account", methods=["POST", "DELETE"])
@app.route("/api/host/delete-account/", methods=["POST", "DELETE"])
@app.route("/delete-host-account", methods=["POST", "DELETE"])
@app.route("/delete-host-account/", methods=["POST", "DELETE"])
def delete_host_account():
    data = get_data()
    email = clean_lower(
        data.get("email")
        or data.get("hostEmail")
        or data.get("host_email")
        or data.get("organiser_email")
        or data.get("organizer_email")
        or session.get("host_email")
    )
    if not email:
        return jsonify(success=False, error="Host not logged in", redirect="/hsign.html"), 401
    host = find_host(email)
    if not host:
        clear_host_session_only()
        purge_orphan_admin_data()
        return jsonify(success=True, message="Organiser account already deleted", redirect="/hsign.html")
    deleted = hard_delete_host_data(email)
    purge_orphan_admin_data()
    clear_host_session_only()
    return jsonify(success=True, message="Organiser account, events, and all related Orbit data deleted permanently", deleted=deleted, redirect="/hsign.html")

@app.route("/debug/users")
def debug_users():
    users = list(users_collection.find({}, {"password": 0}))

    for u in users:
        u["_id"] = str(u["_id"])
        u["created_at"] = safe_dt(u.get("created_at"))
        u["updated_at"] = safe_dt(u.get("updated_at"))

    return jsonify(users)


@app.route("/debug/hosts")
def debug_hosts():
    hosts = list(hosts_collection.find({}, {"password": 0}))

    for h in hosts:
        h["_id"] = str(h["_id"])
        h["created_at"] = safe_dt(h.get("created_at"))
        h["updated_at"] = safe_dt(h.get("updated_at"))

    return jsonify(hosts)


@app.route("/debug/events")
def debug_events():
    events = list(events_collection.find({}))

    for e in events:
        e["_id"] = str(e["_id"])
        e["created_at"] = safe_dt(e.get("created_at"))
        e["updated_at"] = safe_dt(e.get("updated_at"))

    return jsonify(events)


@app.route("/debug/registrations")
def debug_registrations():
    regs = list(registrations_collection.find({}))

    for r in regs:
        r["_id"] = str(r["_id"])
        r["registered_at"] = safe_dt(r.get("registered_at"))

    return jsonify(regs)


@app.route("/debug/registrations/<email>", methods=["GET"])
def debug_registrations_by_email(email):
    """Debug endpoint to see all registrations for a specific user email."""
    email = clean_lower(email)
    if not email:
        return jsonify({"error": "Email required"}), 400
    
    # Find registrations
    regs = list(registrations_collection.find({"user_email": email}))
    
    # Find user
    user = find_user(email)
    
    # Format response
    for r in regs:
        r["_id"] = str(r["_id"])
        r["registered_at"] = safe_dt(r.get("registered_at"))
        r["created_at"] = safe_dt(r.get("created_at"))
        r["cancelled_at"] = safe_dt(r.get("cancelled_at"))
    
    return jsonify({
        "email": email,
        "user_exists": bool(user),
        "user_created_at": safe_dt(user.get("created_at")) if user else None,
        "registration_count": len(regs),
        "registrations": regs
    })


@app.route("/debug/otps")
def debug_otps():
    otps = list(otp_collection.find({}))

    for o in otps:
        o["_id"] = str(o["_id"])
        o["expires_at"] = safe_dt(o.get("expires_at"))
        o["created_at"] = safe_dt(o.get("created_at"))

    return jsonify(otps)



def current_month_range():
    start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


@app.route("/cancel-event", methods=["POST", "PUT"])
@app.route("/api/events/<event_id>/cancel", methods=["POST", "PUT"])
def cancel_event(event_id=None):
    data = get_data()
    event_id = event_id or clean(data.get("event_id"))
    host_email = clean_lower(data.get("host_email") or data.get("owner_email") or session.get("host_email") or data.get("organiser_email") or data.get("organizer_email"))
    reason = clean(data.get("reason") or data.get("cancel_reason") or data.get("cancellation_reason"))
    refund_all_requested = bool(data.get("refund_all_requested", True))

    if not event_id:
        return jsonify(success=False, error="Event ID is required"), 400
    if not reason:
        return jsonify(success=False, error="Cancellation reason is required"), 400

    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return jsonify(success=False, error="Event not found"), 404

    repair_duplicate_active_registrations(event_id=event_id)
    event = format_event(event)
    organiser_email = clean_lower(event.get("host_email") or event.get("organizer_email") or event.get("organiser_email"))
    if host_email and organiser_email and organiser_email != host_email:
        return jsonify(success=False, error="You can cancel only your event"), 403
    if not host_email:
        host_email = organiser_email

    if clean_lower(event.get("status")) == "cancelled":
        return jsonify(success=True, message="This event is already cancelled.", monthly_limit=2, event=event)

    if event_is_completed(event):
        return jsonify(success=False, error="Past or completed events cannot be cancelled. They stay visible under past activity."), 400

    month_start, month_end = current_month_range()
    cancelled_this_month = events_collection.count_documents({
        "$or": [{"organiser_email": host_email}, {"organizer_email": host_email}, {"host_email": host_email}],
        "status": {"$regex": "^cancelled$", "$options": "i"},
        "cancelled_at": {"$gte": month_start, "$lt": month_end},
    })
    if cancelled_this_month >= 2:
        return jsonify(success=False, error="Monthly cancellation limit reached. Please contact Orbit support."), 400

    cancelled_at = now()
    events_collection.update_one({"event_id": event_id}, {"$set": {
        "status": "Cancelled",
        "approval_status": "Cancelled",
        "payment_status": "Refund requested" if refund_all_requested else event.get("payment_status", "Held by Orbit"),
        "settlement_status": "Refund requested" if refund_all_requested else event.get("settlement_status", "Held by Orbit"),
        "cancel_reason": reason,
        "cancellation_reason": reason,
        "refund_all_requested": refund_all_requested,
        "cancelled_at": cancelled_at,
        "updated_at": cancelled_at,
    }})

    regs = list(registrations_collection.find({"event_id": event_id}))
    registrations_collection.update_many({"event_id": event_id}, {"$set": {
        "status": "cancelled_by_organiser",
        "registration_status": "cancelled_by_organiser",
        "cancellation_reason": reason,
        "cancelled_at": cancelled_at,
        "refund_requested": refund_all_requested,
        "refund_status": "Pending" if refund_all_requested else "Not requested",
        "payment_status": "Refund requested" if refund_all_requested else "Cancelled",
    }})
    payments_collection.update_many({"event_id": event_id}, {"$set": {
        "refund_requested": refund_all_requested,
        "refund_status": "Pending" if refund_all_requested else "Not requested",
        "payment_status": "Refund requested" if refund_all_requested else "Cancelled",
        "settlement_status": "Refund requested" if refund_all_requested else "Cancelled",
        "cancelled_at": cancelled_at,
    }})
    users_collection.update_many({"registered_events": event_id}, {"$pull": {"registered_events": event_id}})
    sync_event_registration_counts(event_id)

    event_title = event.get("title", "your event")
    refund_docs = []
    for reg in regs:
        user_email = clean_lower(reg.get("user_email"))
        amount_paid = int(float(reg.get("amount_paid") or reg.get("amount") or 0))
        refundable_amount = int(float(reg.get("event_amount") or event.get("price") or 0)) if refund_all_requested and amount_paid > 0 else 0
        if refund_all_requested and amount_paid > 0:
            refund_doc = {
                "refund_id": f"REF{10001 + refunds_collection.count_documents({})}",
                "registration_id": reg.get("registration_id"),
                "event_id": event_id,
                "event_title": event_title,
                "user_email": user_email,
                "user_name": reg.get("user_name", ""),
                "amount": refundable_amount,
                "platform_fee_kept": PLATFORM_USER_FEE,
                "reason": reason,
                "refund_status": "Pending",
                "status": "Pending",
                "requested_at": cancelled_at,
                "requested_by": "organiser",
                "user_upi_id": reg.get("user_upi_id", ""),
            }
            refunds_collection.update_one({"registration_id": reg.get("registration_id")}, {"$set": clean_mongo_update(refund_doc)}, upsert=True)
            refund_doc.pop("_id", None)
            refund_docs.append(refund_doc)
        if user_email:
            create_notification(
                user_email,
                "Event cancelled",
                f"This event has been cancelled. Reason: {reason}",
                "user",
                {"type": "event_cancelled", "related_event_id": event_id},
            )

    if host_email:
        create_notification(
            host_email,
            "Cancellation processed",
            "This event has been cancelled. Registered users will be notified by Orbit.",
            "host",
            {"type": "event_cancelled", "related_event_id": event_id},
        )

    if refund_docs:
        # Cancellation/refund rows are saved for ADP; no admin notification row.
        pass

    updated = events_collection.find_one({"event_id": event_id})
    return jsonify(
        success=True,
        message="This event has been cancelled. Registered users will be notified by Orbit.",
        cancelled_count=cancelled_this_month + 1,
        monthly_limit=2,
        affected_registrations=len(regs),
        refunds=refund_docs,
        event=format_event(updated),
    )


@app.route("/delete-event", methods=["POST", "DELETE"])
@app.route("/api/events/<event_id>/delete", methods=["POST", "DELETE"])
@app.route("/api/organiser/event/delete/<event_id>", methods=["POST", "DELETE"])
def delete_event(event_id=None):
    """Permanently delete a fresh organiser event from MongoDB.

    Important Orbit rule:
    - If an event already has any registration/payment/refund/settlement/review
      history, it must be cancelled instead of deleted so admin records stay safe.
    - If it has no linked records, it can be removed completely from MongoDB and
      from user saved/interested/registered references.
    """
    data = get_data()
    event_id = clean(event_id or data.get("event_id") or data.get("id"))
    host_email = clean_lower(
        data.get("host_email")
        or data.get("organiser_email")
        or data.get("organizer_email")
        or session.get("host_email")
        or session.get("email")
    )

    if not event_id:
        return jsonify(success=False, error="Event ID is required."), 400

    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return jsonify(success=True, message="Event already deleted.", deleted=True, event_id=event_id)

    organiser_email = clean_lower(event.get("host_email") or event.get("organiser_email") or event.get("organizer_email") or event.get("email"))
    if host_email and organiser_email and host_email != organiser_email:
        return jsonify(success=False, error="You can delete only your own event."), 403

    linked_counts = {
        "registrations": registrations_collection.count_documents({"event_id": event_id}),
        "payments": payments_collection.count_documents({"event_id": event_id}),
        "refunds": refunds_collection.count_documents({"event_id": event_id}),
        "settlements": settlements_collection.count_documents({"event_id": event_id}),
        "reviews": reviews_collection.count_documents({"event_id": event_id}),
    }
    linked_total = sum(linked_counts.values())
    if linked_total > 0:
        return jsonify(
            success=False,
            error="This event already has registration/payment/refund/settlement/review history. Cancel it instead of deleting so admin records stay accurate.",
            linked_counts=linked_counts,
        ), 400

    deleted = events_collection.delete_one({"event_id": event_id}).deleted_count
    # Clean harmless event-only references so it disappears everywhere.
    notifications_collection.delete_many({"$or": [
        {"event_id": event_id},
        {"related_event_id": event_id},
        {"target_event_id": event_id},
    ]})
    reports_collection.delete_many({"event_id": event_id})
    users_collection.update_many({}, {"$pull": {
        "saved_events": event_id,
        "interested_events": event_id,
        "registered_events": event_id,
    }})

    return jsonify(success=True, deleted=bool(deleted), event_id=event_id, message="Event permanently deleted from MongoDB.")

# ===================== ADMIN PORTAL - WORKING CONNECTION =====================
# Login page: /admin.html
# Dashboard page: /adp.html
# Login credentials reset automatically:
# username: admin
# email: admin@orbit.com
# password: admin12345

def ensure_default_admin():
    admin_email = "admin@orbit.com"
    admin_password = "admin12345"
    admins_collection.update_one(
        {"email": admin_email},
        {
            "$set": {
                "name": "Orbit Admin",
                "username": "admin",
                "email": admin_email,
                "password": generate_password_hash(admin_password),
                "role": "admin",
                "updated_at": now(),
            },
            "$setOnInsert": {"created_at": now()},
        },
        upsert=True,
    )


# This runs when Flask starts, so old wrong passwords cannot break login.
ensure_default_admin()


@app.route("/admin.html", methods=["GET"])
@app.route("/admin", methods=["GET"])
def admin_page():
    return render_page("admin.html")


@app.route("/adp.html", methods=["GET"])
@app.route("/admin-dashboard", methods=["GET"])
def adp_page():
    # Render dashboard page; API endpoints verify admin data. Do not redirect on
    # refresh just because another tab changed the shared Flask role cookie.
    return render_page("adp.html", admin_name=session.get("admin_name", "Orbit Admin"))


@app.route("/admin-login", methods=["POST"])
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = get_data()

    username_or_email = clean_lower(
        data.get("username")
        or data.get("email")
        or data.get("adminUser")
        or data.get("admin_username")
    )
    password = clean(data.get("password") or data.get("adminPass") or data.get("admin_password"))

    # Force reset before login check.
    ensure_default_admin()

    if not username_or_email or not password:
        msg = "Enter admin username/email and password"
        if request.is_json:
            return jsonify(success=False, error=msg), 400
        return jsonify(success=False, error=msg), 400

    # Accept both username and email from the admin form.
    if username_or_email in ["admin", "orbitadmin", "orbit admin"]:
        username_or_email = "admin@orbit.com"

    admin = admins_collection.find_one({
        "$or": [
            {"username": username_or_email},
            {"email": username_or_email},
        ]
    })

    if not admin:
        msg = "Admin account not found"
        if request.is_json:
            return jsonify(success=False, error=msg), 404
        return jsonify(success=False, error=msg), 404

    stored_password = admin.get("password", "")
    password_ok = False
    try:
        password_ok = check_password_hash(stored_password, password)
    except Exception:
        password_ok = False

    # fallback only for testing, if password was manually typed plain in Compass
    if stored_password == password:
        password_ok = True

    if not password_ok:
        msg = "Invalid password"
        if request.is_json:
            return jsonify(success=False, error=msg), 401
        return jsonify(success=False, error=msg), 401

    if admin.get("banned") or admin.get("is_banned"):
        msg = "Admin account has been banned"
        if request.is_json:
            return jsonify(success=False, error=msg), 403
        return jsonify(success=False, error=msg), 403

    set_active_admin_session(admin)

    # JSON request gets JSON; normal HTML form gets direct redirect.
    if request.is_json:
        return jsonify(
            success=True,
            message="Admin login successful",
            redirect="/adp.html",
            admin={
                "name": session["admin_name"],
                "email": session["admin_email"],
                "role": "admin",
            },
        )

    return redirect("/adp.html")


@app.route("/admin-logout", methods=["GET", "POST"])
def admin_logout():
    clear_admin_session_only()
    return redirect("/admin.html")


@app.route("/admin-reset-default", methods=["GET"])
def admin_reset_default():
    ensure_default_admin()
    return jsonify(
        success=True,
        message="Admin reset successful",
        username="admin",
        email="admin@orbit.com",
        password="admin12345",
        dashboard="/adp.html",
    )


@app.route("/admin-check", methods=["GET"])
def admin_check():
    ensure_default_admin()
    admin = admins_collection.find_one({"email": "admin@orbit.com"}, {"password": 0})
    if admin:
        admin["_id"] = str(admin["_id"])
        admin["created_at"] = safe_dt(admin.get("created_at"))
        admin["updated_at"] = safe_dt(admin.get("updated_at"))
    return jsonify(
        success=True,
        message="Correct app.py is running",
        admin_exists=bool(admin),
        admin=admin,
        login_username="admin",
        login_email="admin@orbit.com",
        login_password="admin12345",
        dashboard="/adp.html",
    )


@app.route("/api/admin/me", methods=["GET"])
def api_admin_me():
    admin_email = clean_lower(session.get("admin_email") or request.args.get("admin_email") or "admin@orbit.com")
    admin = admins_collection.find_one({"email": admin_email}) or admins_collection.find_one({"username": session.get("admin_username", "admin")})
    if not admin:
        return jsonify(success=False, error="Admin not logged in"), 401
    if admin.get("banned") or admin.get("is_banned"):
        return jsonify(success=False, error="Admin account has been banned", logged_in=False), 403
    return jsonify(success=True, admin={
        "name": admin.get("name", session.get("admin_name", "Orbit Admin")),
        "email": admin.get("email", admin_email),
        "username": admin.get("username", session.get("admin_username", "admin")),
        "role": "admin",
    })


@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    return jsonify(success=True, stats={
        "users": users_collection.count_documents({}),
        "organisers": hosts_collection.count_documents({}),
        "events": events_collection.count_documents({}),
        "registrations": registrations_collection.count_documents({}),
    })


@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    users = list(users_collection.find({}, {"password": 0}).sort("created_at", -1))
    for u in users:
        u["_id"] = str(u["_id"])
        u["created_at"] = safe_dt(u.get("created_at"))
        u["updated_at"] = safe_dt(u.get("updated_at"))
    return jsonify(success=True, users=users)


@app.route("/api/admin/organisers", methods=["GET"])
def api_admin_organisers():
    hosts = list(hosts_collection.find({}, {"password": 0}).sort("created_at", -1))
    for h in hosts:
        h["_id"] = str(h["_id"])
        h["created_at"] = safe_dt(h.get("created_at"))
        h["updated_at"] = safe_dt(h.get("updated_at"))
    return jsonify(success=True, organisers=hosts)


@app.route("/api/admin/events", methods=["GET"])
def api_admin_events():
    if not (session.get("admin_email") or session.get("role") == "admin"):
        return jsonify(success=False, error="Admin not logged in"), 401
    events = [format_event(e) for e in events_collection.find({}).sort("created_at", -1)]
    return jsonify(success=True, events=events, all_events=events)

def build_admin_dashboard_payload():
    purge_orphan_admin_data()
    mark_completed_events()
    repair_duplicate_active_registrations()
    users = [serialize_doc(u) for u in users_collection.find({}, {"password": 0}).sort("created_at", -1)]
    organisers = [serialize_doc(h) for h in hosts_collection.find({}, {"password": 0}).sort("created_at", -1)]
    events_raw = list(events_collection.find({}).sort("created_at", -1))
    events = [format_event(e) for e in events_raw]
    pending_events = [e for e in events if clean_lower(e.get("approval_status") or e.get("status")) == "pending"]
    regs_raw = [serialize_doc(r) for r in registrations_collection.find({"status": {"$ne": "duplicate_inactive"}, "registration_status": {"$ne": "duplicate_inactive"}}).sort("registered_at", -1)]
    reviews = [serialize_doc(r) for r in reviews_collection.find({}).sort("created_at", -1)]
    for r in reviews:
        r["created_at"] = safe_dt(r.get("created_at"))

    # Ensure every paid registration has a payment row that the admin portal can show.
    for r in regs_raw:
        event_amount = int(float(r.get("event_amount") or r.get("event_price") or 0)) * int(float(r.get("tickets") or 1)) if not r.get("event_amount") else int(float(r.get("event_amount") or 0))
        amount_paid = int(float(r.get("amount_paid") or r.get("amount") or 0))
        if amount_paid <= 0 and event_amount <= 0:
            continue
        if "platform_fee" not in r:
            r["platform_fee"] = int(float(r.get("platform_fee_earned") or r.get("admin_revenue") or (PLATFORM_USER_FEE + PLATFORM_ORGANISER_FEE)))
        payment_doc = {
            **r,
            "payment_id": r.get("payment_id") or f"PAY-{r.get('registration_id')}",
            "payment_receiver": "orbit",
            "payment_status": r.get("payment_status") or "Paid to Orbit",
            "settlement_status": r.get("settlement_status") or "Held by Orbit",
            "paid_at": r.get("paid_at") or r.get("registered_at"),
            "platform_fee": r.get("platform_fee") or r.get("platform_fee_earned") or r.get("admin_revenue") or 0,
        }
        payments_collection.update_one({"registration_id": r.get("registration_id")}, {"$set": clean_mongo_update(payment_doc)}, upsert=True)

    payments = [serialize_doc(p) for p in payments_collection.find({"status": {"$ne": "duplicate_inactive"}, "registration_status": {"$ne": "duplicate_inactive"}}).sort("created_at", -1)]
    for p in payments:
        p["platform_fee"] = int(float(p.get("platform_fee") or p.get("platform_fee_earned") or p.get("admin_revenue") or 0))
        p["payment_status"] = p.get("payment_status") or "Paid to Orbit"
        p["paid_at"] = safe_dt(p.get("paid_at") or p.get("created_at") or p.get("registered_at"))
        # Enrich payment rows with user and organiser UPI / QR data for admin visibility
        p["user_upi_id"] = p.get("user_upi_id") or p.get("upi_reference") or p.get("transaction_id") or ""
        try:
            user_email = clean_lower(p.get("user_email") or p.get("email") or p.get("recipient_email") or p.get("payer_email") or "")
            if user_email:
                u = users_collection.find_one({"email": {"$regex": f"^{re.escape(user_email)}$", "$options": "i"}}, {"profile": 1})
                if u and isinstance(u.get("profile"), dict):
                    p["user_upi_id"] = p.get("user_upi_id") or clean(u["profile"].get("upi_id") or u["profile"].get("upi") or "")
                    p["user_upi_qr"] = clean(u["profile"].get("upi_qr_image") or u["profile"].get("upi_qr") or u["profile"].get("user_qr_image") or "")
        except Exception:
            p["user_upi_qr"] = p.get("user_upi_qr") or ""
        try:
            organiser_email = clean_lower(p.get("organiser_email") or p.get("organizer_email") or p.get("host_email") or p.get("event_host_email") or "")
            organiser_upi = ""
            organiser_qr = ""
            # Prefer event-level fields if present
            if organiser_email:
                h = hosts_collection.find_one({"email": {"$regex": f"^{re.escape(organiser_email)}$", "$options": "i"}}, {"profile": 1, "upi_id": 1, "upi_qr_image": 1})
                if h:
                    organiser_upi = clean(h.get("upi_id") or (h.get("profile") or {}).get("upi_id") or "")
                    organiser_qr = clean(h.get("upi_qr_image") or (h.get("profile") or {}).get("upi_qr_image") or "")
            # Fallback to event data if host record not found
            if not organiser_upi:
                organiser_upi = clean(p.get("organiser_upi_id") or p.get("organizer_upi_id") or p.get("organiser_upi") or p.get("organizer_upi") or "")
            if not organiser_qr:
                organiser_qr = clean(p.get("organiser_upi_qr") or p.get("organizer_upi_qr") or p.get("organiser_qr") or p.get("org_qr") or "")
            p["organiser_upi_id"] = organiser_upi
            p["organiser_upi_qr"] = organiser_qr
        except Exception:
            p["organiser_upi_id"] = p.get("organiser_upi_id") or ""
            p["organiser_upi_qr"] = p.get("organiser_upi_qr") or ""

    # Professional admin view: one payment row per registration/payment.
    # This prevents the same user's single booking from appearing twice when a
    # payment row is rebuilt from a registration and also exists in payments.
    payment_map = {}
    for p in payments:
        reg_id = clean(p.get("registration_id"))
        pay_id = clean(p.get("payment_id"))
        if pay_id.startswith("PAY-") and not reg_id:
            reg_id = pay_id[4:]
        key = reg_id or pay_id or f"{clean_lower(p.get('user_email'))}|{clean(p.get('event_id') or p.get('event_title'))}|{p.get('amount_paid') or p.get('amount') or 0}"
        old = payment_map.get(key)
        # Prefer the richer/current record if two sources describe the same booking.
        if not old or len(str(p)) >= len(str(old)):
            payment_map[key] = p
    payments = sorted(payment_map.values(), key=lambda x: str(x.get("paid_at") or x.get("created_at") or x.get("registered_at") or ""), reverse=True)

    refunds = [serialize_doc(x) for x in refunds_collection.find({}).sort("requested_at", -1)]
    # Enrich refunds that are missing event/user data from registration rows
    reg_map = {r.get('registration_id'): r for r in regs_raw}
    for r in refunds:
        if not r.get('event_id') or not r.get('user_email'):
            reg = reg_map.get(r.get('registration_id'))
            if reg:
                r.setdefault('event_id', reg.get('event_id'))
                r.setdefault('event_title', reg.get('event_title'))
                r.setdefault('user_email', reg.get('user_email'))
                r.setdefault('user_name', reg.get('user_name'))
                r.setdefault('amount', r.get('amount') or reg.get('refundable_amount') or reg.get('amount_paid') or reg.get('amount') or 0)
                r.setdefault('requested_at', r.get('requested_at') or safe_dt(reg.get('cancelled_at') or reg.get('registered_at')))
    existing_refund_regs = {x.get("registration_id") for x in refunds}
    for r in regs_raw:
        if r.get("refund_requested") and r.get("registration_id") not in existing_refund_regs:
            refunds.append({
                "refund_id": r.get("refund_id") or r.get("registration_id"),
                "registration_id": r.get("registration_id"),
                "event_id": r.get("event_id"),
                "event_title": r.get("event_title"),
                "user_email": r.get("user_email"),
                "user_name": r.get("user_name"),
                "amount": r.get("refundable_amount") or r.get("event_amount") or r.get("amount_paid") or r.get("amount") or 0,
                "reason": r.get("cancellation_reason") or r.get("reason") or "Refund requested",
                "status": r.get("refund_status") or "Pending",
                "refund_status": r.get("refund_status") or "Pending",
                "requested_at": safe_dt(r.get("cancelled_at") or r.get("registered_at")),
                "user_upi_id": r.get("user_upi_id", ""),
            })
    for r in refunds:
        r["status"] = r.get("status") or r.get("refund_status") or "Pending"
        r["requested_at"] = safe_dt(r.get("requested_at"))

    refund_map = {}
    for r in refunds:
        key = clean(r.get("registration_id")) or clean(r.get("refund_id")) or f"{clean_lower(r.get('user_email'))}|{clean(r.get('event_id') or r.get('event_title'))}|{r.get('requested_at') or ''}"
        old = refund_map.get(key)
        if not old or len(str(r)) >= len(str(old)):
            refund_map[key] = r
    refunds = sorted(refund_map.values(), key=lambda x: str(x.get("requested_at") or x.get("created_at") or ""), reverse=True)

    stored_settlements = {x.get("event_id"): serialize_doc(x) for x in settlements_collection.find({})}
    event_map = {e.get("event_id"): e for e in events}
    settlement_map = {}
    for pmt in payments:
        if clean_lower(pmt.get("payment_status")) == "refunded" or clean_lower(pmt.get("refund_status")) in ["approved", "refunded"]:
            continue
        eid = pmt.get("event_id") or pmt.get("event_title")
        ev = event_map.get(eid, {})
        item = settlement_map.setdefault(eid, {
            "event_id": eid,
            "event_title": pmt.get("event_title") or ev.get("title", ""),
            "organiser_email": pmt.get("organiser_email") or ev.get("organiser_email", ""),
            "organiser_name": pmt.get("organiser_name") or ev.get("organiser_name", ""),
            "organiser_upi_id": ev.get("upi_id") or pmt.get("organiser_upi_id") or "",
            "total_paid_amount": 0,
            "total_collected": 0,
            "platform_fee": 0,
            "total_platform_fee_earned": 0,
            "organiser_payout_amount": 0,
            "total_organiser_payout": 0,
            "event_completion_status": "Completed" if event_is_completed(ev) else clean(ev.get("status") or "Upcoming"),
            "settlement_status": "Settlement ready" if event_is_completed(ev) else "Held by Orbit",
        })
        item["total_paid_amount"] += int(float(pmt.get("amount_paid") or pmt.get("amount") or 0))
        item["total_collected"] = item["total_paid_amount"]
        item["platform_fee"] += int(float(pmt.get("platform_fee") or pmt.get("platform_fee_earned") or pmt.get("admin_revenue") or 0))
        item["total_platform_fee_earned"] = item["platform_fee"]
        item["organiser_payout_amount"] += int(float(pmt.get("organiser_payout") or pmt.get("organizer_payout") or 0))
        item["total_organiser_payout"] = item["organiser_payout_amount"]
        if clean_lower(pmt.get("settlement_status")) in ["settled", "paid to organiser"]:
            item["settlement_status"] = "Paid to organiser"
    for eid, stored in stored_settlements.items():
        if eid in settlement_map:
            settlement_map[eid].update(stored)
            if stored.get("settlement_status") == "Settled":
                settlement_map[eid]["settlement_status"] = "Paid to organiser"
    settlements = list(settlement_map.values())

    # Admin notifications are intentionally not loaded into ADP.
    notes = []

    paid_payments = [p for p in payments if clean_lower(p.get("payment_status")) not in ["refunded"]]
    payments_held = sum(int(float(p.get("amount_paid") or p.get("amount") or 0)) for p in paid_payments if clean_lower(p.get("settlement_status")) not in ["settled", "paid to organiser"])
    orbit_revenue = sum(int(float(p.get("platform_fee") or p.get("platform_fee_earned") or p.get("admin_revenue") or 0)) for p in paid_payments)
    pending_refunds = sum(1 for r in refunds if clean_lower(r.get("status") or r.get("refund_status")) in ["requested", "pending", "refund requested"])
    pending_settlements = sum(int(float(s.get("organiser_payout_amount") or 0)) for s in settlements if clean_lower(s.get("settlement_status")) in ["settlement ready", "held by orbit", "pending", "pending event completion"])
    status_counts = {"active": 0, "completed": 0, "cancelled": 0}
    for e in events:
        st = clean_lower(e.get("status") or e.get("approval_status"))
        if st == "cancelled":
            status_counts["cancelled"] += 1
        elif st == "completed" or event_is_completed(e):
            status_counts["completed"] += 1
        elif st in ["approved", "active", "published", "live"] or clean_lower(e.get("approval_status")) == "approved":
            status_counts["active"] += 1

    stats = {
        "total_users": len(users),
        "total_organisers": len(organisers),
        "total_events": len(events),
        "active_events": status_counts["active"],
        "completed_events": status_counts["completed"],
        "cancelled_events": status_counts["cancelled"],
        "pending_events": len(pending_events),
        "total_registrations": len(regs_raw),
        "payments_held_by_orbit": payments_held,
        "pending_settlements": pending_settlements,
        "pending_refunds": pending_refunds,
        "orbit_revenue": orbit_revenue,
        "total_payment_collected": sum(int(float(p.get("amount_paid") or p.get("amount") or 0)) for p in paid_payments),
        "platform_fee_earned": orbit_revenue,
        "admin_revenue": orbit_revenue,
        "organiser_payout_pending": pending_settlements,
        "refund_requests": pending_refunds,
    }
    reports = {
        "approved_events": sum(1 for e in events if clean_lower(e.get("approval_status")) == "approved"),
        "rejected_events": sum(1 for e in events if clean_lower(e.get("approval_status")) == "rejected"),
        "completed_events": status_counts["completed"],
        "total_cancellations": status_counts["cancelled"],
        "total_refunded_amount": sum(int(float(r.get("amount") or 0)) for r in refunds if clean_lower(r.get("status") or r.get("refund_status")) in ["refunded", "approved"]),
        "pending_payout": pending_settlements,
    }
    return {"success": True, "stats": stats, "users": users, "organisers": organisers, "organizers": organisers, "events": events, "pending_events": pending_events, "registrations": regs_raw, "payments": payments, "refunds": refunds, "settlements": settlements, "reviews": reviews, "reports": reports, "notifications": notes}


def enrich_payment_row(p):
    """Populate user/organiser UPI and QR fields on a payment row (in-place)."""
    try:
        p["user_upi_id"] = p.get("user_upi_id") or p.get("upi_reference") or p.get("transaction_id") or ""
        user_email = clean_lower(p.get("user_email") or p.get("email") or p.get("recipient_email") or p.get("payer_email") or "")
        if user_email:
            u = users_collection.find_one({"email": {"$regex": f"^{re.escape(user_email)}$", "$options": "i"}}, {"profile": 1})
            if u and isinstance(u.get("profile"), dict):
                p["user_upi_id"] = p.get("user_upi_id") or clean(u["profile"].get("upi_id") or u["profile"].get("upi") or "")
                p["user_upi_qr"] = clean(u["profile"].get("upi_qr_image") or u["profile"].get("upi_qr") or u["profile"].get("user_qr_image") or "")
    except Exception:
        p["user_upi_qr"] = p.get("user_upi_qr") or ""

    try:
        organiser_email = clean_lower(p.get("organiser_email") or p.get("organizer_email") or p.get("host_email") or p.get("event_host_email") or "")
        organiser_upi = ""
        organiser_qr = ""
        if organiser_email:
            h = hosts_collection.find_one({"email": {"$regex": f"^{re.escape(organiser_email)}$", "$options": "i"}}, {"profile": 1, "upi_id": 1, "upi_qr_image": 1})
            if h:
                organiser_upi = clean(h.get("upi_id") or (h.get("profile") or {}).get("upi_id") or "")
                organiser_qr = clean(h.get("upi_qr_image") or (h.get("profile") or {}).get("upi_qr_image") or "")
        if not organiser_upi:
            organiser_upi = clean(p.get("organiser_upi_id") or p.get("organizer_upi_id") or p.get("organiser_upi") or p.get("organizer_upi") or "")
        if not organiser_qr:
            organiser_qr = clean(p.get("organiser_upi_qr") or p.get("organizer_upi_qr") or p.get("organiser_qr") or p.get("org_qr") or "")
        p["organiser_upi_id"] = organiser_upi
        p["organiser_upi_qr"] = organiser_qr
    except Exception:
        p["organiser_upi_id"] = p.get("organiser_upi_id") or ""
        p["organiser_upi_qr"] = p.get("organiser_upi_qr") or ""


def require_admin_json():
    if not (session.get("admin_email") or session.get("role") == "admin"):
        return jsonify(success=False, error="Admin not logged in"), 401
    return None


@app.route("/admin/purge-orphan-data", methods=["POST"])
def admin_purge_orphan_data():
    denied = require_admin_json()
    if denied:
        return denied
    purge_orphan_admin_data()
    return jsonify(success=True, message="Deleted-account records removed from admin portal")


@app.route("/admin/orbit-payment-settings", methods=["GET", "POST"])
@app.route("/api/admin/orbit-payment-settings", methods=["GET", "POST"])
def admin_orbit_payment_settings():
    denied = require_admin_json()
    if denied:
        return denied

    if request.method == "POST":
        data = get_data()
        upi_id = clean(data.get("upi_id") or "")
        qr_image = clean(data.get("qr_image") or "")

        qr_file = request.files.get("qr_image")
        if qr_file and qr_file.filename:
            filename = secure_filename(qr_file.filename)
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            allowed_ext = {"png", "jpg", "jpeg"}
            if ext not in allowed_ext:
                return jsonify(success=False, error="Upload must be a PNG or JPG image."), 400

            content = qr_file.read()
            if len(content) > 2 * 1024 * 1024:
                return jsonify(success=False, error="QR image must be 2MB or smaller."), 400

            upload_dir = os.path.join(app.root_path, "static", "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            saved_name = f"orbit_qr_{int(time.time())}.{ext}"
            saved_path = os.path.join(upload_dir, saved_name)
            with open(saved_path, "wb") as out_file:
                out_file.write(content)
            qr_image = f"/static/uploads/{saved_name}"

        settings = update_orbit_payment_settings(upi_id, qr_image)
        return jsonify(success=True, message="Orbit payment settings saved", orbit_payment=settings)

    settings = get_orbit_payment_settings()
    return jsonify(success=True, orbit_payment=settings)


@app.route("/admin/dashboard-data", methods=["GET"])
@app.route("/api/admin/dashboard", methods=["GET"])
def admin_dashboard_data():
    # Read-only dashboard data must load reliably for ADP.
    # Write actions such as approve/refund/settle still check admin session.
    return jsonify(build_admin_dashboard_payload())


@app.route("/admin/events/pending", methods=["GET"])
def admin_pending_events():
    denied = require_admin_json()
    if denied:
        return denied
    events = [format_event(e) for e in events_collection.find({"$or": [{"status": {"$regex": "^pending$", "$options": "i"}}, {"approval_status": {"$regex": "^pending$", "$options": "i"}}]}).sort("created_at", -1)]
    return jsonify(success=True, events=events, pending_events=events)


@app.route("/admin/event/approve/<event_id>", methods=["POST"])
@app.route("/api/admin/event/approve", methods=["POST"])
def admin_approve_event(event_id=None):
    denied = require_admin_json()
    if denied:
        return denied
    data = get_data()
    event_id = event_id or clean(data.get("event_id"))
    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return jsonify(success=False, error="Event not found"), 404
    
    # Ensure we have a valid owner email
    owner_email = clean_lower(event.get("host_email") or event.get("organizer_email") or event.get("organiser_email"))
    if not owner_email:
        return jsonify(success=False, error="Event has no owner email - cannot approve"), 400
    
    # Approve the event
    events_collection.update_one({"event_id": event_id}, {"$set": {"status": "Approved", "approval_status": "Approved", "organiser_email": owner_email, "organizer_email": owner_email, "host_email": owner_email, "payment_status": "Held by Orbit" if int(float(event.get("price") or 0)) > 0 else "Free", "settlement_status": "Pending event completion" if int(float(event.get("price") or 0)) > 0 else "Free Event", "updated_at": now()}})
    
    # Send notification to organiser
    create_notification(owner_email, "Event approved/live", f"{event.get('title')} is approved and live on Orbit.", "organiser", {"type": "event_approved", "related_event_id": event_id})
    
    return jsonify(success=True, message="Event approved", event=format_event(events_collection.find_one({"event_id": event_id})))


@app.route("/admin/event/reject/<event_id>", methods=["POST"])
@app.route("/api/admin/event/reject", methods=["POST"])
def admin_reject_event(event_id=None):
    denied = require_admin_json()
    if denied:
        return denied
    data = get_data()
    event_id = event_id or clean(data.get("event_id"))
    reason = clean(data.get("reason"))
    event = events_collection.find_one({"event_id": event_id})
    if not event:
        return jsonify(success=False, error="Event not found"), 404
    
    # Ensure we have a valid owner email
    owner_email = clean_lower(event.get("organiser_email") or event.get("organizer_email") or event.get("host_email"))
    if not owner_email:
        return jsonify(success=False, error="Event has no owner email - cannot reject"), 400
    
    # Reject the event
    events_collection.update_one({"event_id": event_id}, {"$set": {"status": "Rejected", "approval_status": "Rejected", "rejection_reason": reason, "updated_at": now()}})
    
    # Send notification to organiser
    create_notification(owner_email, "Event rejected", f"{event.get('title')} was rejected." + (f" Reason: {reason}" if reason else ""), "organiser")
    
    return jsonify(success=True, message="Event rejected")


@app.route("/admin/refunds", methods=["GET"])
def admin_refunds():
    return jsonify(success=True, refunds=build_admin_dashboard_payload()["refunds"])


@app.route("/admin/refund/approve/<refund_id>", methods=["POST"])
def admin_approve_refund(refund_id):
    denied = require_admin_json()
    if denied:
        return denied
    refund = refunds_collection.find_one(_refund_lookup_query(refund_id))
    reg = registrations_collection.find_one({"registration_id": refund_id}) if not refund else registrations_collection.find_one({"registration_id": refund.get("registration_id")})
    if not refund and not reg:
        return jsonify(success=False, error="Refund not found"), 404
    refund_doc = refund or {"refund_id": refund_id, "registration_id": reg.get("registration_id"), "user_email": reg.get("user_email"), "event_id": reg.get("event_id"), "event_title": reg.get("event_title"), "amount": reg.get("amount_paid") or reg.get("amount") or 0, "reason": reg.get("cancellation_reason", "Refund requested"), "requested_at": now()}
    refund_doc.update({"refund_status": "Refund initiated", "status": "Refund initiated", "initiated_at": now(), "processed_at": now()})
    refunds_collection.update_one({"refund_id": refund_doc.get("refund_id", refund_id)}, {"$set": clean_mongo_update(refund_doc)}, upsert=True)
    registrations_collection.update_one({"registration_id": refund_doc.get("registration_id")}, {"$set": {"refund_status": "Refund initiated", "payment_status": "Refund initiated", "updated_at": now()}})
    payments_collection.update_one({"registration_id": refund_doc.get("registration_id")}, {"$set": {"refund_status": "Refund initiated", "payment_status": "Refund initiated", "settlement_status": "Refund initiated", "updated_at": now()}})
    create_notification(refund_doc.get("user_email"), "Refund initiated", f"Orbit has initiated your refund for {refund_doc.get('event_title', 'the event')}.", "user", {"type": "refund_initiated", "related_event_id": refund_doc.get("event_id"), "registration_id": refund_doc.get("registration_id"), "refund_id": refund_doc.get("refund_id")})
    return jsonify(success=True, message="Refund initiated", refund=serialize_doc(refund_doc))


@app.route("/admin/refund/reject/<refund_id>", methods=["POST"])
def admin_reject_refund(refund_id):
    denied = require_admin_json()
    if denied:
        return denied
    data = get_data()
    reason = clean(data.get("reason"))
    refund = refunds_collection.find_one(_refund_lookup_query(refund_id))
    # If no refund doc exists, try to enrich from the registration before upserting
    if not refund:
        reg = registrations_collection.find_one({"registration_id": refund_id})
        if reg:
            refund = {
                "refund_id": refund_id,
                "registration_id": reg.get("registration_id"),
                "user_email": reg.get("user_email"),
                "event_id": reg.get("event_id"),
                "event_title": reg.get("event_title"),
                "amount": reg.get("amount_paid") or reg.get("amount") or 0,
                "reason": reg.get("cancellation_reason", "Refund requested"),
                "requested_at": reg.get("cancelled_at") or now(),
            }
        else:
            refund = {"refund_id": refund_id, "registration_id": refund_id}

    refunds_collection.update_one({"refund_id": refund.get("refund_id", refund_id)}, {"$set": clean_mongo_update({**refund, "refund_status": "Rejected", "status": "Rejected", "rejection_reason": reason, "processed_at": now()})}, upsert=True)
    registrations_collection.update_one({"registration_id": refund.get("registration_id", refund_id)}, {"$set": {"refund_status": "Rejected", "payment_status": "Paid to Orbit - On Hold"}})
    reg = registrations_collection.find_one({"registration_id": refund.get("registration_id", refund_id)}) or {}
    create_notification(reg.get("user_email"), "Refund rejected", f"Your refund request for {reg.get('event_title', 'the event')} was rejected." + (f" Reason: {reason}" if reason else ""), "user")
    return jsonify(success=True, message="Refund rejected")


@app.route("/admin/settlements", methods=["GET"])
def admin_settlements():
    return jsonify(success=True, settlements=build_admin_dashboard_payload()["settlements"])


@app.route("/admin/settle/<event_id>", methods=["POST"])
def admin_settle_event(event_id):
    denied = require_admin_json()
    if denied:
        return denied
    event = events_collection.find_one({"$or": [{"event_id": event_id}, {"title": event_id}]}) or {}
    real_event_id = event.get("event_id") or event_id
    regs = list(registrations_collection.find({"event_id": real_event_id, "payment_status": {"$ne": "Refunded"}, "status": {"$ne": "duplicate_inactive"}, "registration_status": {"$ne": "duplicate_inactive"}}))
    if not regs:
        return jsonify(success=False, error="No payable registrations found for this event"), 404
    total_collected = sum(int(float(r.get("amount_paid") or r.get("amount") or 0)) for r in regs)
    admin_revenue = sum(int(float(r.get("admin_revenue") or 0)) for r in regs)
    payout = sum(int(float(r.get("organiser_payout") or r.get("organizer_payout") or 0)) for r in regs)
    paid_at = now()
    doc = {"event_id": real_event_id, "event_title": event.get("title") or regs[0].get("event_title"), "organiser_email": event.get("organiser_email") or regs[0].get("organiser_email"), "organiser_name": event.get("organiser_name") or regs[0].get("organiser_name"), "organiser_upi_id": event.get("upi_id") or "", "total_collected": total_collected, "total_paid_amount": total_collected, "total_admin_revenue": admin_revenue, "platform_fee": admin_revenue, "total_organiser_payout": payout, "organiser_payout_amount": payout, "event_completion_status": "Completed" if event_is_completed(event) else event.get("status", "Upcoming"), "settlement_status": "Settled", "settled_at": paid_at, "paid_at": paid_at}
    settlements_collection.update_one({"event_id": real_event_id}, {"$set": clean_mongo_update(doc)}, upsert=True)
    settlement_update = {"settlement_status": "Settled", "payment_status": "Settled to Organiser", "payout_status": "Paid by Orbit", "paid_by_orbit": payout, "admin_paid_amount": payout, "settled_at": paid_at, "admin_paid_at": paid_at, "updated_at": paid_at}
    registrations_collection.update_many({"event_id": real_event_id, "payment_status": {"$ne": "Refunded"}}, {"$set": settlement_update})
    payments_collection.update_many({"event_id": real_event_id, "payment_status": {"$ne": "Refunded"}}, {"$set": settlement_update})
    events_collection.update_one({"event_id": real_event_id}, {"$set": {"payment_status": "Settled", "settlement_status": "Settled", "updated_at": paid_at}})
    create_notification(doc.get("organiser_email"), "Payment received", f"Your payment is received after completion of event for {doc.get('event_title')} — ₹{payout}.", "organiser", {"type": "payment_received", "related_event_id": real_event_id})
    return jsonify(success=True, message="Settlement completed", settlement=serialize_doc(doc))


@app.route("/admin/notifications", methods=["GET"])
def admin_notifications():
    # ADP notification section is removed. Keep endpoint harmless for old JS calls.
    return jsonify(success=True, notifications=[])


@app.route("/admin/notifications/read/<notification_id>", methods=["POST"])
@app.route("/notifications/read/<notification_id>", methods=["POST"])
def admin_notification_read(notification_id):
    notifications_collection.update_one({"notification_id": notification_id}, {"$set": {"status": "Read", "unread": False}})
    return jsonify(success=True)


@app.route("/admin/logout", methods=["GET", "POST"])
def admin_logout_alias():
    return admin_logout()



# =================== ADMIN ROUTE ALIASES + ACTION FIXES ===================
# These aliases match the URLs used by adp.html so admin tables/buttons persist after refresh.

@app.route("/admin/users", methods=["GET"])
def admin_users_alias():
    return api_admin_users()


@app.route("/admin/organisers", methods=["GET"])
@app.route("/admin/organizers", methods=["GET"])
def admin_organisers_alias():
    return api_admin_organisers()


@app.route("/admin/registrations", methods=["GET"])
def admin_registrations_alias():
    payload = build_admin_dashboard_payload()
    return jsonify(success=True, registrations=payload.get("registrations", []), payments=payload.get("payments", []))


@app.route("/admin/reviews", methods=["GET"])
@app.route("/api/admin/reviews", methods=["GET"])
@app.route("/api/admin/ratings", methods=["GET"])
def admin_reviews_alias():
    denied = require_admin_json()
    if denied:
        return denied
    reviews = [serialize_doc(r) for r in reviews_collection.find({}).sort("created_at", -1)]
    for r in reviews:
        r["created_at"] = safe_dt(r.get("created_at"))
    return jsonify(success=True, reviews=reviews)


def _id_or_email_query(identifier, email_field="email"):
    identifier = clean(identifier)
    query = {"$or": [{email_field: identifier}, {"user_id": identifier}, {"host_id": identifier}, {"organiser_id": identifier}, {"organizer_id": identifier}]}
    try:
        from bson import ObjectId
        if ObjectId.is_valid(identifier):
            query["$or"].append({"_id": ObjectId(identifier)})
    except Exception:
        pass
    return query


def _admin_account_action(collection, identifier, action, account_type):
    denied = require_admin_json()
    if denied:
        return denied
    action = clean_lower(action)
    if action == "flag":
        update = {"flagged": True, "account_status": "Flagged", "status": "Flagged", "updated_at": now()}
        message = f"{account_type.title()} flagged for admin review"
    elif action == "ban":
        update = {"banned": True, "blocked": True, "account_status": "Banned", "status": "Banned", "updated_at": now()}
        message = f"{account_type.title()} banned"
    elif action == "unban":
        update = {"banned": False, "blocked": False, "flagged": False, "account_status": "Active", "status": "Active", "updated_at": now()}
        message = f"{account_type.title()} restored"
    else:
        return jsonify(success=False, error="Invalid admin action"), 400
    result = collection.update_one(_id_or_email_query(identifier), {"$set": update})
    if result.matched_count == 0:
        return jsonify(success=False, error=f"{account_type.title()} not found"), 404
    return jsonify(success=True, message=message, updated=update)


@app.route("/admin/user/flag/<identifier>", methods=["POST"])
def admin_flag_user(identifier):
    return _admin_account_action(users_collection, identifier, "flag", "user")


@app.route("/admin/user/ban/<identifier>", methods=["POST"])
def admin_ban_user(identifier):
    return _admin_account_action(users_collection, identifier, "ban", "user")


@app.route("/admin/user/unban/<identifier>", methods=["POST"])
def admin_unban_user(identifier):
    return _admin_account_action(users_collection, identifier, "unban", "user")


@app.route("/admin/organiser/flag/<identifier>", methods=["POST"])
@app.route("/admin/organizer/flag/<identifier>", methods=["POST"])
def admin_flag_organiser(identifier):
    return _admin_account_action(hosts_collection, identifier, "flag", "organiser")


@app.route("/admin/organiser/ban/<identifier>", methods=["POST"])
@app.route("/admin/organizer/ban/<identifier>", methods=["POST"])
def admin_ban_organiser(identifier):
    return _admin_account_action(hosts_collection, identifier, "ban", "organiser")


@app.route("/admin/organiser/unban/<identifier>", methods=["POST"])
@app.route("/admin/organizer/unban/<identifier>", methods=["POST"])
def admin_unban_organiser(identifier):
    return _admin_account_action(hosts_collection, identifier, "unban", "organiser")



# =================== ORBIT ALIGNMENT ROUTE ALIASES ===================
# These aliases keep profile.html, organiser.html, and adp.html connected to the
# same MongoDB collections instead of falling back to stale browser cache.

@app.route("/api/admin/dashboard-data", methods=["GET"])
def api_admin_dashboard_data_alias():
    return admin_dashboard_data()

@app.route("/api/admin/registrations", methods=["GET"])
@app.route("/api/admin/bookings", methods=["GET"])
@app.route("/api/registrations", methods=["GET"])
@app.route("/api/bookings", methods=["GET"])
def api_admin_registrations_alias():
    # For organiser-specific calls, return only that organiser's rows.
    host_email = clean_lower(request.args.get("host_email") or request.args.get("organiser_email") or request.args.get("organizer_email"))
    if host_email:
        return host_attendees()
    return admin_registrations_alias()

@app.route("/admin/payments", methods=["GET"])
@app.route("/api/admin/payments", methods=["GET"])
@app.route("/payments", methods=["GET"])
@app.route("/api/payments", methods=["GET"])
def admin_payments_alias():
    host_email = clean_lower(request.args.get("host_email") or request.args.get("organiser_email") or request.args.get("organizer_email"))
    if host_email:
        events = list(events_collection.find({"$or": [{"organiser_email": host_email}, {"organizer_email": host_email}, {"host_email": host_email}]}, {"event_id": 1}))
        ids = [e.get("event_id") for e in events if e.get("event_id")]
        rows = [serialize_doc(p) for p in payments_collection.find({"event_id": {"$in": ids}}).sort("created_at", -1)]
        # Enrich rows with UPI/QR data so host-specific payment views match admin dashboard
        for r in rows:
            try:
                enrich_payment_row(r)
            except Exception:
                pass
        return jsonify(success=True, payments=rows, records=rows)
    payload = build_admin_dashboard_payload()
    return jsonify(success=True, payments=payload.get("payments", []), payment_records=payload.get("payments", []))

@app.route("/api/admin/refunds", methods=["GET"])
@app.route("/refunds", methods=["GET"])
@app.route("/api/refunds", methods=["GET"])
@app.route("/admin/refund-requests", methods=["GET"])
@app.route("/api/admin/refund-requests", methods=["GET"])
def admin_refunds_alias():
    return admin_refunds()

@app.route("/api/admin/notifications", methods=["GET"])
def api_admin_notifications_alias():
    return admin_notifications()

@app.route("/api/host/registrations", methods=["GET"])
@app.route("/api/organiser/registrations", methods=["GET"])
@app.route("/api/organizer/registrations", methods=["GET"])
@app.route("/api/organiser/attendees", methods=["GET"])
@app.route("/api/attendees", methods=["GET"])
def organiser_registration_aliases():
    return host_attendees()

@app.route("/api/host/notifications", methods=["GET"])
@app.route("/api/organiser/notifications", methods=["GET"])
def host_notifications_alias():
    email = clean_lower(request.args.get("host_email") or request.args.get("email") or request.args.get("organiser_email") or session.get("host_email"))
    host, error_response = require_existing_host(email)
    if error_response:
        return error_response
    email = clean_lower(host.get("email") or email)
    notes = [serialize_doc(n) for n in notifications_collection.find({"$or": [
        {"email": email}, {"recipient_id": email}, {"recipient_email": email}, {"host_email": email},
        {"recipient_type": "host", "email": email}, {"recipient_type": "organiser", "email": email},
    ]}).sort("created_at", -1)]
    for n in notes:
        n["created_at"] = safe_dt(n.get("created_at"))
    return jsonify(success=True, notifications=notes, alerts=notes)

def _mongo_id_query(identifier):
    identifier = clean(identifier)
    queries = []
    if identifier:
        queries.append({"_id": identifier})
    try:
        from bson import ObjectId
        if ObjectId.is_valid(identifier):
            queries.append({"_id": ObjectId(identifier)})
    except Exception:
        pass
    return queries


def _payment_lookup_query(identifier):
    identifier = clean(identifier)
    base = [{"payment_id": identifier}, {"registration_id": identifier}, {"transaction_id": identifier}, {"reference_id": identifier}]
    return {"$or": base + _mongo_id_query(identifier)}


def _refund_lookup_query(identifier):
    identifier = clean(identifier)
    base = [{"refund_id": identifier}, {"registration_id": identifier}]
    return {"$or": base + _mongo_id_query(identifier)}


@app.route("/admin/settle/ready/<event_id>", methods=["POST"])
def admin_mark_settlement_ready(event_id):
    denied = require_admin_json()
    if denied:
        return denied
    event = events_collection.find_one({"$or": [{"event_id": event_id}, {"title": event_id}]}) or {}
    real_event_id = event.get("event_id") or event_id
    update = {"settlement_status": "Settlement ready", "payment_status": "Settlement ready", "ready_at": now(), "updated_at": now()}
    events_collection.update_one({"event_id": real_event_id}, {"$set": update})
    registrations_collection.update_many({"event_id": real_event_id, "payment_status": {"$ne": "Refunded"}}, {"$set": update})
    payments_collection.update_many({"event_id": real_event_id, "payment_status": {"$ne": "Refunded"}}, {"$set": update})
    settlements_collection.update_one({"event_id": real_event_id}, {"$set": {"event_id": real_event_id, "event_title": event.get("title", real_event_id), "organiser_email": event.get("organiser_email", ""), "organiser_name": event.get("organiser_name", ""), "settlement_status": "Settlement ready", "ready_at": now(), "updated_at": now()}}, upsert=True)
    create_notification(event.get("organiser_email"), "Payment ready", f"Orbit marked payout ready for {event.get('title', 'your event')}.", "organiser", {"type": "payment_ready", "related_event_id": real_event_id})
    return jsonify(success=True, message="Settlement marked ready", event_id=real_event_id)


@app.route("/admin/payment/status/<payment_id>", methods=["POST"])
def admin_payment_status(payment_id):
    denied = require_admin_json()
    if denied:
        return denied
    data = get_data()
    status = clean(data.get("status") or data.get("payment_status") or "Paid")
    status_l = clean_lower(status)
    payment = payments_collection.find_one(_payment_lookup_query(payment_id)) or {}
    registration_id = payment.get("registration_id") or clean(data.get("registration_id")) or payment_id
    reg = registrations_collection.find_one({"registration_id": registration_id}) or {}
    if not payment and reg:
        payment = {**reg, "payment_id": f"PAY-{registration_id}"}
    if not payment and not reg:
        return jsonify(success=False, error="Payment/registration not found"), 404

    update = {"payment_status": status, "status": status, "updated_at": now()}
    is_initiated = any(word in status_l for word in ["initiated", "initiate", "processing", "process"])
    is_ready = "ready" in status_l
    is_paid = any(word in status_l for word in ["paid", "received"])

    if is_initiated:
        update["initiated_at"] = payment.get("initiated_at") or reg.get("initiated_at") or now()
        update["settlement_status"] = "Payment initiated"
    if is_ready:
        update["paid_at"] = payment.get("paid_at") or reg.get("paid_at") or now()
        update["settlement_status"] = "Settlement ready"
    elif is_paid:
        update["paid_at"] = payment.get("paid_at") or reg.get("paid_at") or now()
        update["settlement_status"] = payment.get("settlement_status") or reg.get("settlement_status") or "Paid to Orbit"

    payments_collection.update_one(
        {"registration_id": registration_id},
        {"$set": clean_mongo_update({**payment, **update, "registration_id": registration_id, "payment_id": payment.get("payment_id") or f"PAY-{registration_id}"})},
        upsert=True,
    )
    registrations_collection.update_one({"registration_id": registration_id}, {"$set": clean_mongo_update(update)})

    final = payments_collection.find_one({"registration_id": registration_id}) or payment or reg
    if is_initiated or is_ready or is_paid:
        event_title = final.get("event_title") or reg.get("event_title") or "your event"
        if is_initiated:
            note_title = "Payment initiated"
            note_message = f"Your payment process for {event_title} has been initiated by Orbit."
            note_type = "payment_initiated"
        elif is_paid:
            note_title = "Payment paid"
            note_message = f"Your payment for {event_title} is marked paid by Orbit."
            note_type = "payment_paid"
        else:
            note_title = "Payment ready"
            note_message = f"Your payment for {event_title} is marked ready by Orbit."
            note_type = "payment_ready"
        event_id_for_note = final.get("event_id") or reg.get("event_id")
        payment_id_for_note = final.get("payment_id") or f"PAY-{registration_id}"
        create_notification(
            final.get("user_email") or reg.get("user_email"),
            note_title,
            note_message,
            "user",
            {
                "type": note_type,
                "related_event_id": event_id_for_note,
                "registration_id": registration_id,
                "payment_id": payment_id_for_note,
            },
        )

        # Organiser-side notification for admin payout/settlement movement.
        # This is separate from the user payment notification and is deduped by
        # create_notification(), so repeated admin clicks do not spam the dashboard.
        event_for_note = events_collection.find_one({"event_id": event_id_for_note}) or {}
        organiser_email_for_note = clean_lower(
            event_for_note.get("organiser_email")
            or event_for_note.get("organizer_email")
            or event_for_note.get("host_email")
            or final.get("organiser_email")
            or reg.get("organiser_email")
            or final.get("host_email")
            or reg.get("host_email")
        )
        organiser_payout_amount = final.get("organiser_payout") or final.get("organizer_payout") or reg.get("organiser_payout") or reg.get("organizer_payout") or 0
        if organiser_email_for_note:
            if is_initiated:
                organiser_title = "Payment initiated"
                organiser_message = f"Orbit has initiated your payout for {event_title}" + (f" — ₹{int(float(organiser_payout_amount or 0))}." if float(organiser_payout_amount or 0) > 0 else ".")
            elif is_paid:
                organiser_title = "Payment received"
                organiser_message = f"Your payment is received after completion of event for {event_title}" + (f" — ₹{int(float(organiser_payout_amount or 0))}." if float(organiser_payout_amount or 0) > 0 else ".")
            else:
                organiser_title = "Payment ready"
                organiser_message = f"Orbit marked your payout ready for {event_title}" + (f" — ₹{int(float(organiser_payout_amount or 0))}." if float(organiser_payout_amount or 0) > 0 else ".")
            create_notification(
                organiser_email_for_note,
                organiser_title,
                organiser_message,
                "organiser",
                {
                    "type": note_type,
                    "related_event_id": event_id_for_note,
                    "registration_id": registration_id,
                    "payment_id": payment_id_for_note,
                },
            )
    return jsonify(success=True, message="Payment status updated", payment_status=status, payment=serialize_doc(final))

@app.route("/admin/refund/paid/<refund_id>", methods=["POST"])
@app.route("/admin/refund/complete/<refund_id>", methods=["POST"])
def admin_mark_refund_paid(refund_id):
    denied = require_admin_json()
    if denied:
        return denied
    refund = refunds_collection.find_one(_refund_lookup_query(refund_id))
    reg = registrations_collection.find_one({"registration_id": refund_id}) if not refund else registrations_collection.find_one({"registration_id": refund.get("registration_id")})
    if not refund and not reg:
        return jsonify(success=False, error="Refund not found"), 404
    refund_doc = refund or {"refund_id": refund_id, "registration_id": reg.get("registration_id"), "user_email": reg.get("user_email"), "event_id": reg.get("event_id"), "event_title": reg.get("event_title"), "amount": reg.get("refundable_amount") or reg.get("event_amount") or reg.get("amount_paid") or 0, "reason": reg.get("cancellation_reason", "Refund requested"), "requested_at": reg.get("cancelled_at") or now()}
    refund_doc.update({"refund_status": "Refunded", "status": "Refunded", "paid_at": now(), "processed_at": now()})
    refunds_collection.update_one({"refund_id": refund_doc.get("refund_id", refund_id)}, {"$set": clean_mongo_update(refund_doc)}, upsert=True)
    registrations_collection.update_one({"registration_id": refund_doc.get("registration_id")}, {"$set": {"refund_status": "Refunded", "payment_status": "Refunded", "registration_status": "Refunded", "status": "refunded", "refunded_at": now(), "updated_at": now()}})
    payments_collection.update_one({"registration_id": refund_doc.get("registration_id")}, {"$set": {"refund_status": "Refunded", "payment_status": "Refunded", "settlement_status": "Refunded", "refunded_at": now(), "updated_at": now()}})
    create_notification(refund_doc.get("user_email"), "Refund completed", f"Your refund for {refund_doc.get('event_title', 'the event')} has been sent by Orbit.", "user", {"type": "refund_sent", "related_event_id": refund_doc.get("event_id"), "registration_id": refund_doc.get("registration_id"), "refund_id": refund_doc.get("refund_id")})
    return jsonify(success=True, message="Refund marked paid", refund=serialize_doc(refund_doc))

@app.route("/admin/notifications/create", methods=["POST"])
@app.route("/api/admin/notifications/create", methods=["POST"])
@app.route("/notifications/create", methods=["POST"])
def create_notification_route():
    data = get_data()
    email = clean_lower(data.get("email") or data.get("recipient_email") or data.get("recipient_id"))
    recipient_type = clean_lower(data.get("recipient_type") or "user") or "user"
    title = clean(data.get("title") or "Orbit update")
    message = clean(data.get("message") or data.get("content") or "")
    if not email or not message:
        return jsonify(success=False, error="Notification email and message are required"), 400
    nid = create_notification(email, title, message, recipient_type, {
        "type": data.get("type") or data.get("category") or "Update",
        "related_event_id": data.get("event_id") or data.get("related_event_id"),
        "event_title": data.get("event_title"),
        "registration_id": data.get("registration_id"),
        "refund_id": data.get("refund_id"),
    })
    return jsonify(success=True, notification_id=nid)

# =================== END ADMIN PORTAL - WORKING CONNECTION ===================

if __name__ == "__main__":
    # Run without debugger and without the auto-reloader to avoid
    # Windows socket errors when files change during testing.
    app.run(debug=False, use_reloader=False)