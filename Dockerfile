# Use Python 3.13 Alpine as the base image
FROM python:3.13-alpine

# Prevents Python from writing .pyc files to disk
ENV PYTHONDONTWRITEBYTECODE 1

# Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED 1

# Set working directory
WORKDIR /code

# Install system-level dependencies
# RUN apk add --no-cache build-base
RUN apk add --no-cache \
    build-base \
    nodejs \
    npm

# Copy requirements first for better caching
COPY ./requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the project into the container
COPY . /code

# Add the project directory to Python's path
ENV PYTHONPATH=/code/poker:$PYTHONPATH

# Create directory for static files
RUN mkdir -p /code/poker/staticfiles

# Run Django's collectstatic command to gather all static files
RUN python /code/poker/manage.py collectstatic --noinput