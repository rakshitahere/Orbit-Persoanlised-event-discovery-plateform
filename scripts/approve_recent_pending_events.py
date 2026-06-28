#!/usr/bin/env python3
"""Approve recent pending events in the local Orbit MongoDB.

Usage: python approve_recent_pending_events.py [--count N]
Defaults to 5 most recent pending events.
"""
import sys
import argparse
from datetime import datetime
import os
from pymongo import MongoClient

# Change your connection line to look like this:
mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(mongo_uri)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5, help="Number of recent pending events to approve")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client.get_database("orbit")
    events = db.events

    # Find most recent pending events (by created_at) that are not approved
    query = {
        "$or": [
            {"status": {"$regex": "pending", "$options": "i"}},
            {"approval_status": {"$regex": "pending", "$options": "i"}}
        ]
    }

    docs = list(events.find(query).sort("created_at", -1).limit(args.count))
    if not docs:
        print("No pending events found to approve.")
        return

    for d in docs:
        eid = d.get("event_id")
        title = d.get("title")
        owner = (d.get("host_email") or d.get("organiser_email") or d.get("organizer_email") or "")
        try:
            price = int(float(d.get("price") or 0))
        except Exception:
            price = 0
        update = {
            "status": "Approved",
            "approval_status": "Approved",
            "organiser_email": owner,
            "organizer_email": owner,
            "host_email": owner,
            "payment_status": "Held by Orbit" if price > 0 else "Free",
            "settlement_status": "Pending event completion" if price > 0 else "Free Event",
            "updated_at": datetime.now()
        }
        events.update_one({"event_id": eid}, {"$set": update})
        print(f"Approved {eid} - {title}")


if __name__ == "__main__":
    main()
