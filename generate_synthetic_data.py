"""
Synthetic UPI Transaction Dataset Generator
--------------------------------------------
Generates realistic UPI transaction records with ~2% fraud rate,
reflecting real-world fraud patterns:
  1. Odd-hour transactions (12am-5am)
  2. High amount to a brand-new receiver
  3. Velocity attacks (rapid repeat transactions in short window)
  4. Location mismatch (device location != usual sender location)
  5. Amount far above sender's historical average (sudden spike)

Usage: python generate_synthetic_data.py --n 200000 --fraud_rate 0.02
"""

import argparse
import uuid
import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

random.seed(42)
np.random.seed(42)

CITIES = ["Bhopal", "Indore", "Delhi", "Mumbai", "Bangalore", "Pune",
          "Hyderabad", "Chennai", "Kolkata", "Jaipur", "Lucknow", "Ahmedabad"]

DEVICE_POOL = [f"dev_{i:05d}" for i in range(20000)]


def random_vpa(prefix="user"):
    return f"{prefix}{random.randint(1000, 999999)}@upi"


def gen_sender_pool(n_senders):
    """Each sender has a stable profile: home city, avg spend, device, avg txns/day."""
    senders = []
    for i in range(n_senders):
        senders.append({
            "sender_vpa": random_vpa("u"),
            "home_city": random.choice(CITIES),
            "avg_amount_30d": round(np.random.lognormal(mean=6.5, sigma=0.8), 2),  # ~ INR 300-3000 typical
            "device_id": random.choice(DEVICE_POOL),
            "avg_txn_per_day": max(1, int(np.random.poisson(2))),
            "known_receivers": [random_vpa("m") for _ in range(random.randint(2, 8))],
        })
    return senders


def random_timestamp(start, end):
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def generate_dataset(n_txns=200000, fraud_rate=0.02, n_senders=15000,
                      start_date="2025-01-01", end_date="2025-09-01"):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    senders = gen_sender_pool(n_senders)

    n_fraud = int(n_txns * fraud_rate)
    n_legit = n_txns - n_fraud

    rows = []

    # ---------- Legit transactions ----------
    for _ in range(n_legit):
        s = random.choice(senders)
        ts = random_timestamp(start, end)
        # legit hours weighted toward daytime
        hour_weights = np.array([1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 9, 9,
                                  9, 9, 9, 8, 8, 8, 9, 9, 8, 6, 4, 2], dtype=float)
        hour = np.random.choice(24, p=hour_weights / hour_weights.sum())
        ts = ts.replace(hour=int(hour), minute=random.randint(0, 59))

        is_new_receiver = random.random() < 0.15
        receiver = random_vpa("m") if is_new_receiver else random.choice(s["known_receivers"])
        amount = max(1, round(np.random.lognormal(
            mean=np.log(max(s["avg_amount_30d"], 10)), sigma=0.5), 2))

        rows.append({
            "transaction_id": str(uuid.uuid4()),
            "sender_vpa": s["sender_vpa"],
            "receiver_vpa": receiver,
            "amount": amount,
            "timestamp": ts,
            "device_id": s["device_id"],
            "location": s["home_city"],
            "sender_home_city": s["home_city"],
            "sender_avg_amount_30d": s["avg_amount_30d"],
            "sender_txn_count_24h": max(1, int(np.random.poisson(s["avg_txn_per_day"]))),
            "is_new_receiver": is_new_receiver,
            "is_fraud": False,
            "fraud_type": "none",
        })

    # ---------- Fraud transactions (mix of 4 patterns) ----------
    fraud_patterns = ["odd_hour_high_amount", "new_receiver_spike",
                       "velocity_attack", "location_mismatch"]

    i = 0
    while i < n_fraud:
        pattern = fraud_patterns[i % len(fraud_patterns)]
        s = random.choice(senders)
        ts = random_timestamp(start, end)

        if pattern == "odd_hour_high_amount":
            ts = ts.replace(hour=random.randint(0, 4), minute=random.randint(0, 59))
            receiver = random_vpa("m")
            amount = round(s["avg_amount_30d"] * random.uniform(8, 25), 2)
            location = s["home_city"]
            device = s["device_id"]
            txn_count_24h = max(1, int(np.random.poisson(s["avg_txn_per_day"])))
            is_new_receiver = True

        elif pattern == "new_receiver_spike":
            ts = ts.replace(hour=random.randint(6, 22), minute=random.randint(0, 59))
            receiver = random_vpa("m")
            amount = round(s["avg_amount_30d"] * random.uniform(10, 40), 2)
            location = s["home_city"]
            device = s["device_id"]
            txn_count_24h = max(1, int(np.random.poisson(s["avg_txn_per_day"])))
            is_new_receiver = True

        elif pattern == "velocity_attack":
            ts = ts.replace(hour=random.randint(0, 23), minute=random.randint(0, 59))
            receiver = random_vpa("m")
            amount = round(s["avg_amount_30d"] * random.uniform(1, 5), 2)
            location = s["home_city"]
            device = s["device_id"]
            txn_count_24h = random.randint(15, 40)  # abnormally high burst
            is_new_receiver = random.random() < 0.6

        else:  # location_mismatch
            ts = ts.replace(hour=random.randint(0, 23), minute=random.randint(0, 59))
            receiver = random_vpa("m")
            amount = round(s["avg_amount_30d"] * random.uniform(2, 10), 2)
            location = random.choice([c for c in CITIES if c != s["home_city"]])
            device = random.choice(DEVICE_POOL)  # different/unrecognized device
            txn_count_24h = max(1, int(np.random.poisson(s["avg_txn_per_day"])))
            is_new_receiver = True

        rows.append({
            "transaction_id": str(uuid.uuid4()),
            "sender_vpa": s["sender_vpa"],
            "receiver_vpa": receiver,
            "amount": amount,
            "timestamp": ts,
            "device_id": device,
            "location": location,
            "sender_home_city": s["home_city"],
            "sender_avg_amount_30d": s["avg_amount_30d"],
            "sender_txn_count_24h": txn_count_24h,
            "is_new_receiver": is_new_receiver,
            "is_fraud": True,
            "fraud_type": pattern,
        })
        i += 1

    df = pd.DataFrame(rows).sample(frac=1, random_state=42).reset_index(drop=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200000)
    parser.add_argument("--fraud_rate", type=float, default=0.02)
    parser.add_argument("--out", type=str, default="upi_transactions.csv")
    args = parser.parse_args()

    df = generate_dataset(n_txns=args.n, fraud_rate=args.fraud_rate)
    df.to_csv(args.out, index=False)
    print(f"Generated {len(df)} transactions -> {args.out}")
    print(df["is_fraud"].value_counts())
    print(df[df.is_fraud]["fraud_type"].value_counts())
