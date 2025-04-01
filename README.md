# Django Poker ♦︎ ♣︎ ♥︎ ♠︎

A basic Python / Django poker game project using Docker.

## Features

- Simple Texas Hold'em Poker in a browser
- Basic HTML display (for now)
- Up to 10 players
- No real money
- Admin manage tables and user accounts (invite only for now)
- Player Stats
- Player Profile

## Environment Variables

To run this project, you will need to add the following environment variables to your .env file

```
SECRET_KEY=your_secret_key_here
DEBUG=False
ALLOWED_HOSTS=127.0.0.1,localhost
REDIS_HOST=redis
REDIS_PORT=6379
```

## Database

After cloning the repository, you'll need to create a fresh local database (SQLite)

```
python manage.py makemigrations
python manage.py migrate
```

Then you can create a superuser for the admin panel.
```
python manage.py createsuperuser
```
Please note that you won't be able to play with this user, unless you assign it to a profile.
You can create new users with this admin account (which will create profile automatically)