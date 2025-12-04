FROM python:3.10-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY gpu_container_exporter.py .

# Make the script executable
RUN chmod +x gpu_container_exporter.py

EXPOSE 9500

# nvidia-smi will be mounted from host via volume
CMD ["python", "-u", "gpu_container_exporter.py", "--port", "9500", "--interval", "10"]
