import os
import base64
import requests
import time
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from io import BytesIO
from PIL import Image
from datetime import datetime
import email.utils

# Define the scope for Gmail API access (read-only)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Headers to mimic a browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
}

# ANSI color codes with bold and underline
class Colors:
    RESET = "\033[0m"
    RED = "\033[31m"                  # Errors
    GREEN = "\033[32m"                # Successful downloads
    YELLOW = "\033[33m"               # Warnings/skips (small/invalid images)
    BLUE = "\033[34m"                 # Message processing
    BOLD_UNDERLINE_MAGENTA = "\033[1;4;95m"  # Bright magenta, bold, underlined for subjects
    BOLD_UNDERLINE_CYAN = "\033[1;4;96m"     # Bright cyan, bold, underlined for dates
    BOLD_WHITE = "\033[1;37m"         # Bold white for "not a briefing" skip

# Function to sanitize folder names and process date into "Briefing for {date}" format
def sanitize_folder_name(subject, raw_date):
    # Process the raw date into a clean format
    parsed_date = email.utils.parsedate_to_datetime(raw_date) if raw_date else None
    if parsed_date:
        # Format date as "Month Day, Year" (e.g., "February 25, 2025")
        date_str = parsed_date.strftime("%B %d, %Y")
    else:
        date_str = "Unknown Date"
    
    # Return folder name in the format "Briefing for {date}"
    return f"Briefing for {date_str}"

# Function to authenticate and create the Gmail API service
def authenticate_gmail():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

# Function to get emails from a specific sender
def get_nyt_emails(service, user_id, sender_email):
    query = f"from:{sender_email}"
    results = service.users().messages().list(userId=user_id, q=query).execute()
    return results.get("messages", [])

# Function to get email metadata
def get_metadata(service, user_id, message_id):
    msg = service.users().messages().get(userId=user_id, id=message_id).execute()
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    subject = None
    date = None
    for header in headers:
        if header["name"].lower() == "subject":
            subject = header["value"]
        if header["name"].lower() == "date":
            date = header["value"]
    return (subject, date)

# Function to extract images from emails and download them
def extract_and_download_images(service, user_id, messages):
    for message in messages:
        msg = service.users().messages().get(userId=user_id, id=message["id"]).execute()
        payload = msg.get("payload", {})
        parts = payload.get("parts", [])

        email_data = get_metadata(service, user_id, message["id"])
        email_subject = email_data[0] or "No Subject"
        email_date = email_data[1] or "No Date"

        if "Briefing" in email_subject:

            print(f"{Colors.BLUE}\nEmail ID: {message['id']}{Colors.RESET}")
            print(f"{Colors.BOLD_UNDERLINE_MAGENTA}\n========== SUBJECT: {email_subject} =========={Colors.RESET}")
            print(f"{Colors.BOLD_UNDERLINE_CYAN}\n========== DATE: {email_date} =========={Colors.RESET}")
            # Create a folder with sanitized subject and processed date
            folder_name = sanitize_folder_name(email_subject, email_date)
            os.makedirs(folder_name, exist_ok=True)  # No print statement here

            if len(parts) == 0:
                data = payload['body'].get('data')
                if data:
                    html_content = base64.urlsafe_b64decode(data).decode("utf-8")
                    with open("email.html", "a", encoding='utf-8') as f:
                        soup = BeautifulSoup(html_content, 'html.parser')
                        images = soup.find_all('img')

                        for i, image in enumerate(images):
                            image_url = image['src']
                            print(f"\nChecking image {i}: {image_url}")

                            if is_image(image_url):
                                dimensions = get_dimensions(image_url)
                                if dimensions:
                                    width, height = dimensions
                                    if width > 500 and height > 500:
                                        filename = os.path.join(folder_name, f"image_{i}.jpg")
                                        download_image(image_url, filename)
                                        print(f"{Colors.GREEN}\nDownloaded image_{i}.jpg with dimensions: {width}x{height}{Colors.RESET}")
                                    else:
                                        print(f"{Colors.YELLOW}\nSkipping image {i}: Too small ({width}x{height}){Colors.RESET}")
                                else:
                                    print(f"{Colors.YELLOW}\nSkipping image {i}: Invalid or no dimensions (Caught you you sneaky fuck){Colors.RESET}")
                            else:
                                print(f"{Colors.RED}\nCaught you, sneaky non-image no. {i}: {image_url}{Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}\nNo data found in the email body.{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}\nMultipart email detected - skipping for now (TODO: Handle parts){Colors.RESET}")
        else:
            print(f"{Colors.BOLD_WHITE}\nEmail with subject: ({email_subject}) is not a daily briefing, hence it will be skipped{Colors.RESET}")

# Function to check if a URL points to a real image
def is_image(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        return 'image' in content_type
    except requests.exceptions.RequestException as e:
        print(f"{Colors.RED}\nError checking {url}: Cannot confirm that it is an image: {e}{Colors.RESET}")
        return False

# Function to get image dimensions and validate content
def get_dimensions(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'image' in content_type:
            image = Image.open(BytesIO(response.content))
            return image.size
        else:
            print(f"{Colors.YELLOW}\nSkipping non-image URL: {url}, It is a (Content-Type: {content_type}){Colors.RESET}")
            return None
    except (requests.exceptions.RequestException, Image.UnidentifiedImageError) as e:
        print(f"{Colors.RED}\nError validating {url}: {e}{Colors.RESET}")
        return None

# Function to download an image with retry logic
def download_image(url, filename):
    retries = 3
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=1)
            if response.status_code == 200:
                with open(filename, "wb") as f:
                    f.write(response.content)
                return
            else:
                print(f"{Colors.RED}\nAttempt {attempt + 1} failed: Status code {response.status_code}{Colors.RESET}")
        except requests.exceptions.RequestException as e:
            print(f"{Colors.RED}\nAttempt {attempt + 1} failed: {e}{Colors.RESET}")
            time.sleep(2)
    print(f"{Colors.RED}\nFailed to download {url} after {retries} attempts, Skipping image{Colors.RESET}")

if __name__ == "__main__":
    try:
        service = authenticate_gmail()
        messages = get_nyt_emails(service, "me", "nytdirect@nytimes.com")
        extract_and_download_images(service, "me", messages)
    except HttpError as error:
        print(f"{Colors.RED}\nAn error occurred: {error}{Colors.RESET}")