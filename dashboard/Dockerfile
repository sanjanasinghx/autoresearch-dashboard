FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy dashboard code
COPY . .

EXPOSE 5050

# Repo is mounted as /repo at runtime
ENV AUTORESEARCH_REPO=/repo

CMD ["python", "app.py", "--repo", "/repo", "--port", "5050"]
