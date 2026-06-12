import os
import io
import json
import glob
from dotenv import load_dotenv
import pandas as pd
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload

# Load environment variables from .env file
load_dotenv()

# =============================================================
# 1. GOOGLE DRIVE API SETUP (Adaptive)
# =============================================================

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


def find_service_account_file():
    """Find Google service account JSON file or env var."""
    # 1. Check for full JSON in env var (Vercel-compatible)
    sa_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    if sa_json:
        try:
            data = json.loads(sa_json)
            if data.get('type') == 'service_account':
                import tempfile
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
                json.dump(data, tmp)
                tmp.close()
                return tmp.name
        except (json.JSONDecodeError, Exception):
            pass

    # 2. Check env var for file path
    env_file = os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE', '')
    if env_file and os.path.exists(env_file):
        return env_file

    # 3. Look for any .json file that looks like a service account key
    patterns = [
        'service-account*.json',
        '*credentials*.json',
        '*.googleapis.com*.json',
        '*-*.json',
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        for m in matches:
            try:
                with open(m, 'r') as f:
                    data = json.load(f)
                if data.get('type') == 'service_account':
                    return m
            except (json.JSONDecodeError, IOError):
                continue

    return None


def authenticate_drive():
    """Authenticates and returns the Google Drive service object."""
    # 1. Try to load from JSON string in environment (for Vercel)
    sa_json_str = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if sa_json_str:
        try:
            info = json.loads(sa_json_str)
            print("[INFO] Using service account from GOOGLE_SERVICE_ACCOUNT_JSON environment variable.")
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            return build('drive', 'v3', credentials=creds, cache_discovery=False)
        except Exception as e:
            print(f"[ERROR] Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

    # 2. Fallback to file path
    sa_file = find_service_account_file()
    if not sa_file:
        print("[ERROR] No Google service account JSON file found.")
        print("        Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE in .env or Vercel Environment Variables.")
        raise FileNotFoundError("Service account JSON not found")

    print(f"[INFO] Using service account: {sa_file}")
    creds = service_account.Credentials.from_service_account_file(
        sa_file, scopes=SCOPES)
    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    return service


def download_file_from_drive(service, file_id, output_path):
    """Downloads a file from Google Drive by its ID."""
    print(f"Downloading file ID: {file_id} from Google Drive...")
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(output_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
        print(f"Download {int(status.progress() * 100)}%.")
    return output_path


# =============================================================
# 2. DATA EXTRACTION & HEADER CLEANING
# =============================================================

def extract_all_excel_data(file_path):
    """
    Extracts all data from every sheet and dynamically fixes 'Unnamed' headers.
    """
    print(f"\n[{os.path.basename(file_path)}] Reading file...")
    try:
        all_sheets = pd.read_excel(file_path, sheet_name=None, engine='openpyxl')
        extracted_data = {}

        for sheet_name, df in all_sheets.items():
            # Drop entirely empty rows and columns
            df = df.dropna(how='all', axis=0).dropna(how='all', axis=1)

            # Count how many columns pandas defaulted to "Unnamed"
            unnamed_count = sum(1 for col in df.columns if str(col).startswith('Unnamed'))

            # If pandas created Unnamed columns, the real header is likely trapped in row 0
            if unnamed_count > 0 and not df.empty:
                new_header = df.iloc[0]
                df.columns = [str(val).strip() if pd.notna(val) else f"Empty_{i}" for i, val in enumerate(new_header)]
                df = df[1:].reset_index(drop=True)

            extracted_data[sheet_name] = df
            print(f"  -> Tab '{sheet_name}': Found {len(df)} rows.")

        return extracted_data
    except Exception as e:
        print(f"  -> An error occurred with this file: {e}")
        return None


# =============================================================
# 3. ADAPTIVE MAIN EXECUTION
# =============================================================

def run_extraction(
    drive_files=None,
    output_json_file=None,
    download_folder=None
):
    """
    Downloads Excel files from Google Drive, extracts all sheet data,
    and saves as a master JSON file.

    All parameters can be set via environment variables:
    - DRIVE_FILE_NAME, DRIVE_FILE_ID
    - OUTPUT_JSON_FILE
    - DOWNLOAD_FOLDER
    """
    # Adaptive defaults: use env vars or sensible defaults
    drive_files = drive_files or {}
    if not drive_files:
        name = os.environ.get('DRIVE_FILE_NAME', '')
        fid = os.environ.get('DRIVE_FILE_ID', '')
        if name and fid:
            drive_files = {name: fid}
        else:
            # Fallback: no files configured
            print("[ERROR] No drive files configured.")
            print("        Set DRIVE_FILE_NAME and DRIVE_FILE_ID in .env")
            print("        Or pass drive_files dict to run_extraction()")
            raise ValueError("No drive files configured")

    output_json_file = output_json_file or os.environ.get('OUTPUT_JSON_FILE', 'all_files_extracted_data.json')
    download_folder = download_folder or os.environ.get('DOWNLOAD_FOLDER', 'data')

    os.makedirs(download_folder, exist_ok=True)

    try:
        drive_service = authenticate_drive()
        master_json_data = {}

        for file_name, file_id in drive_files.items():
            local_file_path = os.path.join(download_folder, file_name)
            download_file_from_drive(drive_service, file_id, local_file_path)
            file_data = extract_all_excel_data(local_file_path)

            if file_data:
                json_ready_file_data = {}
                for sheet_name, df in file_data.items():
                    json_ready_file_data[sheet_name] = df.to_dict(orient='records')
                master_json_data[file_name] = json_ready_file_data

        print("\n--- Saving Master JSON ---")
        with open(output_json_file, 'w', encoding='utf-8') as json_file:
            json.dump(master_json_data, json_file, indent=4, default=str)
        print(f"[SUCCESS] Extracted data saved to: {output_json_file}")
        return output_json_file

    except Exception as e:
        print(f"[ERROR] Extraction failed: {e}")
        raise


if __name__ == "__main__":
    # Support both old hardcoded style and .env-driven style
    drive_files = {}
    env_name = os.environ.get('DRIVE_FILE_NAME', '')
    env_id = os.environ.get('DRIVE_FILE_ID', '')
    if env_name and env_id:
        drive_files = {env_name: env_id}
    else:
        # Last resort fallback (for backwards compat with your notebook)
        print("[WARNING] Using fallback drive file mapping. Add DRIVE_FILE_NAME/ID to .env for full adaptability.")
        drive_files = {
            "Wohlig Active Employee Data.xlsx": "1EAGD1LreF9KF3kSyqsOTSinmo9iSEDWn",
        }

    run_extraction(drive_files)
