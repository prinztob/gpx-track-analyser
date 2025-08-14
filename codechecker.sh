pyenv local 3.13
poetry install --with dev
poetry run ruff check --fix
poetry run ruff format
poetry run mypy .
poetry run pytest test