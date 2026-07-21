FROM python:3.11-slim-buster

WORKDIR /app
RUN chmod a+rwx /app

# Install pip packages
RUN python -m pip install --upgrade pip wheel

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt --no-cache-dir

COPY . .

# run app
CMD ["gunicorn", "main:app", "--config", "gunicorn.conf.py"]

