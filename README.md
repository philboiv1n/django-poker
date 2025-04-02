# Django Poker ♦︎ ♣︎ ♥︎ ♠︎

A basic Python/Django poker game project using Docker.

## Features

- Simple Texas Hold’em Poker in a browser
- Up to 10 players
- No real money
- Player profiles
- Runs on Django with SQLite, Redis, and WebSockets
- Basic HTML front-end using Tailwind and vanilla JavaScript

## Development Environment Setup

Here’s a step-by-step guide to running this project locally using Visual Studio Code and Docker.

1. Clone the repository
Open VS Code and clone this repository locally:
```
git clone https://github.com/philboiv1n/django-poker.git
```

2. Generate a Django secret key
You can generate a key in different ways. Here’s a quick Python snippet:
```
python -c 'import random; key="".join([random.choice("abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)") for i 
in range(50)]); print(key)'
```

3. Create a .env file in the root of your project and add the following environment variables:
```
SECRET_KEY=your_secret_key_here
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
REDIS_HOST=redis
REDIS_PORT=6379
```
Set DEBUG=False for production.

4. Ensure Docker Engine is running
Make sure Docker is installed and the engine is started.

5. Build and start the Docker container
From your project root directory, run:
```
docker compose up --build
```

6. Access the application
Open you browser and visit http://localhost

7. Connect to the container through Visual Studio Code
- Click the Remote Explorer icon (bottom-left corner)
- Select "Attach to Running Container..."
- Choose the container named /poker-django


8. Set up the database (SQLite)
Inside the container, navigate to the project directory (/poker) and run:
```
python manage.py makemigrations
python manage.py migrate
```

9. Create a superuser for the admin panel
```
python manage.py createsuperuser
```
Note: You won’t be able to play with this user unless you assign it to a profile.
You can create new users from the admin panel, which will automatically generate a profile for them.