# Use an official lightweight Python image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirement file first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

# Expose any required ports (if needed for logging or monitoring)
EXPOSE 8080

# Run the script
CMD ["python", "-u", "HFT_API.py"]
