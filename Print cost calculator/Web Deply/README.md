Print Cost Calculator (Flask)

Quick start

1. Create and activate a virtual environment (Windows PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run locally:

```powershell
python app.py
```

3. Open http://127.0.0.1:5000/ in your browser.

Notes
- `app.py` contains a simple `calculate_cost` stub — replace with your real pricing rules.
- Orders are appended to `orders.json` as JSON lines.
- For production, run with Gunicorn/WSGI and configure appropriate security, validation and persistence.
