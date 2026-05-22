import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from datetime import datetime, UTC

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

        print("ROWS FOUND:", len(rows))

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
        }

        # =========================
        # 6. Default AI insights
        # =========================
        ai = {
            "bedding_prediction": "No data available yet.",
            "peak_hour": "N/A",
            "avg_revenue": 0
        }

        # Stop here if there are no check-ins
        if df.empty:
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "data": data,
                    "ai": ai,
                    "hotel_name": hotel_name
                }
            )

        # =========================
        # 7. Check-in time analysis
        # Used for:
        # - Check-In Time Overview chart
        # - Peak Hour AI insight
        # - Peak Month chart
        # =========================
        if "submitted_at" in df.columns:
            df["submitted_at"] = pd.to_datetime(
                df["submitted_at"],
                errors="coerce",
                utc=True
            )

            valid_dates = df.dropna(subset=["submitted_at"])

            if not valid_dates.empty:
                # Hour chart
                hour_counts = valid_dates["submitted_at"].dt.hour.value_counts().sort_index()

                data["checkin_labels"] = [f"{int(h)}:00" for h in hour_counts.index]
                data["checkin_values"] = [int(v) for v in hour_counts.values]

                if not hour_counts.empty:
                    peak_hour = int(hour_counts.idxmax())
                    ai["peak_hour"] = f"{peak_hour}:00"

                # Peak month chart
                month_counts = valid_dates["submitted_at"].dt.month_name().value_counts()

                data["month_labels"] = [str(x) for x in month_counts.index]
                data["month_values"] = [int(v) for v in month_counts.values]

        # =========================
        # 8. Gender analysis
        # Used for:
        # - Gender Mix Chart
        # - Gender text in AI panel
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

        # =========================
        # 11. AI message
        # =========================
        ai["bedding_prediction"] = "AI is analyzing guest check-in behavior."


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


        # =========================
        # 13. Send data to dashboard HTML
        # =========================
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

    # Get selected filter from URL:
    # /checkins?status=approved
    # /checkins?status=pending
    # /checkins?status=rejected
    # /checkins?status=all
    status_filter = request.query_params.get("status", "all")

    query = (
        supabase.table("pre_checkin_submissions")
        .select("*")
        .eq("hotel_id", hotel_id)
        .order("submitted_at", desc=True)
        .limit(200)
    )

    if status_filter == "approved":
        query = query.eq("verification_status", "approved")

    elif status_filter == "pending":
        query = query.eq("verification_status", "pending")

    elif status_filter == "rejected":
        query = query.eq("verification_status", "rejected")

    res = query.execute()
    checkins = res.data or []

    print("HOTEL ID:", hotel_id)
    print("STATUS FILTER:", status_filter)
    print("CHECKINS FOUND:", len(checkins))

    return templates.TemplateResponse(
        request,
        "checkins.html",
        {
            "checkins": checkins,
            "hotel_name": hotel_name,
            "status_filter": status_filter
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
            "verified_at": datetime.now(UTC).isoformat(),
            "verified_by": request.session.get("user")
        })
        .eq("id", guest_id)
        .execute()
    )

    print("SUPABASE UPDATE RESULT:", result)

    return RedirectResponse(url="/checkins", status_code=303)



@app.post("/approve-all-guests")
async def approve_all_guests(request: Request):
    print("APPROVE ALL ROUTE REACHED")  # debug

    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=303)

    hotel_id = request.session.get("hotel_id")
    manager_email = request.session.get("user")

    print("HOTEL ID:", hotel_id)

    result = (
        supabase.table("pre_checkin_submissions")
        .update({
            "verification_status": "approved",
            "verified_at": datetime.utcnow().isoformat(),
            "verified_by": manager_email
        })
        .eq("hotel_id", hotel_id)
        .eq("verification_status", "pending")  # only pending guests
        .execute()
    )

    print("SUPABASE RESULT:", result)

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





