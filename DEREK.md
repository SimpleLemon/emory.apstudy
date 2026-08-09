Tailwind import guard:
```python scripts/check_tailwind_imports.py```

Local Testing:
source .venv/bin/activate
flask --app app:create_app run --host 127.0.0.1 --port 8000

Local End Flask Processes:
pgrep -af flask || echo 'No flask processes found'; pkill -f flask || true; pgrep -af flask || echo 'No flask processes remain'
