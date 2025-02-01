# Django Poker

A basic Python / Django poker game project using Docker.

## Random notes

Inactive player can be removed manually with this command:

```
python manage.py remove_inactive_players
```

You need to create a .env file with the following :

```
DJANGO_SECRET_KEY=your-secret-key-here
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
REDIS_HOST=redis
REDIS_PORT=6379
```
