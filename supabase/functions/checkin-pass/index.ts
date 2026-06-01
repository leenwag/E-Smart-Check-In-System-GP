import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  const url = new URL(req.url);

  try {
    const submissionId = url.searchParams.get("sid");
    const bookingRef = url.searchParams.get("ref");

    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
    );

    if (!submissionId) {
      return deliverHtmlPage(
        supabase,
        "error-invalid-link",
        errorPage("Invalid Link", "This check-in pass link is missing required information."),
      );
    }

    const { data: submission, error: submissionError } = await supabase
      .from("pre_checkin_submissions")
      .select("*")
      .eq("id", submissionId)
      .single();

    if (submissionError || !submission) {
      return deliverHtmlPage(
        supabase,
        `error-not-found-${submissionId}`,
        errorPage("Pass Not Found", "We could not find this check-in pass."),
      );
    }

    if (submission.verification_status !== "approved") {
      return deliverHtmlPage(
        supabase,
        `error-pending-${submissionId}`,
        errorPage("Not Yet Approved", "Your pre-arrival check-in has not been approved yet. Please contact the hotel."),
      );
    }

    const { data: booking } = await supabase
      .from("bookings")
      .select("*")
      .eq("id", submission.booking_id)
      .single();

    if (!booking) {
      return deliverHtmlPage(
        supabase,
        `error-booking-${submissionId}`,
        errorPage("Booking Not Found", "The reservation linked to this pass could not be found."),
      );
    }

    const reservationNumber = booking.booking_reference ||
      `BK${String(booking.id).padStart(6, "0")}`;

    if (bookingRef && bookingRef !== reservationNumber) {
      return deliverHtmlPage(
        supabase,
        `error-invalid-ref-${submissionId}`,
        errorPage("Invalid Pass", "This check-in pass link is not valid."),
      );
    }

    const { data: hotel } = await supabase
      .from("hotels")
      .select("*")
      .eq("id", submission.hotel_id)
      .single();

    let roomNumber = "Assigned at check-in";
    let roomTypeName = "Standard";

    if (booking.room_id) {
      const { data: room } = await supabase
        .from("rooms")
        .select("room_number, room_type_id")
        .eq("id", booking.room_id)
        .maybeSingle();

      if (room?.room_number) roomNumber = room.room_number;

      if (room?.room_type_id) {
        const { data: roomType } = await supabase
          .from("room_types")
          .select("name")
          .eq("id", room.room_type_id)
          .maybeSingle();
        if (roomType?.name) roomTypeName = roomType.name;
      }
    }

    const guestName = submission.guest_full_name ||
      `${booking.guest_first_name || ""} ${booking.guest_last_name || ""}`.trim() ||
      "Guest";

    const hotelLocation = [hotel?.address, hotel?.city, hotel?.country]
      .filter(Boolean)
      .join(", ");

    const html = passPage({
      guestName,
      hotelName: hotel?.name || "Hotel",
      hotelLocation,
      hotelEmail: hotel?.email || "",
      hotelPhone: hotel?.phone || "",
      reservationNumber,
      checkInDate: formatDate(booking.check_in_date),
      checkOutDate: formatDate(booking.check_out_date),
      roomNumber,
      roomTypeName,
      numberOfGuests: booking.number_of_guests,
      submittedGuests: submission.submitted_guests_count,
      nationality: submission.nationality || "Not provided",
      totalAmount: booking.total_amount,
      verifiedAt: submission.verified_at
        ? formatDateTime(submission.verified_at)
        : "Recently approved",
    });

    return deliverHtmlPage(supabase, `pass-${submissionId}`, html);
  } catch (error) {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
    );
    return deliverHtmlPage(
      supabase,
      "error-server",
      errorPage("Something Went Wrong", error instanceof Error ? error.message : "Please try again later."),
    );
  }
});

/**
 * Supabase serves .html as text/plain (shows source on iPhone).
 * Wrap the pass in SVG (image/svg+xml) so mobile browsers render it.
 */
function wrapPassHtmlInSvg(html: string): string {
  const styles = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)]
    .map((match) => `<style>${match[1]}</style>`)
    .join("");
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*)<\/body>/i);
  const inner = bodyMatch ? bodyMatch[1] : html;

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 480 1680" preserveAspectRatio="xMidYMin meet">
<foreignObject width="480" height="1680">
<div xmlns="http://www.w3.org/1999/xhtml">${styles}${inner}</div>
</foreignObject>
</svg>`;
}

function guestInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "G";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

async function deliverHtmlPage(
  supabase: ReturnType<typeof createClient>,
  cacheKey: string,
  html: string,
): Promise<Response> {
  const svg = wrapPassHtmlInSvg(html);
  const path = `passes/${cacheKey}.svg`;
  const body = new TextEncoder().encode(svg);

  const { error: uploadError } = await supabase.storage
    .from("checkin-identity-docs")
    .upload(path, body, {
      contentType: "image/svg+xml; charset=utf-8",
      upsert: true,
    });

  if (uploadError) {
    return new Response(svg, {
      status: 200,
      headers: {
        ...corsHeaders,
        "Content-Type": "image/svg+xml; charset=utf-8",
        "Cache-Control": "no-store, max-age=0",
      },
    });
  }

  const { data } = supabase.storage
    .from("checkin-identity-docs")
    .getPublicUrl(path);

  return new Response(null, {
    status: 302,
    headers: {
      Location: `${data.publicUrl}?v=${Date.now()}`,
      "Cache-Control": "no-store",
    },
  });
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
}

function formatDateTime(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function detailRow(icon: string, label: string, value: string): string {
  return `<div class="row">
    <div class="row-icon">${icon}</div>
    <div class="row-body">
      <div class="row-label">${escapeHtml(label)}</div>
      <div class="row-value">${escapeHtml(value)}</div>
    </div>
  </div>`;
}

function errorPage(title: string, message: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(title)}</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;background:#e8eef3;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
    .card{max-width:420px;width:100%;background:#fff;border-radius:16px;padding:32px 24px;text-align:center;box-shadow:0 10px 40px rgba(18,52,75,.12)}
    h1{color:#12344b;font-size:22px;margin-bottom:10px}
    p{color:#5a7181;font-size:15px;line-height:1.5}
  </style>
</head>
<body>
  <div class="card">
    <h1>${escapeHtml(title)}</h1>
    <p>${escapeHtml(message)}</p>
  </div>
</body>
</html>`;
}

function passPage(data: {
  guestName: string;
  hotelName: string;
  hotelLocation: string;
  hotelEmail: string;
  hotelPhone: string;
  reservationNumber: string;
  checkInDate: string;
  checkOutDate: string;
  roomNumber: string;
  roomTypeName: string;
  numberOfGuests: number;
  submittedGuests: number | null;
  nationality: string;
  totalAmount: number;
  verifiedAt: string;
}): string {
  const guestsText =
    `${data.numberOfGuests} booked · ${data.submittedGuests ?? data.numberOfGuests} submitted`;
  const initials = guestInitials(data.guestName);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Check-in Pass - ${escapeHtml(data.reservationNumber)}</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;background:linear-gradient(165deg,#dce8f2 0%,#c5d9e8 45%,#b8cedd 100%);min-height:100vh;padding:20px 16px 28px;color:#12344b;-webkit-font-smoothing:antialiased}
    .wrap{max-width:420px;margin:0 auto}
    .hero{background:linear-gradient(145deg,#0f2d42 0%,#1a4a6e 55%,#1e5a85 100%);color:#fff;border-radius:24px 24px 0 0;padding:28px 22px 24px;text-align:center;box-shadow:0 8px 32px rgba(15,45,66,.25)}
    .hero-hotel{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:rgba(255,255,255,.72);margin-bottom:8px}
    .hero h1{font-size:26px;font-weight:800;line-height:1.2;letter-spacing:-.02em;margin-bottom:14px}
    .badge{display:inline-flex;align-items:center;gap:6px;background:rgba(16,185,129,.95);color:#fff;font-size:13px;font-weight:700;padding:8px 16px;border-radius:999px;box-shadow:0 4px 14px rgba(16,185,129,.35)}
    .shell{background:#fff;border-radius:0 0 24px 24px;box-shadow:0 20px 50px rgba(18,52,75,.14);overflow:hidden;margin-bottom:18px}
    .guest{display:flex;align-items:center;gap:16px;padding:22px 20px;border-bottom:1px solid #eef3f7}
    .avatar{width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#1a4a6e,#2d7ab8);color:#fff;font-size:20px;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0;box-shadow:0 4px 12px rgba(26,74,110,.3)}
    .guest-name{font-size:22px;font-weight:800;line-height:1.2;color:#12344b;margin-bottom:6px}
    .ref-pill{display:inline-block;font-size:12px;font-weight:700;color:#1a4a6e;background:#e8f4fc;padding:6px 12px;border-radius:8px;letter-spacing:.02em}
    .verified{margin:0 20px 8px;background:linear-gradient(90deg,#ecfdf5,#d1fae5);border:1px solid #a7f3d0;border-radius:14px;padding:12px 16px;font-size:13px;font-weight:600;color:#047857;text-align:center}
    .dates{display:flex;gap:10px;padding:0 20px 20px}
    .date-card{flex:1;background:#f4f8fb;border:1px solid #e2ecf4;border-radius:14px;padding:14px 12px;text-align:center}
    .date-card .lbl{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#8ea0ae;margin-bottom:6px}
    .date-card .val{font-size:15px;font-weight:800;color:#12344b;line-height:1.3}
    .section{padding:8px 20px 20px}
    .section-title{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:#8ea0ae;margin-bottom:12px;padding-left:2px}
    .row{display:flex;align-items:flex-start;gap:14px;padding:14px 0;border-bottom:1px solid #f0f4f8}
    .row:last-child{border-bottom:none;padding-bottom:0}
    .row-icon{width:40px;height:40px;border-radius:12px;background:#f0f6fb;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
    .row-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#8ea0ae;margin-bottom:4px}
    .row-value{font-size:15px;font-weight:700;color:#12344b;line-height:1.4}
    .amount{background:linear-gradient(135deg,#0f2d42,#1a4a6e);color:#fff;padding:20px 22px;display:flex;justify-content:space-between;align-items:center}
    .amount .lbl{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:rgba(255,255,255,.65)}
    .amount .val{font-size:24px;font-weight:800;letter-spacing:-.02em}
    .footer{text-align:center;font-size:12px;color:#7a92a3;line-height:1.7;margin-top:4px}
    .footer a{color:#1a4a6e;font-weight:700;text-decoration:none}
    .footer .brand{margin-top:12px;font-size:11px;color:#a8bcc9;font-weight:600}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="hero-hotel">${escapeHtml(data.hotelName)}</div>
      <h1>Digital Check-in Pass</h1>
      <span class="badge">&#10003; Approved</span>
    </div>

    <div class="shell">
      <div class="guest">
        <div class="avatar">${escapeHtml(initials)}</div>
        <div>
          <div class="guest-name">${escapeHtml(data.guestName)}</div>
          <span class="ref-pill">${escapeHtml(data.reservationNumber)}</span>
        </div>
      </div>

      <div class="verified">Approved ${escapeHtml(data.verifiedAt)}</div>

      <div class="dates">
        <div class="date-card">
          <div class="lbl">Check-in</div>
          <div class="val">${escapeHtml(data.checkInDate)}</div>
        </div>
        <div class="date-card">
          <div class="lbl">Check-out</div>
          <div class="val">${escapeHtml(data.checkOutDate)}</div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Stay details</div>
        ${detailRow("&#127976;", "Hotel", data.hotelName)}
        ${detailRow("&#128205;", "Location", data.hotelLocation)}
        ${detailRow("&#128719;", "Room", `${data.roomNumber} · ${data.roomTypeName}`)}
        ${detailRow("&#128101;", "Guests", guestsText)}
        ${detailRow("&#127758;", "Nationality", data.nationality)}
      </div>

      <div class="amount">
        <span class="lbl">Total amount</span>
        <span class="val">${Number(data.totalAmount).toFixed(2)} JOD</span>
      </div>
    </div>

    <div class="footer">
      ${data.hotelPhone ? `<p><a href="tel:${escapeHtml(data.hotelPhone)}">${escapeHtml(data.hotelPhone)}</a></p>` : ""}
      ${data.hotelEmail ? `<p><a href="mailto:${escapeHtml(data.hotelEmail)}">Contact hotel</a></p>` : ""}
      <p class="brand">Powered by E-Smart Check-In</p>
    </div>
  </div>
</body>
</html>`;
}
