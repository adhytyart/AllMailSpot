import imaplib
import email
from email.header import decode_header
from datetime import datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, template_folder="../templates")

def decode_header_value(header_val):
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    result = []
    for text, encoding in decoded_parts:
        if isinstance(text, bytes):
            result.append(text.decode(encoding or "utf-8", errors="ignore"))
        else:
            result.append(str(text))
    return "".join(result)

def extract_email_body(msg):
    html_body = ""
    text_body = ""
    
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            if "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        decoded_content = payload.decode(charset, errors="ignore")
                        if content_type == "text/html":
                            html_body += decoded_content
                        elif content_type == "text/plain":
                            text_body += decoded_content
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded_content = payload.decode(charset, errors="ignore")
                if msg.get_content_type() == "text/html":
                    html_body = decoded_content
                else:
                    text_body = decoded_content
        except Exception:
            pass
            
    return html_body if html_body else f"<pre>{text_body}</pre>"

def detect_sending_source(msg, from_email):
    headers_str = "".join([str(v) for k, v in msg.items()]).lower()
    received_str = " ".join(msg.get_all("Received", [])).lower()
    combined = headers_str + " " + received_str + " " + from_email.lower()
    
    if "klaviyo" in combined or "uclm" in combined:
        return "Klaviyo"
    elif "mailchimp" in combined or "mcsv.net" in combined:
        return "Mailchimp"
    elif "sendgrid" in combined or "em." in combined:
        return "SendGrid"
    elif "amazonses" in combined or "amazon" in combined:
        return "Amazon SES"
    elif "postmark" in combined:
        return "Postmark"
    elif "google" in combined or "gmail" in combined:
        return "Google / Gmail"
    elif "smtp" in combined:
        return "SMTP"
    else:
        return "Other / Direct"

@app.route("/")
def home():
    return render_template("index.html")

# Single session endpoint to fetch message IDs and chunk of messages in one connection
@app.route("/api/fetch-chunk", methods=["POST"])
def fetch_chunk_api():
    data = request.json or {}
    accounts = data.get("accounts", [])
    target_date_str = data.get("date")
    offset = int(data.get("offset", 0))
    limit = int(data.get("limit", 7)) # Chunk size strictly max 7
    
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date() if target_date_str else datetime.now().date()
    except ValueError:
        target_date = datetime.now().date()

    formatted_date_str = target_date.strftime("%d-%b-%Y")
    all_fetched_emails = []

    for acc in accounts:
        password = acc.get("app_password", "").strip()
        spot_id = acc.get("spot_id", "").strip()
        if not password or not spot_id:
            continue
            
        try:
            # Single session login
            mail = imaplib.IMAP4_SSL("imap.mail.yahoo.com", 993)
            mail.login(spot_id, password)
            mail.select("INBOX", readonly=True)

            status, response = mail.search(None, f'ON "{formatted_date_str}"')
            if status != "OK" or not response[0]:
                mail.logout()
                continue

            msg_ids = [m.decode() for m in response[0].split()]
            reversed_ids = list(reversed(msg_ids)) # Newest first
            
            # Slice chunk of max 7
            chunk_ids = reversed_ids[offset : offset + limit]
            
            for msg_id in chunk_ids:
                try:
                    res, msg_data = mail.fetch(str(msg_id), "(RFC822)")
                    if res != "OK":
                        continue
                        
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    subject = decode_header_value(msg.get("Subject", "(No Subject)"))
                    from_str = decode_header_value(msg.get("From", "Unknown"))
                    
                    real_name, email_addr = email.utils.parseaddr(from_str)
                    from_name = real_name if real_name else email_addr
                    from_email = email_addr if email_addr else from_str
                    date_sent = msg.get("Date", "")
                    
                    source_type = detect_sending_source(msg, from_email)
                    body_content = extract_email_body(msg)
                    
                    all_fetched_emails.append({
                        "id": f"{spot_id}_{msg_id}",
                        "account": spot_id,
                        "from_name": from_name,
                        "from_email": from_email,
                        "subject": subject,
                        "date": date_sent,
                        "source": source_type,
                        "content": body_content
                    })
                except Exception as inner_err:
                    print(f"Skipping msg {msg_id}: {str(inner_err)}")
                    continue
                
            mail.logout()
        except Exception as e:
            print(f"Session error for {spot_id}: {str(e)}")

    return jsonify({
        "status": "success",
        "emails": all_fetched_emails,
        "has_more": len(all_fetched_emails) > 0
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True, port=5000)