FROM python:3.9-slim-buster

WORKDIR /app
RUN chmod a+rwx /app

# RUN apt-get update && apt-get install libopencv-dev ffmpeg -y
# RUN apt-get update && apt-get install ffmpeg -y
# Switch to archived Debian repos for Buster (EOL)
RUN sed -i 's|deb.debian.org|archive.debian.org|g' /etc/apt/sources.list && \
    sed -i 's|security.debian.org|archive.debian.org|g' /etc/apt/sources.list && \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99no-check-valid && \
    apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# RUN apt-get update

# Install pip packages
RUN python -m pip install --upgrade pip wheel

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt --no-cache-dir

RUN pip install --no-cache-dir sqlalchemy==2.0.43 cryptography==46.0.3

COPY . .

# set environment variables
CMD ["gunicorn", "main:app", "--config", "gunicorn.conf.py"]

