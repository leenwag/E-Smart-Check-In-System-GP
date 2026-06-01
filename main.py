import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client
import joblib
import numpy as np

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from datetime import datetime, UTC, timezone

import httpx

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


#load ai model
congestion_model = joblib.load("congestion_model.pkl")


# -----------------------------
# FastAPI setup
# -----------------------------
app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key="smart_dashboard_secret_key")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def send_approval_email(submission_ids):
    """Notify approved guests via Supabase edge function + SendGrid."""
    ids = [submission_id for submission_id in submission_ids if submission_id]
    if not ids:
        return

    try:
        response = httpx.post(
            f"{SUPABASE_URL}/functions/v1/send-approval-email",
            headers={
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
            },
            json={"submission_ids": ids},
            timeout=30.0,
        )
        response.raise_for_status()
        print("APPROVAL EMAIL RESULT:", response.json())
    except Exception as e:
        print("APPROVAL EMAIL ERROR:", e)



# -----------------------------
# Home
# -----------------------------
@app.get("/")
def home():
    return RedirectResponse(url="/login", status_code=302)


# -----------------------------
# Login Page
# -----------------------------
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {}
    )


# -----------------------------
# Login Action
# -----------------------------
@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    response = (
        supabase.table("hotels")
        .select("*")
        .eq("admin_email", email)
        .eq("password", password)
        .execute()
    )

    if not response.data:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email or password"}
        )

    hotel = response.data[0]

    request.session["user"] = hotel["email"]
    request.session["hotel_id"] = hotel["id"]
    request.session["hotel_name"] = hotel["name"]

    return RedirectResponse(url="/dashboard", status_code=303)


# -----------------------------
# Dashboard
# -----------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    total_revenue = 0
    revenue_labels = []
    revenue_values = []
    try:
       
        # =========================
        # 1. Check login session
        # =========================
        if "user" not in request.session:
            return RedirectResponse(url="/login", status_code=302)

        hotel_id = request.session.get("hotel_id")
        hotel_name = request.session.get("hotel_name", "Hotel")

        if not hotel_id:
            return RedirectResponse(url="/login", status_code=302)

        print("SESSION DATA:", dict(request.session))
        print("HOTEL ID:", hotel_id)
        print("HOTEL NAME:", hotel_name)

        # =========================
        # 2. Load check-in data
        # =========================
        checkins_res = (
            supabase.table("pre_checkin_submissions")
            .select("*")
            .eq("hotel_id", hotel_id)
            .execute()
        )

        rows = checkins_res.data or []
        df = pd.DataFrame(rows)

    

        # =========================
        # 3. Load rooms data
        # =========================
        rooms_res = (
            supabase.table("rooms")
            .select("*")
            .eq("hotel_id", hotel_id)
            .execute()
        )

        rooms = rooms_res.data or []
        rooms_df = pd.DataFrame(rooms)

        # =========================
        # 4. Room status calculations
        # =========================
        room_status_counts = {}
        available_rooms = 0
        occupied_rooms = 0

        if not rooms_df.empty and "status" in rooms_df.columns:
            rooms_df["status"] = (
                rooms_df["status"]
                .fillna("Unknown")
                .astype(str)
                .str.strip()
                .str.title()
            )

            room_status_counts = rooms_df["status"].value_counts().to_dict()

            available_rooms = int(room_status_counts.get("Available", 0))
            occupied_rooms = int(room_status_counts.get("Occupied", 0))

            print("ROOMS FOUND:", len(rooms_df))
            print("ROOM STATUS COUNTS:", room_status_counts)

        # =========================
        # 5. Default dashboard data
        # IMPORTANT:
        # Every value used in index.html must exist here,
        # even if the database has no data.
        # =========================
        data = {
            "hotel_name": hotel_name,
            "total_checkins": 0,
            "available_rooms": available_rooms,
            "occupied_rooms": occupied_rooms,

            "recent_guests": [],
            "room_status": room_status_counts,
            "gender_mix": {},

            "checkin_labels": [],
            "checkin_values": [],

            "month_labels": [],
            "month_values": [],

            "age_labels": [],
            "age_values": [],

            "room_status_labels": list(room_status_counts.keys()),
            "room_status_values": list(room_status_counts.values()),

            "gender_labels": [],
            "gender_values": [], 


            "revenue": total_revenue,
            "revenue_labels": revenue_labels,
             "revenue_values": revenue_values,


            "ai_prediction": "Low Congestion",
            "ai_prediction_label": "Safe",
            "ai_prediction_probability": 0,
            "ai_prediction_message": "Normal check-in activity expected.",


            "ai_forecast_labels": [],
            "ai_forecast_values": [],

        }


        # =========================
        # 7. Check-in time analysis
        # Used for:
        # - Check-In Time Overview chart
        # - Peak Month chart
        # =========================
        if "arrival_time" in df.columns:
            df["arrival_time"] = pd.to_datetime(
                df["arrival_time"],
                errors="coerce",
                utc=True
            )

            valid_dates = df.dropna(subset=["arrival_time"])

            if not valid_dates.empty:
                # Hour chart
                hour_counts = valid_dates["arrival_time"].dt.hour.value_counts().sort_index()

                data["checkin_labels"] = [f"{int(h)}:00" for h in hour_counts.index]
                data["checkin_values"] = [int(v) for v in hour_counts.values]

                if not hour_counts.empty:
                    peak_hour = int(hour_counts.idxmax())

                # Peak month chart
                month_counts = valid_dates["arrival_time"].dt.month_name().value_counts()

                data["month_labels"] = [str(x) for x in month_counts.index]
                data["month_values"] = [int(v) for v in month_counts.values]

        # =========================
        # 8. Gender analysis
        # Used for:
        # - Gender Mix Chart
        # =========================
        if "guest_gender" in df.columns:
            df["guest_gender"] = (
                df["guest_gender"]
                .fillna("Unknown")
                .astype(str)
                .str.strip()
                .str.title()
            )

            gender_counts = df["guest_gender"].value_counts()

            data["gender_mix"] = gender_counts.to_dict()
            data["gender_labels"] = [str(x) for x in gender_counts.index]
            data["gender_values"] = [int(v) for v in gender_counts.values]

        # =========================
        # 9. Age group analysis
        # This supports two cases:
        # A) If the table has guest_age
        # B) If the table has date_of_birth
        # =========================
        if "guest_age" in df.columns:
            df["guest_age"] = pd.to_numeric(df["guest_age"], errors="coerce")

        elif "date_of_birth" in df.columns:
            df["date_of_birth"] = pd.to_datetime(
                df["date_of_birth"],
                errors="coerce",
                utc=True
            )

            today = pd.Timestamp.now(tz="UTC")
            df["guest_age"] = ((today - df["date_of_birth"]).dt.days / 365.25)

        # Create age groups only if guest_age now exists
        if "guest_age" in df.columns:
            valid_ages = df.dropna(subset=["guest_age"]).copy()

            if not valid_ages.empty:
                valid_ages["age_group"] = pd.cut(
                    valid_ages["guest_age"],
                    bins=[0, 18, 30, 45, 60, 120],
                    labels=["0-18", "19-30", "31-45", "46-60", "60+"]
                )

                age_group_counts = valid_ages["age_group"].value_counts().sort_index()

                data["age_labels"] = [str(x) for x in age_group_counts.index]
                data["age_values"] = [int(v) for v in age_group_counts.values]

                print("AGE GROUP COUNTS:", age_group_counts.to_dict())

        # =========================
        # 10. Recent guests
        # Shows latest 5 rows from this hotel
        # =========================
        data["total_checkins"] = int(len(df))

        recent_guests = []
        for _, row in df.head(5).iterrows():
            recent_guests.append({
                "guest_name": row.get("guest_full_name", "Unknown Guest"),
                "room": row.get("hotel_id", "N/A")
            })

        data["recent_guests"] = recent_guests

       
        # ==============================
        # 12. Revenue Chart Data
        # ==============================

        bookings_res = (
           supabase.table("bookings")
            .select("id, hotel_id, created_at")
            .eq("hotel_id", hotel_id)
            .execute()
        )

        booking_ids = [b["id"] for b in bookings_res.data or []]

        revenue_labels = []
        revenue_values = []
        total_revenue = 0    
        if booking_ids:
            payments_res = (
                supabase.table("payments")
                .select("amount, booking_id")
                .in_("booking_id", booking_ids)
                .execute()
            )

        payments = payments_res.data or []

        total_revenue = sum(
            float(p["amount"])
            for p in payments
            if p.get("amount") is not None
        )

        payments_df = pd.DataFrame(payments)

        if not payments_df.empty:
            payments_df["amount"] = pd.to_numeric(payments_df["amount"], errors="coerce").fillna(0)

            # get booking dates
            bookings_df = pd.DataFrame(bookings_res.data or [])

            if "id" in bookings_df.columns and "created_at" in bookings_df.columns:
                merged_df = payments_df.merge(
                    bookings_df[["id", "created_at"]],
                    left_on="booking_id",
                    right_on="id",
                    how="left"
                )

                merged_df["created_at"] = pd.to_datetime(merged_df["created_at"], errors="coerce")
                merged_df = merged_df.dropna(subset=["created_at"])

                monthly_revenue = (
                    merged_df.groupby(merged_df["created_at"].dt.month_name())["amount"]
                    .sum()
                )

                revenue_labels = list(monthly_revenue.index)
                revenue_values = [float(v) for v in monthly_revenue.values]

        data["revenue"] = total_revenue
        data["total_revenue"] = total_revenue
        data["revenue_labels"] = revenue_labels
        data["revenue_values"] = revenue_values


        # ==============================
        # AI Congestion Prediction card
        # ===============================
        try:
            now = datetime.now()

            hour = now.hour
            month = now.month
            weekday = now.weekday()

            # Jordan weekend: Thursday, Friday, Saturday
            is_weekend = 1 if weekday in [3, 4, 5] else 0

            # Real hotel peak hours
            is_peak_hour = 1 if (
                7 <= hour <= 10 or
                11 <= hour <= 13 or
                14 <= hour <= 18
            ) else 0

            # Aqaba tourism peak months
            is_peak_season = 1 if month in [6, 7, 8, 12] else 0

            if not df.empty and "booked_guests_count" in df.columns:
                booked_guests_count = pd.to_numeric(
                    df["booked_guests_count"],
                    errors="coerce"
                ).fillna(1).mean()
            else:
                booked_guests_count = 1

            submitted_guests_count = len(df)

            if not df.empty and "requires_additional_payment" in df.columns:
                requires_additional_payment = int(
                    pd.to_numeric(
                        df["requires_additional_payment"],
                        errors="coerce"
                    ).fillna(0).max()
                )
            else:
                requires_additional_payment = 0

            # IMPORTANT:
            # Keep these columns the same as the model training columns.
            # This avoids feature mismatch errors.
            ai_input = pd.DataFrame([{
                "month": month,
                "weekday": weekday,
                "is_weekend": is_weekend,
                "hotel_id": hotel_id,
                "booked_guests_count": booked_guests_count,
                "submitted_guests_count": submitted_guests_count,
                "requires_additional_payment": requires_additional_payment
            }])

            prediction = congestion_model.predict(ai_input)[0]

            if hasattr(congestion_model, "predict_proba"):
                probability = congestion_model.predict_proba(ai_input)[0][1] * 100
            else:
                probability = 100 if prediction == 1 else 0

            # Business logic correction for realistic hotel behavior

            # Late night should rarely be high congestion
            if 0 <= hour <= 5:
                probability *= 0.45

            # Non-peak hours should be reduced
            if is_peak_hour == 0:
                probability *= 0.75

            # Peak tourism season slightly increases expected activity
            if is_peak_season == 1:
                probability *= 1.08

            # Keep probability realistic
            probability = max(10, min(probability, 92))

            # Final label based on adjusted probability
            if probability >= 75:
                data["ai_prediction"] = "High Congestion"
                data["ai_prediction_label"] = "Busy"
                data["ai_prediction_message"] = "Prepare extra staff for check-in."

            elif probability >= 45:
                data["ai_prediction"] = "Moderate Congestion"
                data["ai_prediction_label"] = "Moderate"
                data["ai_prediction_message"] = "Expected medium guest activity."

            else:
                data["ai_prediction"] = "Low Congestion"
                data["ai_prediction_label"] = "Safe"
                data["ai_prediction_message"] = "Normal hotel activity expected."

            data["ai_prediction_probability"] = round(probability, 1)

        except Exception as e:
            print("AI PREDICTION ERROR:", e)

            booked_guests_count = 1
            submitted_guests_count = 0
            requires_additional_payment = 0

            data["ai_prediction"] = "Prediction unavailable"
            data["ai_prediction_label"] = "N/A"
            data["ai_prediction_probability"] = 0
            data["ai_prediction_message"] = "AI model could not generate a prediction."


        # =========================
        # Long AI Forecast - 30 Days
        # =========================
        forecast_labels = []
        forecast_values = []

        for i in range(30):
            future_date = datetime.now() + pd.Timedelta(days=i)

            future_month = future_date.month
            future_weekday = future_date.weekday()

            # Jordan weekend: Thursday, Friday, Saturday
            future_is_weekend = 1 if future_weekday in [3, 4, 5] else 0

            forecast_input = pd.DataFrame([{
                "month": future_month,
                "weekday": future_weekday,
                "is_weekend": future_is_weekend,
                "hotel_id": hotel_id,
                "booked_guests_count": booked_guests_count,
                "submitted_guests_count": submitted_guests_count,
                "requires_additional_payment": requires_additional_payment
            }])

            try:
                if hasattr(congestion_model, "predict_proba"):
                    risk = congestion_model.predict_proba(forecast_input)[0][1] * 100
                else:
                    risk = congestion_model.predict(forecast_input)[0] * 100

                # Keep model output realistic before variation
                risk = max(10, min(risk, 92))

                # Add realistic daily variation so chart does not look flat
                variation = np.random.randint(-6, 7)

                # Jordan weekends are usually busier
                if future_is_weekend:
                    variation += 5

                # Tourism season slightly increases risk
                if future_month in [6, 7, 8, 12]:
                    variation += 3

                risk += variation

                # Keep final forecast risk realistic
                risk = max(15, min(risk, 95))

            except Exception as e:
                print("AI FORECAST ERROR:", e)
                risk = 0

            forecast_labels.append(future_date.strftime("%d %b"))
            forecast_values.append(round(risk, 1))

        data["ai_forecast_labels"] = forecast_labels
        data["ai_forecast_values"] = forecast_values

        # =========================
        # 13. Send data to dashboard HTML
        # =========================
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "data": data,
                "hotel_name": hotel_name
            }
        )
    
        

    except Exception as e:
        print("DASHBOARD ERROR:", e)

        return HTMLResponse(
            content=f"""
            <h1>Dashboard Error</h1>
            <p>{str(e)}</p>
            <a href="/login">Back to login</a>
            """,
            status_code=500
        )
    

        



# -----------------------------
# Check-ins Page
# -----------------------------
@app.get("/checkins", response_class=HTMLResponse)
def checkins_page(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    hotel_id = request.session.get("hotel_id")
    hotel_name = request.session.get("hotel_name", "Hotel")

    status_filter = request.query_params.get("status", "all")
    search_name = request.query_params.get("search", "").strip()
    date_filter = request.query_params.get("date_filter", "all")
    selected_date = request.query_params.get("selected_date", "")

    # Get all data for cards
    all_res = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .eq("hotel_id", hotel_id)
        .execute()
    )

    all_checkins = all_res.data or []

    total_count = len(all_checkins)
    approved_count = len([g for g in all_checkins if g.get("verification_status") == "approved"])
    pending_count = len([g for g in all_checkins if g.get("verification_status") == "pending"])
    rejected_count = len([g for g in all_checkins if g.get("verification_status") == "rejected"])

    query = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .eq("hotel_id", hotel_id)
        .order("arrival_time", desc=True)
        .limit(200)
    )

    if status_filter in ["approved", "pending", "rejected"]:
        query = query.eq("verification_status", status_filter)

    if search_name:
        query = query.ilike("guest_full_name", f"%{search_name}%")

    if selected_date:
        if date_filter == "day":
            query = query.gte("arrival_time", f"{selected_date}T00:00:00")
            query = query.lt("arrival_time", f"{selected_date}T23:59:59")

        elif date_filter == "month":
            year, month = selected_date.split("-")
            start_date = f"{year}-{month}-01T00:00:00"

            if month == "12":
                end_date = f"{int(year) + 1}-01-01T00:00:00"
            else:
                end_date = f"{year}-{int(month) + 1:02d}-01T00:00:00"

            query = query.gte("arrival_time", start_date)
            query = query.lt("arrival_time", end_date)

        elif date_filter == "year":
            start_date = f"{selected_date}-01-01T00:00:00"
            end_date = f"{int(selected_date) + 1}-01-01T00:00:00"

            query = query.gte("arrival_time", start_date)
            query = query.lt("arrival_time", end_date)

    res = query.execute()
    checkins = res.data or []

    return templates.TemplateResponse(
        request,
        "checkins.html",
        {
            "checkins": checkins,
            "hotel_name": hotel_name,
            "status_filter": status_filter,
            "search_name": search_name,
            "date_filter": date_filter,
            "selected_date": selected_date,
            "total_count": total_count,
            "approved_count": approved_count,
            "pending_count": pending_count,
            "rejected_count": rejected_count,
        }
    )

# -----------------------------
# Rooms Page
# -----------------------------
@app.get("/rooms", response_class=HTMLResponse)
async def rooms(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    hotel_id = request.session.get("hotel_id")
    hotel_name = request.session.get("hotel_name", "Hotel")

    res = (
        supabase.table("rooms")
        .select("*")
        .eq("hotel_id", hotel_id)
        .order("room_number")
        .execute()
    )

    rooms = res.data or []

    total_rooms = len(rooms)

    available_rooms = len([
        room for room in rooms
        if room.get("status") == "available"
    ])

    occupied_rooms = len([
        room for room in rooms
        if room.get("status") == "occupied"
    ])

    prices = [
        float(room.get("price") or 0)
        for room in rooms
    ]

    average_price = round(sum(prices) / len(prices), 2) if prices else 0

    return templates.TemplateResponse(
        request,
        "rooms.html",
        {
            "rooms": rooms,
            "hotel_name": hotel_name,
            "total_rooms": total_rooms,
            "available_rooms": available_rooms,
            "occupied_rooms": occupied_rooms,
            "average_price": average_price,
        }
    )


@app.post("/update-room-status")
async def update_room_status(request: Request):
    form = await request.form()

    room_id = form.get("room_id")
    new_status = form.get("status")

    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    hotel_id = request.session["hotel_id"]

    supabase.table("rooms").update({
        "status": new_status
    }).eq("id", room_id).eq("hotel_id", hotel_id).execute()

    return RedirectResponse(url="/rooms", status_code=303)






@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    hotel_name = request.session.get("hotel_name", "Hotel")
    hotel_id = request.session.get("hotel_id", "")

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "hotel_name": hotel_name,
            "hotel_id": hotel_id
        }
    )






@app.post("/approve-all-guests")
async def approve_all_guests(request: Request):
    print("APPROVE ALL ROUTE REACHED")

    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    hotel_id = request.session.get("hotel_id")
    manager_email = request.session.get("user")

    print("HOTEL ID:", hotel_id)

    pending_res = (
        supabase.table("pre_checkin_submissions")
        .select("id")
        .eq("hotel_id", hotel_id)
        .eq("verification_status", "pending")
        .execute()
    )

    pending_ids = [guest["id"] for guest in (pending_res.data or [])]

    result = (
        supabase.table("pre_checkin_submissions")
        .update({
            "verification_status": "approved",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": manager_email
        })
        .eq("hotel_id", hotel_id)
        .eq("verification_status", "pending")
        .execute()
    )

    print("SUPABASE RESULT:", result)

    send_approval_email(pending_ids)

    return RedirectResponse(url="/checkins", status_code=303)





# Reject guest
@app.post("/reject-guest")
async def reject_guest(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    guest_id = form.get("guest_id")

    supabase.table("pre_checkin_submissions").update({
        "verification_status": "rejected",
        "verified_at": datetime.utcnow().isoformat(),
        "verified_by": request.session.get("user")
    }).eq("id", guest_id).execute()

    return RedirectResponse(url="/checkins", status_code=303)


# Approve guest 

@app.post("/approve-guest")
async def approve_guest(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    guest_id = form.get("guest_id")

    print("GUEST ID RECEIVED:", guest_id)

    if not guest_id:
        return RedirectResponse(url="/checkins", status_code=303)

    result = (
        supabase.table("pre_checkin_submissions")
        .update({
            "verification_status": "approved",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": request.session.get("user")
        })
        .eq("id", guest_id)
        .execute()
    )

    print("SUPABASE UPDATE RESULT:", result)

    send_approval_email([guest_id])

    return RedirectResponse(url="/checkins", status_code=303)

# -----------------------------
# Logout
# -----------------------------
@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# -----------------------------
# Admin JSON check-ins
# -----------------------------
@app.get("/admin/checkins")
def list_checkins():
    res = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .order("arrival_time", desc=True)
        .limit(200)
        .execute()
    )

    return JSONResponse(content=res.data or [])




@app.get("/test")
def test():
    print("TEST ROUTE OPENED")
    return {"message": "FastAPI is working!"}


def _format_pass_date(date_string):
    if not date_string:
        return "N/A"
    date = datetime.fromisoformat(str(date_string).replace("Z", "+00:00"))
    return date.strftime("%b %d, %Y")


def _format_pass_datetime(date_string):
    if not date_string:
        return "Recently approved"
    date = datetime.fromisoformat(str(date_string).replace("Z", "+00:00"))
    return date.strftime("%b %d, %Y, %I:%M %p").lstrip("0").replace(" 0", " ")


@app.get("/checkin-pass", response_class=HTMLResponse)
def checkin_pass_page(request: Request, sid: int, ref: str = ""):
    """Public guest pass page — opened when QR code is scanned."""
    submission_res = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .eq("id", sid)
        .execute()
    )

    if not submission_res.data:
        return templates.TemplateResponse(
            request,
            "checkin_pass.html",
            {
                "error": True,
                "error_title": "Pass Not Found",
                "error_message": "We could not find this check-in pass.",
            },
        )

    submission = submission_res.data[0]

    if submission.get("verification_status") != "approved":
        return templates.TemplateResponse(
            request,
            "checkin_pass.html",
            {
                "error": True,
                "error_title": "Not Yet Approved",
                "error_message": "Your pre-arrival check-in has not been approved yet. Please contact the hotel.",
            },
        )

    booking_res = (
        supabase.table("bookings")
        .select("*")
        .eq("id", submission["booking_id"])
        .execute()
    )

    if not booking_res.data:
        return templates.TemplateResponse(
            request,
            "checkin_pass.html",
            {
                "error": True,
                "error_title": "Booking Not Found",
                "error_message": "The reservation linked to this pass could not be found.",
            },
        )

    booking = booking_res.data[0]
    reservation_number = booking.get("booking_reference") or f"BK{booking['id']:06d}"

    if ref and ref != reservation_number:
        return templates.TemplateResponse(
            request,
            "checkin_pass.html",
            {
                "error": True,
                "error_title": "Invalid Pass",
                "error_message": "This check-in pass link is not valid.",
            },
        )

    hotel_res = (
        supabase.table("hotels")
        .select("*")
        .eq("id", submission["hotel_id"])
        .execute()
    )
    hotel = hotel_res.data[0] if hotel_res.data else {}

    room_number = "Assigned at check-in"
    room_type_name = "Standard"

    if booking.get("room_id"):
        room_res = (
            supabase.table("rooms")
            .select("room_number, room_type_id")
            .eq("id", booking["room_id"])
            .execute()
        )
        if room_res.data:
            room = room_res.data[0]
            room_number = room.get("room_number") or room_number
            if room.get("room_type_id"):
                type_res = (
                    supabase.table("room_types")
                    .select("name")
                    .eq("id", room["room_type_id"])
                    .execute()
                )
                if type_res.data:
                    room_type_name = type_res.data[0].get("name") or room_type_name

    guest_name = submission.get("guest_full_name") or (
        f"{booking.get('guest_first_name', '')} {booking.get('guest_last_name', '')}".strip()
    ) or "Guest"

    submitted_guests = submission.get("submitted_guests_count")
    booked_guests = booking.get("number_of_guests", 1)

    return templates.TemplateResponse(
        request,
        "checkin_pass.html",
        {
            "error": False,
            "guest_name": guest_name,
            "hotel_name": hotel.get("name", "Hotel"),
            "hotel_location": ", ".join(
                part for part in [
                    hotel.get("address"),
                    hotel.get("city"),
                    hotel.get("country"),
                ] if part
            ),
            "hotel_email": hotel.get("email", ""),
            "hotel_phone": hotel.get("phone", ""),
            "reservation_number": reservation_number,
            "check_in_date": _format_pass_date(booking.get("check_in_date")),
            "check_out_date": _format_pass_date(booking.get("check_out_date")),
            "room_label": f"{room_number} ({room_type_name})",
            "guests_text": f"{booked_guests} booked / {submitted_guests or booked_guests} submitted",
            "nationality": submission.get("nationality") or "Not provided",
            "total_amount": f"{float(booking.get('total_amount') or 0):.2f}",
            "verified_at": _format_pass_datetime(submission.get("verified_at")),
        },
    )






