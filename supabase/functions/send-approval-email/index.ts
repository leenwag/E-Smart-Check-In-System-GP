import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import QRCode from "https://esm.sh/qrcode@1.5.3";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

interface ApprovalEmailRequest {
  submission_id?: number | string;
  submission_ids?: Array<number | string>;
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const supabaseClient = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
    );

    const body: ApprovalEmailRequest = await req.json();
    const ids = [
      ...(body.submission_id != null ? [body.submission_id] : []),
      ...(body.submission_ids ?? []),
    ].map((id) => String(id));

    if (ids.length === 0) {
      return new Response(
        JSON.stringify({ error: "submission_id or submission_ids is required" }),
        {
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          status: 400,
        },
      );
    }

    const results = [];

    for (const submissionId of ids) {
      try {
        const result = await sendApprovalEmailForSubmission(
          supabaseClient,
          submissionId,
        );
        results.push({ submissionId, status: "sent", ...result });
      } catch (error) {
        console.error(
          `[send-approval-email] Failed for submission ${submissionId}:`,
          error,
        );
        results.push({
          submissionId,
          status: "failed",
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }

    const failed = results.filter((item) => item.status === "failed");

    return new Response(
      JSON.stringify({
        message: "Processed approval emails",
        count: results.length,
        results,
      }),
      {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: failed.length === results.length ? 500 : 200,
      },
    );
  } catch (error) {
    return new Response(
      JSON.stringify({
        error: error instanceof Error ? error.message : String(error),
      }),
      {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
        status: 500,
      },
    );
  }
});

async function sendApprovalEmailForSubmission(
  supabaseClient: ReturnType<typeof createClient>,
  submissionId: string,
) {
  const { data: submission, error: submissionError } = await supabaseClient
    .from("pre_checkin_submissions")
    .select("*")
    .eq("id", submissionId)
    .single();

  if (submissionError || !submission) {
    throw new Error(`Submission ${submissionId} not found`);
  }

  if (submission.verification_status !== "approved") {
    throw new Error(
      `Submission ${submissionId} is not approved (status: ${submission.verification_status})`,
    );
  }

  const { data: booking, error: bookingError } = await supabaseClient
    .from("bookings")
    .select("*")
    .eq("id", submission.booking_id)
    .single();

  if (bookingError || !booking) {
    throw new Error(`Booking ${submission.booking_id} not found`);
  }

  const { data: hotel, error: hotelError } = await supabaseClient
    .from("hotels")
    .select("*")
    .eq("id", submission.hotel_id)
    .single();

  if (hotelError || !hotel) {
    throw new Error(`Hotel ${submission.hotel_id} not found`);
  }

  let roomNumber = "Assigned at check-in";
  let roomTypeName = "Standard";

  if (booking.room_id) {
    const { data: room } = await supabaseClient
      .from("rooms")
      .select("room_number, room_type_id")
      .eq("id", booking.room_id)
      .maybeSingle();

    if (room?.room_number) {
      roomNumber = room.room_number;
    }

    if (room?.room_type_id) {
      const { data: roomType } = await supabaseClient
        .from("room_types")
        .select("name")
        .eq("id", room.room_type_id)
        .maybeSingle();

      if (roomType?.name) {
        roomTypeName = roomType.name;
      }
    }
  }

  const guestName = submission.guest_full_name ||
    `${booking.guest_first_name || ""} ${booking.guest_last_name || ""}`.trim() ||
    "Guest";
  const guestEmail = submission.guest_email || booking.guest_email;
  const reservationNumber = booking.booking_reference ||
    `BK${String(booking.id).padStart(6, "0")}`;

  if (!guestEmail || !guestEmail.includes("@")) {
    throw new Error(`Invalid guest email for submission ${submissionId}`);
  }

  const hotelLocation = [hotel.address, hotel.city, hotel.country]
    .filter(Boolean)
    .join(", ");

  const qrPayload = buildCheckinPassQrUrl(submission.id, reservationNumber);

  const qrBase64 = await generateQrCodeBase64(qrPayload);
  const qrImageUrl = await uploadQrCodeImage(
    supabaseClient,
    submissionId,
    qrBase64,
  );

  const emailHtml = generateApprovalEmail({
    guestName,
    hotelName: hotel.name,
    hotelLocation,
    hotelEmail: hotel.email || "",
    reservationNumber,
    checkInDate: formatDate(booking.check_in_date),
    checkOutDate: formatDate(booking.check_out_date),
    roomNumber,
    roomTypeName,
    numberOfGuests: booking.number_of_guests,
    submittedGuests: submission.submitted_guests_count,
    totalAmount: booking.total_amount,
    nationality: submission.nationality || "Not provided",
    qrImageUrl,
  });

  const emailSent = await sendEmail({
    to: guestEmail,
    subject: `Check-in Approved - ${reservationNumber}`,
    html: emailHtml,
  });

  if (!emailSent) {
    throw new Error("Email provider not configured or send failed");
  }

  return {
    guestEmail,
    reservationNumber,
  };
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
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

/**
 * Public Supabase pass URL — works for any guest scanning the QR (no localhost).
 * Requires `checkin-pass` edge function deployed with JWT verification off.
 */
function buildCheckinPassQrUrl(
  submissionId: number | string,
  reservationNumber: string,
): string {
  const sid = encodeURIComponent(String(submissionId));
  const ref = encodeURIComponent(reservationNumber);
  const supabaseUrl = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/$/, "");

  if (!supabaseUrl.startsWith("https://")) {
    throw new Error("SUPABASE_URL must be set to your public Supabase project URL");
  }

  return `${supabaseUrl}/functions/v1/checkin-pass?sid=${sid}&ref=${ref}`;
}

async function generateQrCodeBase64(text: string): Promise<string> {
  const dataUrl = await QRCode.toDataURL(text, {
    width: 320,
    margin: 4,
    errorCorrectionLevel: "M",
  });

  const base64 = dataUrl.split(",")[1];
  if (!base64) {
    throw new Error("Failed to generate QR code image");
  }

  return base64;
}

async function uploadQrCodeImage(
  supabaseClient: ReturnType<typeof createClient>,
  submissionId: string,
  base64: string,
): Promise<string> {
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  const path = `qr-codes/${submissionId}.png`;

  const { error: uploadError } = await supabaseClient.storage
    .from("checkin-identity-docs")
    .upload(path, bytes, {
      contentType: "image/png",
      upsert: true,
    });

  if (uploadError) {
    throw new Error(`Failed to upload QR code: ${uploadError.message}`);
  }

  const { data } = supabaseClient.storage
    .from("checkin-identity-docs")
    .getPublicUrl(path);

  return `${data.publicUrl}?v=${Date.now()}`;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function summaryRow(label: string, value: string): string {
  return `<tr>
    <td style="padding:10px 12px;background:#e8f0f6;color:#5a7181;font-size:11px;font-weight:bold;text-transform:uppercase;width:42%">${label}</td>
    <td style="padding:10px 12px;background:#ffffff;color:#12344b;font-size:14px;font-weight:bold">${value}</td>
  </tr>`;
}

function generateApprovalEmail({
  guestName,
  hotelName,
  hotelLocation,
  hotelEmail,
  reservationNumber,
  checkInDate,
  checkOutDate,
  roomNumber,
  roomTypeName,
  numberOfGuests,
  submittedGuests,
  totalAmount,
  nationality,
  qrImageUrl,
}: {
  guestName: string;
  hotelName: string;
  hotelLocation: string;
  hotelEmail: string;
  reservationNumber: string;
  checkInDate: string;
  checkOutDate: string;
  roomNumber: string;
  roomTypeName: string;
  numberOfGuests: number;
  submittedGuests: number | null;
  totalAmount: number;
  nationality: string;
  qrImageUrl: string;
}): string {
  const supportEmail = escapeHtml(hotelEmail || "support@esmart.com");
  const guestsText = `${numberOfGuests} booked / ${submittedGuests ?? numberOfGuests} submitted`;

  return `<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#e8eef3;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#e8eef3;padding:16px 8px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;">

<tr><td style="background:#12344b;padding:28px 20px;text-align:center;">
  <div style="color:#d3e5f1;font-size:14px;margin-bottom:6px;">${escapeHtml(hotelName)}</div>
  <div style="color:#ffffff;font-size:26px;font-weight:bold;">Check-in Approved</div>
  <div style="color:#b8cedd;font-size:14px;margin-top:8px;">Your pre-arrival check-in has been accepted</div>
</td></tr>

<tr><td style="padding:22px 20px 8px 20px;">
  <p style="margin:0 0 8px 0;color:#12344b;font-size:17px;font-weight:bold;">Dear ${escapeHtml(guestName)},</p>
  <p style="margin:0;color:#5a7181;font-size:14px;line-height:1.5;">Present the QR code below at ${escapeHtml(hotelName)} front desk on arrival.</p>
</td></tr>

<tr><td style="padding:8px 20px 20px 20px;">
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #d6e4ee;border-radius:10px;overflow:hidden;">
    <tr><td colspan="2" style="background:#12344b;color:#ffffff;padding:12px 14px;font-size:15px;font-weight:bold;">Reservation Summary</td></tr>
    ${summaryRow("Hotel", escapeHtml(hotelName))}
    ${summaryRow("Location", escapeHtml(hotelLocation))}
    ${summaryRow("Reservation #", escapeHtml(reservationNumber))}
    ${summaryRow("Check-in", escapeHtml(checkInDate))}
    ${summaryRow("Check-out", escapeHtml(checkOutDate))}
    ${summaryRow("Room", escapeHtml(`${roomNumber} (${roomTypeName})`))}
    ${summaryRow("Guests", escapeHtml(guestsText))}
    ${summaryRow("Nationality", escapeHtml(nationality))}
    <tr><td colspan="2" style="background:#12344b;color:#ffffff;padding:14px;text-align:right;">
      <span style="font-size:12px;color:#b8cedd;display:block;margin-bottom:4px;">TOTAL AMOUNT</span>
      <span style="font-size:24px;font-weight:bold;">${Number(totalAmount).toFixed(2)} JOD</span>
    </td></tr>
    <tr><td colspan="2" align="center" style="padding:18px 14px;background:#f4f8fb;border-top:1px solid #d6e4ee;">
      <div style="color:#12344b;font-size:15px;font-weight:bold;margin-bottom:6px;">Your Check-in QR Code</div>
      <div style="color:#5a7181;font-size:13px;margin-bottom:14px;">Show this at the front desk for a faster arrival experience.</div>
      <img src="${qrImageUrl}" alt="Check-in QR Code" width="200" height="200" style="display:block;margin:0 auto;border:4px solid #12344b;border-radius:8px;background:#ffffff;" />
      <div style="color:#8ea0ae;font-size:11px;margin-top:10px;">If the QR code is not visible, tap &quot;Display images&quot; in your email app.</div>
    </td></tr>
  </table>
</td></tr>

<tr><td style="background:#f4f8fb;padding:16px 20px;text-align:center;border-top:1px solid #d6e4ee;">
  <p style="margin:0 0 6px 0;color:#5a7181;font-size:13px;">Need help? <a href="mailto:${supportEmail}" style="color:#12344b;font-weight:bold;text-decoration:none;">Contact the hotel</a></p>
  <p style="margin:0;color:#8ea0ae;font-size:11px;">&copy; ${new Date().getFullYear()} ${escapeHtml(hotelName)}</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>`;
}

async function sendEmail({
  to,
  subject,
  html,
}: {
  to: string;
  subject: string;
  html: string;
}): Promise<boolean> {
  const SENDGRID_API_KEY = Deno.env.get("SENDGRID_API_KEY");
  const SENDGRID_FROM_EMAIL = Deno.env.get("SENDGRID_FROM_EMAIL");
  const SENDGRID_FROM_NAME = Deno.env.get("SENDGRID_FROM_NAME") || "E-Smart Check-In";

  if (!to || !to.includes("@")) {
    throw new Error(`Invalid email address: ${to}`);
  }

  if (!SENDGRID_API_KEY || !SENDGRID_FROM_EMAIL) {
    console.warn("SendGrid is not configured. Email was not sent.");
    console.log(`[MOCK] Would send approval email to: ${to}`);
    console.log(`Subject: ${subject}`);
    return false;
  }

  const response = await fetch("https://api.sendgrid.com/v3/mail/send", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${SENDGRID_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: {
        name: SENDGRID_FROM_NAME,
        email: SENDGRID_FROM_EMAIL,
      },
      personalizations: [{ to: [{ email: to }] }],
      subject,
      content: [{ type: "text/html", value: html }],
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`SendGrid API error: ${response.status} - ${errorText}`);
  }

  return true;
}
