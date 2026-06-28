#!/usr/bin/env python3
"""Fix stuck events in the local Orbit MongoDB by resetting their status and ensuring proper sync.

This script helps diagnose and fix events that may have gotten stuck during the approval
process by checking for inconsistent states and correcting them.

Usage: python fix_stuck_events.py [--dry-run] [--fix-all]
  --dry-run   : Show what would be fixed without making changes
  --fix-all   : Fix all inconsistent events (default is to prompt for each)
"""
import sys
import argparse
from datetime import datetime
from pymongo import MongoClient


MONGO_URI = "mongodb://localhost:27017/"


def main():
    parser = argparse.ArgumentParser(description="Fix stuck events in Orbit")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying them")
    parser.add_argument("--fix-all", action="store_true", help="Fix all issues without prompting")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client.get_database("orbit")
    events = db.events

    print("🔍 Scanning for stuck/inconsistent events...\n")

    # Find events with potential issues
    stuck_events = list(events.find({
        "$or": [
            # Events in pending state for too long
            {
                "approval_status": {"$regex": "pending", "$options": "i"},
                "created_at": {"$exists": True}
            },
            # Events with missing owner emails
            {
                "approval_status": {"$regex": "approved", "$options": "i"},
                "$or": [
                    {"organiser_email": {"$in": [None, ""]}},
                    {"organizer_email": {"$in": [None, ""]}},
                    {"host_email": {"$in": [None, ""]}}
                ]
            },
            # Events with conflicting status/approval_status
            {
                "$expr": {
                    "$and": [
                        {"$ne": ["$status", "$approval_status"]},
                        {"$ne": ["$approval_status", "Pending"]},
                    ]
                }
            }
        ]
    }).sort("created_at", -1).limit(100))

    if not stuck_events:
        print("✅ No stuck events found!")
        return

    print(f"Found {len(stuck_events)} potentially stuck events:\n")

    fixed_count = 0
    for event in stuck_events:
        eid = event.get("event_id")
        title = event.get("title", "Untitled")
        status = event.get("status", "Unknown")
        approval = event.get("approval_status", "Unknown")
        owner = event.get("organiser_email") or event.get("organizer_email") or event.get("host_email") or "NO_EMAIL"
        created = event.get("created_at", "Unknown")

        issues = []
        if not owner or owner == "NO_EMAIL":
            issues.append("❌ Missing owner email")
        if approval == "Pending":
            issues.append("⏳ Still pending approval")
        if status != approval and approval != "Pending":
            issues.append(f"⚠️  Status/Approval mismatch: {status} vs {approval}")

        if not issues:
            continue

        print(f"\nEvent: {title}")
        print(f"  ID: {eid}")
        print(f"  Created: {created}")
        print(f"  Status: {status}")
        print(f"  Approval: {approval}")
        print(f"  Owner: {owner}")
        for issue in issues:
            print(f"  {issue}")

        # Propose fix
        should_fix = args.fix_all
        if not args.dry_run and not should_fix:
            response = input("  Fix this event? (y/n): ").strip().lower()
            should_fix = response == 'y'

        if should_fix and not args.dry_run:
            try:
                # For events with no email, try to find the host in users collection
                if owner == "NO_EMAIL":
                    users = db.users
                    host = users.find_one({})  # Use first organiser/host found
                    if host:
                        owner = host.get("email")
                        print(f"  📧 Found host email: {owner}")

                if owner and owner != "NO_EMAIL":
                    # Sync the emails and ensure approval status matches status
                    update = {
                        "organiser_email": owner,
                        "organizer_email": owner,
                        "host_email": owner,
                        "updated_at": datetime.now(),
                    }

                    if approval == "Pending" and status != "Pending":
                        # Approval is pending but status changed - sync them
                        update["approval_status"] = status
                        print(f"  🔄 Syncing approval_status to: {status}")
                    elif status == "Pending" and approval != "Pending":
                        # Status is pending but approval changed - this shouldn't happen
                        update["status"] = approval
                        print(f"  🔄 Syncing status to: {approval}")

                    events.update_one({"event_id": eid}, {"$set": update})
                    fixed_count += 1
                    print(f"  ✅ Fixed!")
                else:
                    print(f"  ❌ Cannot fix - no valid owner email found")
            except Exception as e:
                print(f"  ❌ Error fixing event: {e}")
        elif args.dry_run and should_fix:
            print(f"  [DRY-RUN] Would be fixed")

    print(f"\n{'🎉' if fixed_count > 0 else '📝'} Summary: {fixed_count} events fixed" + 
          (" (dry-run mode)" if args.dry_run else ""))
    print("\nNext steps:")
    print("1. Log into the admin dashboard at /admin")
    print("2. Go to Events tab")
    print("3. Refresh the page (F5) to see updated events")
    print("4. New events and approvals should now work properly")


if __name__ == "__main__":
    main()
