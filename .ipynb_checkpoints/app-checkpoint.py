from flask import Flask, render_template, request, redirect, url_for, session
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import json


app = Flask(__name__)
app.secret_key = "smart_dashboard_secret_key"

# Load hotel data
def load_data():
    return pd.read_csv("hotel_data.csv")

# Dashboard metrics
def get_dashboard_data():
    df = load_data()

    total_checkins = len(df)
    available_rooms = (df["status"] == "Available").sum()
    occupied_rooms = (df["status"] == "Occupied").sum()
    total_revenue = df["revenue"].sum()

    gender_mix = df["gender"].value_counts().to_dict()

    age_groups = {
        "18-25": int(((df["age"] >= 18) & (df["age"] <= 25)).sum()),
        "26-35": int(((df["age"] >= 26) & (df["age"] <= 35)).sum()),
        "36-45": int(((df["age"] >= 36) & (df["age"] <= 45)).sum()),
        "46+": int((df["age"] >= 46).sum())
    }

    room_status = df["status"].value_counts().to_dict()

    recent_guests = df[["guest_name", "room"]].to_dict(orient="records")

    checkin_counts = df["check_in_time"].value_counts().sort_index()
    checkin_labels = [f"{int(hour)}:00" for hour in checkin_counts.index.tolist()]
    checkin_values = [int(v) for v in checkin_counts.values.tolist()]

    room_status_labels = list(room_status.keys())
    room_status_values = [int(v) for v in room_status.values()]

    gender_labels = list(gender_mix.keys())
    gender_values = [int(v) for v in gender_mix.values()]

    return {
        "total_checkins": int(total_checkins),
        "available_rooms": int(available_rooms),
        "occupied_rooms": int(occupied_rooms),
        "total_revenue": float(total_revenue),
        "gender_mix": gender_mix,
        "age_groups": age_groups,
        "room_status": room_status,
        "recent_guests": recent_guests,
        "checkin_labels": checkin_labels,
        "checkin_values": checkin_values,
        "room_status_labels": room_status_labels,
        "room_status_values": room_status_values,
        "gender_labels": gender_labels,
        "gender_values": gender_values
    }

# Simple AI model
def get_ai_insights():
    df = load_data()

    # Convert Yes/No to numbers
    df["extra_bedding_encoded"] = df["extra_bedding"].map({"Yes": 1, "No": 0})
    df["gender_encoded"] = df["gender"].map({"Male": 0, "Female": 1})

    X = df[["age", "nights", "check_in_time", "gender_encoded"]]
    y = df["extra_bedding_encoded"]

    model = RandomForestClassifier(n_estimators=50, random_state=42)
    model.fit(X, y)

    # Predict for a sample future guest
    sample_guest = pd.DataFrame([{
        "age": 34,
        "nights": 3,
        "check_in_time": 15,
        "gender_encoded": 1
    }])

    prediction = model.predict(sample_guest)[0]

    bedding_prediction = "Likely to request extra bedding" if prediction == 1 else "Unlikely to request extra bedding"

    peak_hour = int(df["check_in_time"].mode()[0])

    return {
        "bedding_prediction": bedding_prediction,
        "peak_hour": f"{peak_hour}:00",
        "avg_revenue": round(df["revenue"].mean(), 2)
    }

@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if email == "manager@hotel.com" and password == "1234":
            session["user"] = email
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid email or password")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    dashboard_data = get_dashboard_data()
    ai_data = get_ai_insights()

    return render_template("index.html", data=dashboard_data, ai=ai_data)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)