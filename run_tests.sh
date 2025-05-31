#!/bin/bash
TEST_PATH=${1:-tests/}
poetry run pytest -s -vvv --cov-report term-missing --cov=src $TEST_PATH