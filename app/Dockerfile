FROM python:3.11-slim

WORKDIR /opt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /opt/app
ENV PYTHONPATH=/opt

CMD ["python", "-m", "app.bot"]
