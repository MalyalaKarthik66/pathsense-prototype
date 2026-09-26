# Gunicorn settings for the Render deployment (gunicorn reads ./gunicorn.conf.py automatically: `gunicorn app:app`).
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
# One process: upload jobs and the live session live in that process's memory (app.JOBS). Threads serve the page,
# video range requests and job polling concurrently while a job runs.
workers = 1
worker_class = "gthread"
threads = 8
timeout = 300            # large uploads on slow links
graceful_timeout = 30
accesslog = "-"
