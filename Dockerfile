# Use the official Python image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy project files into the container
COPY . .

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

# Create a directory for data
RUN mkdir -p /app/data

# Set environment variables for the container
ENV PYTHONUNBUFFERED=1

# Expose port if required
# EXPOSE 8000  # Uncomment and specify if the bot listens on certain ports

# Command to run the bot
CMD ["python", "govnobot.py"]
