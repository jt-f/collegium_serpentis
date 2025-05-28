#!/bin/bash
poetry run black --line-length=88 --exclude='/(\.git|\.venv|venv|env|build|dist|__pycache__)/' src tests
