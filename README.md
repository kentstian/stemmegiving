# QR Code Voting App

A simple Flask web application for informal voting using QR codes and SQLite.

## Features
- Administrator registration and login
- Create polls with title, description and status
- Add candidates manually or via CSV/Excel upload
- Activate and close polls
- Generate and download QR codes for voting
- Live results and vote export to CSV
- Duplicate-vote prevention using browser cookie, IP hash, and user-agent hash

## Requirements
- Python 3.8+
- Windows / VS Code

## Setup
1. Open VS Code and open the `voting_app` folder.
2. Create and activate a virtual environment in the terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Install dependencies:

```powershell
pip install -r requirements.txt
```

4. Run the application:

```powershell
python app.py
```

5. Open the browser:

```
http://localhost:5000
```

## Usage
- Register a new administrator account
- Create a new poll and add candidates
- Activate the poll, then open the QR page and scan it with a phone
- Voters can choose one candidate and submit one vote per poll
- Export results from the admin dashboard

## Notes
- This application is intended for informal voting only.
- For serious or high-stakes voting, add stronger verification such as email confirmation, SMS codes, or user accounts.
