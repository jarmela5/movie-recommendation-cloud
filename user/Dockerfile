FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY users.py user.sql ./

# database lives on a volume so it survives the container
ENV USER_DB=/app/data/users.db
EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--threads", "8", "users:app"]
