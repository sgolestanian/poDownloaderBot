FROM python:3.12.5-slim

# تنظیم میرور رانفلر برای apt (Ubuntu) - بدون بررسی GPG
# RUN echo "deb [trusted=yes] http://mirror-linux.runflare.com/debian bookworm main" > /etc/apt/sources.list && \
#     echo "deb [trusted=yes] http://mirror-linux.runflare.com/debian-security bookworm-security main" >> /etc/apt/sources.list

RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# تنظیم working directory
WORKDIR /app

# کپی فایل‌های requirements
COPY requirements.txt .

# نصب کتابخانه‌های پایتون
RUN pip install --no-cache-dir -r requirements.txt

# کپی کل پروژه
COPY . .

# اجرای برنامه
CMD ["python", "main.py"]
