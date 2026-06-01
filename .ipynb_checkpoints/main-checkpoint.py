import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware


# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# -----------------------------
# FastAPI setup
# -----------------------------
app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key="smart_dashboard_secret_key")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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
        .eq("email", email)
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
    try:
        if "user" not in request.session:
            return RedirectResponse(url="/login", status_code=302)

        hotel_id = request.session.get("hotel_id")
        hotel_name = request.session.get("hotel_name", "Hotel")

        print("SESSION DATA:", dict(request.session))
        print("HOTEL ID:", hotel_id)
        print("HOTEL NAME:", hotel_name)

        if not hotel_id:
            return RedirectResponse(url="/login", status_code=302)

        res = (
            supabase.table("pre_checkin_submissions")
            .select("*")
            .eq("hotel_id", hotel_id)
            .execute()
        )


        rooms_res = (
        supabase.table("rooms")
        .select("*")
        .eq("hotel_id", hotel_id)
        .execute())

        rooms = rooms_res.data or []
        rooms_df = pd.DataFrame(rooms)
            

        rows = res.data or []
        print("ROWS FOUND:", len(rows))

        df = pd.DataFrame(rows)


        room_status_counts = {}

        available_rooms = 0
        occupied_rooms = 0

        if not rooms_df.empty and "status" in rooms_df.columns:
            rooms_df["status"] = rooms_df["status"].fillna("Unknown").astype(str).str.strip().str.title()

            room_status_counts = rooms_df["status"].value_counts().to_dict()
            print("ROOMS FOUND:", len(rooms_df))
            print("ROOM STATUS COUNTS:", room_status_counts)

            available_rooms = int(room_status_counts.get("Available", 0))
            occupied_rooms = int(room_status_counts.get("Occupied", 0))

        data = {
            "hotel_name": hotel_name,
            "total_checkins": 0,
            "available_rooms": available_rooms,
            "occupied_rooms": occupied_rooms,
            "total_revenue": 0,
            "month_labels": [],
            "month_values": [],
            "recent_guests": [],
            "room_status": room_status_counts,
            "gender_mix": {},
            "checkin_labels": [],
            "checkin_values": [],
            "room_status_labels": list(room_status_counts.keys()),
            "room_status_values": list(room_status_counts.values()),
            "gender_labels": [],
            "gender_values": []

        }

        ai = {
            "bedding_prediction": "No data available yet.",
            "peak_hour": "N/A",
            "avg_revenue": 0
        }

        if not df.empty:
            if "submitted_at" in df.columns:
                df["submitted_at"] = pd.to_datetime(df["submitted_at"], errors="coerce", utc=True)

                valid_dates = df.dropna(subset=["submitted_at"])

                if not valid_dates.empty:
                    hour_counts = valid_dates["submitted_at"].dt.hour.value_counts().sort_index()

                    data["checkin_labels"] = [f"{int(h)}:00" for h in hour_counts.index]
                    data["checkin_values"] = [int(v) for v in hour_counts.values]

                    if not hour_counts.empty:
                        peak_hour = int(hour_counts.idxmax())
                        ai["peak_hour"] = f"{peak_hour}:00"

                    month_counts = valid_dates["submitted_at"].dt.month_name().value_counts()

                    data["month_labels"] = [str(x) for x in month_counts.index]
                    data["month_values"] = [int(v) for v in month_counts.values]

            if "guest_gender" in df.columns:
                df["guest_gender"] = df["guest_gender"].fillna("Unknown").astype(str).str.strip().str.title()
                gender_counts = df["guest_gender"].value_counts()

                data["gender_mix"] = gender_counts.to_dict()
                data["gender_labels"] = [str(x) for x in gender_counts.index]
                data["gender_values"] = [int(v) for v in gender_counts.values]

            data["total_checkins"] = int(len(df))

            recent_guests = []
            for _, row in df.head(5).iterrows():
                recent_guests.append({
                    "guest_name": row.get("guest_full_name", "Unknown Guest"),
                    "room": row.get("hotel_id", "N/A")
                })

            data["recent_guests"] = recent_guests
            ai["bedding_prediction"] = "AI is analyzing guest check-in behavior."

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "data": data,
                "ai": ai,
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

    res = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .eq("hotel_id", hotel_id)
        .order("submitted_at", desc=True)
        .limit(200)
        .execute()
    )

    checkins = res.data or []

    return templates.TemplateResponse(
        request,
        "checkins.html",
        {
            "checkins": checkins,
            "hotel_name": hotel_name
        }
    )


# -----------------------------
# Rooms Page
# -----------------------------
@app.get("/rooms", response_class=HTMLResponse)
def rooms_page(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    hotel_id = request.session.get("hotel_id")
    hotel_name = request.session.get("hotel_name", "Hotel")

    res = (
        supabase.table("rooms")
        .select("*")
        .eq("hotel_id", hotel_id)
        .execute()
    )

    rooms = res.data or []

    return templates.TemplateResponse(
        request,
        "rooms.html",
        {
            "rooms": rooms,
            "hotel_name": hotel_name
        }
    )




@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)

    hotel_id = request.session.get("hotel_id")
    hotel_name = request.session.get("hotel_name", "Hotel")

    checkins_res = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .eq("hotel_id", hotel_id)
        .execute()
    )

    df_checkins = pd.DataFrame(checkins_res.data or [])

    total_checkins = len(df_checkins)

    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "hotel_name": hotel_name,
            "total_checkins": total_checkins,
            "total_bookings": 0,
            "total_revenue": 0,
            "available_rooms": 0,
            "occupied_rooms": 0,
            "checkin_labels": [],
            "checkin_values": [],
            "gender_labels": [],
            "gender_values": [],
            "nationality_labels": [],
            "nationality_values": []
        }
    )

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
        .order("submitted_at", desc=True)
        .limit(200)
        .execute()
    )

    return JSONResponse(content=res.data or [])

@app.get("/test")
def test():
    print("TEST ROUTE OPENED")
    return {"message": "FastAPI is working!"}


