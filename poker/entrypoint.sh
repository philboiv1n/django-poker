#!/bin/sh
# Run npm install (for Tailwind) in the static_src directory at container startup
cd /code/poker/theme/static_src && npm install
exec "$@"