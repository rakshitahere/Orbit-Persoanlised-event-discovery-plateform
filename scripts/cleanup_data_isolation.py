#!/usr/bin/env python
"""
Data Isolation Cleanup Script
==============================
This script helps diagnose and fix data isolation issues where new users
see cancellation history from previous accounts that reused the same email.

Usage:
  python scripts/cleanup_data_isolation.py <email>  # Show registrations for email
  python scripts/cleanup_data_isolation.py clean-all # Clean up all orphaned data
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pymongo import MongoClient
from datetime import datetime

# MongoDB connection
MONGO_URI = "mongodb://localhost:27017/"
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client["orbit"]

users_collection = db["users"]
registrations_collection = db["registrations"]
payments_collection = db["payments"]
refunds_collection = db["refunds"]

def clean_lower(v):
    return str(v or "").strip().lower()

def show_registrations_for_email(email):
    """Show all registrations for a specific email."""
    email = clean_lower(email)
    if not email:
        print("❌ Email required")
        return
    
    user = users_collection.find_one({"email": email})
    regs = list(registrations_collection.find({"user_email": email}))
    
    print(f"\n{'='*80}")
    print(f"Email: {email}")
    print(f"{'='*80}")
    
    if user:
        print(f"✅ User exists")
        print(f"   Created: {user.get('created_at', 'Unknown')}")
        print(f"   Name: {user.get('name', 'Unknown')}")
    else:
        print(f"❌ User DOES NOT EXIST - This is orphaned data!")
    
    print(f"\nRegistrations: {len(regs)}")
    for i, reg in enumerate(regs, 1):
        status = reg.get("status", "unknown")
        event_title = reg.get("event_title", "Unknown")[:50]
        created_at = reg.get("created_at", "Unknown")
        cancelled_at = reg.get("cancelled_at")
        print(f"  {i}. {event_title}")
        print(f"     Status: {status} | Created: {created_at}")
        if cancelled_at:
            print(f"     Cancelled: {cancelled_at}")
    
    return bool(user), regs

def cleanup_orphaned_registrations():
    """Remove registrations where the user no longer exists."""
    print(f"\n{'='*80}")
    print("Cleaning up orphaned registrations...")
    print(f"{'='*80}")
    
    # Get all unique user emails from registrations
    all_regs = list(registrations_collection.find({}, {"user_email": 1}))
    user_emails = set()
    for reg in all_regs:
        email = clean_lower(reg.get("user_email") or "")
        if email:
            user_emails.add(email)
    
    total_deleted = 0
    
    # For each email, check if user exists; if not, delete their data
    for email in sorted(user_emails):
        user = users_collection.find_one({"email": email})
        if not user:
            reg_count = registrations_collection.count_documents({"user_email": email})
            pay_count = payments_collection.count_documents({"user_email": email})
            ref_count = refunds_collection.count_documents({"user_email": email})
            
            # Delete orphaned data
            registrations_collection.delete_many({"user_email": email})
            payments_collection.delete_many({"user_email": email})
            refunds_collection.delete_many({"user_email": email})
            
            print(f"❌ Deleted orphaned data for {email}:")
            print(f"   - {reg_count} registrations")
            print(f"   - {pay_count} payments")
            print(f"   - {ref_count} refunds")
            
            total_deleted += reg_count + pay_count + ref_count
    
    print(f"\n✅ Total records deleted: {total_deleted}")

def main():
    try:
        # Test MongoDB connection
        client.server_info()
        print("✅ MongoDB Connected")
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        sys.exit(1)
    
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "clean-all":
        cleanup_orphaned_registrations()
    else:
        email = command
        user_exists, regs = show_registrations_for_email(email)
        
        if not user_exists and regs:
            print(f"\n⚠️  WARNING: This email has {len(regs)} registrations but user doesn't exist!")
            print("   This data is orphaned and should be cleaned up.")
            print(f"\n   To delete this data, run:")
            print(f"   python scripts/cleanup_data_isolation.py clean-all")

if __name__ == "__main__":
    main()
